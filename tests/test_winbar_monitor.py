import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import winbar_monitor as wm


ROOT = Path(__file__).parent / "fixtures"


class ParserTests(unittest.TestCase):
    def test_gpu_csv_handles_na(self):
        result = wm.parse_gpu_csv("RTX 4060, 42, N/A, 1024, 8192, 55, N/A\n")
        self.assertEqual(result[0]["utilization_gpu"], 42)
        self.assertIsNone(result[0]["utilization_memory"])
        self.assertIsNone(result[0]["power_draw"])

    def test_normalize_no_gpu_fixture(self):
        raw = json.loads((ROOT / "no_gpu.json").read_text())
        result = wm.normalize_metrics(raw)
        self.assertEqual(result["gpus"], [])
        self.assertEqual(result["cpu_percent"], 12)

    def test_normalize_na_fixture(self):
        raw = json.loads((ROOT / "na_gpu.json").read_text())
        result = wm.normalize_metrics(raw)
        self.assertEqual(len(result["gpus"]), 1)
        self.assertIsNone(result["gpus"][0]["temperature"])
        self.assertIsNone(result["cpu_percent"])

    def test_json_with_warning(self):
        self.assertEqual(wm._first_json_object("WARNING\n{" + '"cpu_percent": 1}' )["cpu_percent"], 1)


class CacheAndTimeoutTests(unittest.TestCase):
    def test_timeout_uses_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cache.json"
            metrics = json.loads((ROOT / "no_gpu.json").read_text())
            wm.write_cache(path, metrics, 1000)
            config = wm.Config(cache_path=path, ssh_timeout=1)
            with patch("winbar_monitor.collect_remote", side_effect=TimeoutError("timeout")):
                cached, at = wm.read_cache(config.cache_path)
            self.assertIsNotNone(cached)
            self.assertEqual(at, 1000)


if __name__ == "__main__":
    unittest.main()
