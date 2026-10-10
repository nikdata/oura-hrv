from core.blob_store import blob_path


class TestBlobPath:
    def test_hrv_path_unchanged(self):
        assert blob_path("hrv", "2026-10-10") == "hrv/2026/10/2026-10-10_sleep-hrv.json"

    def test_rhr_path_unchanged(self):
        assert blob_path("rhr", "2026-10-10") == "rhr/2026/10/2026-10-10_sleep-rhr.json"

    def test_sleep_path(self):
        assert blob_path("sleep", "2026-10-10") == "sleep/2026/10/2026-10-10_sleep.json"

    def test_readiness_path(self):
        assert blob_path("readiness", "2026-01-05") == "readiness/2026/01/2026-01-05_readiness.json"
