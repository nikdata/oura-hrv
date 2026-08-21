import json
import re
from pathlib import Path

import pytest

from core.data_processor import extract_hrv_timeseries, extract_rhr_data, process_sleep_document

EXAMPLE_DIR = Path(__file__).resolve().parent.parent / "example"

HRV_EXPECTED_KEYS = {"date", "date_readable", "timezone", "timezone_offset", "hrv", "unit", "source", "measurement_type"}
RHR_EXPECTED_KEYS = {"date", "date_readable", "timezone", "timezone_offset", "rhr", "unit", "source", "measurement_type"}


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
