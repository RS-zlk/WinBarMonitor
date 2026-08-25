import json
import subprocess
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

    def test_remote_timeout_message_is_short(self):
        with patch(
            "winbar_monitor.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["ssh", "encoded-command"], timeout=1),
        ):
            with self.assertRaisesRegex(RuntimeError, r"SSH/PowerShell 采集超时（1 秒）"):
                wm.collect_remote(wm.Config(ssh_timeout=1))

    def test_windows_seven_digit_boot_time(self):
        value = wm._uptime("2020-01-02T03:04:05.5000000+08:00")
        self.assertNotEqual(value, "N/A")


class RuntimeCompatibilityTests(unittest.TestCase):
    def test_macos_system_python_can_compile_plugin(self):
        interpreter = Path("/usr/bin/python3")
        if not interpreter.exists():
            self.skipTest("macOS system Python is unavailable")
        project_root = Path(__file__).resolve().parents[1]
        subprocess.run(
            [str(interpreter), "-m", "py_compile", "winbar_monitor.py", "winbar.1m.py"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
