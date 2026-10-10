import json
from unittest.mock import patch

import azure.functions as func
import pytest

from functions import webhook_receiver as wr

NIGHT = {
    "id": "night-id", "day": "2026-10-10", "type": "long_sleep",
    "bedtime_start": "2026-10-09T23:12:00-05:00", "bedtime_end": "2026-10-10T07:00:00-05:00",
    "total_sleep_duration": 25200, "time_in_bed": 28080,
    "hrv": {"timestamp": "2026-10-09T23:12:00-05:00", "interval": 300, "items": [None, 25.0, 30.5]},
    "heart_rate": {"timestamp": "2026-10-09T23:12:00-05:00", "interval": 300, "items": [62, 55, 57]},
    "lowest_heart_rate": 55,
}

READINESS = {"id": "r-id", "day": "2026-10-10", "score": 82, "contributors": {}, "timestamp": "2026-10-10T00:00:00-05:00"}


def _post(payload):
    return func.HttpRequest(
        method="POST", url="/api/webhook",
        headers={"x-oura-signature": "sig", "x-oura-timestamp": "ts"},
        body=json.dumps(payload).encode(),
    )


@pytest.fixture
def mocks():
    """Stub out everything that talks to Oura or Azure."""
    with patch.object(wr, "get_client_credentials", return_value=("id", "secret")), \
         patch.object(wr, "verify_hmac", return_value=True) as verify, \
         patch.object(wr, "fetch_sleep_document") as fetch_sleep, \
         patch.object(wr, "fetch_readiness_document") as fetch_readiness, \
         patch.object(wr, "upload_blob", side_effect=lambda m, d, b: f"{m}/{d}") as upload:
        yield {"verify": verify, "fetch_sleep": fetch_sleep, "fetch_readiness": fetch_readiness, "upload": upload}


def _uploaded_metrics(upload_mock):
    return sorted(call.args[0] for call in upload_mock.call_args_list)


class TestWebhookReceiver:
    @pytest.mark.parametrize("event_type", ["create", "update"])
    def test_overnight_sleep_writes_hrv_rhr_sleep(self, mocks, event_type):
        mocks["fetch_sleep"].return_value = NIGHT
        resp = wr.webhook_receiver(_post({"data_type": "sleep", "event_type": event_type, "object_id": "night-id"}))

        assert resp.status_code == 200
        mocks["fetch_sleep"].assert_called_once_with("night-id")
        assert _uploaded_metrics(mocks["upload"]) == ["hrv", "rhr", "sleep"]
        for call in mocks["upload"].call_args_list:
            assert call.args[1] == "2026-10-10"
        sleep_call = next(c for c in mocks["upload"].call_args_list if c.args[0] == "sleep")
        assert json.loads(sleep_call.args[2])[0]["total_sleep_seconds"] == 25200

    @pytest.mark.parametrize("nap_type", ["sleep", "late_nap", "rest", "deleted"])
    def test_nap_writes_nothing(self, mocks, nap_type):
        mocks["fetch_sleep"].return_value = {**NIGHT, "type": nap_type}
        resp = wr.webhook_receiver(_post({"data_type": "sleep", "event_type": "create", "object_id": "nap-id"}))

        assert resp.status_code == 200
        mocks["upload"].assert_not_called()

    def test_readiness_writes_readiness_only(self, mocks):
        mocks["fetch_readiness"].return_value = READINESS
        resp = wr.webhook_receiver(_post({"data_type": "daily_readiness", "event_type": "create", "object_id": "r-id"}))

        assert resp.status_code == 200
        mocks["fetch_readiness"].assert_called_once_with("r-id")
        mocks["fetch_sleep"].assert_not_called()
        assert _uploaded_metrics(mocks["upload"]) == ["readiness"]
        assert json.loads(mocks["upload"].call_args.args[2]) == [{
            "day": "2026-10-10", "readiness": 82,
            "source": "oura_ring_readiness", "measurement_type": "readiness_score",
        }]

    def test_readiness_without_score_writes_nothing(self, mocks):
        mocks["fetch_readiness"].return_value = {**READINESS, "score": None}
        resp = wr.webhook_receiver(_post({"data_type": "daily_readiness", "event_type": "update", "object_id": "r-id"}))

        assert resp.status_code == 200
        mocks["upload"].assert_not_called()

    def test_unknown_data_type_ignored(self, mocks):
        resp = wr.webhook_receiver(_post({"data_type": "daily_activity", "event_type": "create", "object_id": "x"}))

        assert resp.status_code == 200
        mocks["fetch_sleep"].assert_not_called()
        mocks["fetch_readiness"].assert_not_called()
        mocks["upload"].assert_not_called()

    def test_delete_event_ignored(self, mocks):
        resp = wr.webhook_receiver(_post({"data_type": "sleep", "event_type": "delete", "object_id": "x"}))

        assert resp.status_code == 200
        mocks["upload"].assert_not_called()

    def test_missing_object_id(self, mocks):
        resp = wr.webhook_receiver(_post({"data_type": "daily_readiness", "event_type": "create"}))
        assert resp.status_code == 400

    def test_bad_signature_rejected(self, mocks):
        mocks["verify"].return_value = False
        resp = wr.webhook_receiver(_post({"data_type": "sleep", "event_type": "create", "object_id": "x"}))

        assert resp.status_code == 403
        mocks["fetch_sleep"].assert_not_called()
        mocks["upload"].assert_not_called()
