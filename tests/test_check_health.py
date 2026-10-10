import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_health  # noqa: E402


class TestFixCommands:
    def test_one_fill_missing_per_date(self):
        assert check_health.fix_commands({
            "2026-10-09": ["sleep"],
            "2026-10-10": list(check_health.METRICS),
        }) == [
            "python scripts/fill_missing.py 2026-10-09",
            "python scripts/fill_missing.py 2026-10-10",
        ]


class TestTodaySleepParams:
    def test_end_date_is_tomorrow(self):
        # Oura's sleep list leaves out end_date, so asking for today..today returns nothing
        from datetime import date
        assert check_health.today_sleep_params(date(2026, 10, 10)) == {
            "start_date": "2026-10-10", "end_date": "2026-10-11",
        }

    def test_month_rollover(self):
        from datetime import date
        assert check_health.today_sleep_params(date(2026, 10, 31))["end_date"] == "2026-11-01"


class TestCheckWebhookSubscriptions:
    @staticmethod
    def _subs(*pairs):
        return [{"id": f"id-{d}-{e}-0000000000", "data_type": d, "event_type": e} for d, e in pairs]

    def _run(self, subs):
        with patch("core.oura_client.list_webhook_subscriptions", return_value=subs):
            return check_health.check_webhook_subscriptions()

    def test_all_four_present(self):
        assert self._run(self._subs(*check_health.REQUIRED_SUBSCRIPTIONS)) is True

    def test_missing_readiness(self, capsys):
        assert self._run(self._subs(("sleep", "create"), ("sleep", "update"))) is False
        out = capsys.readouterr().out
        assert "daily_readiness/create" in out
        assert "daily_readiness/update" in out


class TestCheckBlobStorage:
    def _run(self, existing, days=1):
        """Run the blob check against a fake container holding `existing` blob names."""
        container = MagicMock()

        def get_blob_client(name):
            client = MagicMock()
            if name in existing:
                client.get_blob_properties.return_value = MagicMock(last_modified=datetime(2026, 10, 10, 12, 0))
            else:
                client.get_blob_properties.side_effect = Exception("not found")
            return client

        container.get_blob_client.side_effect = get_blob_client
        service = MagicMock()
        service.get_container_client.return_value = container
        with patch("azure.storage.blob.BlobServiceClient", return_value=service), \
             patch("azure.identity.DefaultAzureCredential"):
            return check_health.check_blob_storage(days)

    def _today_paths(self, metrics):
        today = datetime.now().date().isoformat()
        return {check_health.blob_path(m, today) for m in metrics}

    def test_all_present(self):
        assert self._run(self._today_paths(check_health.METRICS)) is True

    def test_missing_sleep_and_readiness(self, capsys):
        assert self._run(self._today_paths(["hrv", "rhr"])) is False
        out = capsys.readouterr().out
        assert "missing Sleep, Readiness" in out
        assert "fill_missing.py" in out
