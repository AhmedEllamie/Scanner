from __future__ import annotations

import unittest
from unittest.mock import patch

import requests

from scanner_service.client import ScannerServiceClient


class ScannerServiceClientTests(unittest.TestCase):
    def test_unavailable_service_raises_request_exception(self) -> None:
        client = ScannerServiceClient("http://127.0.0.1:1", timeout_seconds=0.2)
        with self.assertRaises(requests.RequestException):
            client.health()

    def test_wait_for_job_timeout_raises(self) -> None:
        client = ScannerServiceClient("http://127.0.0.1:8008", timeout_seconds=0.2)
        with patch.object(
            ScannerServiceClient,
            "get_job",
            return_value={"ok": True, "job": {"status": "running"}},
        ):
            with self.assertRaises(TimeoutError):
                client.wait_for_job("job-1", poll_interval_seconds=0.01, timeout_seconds=0.05)


if __name__ == "__main__":
    unittest.main()

