from unittest.mock import MagicMock, patch

from core import oura_client as oc


def _response(status, body=None):
    r = MagicMock(status_code=status)
    r.json.return_value = body or {}
    return r


class TestFetchReadinessForDay:
    def test_asks_for_that_day_and_returns_its_doc(self):
        doc = {"day": "2026-10-15", "score": 74}
        with patch.object(oc, "refresh_access_token", return_value="tok"), \
             patch.object(oc.requests, "get", return_value=_response(200, {"data": [doc]})) as get:
            assert oc.fetch_readiness_for_day("2026-10-15") == doc

        assert get.call_args.args[0].endswith("/usercollection/daily_readiness")
        assert get.call_args.kwargs["params"] == {"start_date": "2026-10-15", "end_date": "2026-10-15"}
        assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer tok"

    def test_ignores_other_days_and_returns_none_when_missing(self):
        body = {"data": [{"day": "2026-10-14", "score": 70}]}
        with patch.object(oc, "refresh_access_token", return_value="tok"), \
             patch.object(oc.requests, "get", return_value=_response(200, body)):
            assert oc.fetch_readiness_for_day("2026-10-15") is None

    def test_retries_once_on_401(self):
        doc = {"day": "2026-10-15", "score": 74}
        with patch.object(oc, "refresh_access_token", side_effect=["old", "new"]), \
             patch.object(oc.requests, "get",
                          side_effect=[_response(401), _response(200, {"data": [doc]})]) as get:
            assert oc.fetch_readiness_for_day("2026-10-15") == doc

        assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer new"
