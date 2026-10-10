import json
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import fill_missing  # noqa: E402

EXAMPLE_DIR = Path(__file__).resolve().parent.parent / "example"


def _fixture(name):
    return json.loads((EXAMPLE_DIR / name).read_text())


DOZE, NIGHT = _fixture("2026-10-06_sleep-sessions.json")["data"]
READINESS = _fixture("2026-10-10_daily-readiness.json")


def _metrics(fills):
    return {m: data for m, data in fills}


class TestBuildFills:
    def test_only_missing_metrics_are_built(self):
        fills = fill_missing.build_fills("2026-10-06", ["sleep"], [DOZE, NIGHT], [])
        assert list(_metrics(fills)) == ["sleep"]

    def test_uses_overnight_sleep_not_doze(self):
        # The doze comes first; the old script used sessions[-1] and would
        # have used whichever came last
        for order in ([DOZE, NIGHT], [NIGHT, DOZE]):
            fills = _metrics(fill_missing.build_fills("2026-10-06", ["hrv", "rhr", "sleep"], order, []))
            # First HRV reading is from the overnight session (starts 22:26), not the 20:40 doze
            assert json.loads(fills["hrv"])[0]["date_readable"].startswith("2026-10-05 22:")
            assert json.loads(fills["sleep"])[0]["bedtime_start_readable"] == "2026-10-05 22:26:30"

    def test_doze_only_day_has_no_data(self):
        fills = _metrics(fill_missing.build_fills("2026-10-06", ["hrv", "sleep"], [DOZE], []))
        assert fills == {"hrv": None, "sleep": None}

    def test_readiness_matched_by_day(self):
        other_day = {**READINESS, "day": "2026-10-09", "score": 50}
        fills = _metrics(fill_missing.build_fills("2026-10-10", ["readiness"], [], [other_day, READINESS]))
        assert json.loads(fills["readiness"])[0]["readiness"] == 67

    def test_no_readiness_doc(self):
        assert _metrics(fill_missing.build_fills("2026-10-10", ["readiness"], [], [])) == {"readiness": None}

    def test_all_four(self):
        fills = _metrics(fill_missing.build_fills(
            "2026-10-06", list(fill_missing.ALL_METRICS), [DOZE, NIGHT], [{**READINESS, "day": "2026-10-06"}],
        ))
        assert set(fills) == {"hrv", "rhr", "sleep", "readiness"}
        assert all(fills.values())


class TestFetchesSingleDayCorrectly:
    def test_sleep_request_includes_the_day(self):
        # Oura's sleep list leaves out end_date, so a one-day fill must ask for day..day+1
        with patch("backfill.fetch_range", return_value=[]) as fetch, patch("backfill.time.sleep"):
            fill_missing.fetch_all("token", "sleep", date(2026, 10, 6), date(2026, 10, 6))
        assert fetch.call_args.args[2:] == ("2026-10-06", "2026-10-07")
