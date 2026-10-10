import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import backfill  # noqa: E402


def _session(day, sleep_type="long_sleep", total=25200, sid="s"):
    return {
        "id": sid, "day": day, "type": sleep_type,
        "bedtime_start": f"{day}T00:30:00-05:00", "bedtime_end": f"{day}T07:30:00-05:00",
        "total_sleep_duration": total, "time_in_bed": total + 1800,
        "hrv": {"timestamp": f"{day}T00:30:00-05:00", "interval": 300, "items": [30.0, 32.0]},
        "heart_rate": {"timestamp": f"{day}T00:30:00-05:00", "interval": 300, "items": [60, 54]},
        "lowest_heart_rate": 54,
    }


class TestPickOvernightByDay:
    def test_nap_after_night_does_not_replace_it(self):
        night = _session("2026-10-10", sid="night")
        nap = _session("2026-10-10", sleep_type="sleep", total=3600, sid="nap")
        assert pick_ids([night, nap]) == {"2026-10-10": "night"}

    def test_only_naps_means_no_day(self):
        sessions = [_session("2026-10-10", sleep_type=t) for t in ("sleep", "late_nap", "rest", "deleted")]
        assert backfill.pick_overnight_by_day(sessions) == {}

    def test_split_night_keeps_longest(self):
        first = _session("2026-10-10", total=9000, sid="first")
        second = _session("2026-10-10", total=16000, sid="second")
        assert pick_ids([first, second]) == {"2026-10-10": "second"}
        assert pick_ids([second, first]) == {"2026-10-10": "second"}

    def test_multiple_days(self):
        sessions = [_session("2026-10-09", sid="a"), _session("2026-10-10", sid="b")]
        assert pick_ids(sessions) == {"2026-10-09": "a", "2026-10-10": "b"}


def pick_ids(sessions):
    return {day: s["id"] for day, s in backfill.pick_overnight_by_day(sessions).items()}


class TestBuildSleepFiles:
    def test_default_metrics_only_sleep(self):
        files = backfill.build_sleep_files(_session("2026-10-10"), ["sleep", "readiness"])
        assert [m for m, _, _ in files] == ["sleep"]
        assert json.loads(files[0][2])[0]["total_sleep_seconds"] == 25200

    def test_all_metrics(self):
        files = backfill.build_sleep_files(_session("2026-10-10"), list(backfill.ALL_METRICS))
        assert [m for m, _, _ in files] == ["hrv", "rhr", "sleep"]
        assert all(day == "2026-10-10" and data for _, day, data in files)

    def test_hrv_rhr_only(self):
        files = backfill.build_sleep_files(_session("2026-10-10"), ["hrv", "rhr"])
        assert [m for m, _, _ in files] == ["hrv", "rhr"]


class TestParseMetrics:
    def test_valid(self):
        assert backfill.parse_metrics("hrv, rhr,sleep") == ["hrv", "rhr", "sleep"]

    @pytest.mark.parametrize("value", ["bogus", "sleep,steps", ""])
    def test_invalid(self, value):
        import argparse
        with pytest.raises(argparse.ArgumentTypeError):
            backfill.parse_metrics(value)


class TestFetchRange:
    def test_follows_next_token(self):
        pages = [
            {"data": [{"id": 1}], "next_token": "abc"},
            {"data": [{"id": 2}], "next_token": None},
        ]
        responses = [MagicMock(status_code=200, json=MagicMock(return_value=p)) for p in pages]
        with patch.object(backfill.requests, "get", side_effect=responses) as get:
            docs = backfill.fetch_range("token", "daily_readiness", "2026-01-01", "2026-01-30")

        assert docs == [{"id": 1}, {"id": 2}]
        assert get.call_count == 2
        assert get.call_args_list[1].kwargs["params"]["next_token"] == "abc"
        assert "daily_readiness" in get.call_args_list[0].args[0]


class TestFetchAll:
    def _requested_ranges(self, collection, start, end):
        from datetime import date
        with patch.object(backfill, "fetch_range", return_value=[]) as fetch, \
             patch.object(backfill.time, "sleep"):
            backfill.fetch_all("token", collection, date.fromisoformat(start), date.fromisoformat(end))
        return [(c.args[2], c.args[3]) for c in fetch.call_args_list]

    def test_sleep_asks_for_one_extra_day(self):
        # Oura's sleep list excludes end_date, so each chunk's end is pushed out by a day
        assert self._requested_ranges("sleep", "2026-01-01", "2026-02-15") == [
            ("2026-01-01", "2026-02-01"),
            ("2026-02-01", "2026-02-16"),
        ]

    def test_readiness_uses_exact_range(self):
        assert self._requested_ranges("daily_readiness", "2026-01-01", "2026-02-15") == [
            ("2026-01-01", "2026-01-31"),
            ("2026-02-01", "2026-02-15"),
        ]
