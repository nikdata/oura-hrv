import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.data_processor import (
    extract_hrv_timeseries, extract_rhr_data, is_overnight_sleep,
    process_readiness_document, process_sleep_document, process_sleep_times,
)

EXAMPLE_DIR = Path(__file__).resolve().parent.parent / "example"

HRV_EXPECTED_KEYS = {"date", "date_readable", "timezone", "timezone_offset", "hrv", "unit", "source", "measurement_type"}
RHR_EXPECTED_KEYS = {"date", "date_readable", "timezone", "timezone_offset", "rhr", "unit", "source", "measurement_type"}
SLEEP_EXPECTED_KEYS = {
    "day", "bedtime_start", "bedtime_start_readable", "bedtime_end", "bedtime_end_readable",
    "timezone_offset", "total_sleep_seconds", "total_sleep_hours", "time_in_bed_seconds",
    "source", "measurement_type",
}
READINESS_EXPECTED_KEYS = {"day", "readiness", "source", "measurement_type"}


def _load_fixture(name: str):
    with open(EXAMPLE_DIR / name) as f:
        return json.load(f)


# --- HRV fixture tests ---

class TestHRVShape:
    @pytest.fixture
    def hrv_fixture(self):
        return _load_fixture("2026-06-01_sleep-hrv.json")

    def test_is_bare_array(self, hrv_fixture):
        assert isinstance(hrv_fixture, list)

    def test_has_entries(self, hrv_fixture):
        assert len(hrv_fixture) > 0

    def test_key_set(self, hrv_fixture):
        for entry in hrv_fixture:
            assert set(entry.keys()) == HRV_EXPECTED_KEYS

    def test_date_is_int(self, hrv_fixture):
        for entry in hrv_fixture:
            assert isinstance(entry["date"], int)

    def test_hrv_is_float(self, hrv_fixture):
        for entry in hrv_fixture:
            assert isinstance(entry["hrv"], float)

    def test_date_readable_format(self, hrv_fixture):
        for entry in hrv_fixture:
            assert re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", entry["date_readable"])

    def test_timezone_present(self, hrv_fixture):
        for entry in hrv_fixture:
            assert entry["timezone"]
            assert entry["timezone_offset"]

    def test_static_fields(self, hrv_fixture):
        for entry in hrv_fixture:
            assert entry["unit"] == "ms"
            assert entry["source"] == "oura_ring_rmssd"
            assert entry["measurement_type"] == "rMSSD"


# --- RHR fixture tests ---

class TestRHRShape:
    @pytest.fixture
    def rhr_fixture(self):
        return _load_fixture("2026-06-01_sleep-rhr.json")

    def test_is_bare_array(self, rhr_fixture):
        assert isinstance(rhr_fixture, list)

    def test_has_entries(self, rhr_fixture):
        assert len(rhr_fixture) > 0

    def test_key_set(self, rhr_fixture):
        for entry in rhr_fixture:
            assert set(entry.keys()) == RHR_EXPECTED_KEYS

    def test_rhr_is_int(self, rhr_fixture):
        for entry in rhr_fixture:
            assert isinstance(entry["rhr"], int)

    def test_date_is_int(self, rhr_fixture):
        for entry in rhr_fixture:
            assert isinstance(entry["date"], int)

    def test_static_fields(self, rhr_fixture):
        for entry in rhr_fixture:
            assert entry["unit"] == "bpm"
            assert entry["source"] == "oura_ring_sleep"
            assert entry["measurement_type"] == "lowest_heart_rate"


# --- Round-trip test: process_sleep_document produces fixture-compatible output ---

class TestProcessSleepDocument:
    @pytest.fixture
    def mock_sleep_doc(self):
        """A minimal sleep document matching what Oura returns."""
        return {
            "id": "test-id",
            "day": "2026-01-15",
            "hrv": {
                "timestamp": "2026-01-14T22:00:00+00:00",
                "interval": 300,
                "items": [None, 25.0, 30.5, 0, 18.0],
            },
            "heart_rate": {
                "timestamp": "2026-01-14T22:00:00+00:00",
                "interval": 300,
                "items": [62, 58, 55, 60, 57],
            },
            "lowest_heart_rate": 55,
        }

    def test_hrv_output_shape(self, mock_sleep_doc):
        hrv_bytes, _, sleep_date = process_sleep_document(mock_sleep_doc)
        assert sleep_date == "2026-01-15"
        assert hrv_bytes is not None

        hrv = json.loads(hrv_bytes)
        assert isinstance(hrv, list)
        # 3 valid readings (None and 0 filtered out)
        assert len(hrv) == 3
        for entry in hrv:
            assert set(entry.keys()) == HRV_EXPECTED_KEYS
            assert isinstance(entry["date"], int)
            assert isinstance(entry["hrv"], float)

    def test_rhr_output_shape(self, mock_sleep_doc):
        _, rhr_bytes, sleep_date = process_sleep_document(mock_sleep_doc)
        assert rhr_bytes is not None

        rhr = json.loads(rhr_bytes)
        assert isinstance(rhr, list)
        assert len(rhr) == 1
        entry = rhr[0]
        assert set(entry.keys()) == RHR_EXPECTED_KEYS
        assert isinstance(entry["rhr"], int)
        assert entry["rhr"] == 55

    def test_no_hrv_data(self):
        doc = {
            "id": "x", "day": "2026-01-15",
            "hrv": {"timestamp": "2026-01-14T22:00:00+00:00", "interval": 300, "items": [None, 0]},
            "heart_rate": {"timestamp": "2026-01-14T22:00:00+00:00", "interval": 300, "items": []},
            "lowest_heart_rate": None,
        }
        hrv_bytes, rhr_bytes, _ = process_sleep_document(doc)
        assert hrv_bytes is None
        assert rhr_bytes is None


class TestIsOvernightSleep:
    def test_long_sleep_is_overnight(self):
        assert is_overnight_sleep({"type": "long_sleep"})

    @pytest.mark.parametrize("sleep_type", ["sleep", "late_nap", "rest", "deleted", None])
    def test_other_types_are_not_overnight(self, sleep_type):
        assert not is_overnight_sleep({"type": sleep_type})

    def test_missing_type_is_not_overnight(self):
        assert not is_overnight_sleep({})


class TestProcessSleepTimes:
    @staticmethod
    def _doc(start, end, total=25200, in_bed=28080):
        return {
            "id": "test-id", "day": "2026-10-10", "type": "long_sleep",
            "bedtime_start": start, "bedtime_end": end,
            "total_sleep_duration": total, "time_in_bed": in_bed,
        }

    def test_output_shape(self):
        doc = self._doc("2026-10-09T23:12:00-05:00", "2026-10-10T07:00:00-05:00")
        sleep_bytes, sleep_date = process_sleep_times(doc)
        assert sleep_date == "2026-10-10"

        sleep = json.loads(sleep_bytes)
        assert isinstance(sleep, list)
        assert len(sleep) == 1
        entry = sleep[0]
        assert set(entry.keys()) == SLEEP_EXPECTED_KEYS
        assert entry["day"] == "2026-10-10"
        assert entry["total_sleep_seconds"] == 25200
        assert entry["total_sleep_hours"] == 7.0
        assert entry["time_in_bed_seconds"] == 28080
        assert entry["source"] == "oura_ring_sleep"
        assert entry["measurement_type"] == "long_sleep"

    def test_times_us_central(self):
        doc = self._doc("2026-10-09T23:12:00-05:00", "2026-10-10T07:00:00-05:00")
        entry = json.loads(process_sleep_times(doc)[0])[0]
        # Readable times stay local to where you slept
        assert entry["bedtime_start_readable"] == "2026-10-09 23:12:00"
        assert entry["bedtime_end_readable"] == "2026-10-10 07:00:00"
        assert entry["timezone_offset"] == "-0500"
        # Unix times are absolute (UTC)
        assert entry["bedtime_start"] == int(datetime(2026, 10, 10, 4, 12, tzinfo=timezone.utc).timestamp())
        assert entry["bedtime_end"] == int(datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc).timestamp())
        assert isinstance(entry["bedtime_start"], int)

    def test_times_while_traveling(self):
        doc = self._doc("2026-10-09T22:30:00+01:00", "2026-10-10T06:45:00+01:00")
        entry = json.loads(process_sleep_times(doc)[0])[0]
        assert entry["bedtime_start_readable"] == "2026-10-09 22:30:00"
        assert entry["bedtime_end_readable"] == "2026-10-10 06:45:00"
        assert entry["timezone_offset"] == "+0100"
        assert entry["bedtime_start"] == int(datetime(2026, 10, 9, 21, 30, tzinfo=timezone.utc).timestamp())

    def test_hours_rounded(self):
        doc = self._doc("2026-10-09T23:00:00-05:00", "2026-10-10T07:00:00-05:00", total=26000)
        entry = json.loads(process_sleep_times(doc)[0])[0]
        assert entry["total_sleep_hours"] == 7.22

    def test_no_total_sleep(self):
        doc = self._doc("2026-10-09T23:00:00-05:00", "2026-10-10T07:00:00-05:00", total=None)
        sleep_bytes, sleep_date = process_sleep_times(doc)
        assert sleep_bytes is None
        assert sleep_date == "2026-10-10"

    def test_missing_bedtimes(self):
        doc = self._doc(None, None)
        assert process_sleep_times(doc)[0] is None


class TestProcessReadiness:
    @pytest.fixture
    def readiness_doc(self):
        """A daily_readiness document shaped like Oura's API response."""
        return {
            "id": "r-id",
            "day": "2026-10-10",
            "score": 82,
            "temperature_deviation": -0.1,
            "temperature_trend_deviation": 0.05,
            "timestamp": "2026-10-10T00:00:00-05:00",
            "contributors": {
                "activity_balance": 80, "body_temperature": 98, "hrv_balance": 75,
                "previous_day_activity": 85, "previous_night": 78, "recovery_index": 90,
                "resting_heart_rate": 88, "sleep_balance": 82, "sleep_regularity": 79,
            },
        }

    def test_output_shape(self, readiness_doc):
        readiness_bytes, day = process_readiness_document(readiness_doc)
        assert day == "2026-10-10"

        readiness = json.loads(readiness_bytes)
        assert isinstance(readiness, list)
        assert len(readiness) == 1
        entry = readiness[0]
        # Only day + score — contributors and temperature are left out
        assert set(entry.keys()) == READINESS_EXPECTED_KEYS
        assert entry["readiness"] == 82
        assert isinstance(entry["readiness"], int)
        assert entry["source"] == "oura_ring_readiness"
        assert entry["measurement_type"] == "readiness_score"

    def test_null_score(self, readiness_doc):
        readiness_doc["score"] = None
        readiness_bytes, day = process_readiness_document(readiness_doc)
        assert readiness_bytes is None
        assert day == "2026-10-10"


class TestSecurityHMAC:
    def test_verify_valid(self):
        import hashlib, hmac as hmac_mod
        from core.security import verify_hmac

        secret = "test-secret"
        timestamp = "2026-01-15T00:00:00Z"
        body = b'{"event_type": "create"}'
        mac = hmac_mod.new(secret.encode(), digestmod=hashlib.sha256)
        mac.update(timestamp.encode())
        mac.update(body)
        sig = mac.hexdigest().upper()
        assert verify_hmac(body, timestamp, sig, secret)

    def test_reject_invalid(self):
        from core.security import verify_hmac
        assert not verify_hmac(b"body", "2026-01-15T00:00:00Z", "bad-sig", "secret")


class TestRealOuraFixtures:
    """Trimmed copies of real Oura API responses (example/)."""

    def test_evening_doze_is_not_overnight(self):
        doze, night = _load_fixture("2026-10-06_sleep-sessions.json")["data"]
        assert doze["day"] == night["day"] == "2026-10-06"
        assert not is_overnight_sleep(doze)
        assert is_overnight_sleep(night)

    def test_sleep_times_from_real_session(self):
        _, night = _load_fixture("2026-10-06_sleep-sessions.json")["data"]
        entry = json.loads(process_sleep_times(night)[0])[0]
        assert set(entry.keys()) == SLEEP_EXPECTED_KEYS
        assert entry["bedtime_start_readable"] == "2026-10-05 22:26:30"
        assert entry["bedtime_end_readable"] == "2026-10-06 06:49:00"
        assert entry["timezone_offset"] == "-0500"
        assert entry["total_sleep_seconds"] == 27510
        assert entry["total_sleep_hours"] == 7.64

    def test_readiness_from_real_doc(self):
        entry = json.loads(process_readiness_document(_load_fixture("2026-10-10_daily-readiness.json"))[0])[0]
        assert entry == {
            "day": "2026-10-10", "readiness": 67,
            "source": "oura_ring_readiness", "measurement_type": "readiness_score",
        }
