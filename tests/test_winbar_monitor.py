import json
import os
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

    def test_render_uses_swiftbar_separator_only_for_actions(self):
        raw = json.loads((ROOT / "na_gpu.json").read_text())
        output = wm.render(wm.normalize_metrics(raw), collected_at=1000)
        lines = output.splitlines()
        self.assertIn("sfimage=desktopcomputer", lines[0])
        self.assertIn("sfcolor=green", lines[0])
        self.assertIn("dropdown=false", lines[0])
        self.assertIn("🟢 在线 · GPU", lines[2])
        self.assertNotIn("refreshOnOpen", output)
        self.assertIn("CPU 占用 · N/A", lines)
        self.assertTrue(any(line.startswith("温度 · ") for line in lines))
        parameter_lines = [line for line in lines[1:] if " | " in line]
        self.assertEqual(len(parameter_lines), 2)
        self.assertTrue(parameter_lines[0].startswith("🔄 手动刷新"))
        self.assertTrue(parameter_lines[1].startswith("🔐 打开 SSH"))


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

    def test_install_keeps_support_module_hidden(self):
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            env = dict(os.environ)
            env["SWIFTBAR_PLUGIN_DIR"] = folder
            env["WINBAR_REFRESH_SECONDS"] = "7"
            subprocess.run(
                [str(project_root / "install.sh")],
                cwd=project_root,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            plugin = Path(folder) / "winbar.7s.py"
            support = Path(folder) / ".winbar_lib" / "winbar_monitor.py"
            self.assertTrue(plugin.exists())
            self.assertTrue(os.access(plugin, os.X_OK))
            self.assertTrue(support.exists())
            self.assertFalse(os.access(support, os.X_OK))
            self.assertFalse((Path(folder) / "winbar_monitor.py").exists())


if __name__ == "__main__":
    unittest.main()
