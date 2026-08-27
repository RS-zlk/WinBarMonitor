import json
import os
import base64
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import winbar_monitor as wm


ROOT = Path(__file__).parent / "fixtures"


class ParserTests(unittest.TestCase):
    def test_default_alias_is_generic(self):
        self.assertEqual(wm.Config().ssh_alias, "windows-monitor")

    def test_hostname_is_normalized_and_can_be_missing(self):
        self.assertEqual(wm.normalize_metrics({"hostname": "  DESKTOP-TEST  "})["hostname"], "DESKTOP-TEST")
        self.assertIsNone(wm.normalize_metrics({})["hostname"])

    def test_powershell_payload_has_hostname_fallback(self):
        self.assertIn("$env:COMPUTERNAME", wm.POWERSHELL_SCRIPT)
        self.assertIn("Win32_ComputerSystem", wm.POWERSHELL_SCRIPT)

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
        self.assertEqual(result["hostname"], "TEST-WINDOWS")

    def test_normalize_multiple_gpus(self):
        result = wm.normalize_metrics({"gpus": [{"name": "GPU A"}, {"name": "GPU B"}]})
        self.assertEqual([gpu["name"] for gpu in result["gpus"]], ["GPU A", "GPU B"])

    def test_render_without_gpu_omits_gpu_section(self):
        rendered = wm.render(wm.normalize_metrics({"hostname": "CPU-ONLY", "gpus": []}))
        self.assertNotIn("🎮", rendered)
        self.assertIn("🔥 Top 进程", rendered)

    def test_render_multiple_gpus(self):
        rendered = wm.render(wm.normalize_metrics({"gpus": [{"name": "GPU A"}, {"name": "GPU B"}]}))
        self.assertIn("🎮 GPU 1 · GPU A", rendered)
        self.assertIn("🎮 GPU 2 · GPU B", rendered)

    def test_normalize_na_fixture(self):
        raw = json.loads((ROOT / "na_gpu.json").read_text())
        result = wm.normalize_metrics(raw)
        self.assertEqual(len(result["gpus"]), 1)
        self.assertIsNone(result["gpus"][0]["temperature"])
        self.assertIsNone(result["cpu_percent"])

    def test_json_with_warning(self):
        self.assertEqual(wm._first_json_object("WARNING\n{" + '"cpu_percent": 1}' )["cpu_percent"], 1)

    def test_render_makes_read_only_metrics_open_the_detail_page(self):
        raw = json.loads((ROOT / "na_gpu.json").read_text())
        output = wm.render(wm.normalize_metrics(raw), collected_at=1000)
        lines = output.splitlines()
        self.assertIn("image=", lines[0])
        self.assertNotIn("sfimage=", lines[0])
        encoded_icon = lines[0].split("image=", 1)[1].split(" ", 1)[0]
        self.assertTrue(base64.b64decode(encoded_icon).startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIn("dropdown=false", lines[0])
        self.assertIn("🟢 在线 · GPU", lines[2])
        self.assertIn("bash=/usr/bin/open", lines[2])
        self.assertNotIn("refreshOnOpen", output)
        self.assertTrue(any(line.startswith("CPU 占用 · N/A | bash=/usr/bin/open") for line in lines))
        self.assertTrue(any(line.startswith("温度 · ") and "bash=/usr/bin/open" in line for line in lines))
        parameter_lines = [line for line in lines[1:] if " | " in line]
        self.assertGreater(len(parameter_lines), 3)
        self.assertTrue(any(line.startswith("📊 查看历史统计") for line in parameter_lines))
        self.assertTrue(any(line.startswith("🔄 手动刷新") for line in parameter_lines))
        self.assertTrue(any(line.startswith("🔐 打开 SSH") for line in parameter_lines))
        self.assertTrue(any(line.startswith("🖥️ TEST-WINDOWS | bash=/usr/bin/open") for line in lines))

    def test_render_hostname_fallback_order(self):
        with patch.dict(os.environ, {"WINBAR_SSH_ALIAS": "my-windows"}, clear=False):
            rendered = wm.render({"gpus": [], "hostname": None})
        self.assertIn("🖥️ my-windows", rendered)
        with patch.dict(os.environ, {"WINBAR_SSH_ALIAS": ""}, clear=False):
            rendered = wm.render({"gpus": [], "hostname": None})
        self.assertIn("🖥️ Windows PC", rendered)

    def test_status_icons_use_distinct_colored_png_data(self):
        icons = {color: wm._desktop_icon_png(color) for color in ("green", "yellow", "red")}
        self.assertEqual(len(set(icons.values())), 3)
        for png in icons.values():
            self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))


class CacheAndTimeoutTests(unittest.TestCase):
    def test_local_config_is_loaded_without_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            config_path = Path(folder) / ".winbar.env"
            config_path.write_text(
                "WINBAR_SSH_ALIAS=lab-monitor\n"
                "WINBAR_RETENTION_DAYS=14 # local policy\n"
                "IGNORED_COMMAND=$(touch should-not-exist)\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"WINBAR_CONFIG_PATH": str(config_path)}, clear=True):
                config = wm.Config.from_env()
            self.assertEqual(config.ssh_alias, "lab-monitor")
            self.assertEqual(config.retention_days, 14)
            self.assertFalse((Path(folder) / "should-not-exist").exists())

    def test_config_boundary_values_are_safe(self):
        with patch.dict(os.environ, {
            "WINBAR_REFRESH_SECONDS": "0",
            "WINBAR_SSH_TIMEOUT": "-4",
            "WINBAR_SSH_ALIAS": "custom-host",
        }, clear=False):
            config = wm.Config.from_env()
        self.assertEqual(config.refresh_seconds, 1)
        self.assertEqual(config.ssh_timeout, 1)
        self.assertEqual(config.ssh_alias, "custom-host")

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

    def test_offline_without_cache_renders_red_status(self):
        with patch("winbar_monitor.collect_remote", side_effect=RuntimeError("offline")):
            with patch.object(wm.Config, "from_env", return_value=wm.Config(cache_path=Path("/definitely/missing/cache.json"))):
                with patch("winbar_monitor.read_cache", return_value=(None, None)):
                    with patch("builtins.print") as printer:
                        wm.main()
        self.assertIn("🔴 离线", printer.call_args.args[0])


class HistoryTests(unittest.TestCase):
    def test_records_prunes_and_generates_self_contained_report(self):
        metrics = wm.normalize_metrics(json.loads((ROOT / "no_gpu.json").read_text()))
        with tempfile.TemporaryDirectory() as folder:
            history_path = Path(folder) / "history.sqlite3"
            report_path = Path(folder) / "history.html"
            now = 2_000_000.0
            wm.record_history(history_path, metrics, now - 40 * 86400, retention_days=30)
            wm.record_history(history_path, metrics, now, retention_days=30)
            wm.write_history_report(history_path, report_path, now)

            with closing(sqlite3.connect(history_path)) as database:
                count = database.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
            self.assertEqual(count, 1)
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("WinBarMonitor 实时概览", report)
            self.assertIn('"count":1', report)
            self.assertIn('id="current-cards"', report)
            self.assertIn("<canvas id=\"util\"", report)
            self.assertIn("className='tooltip'", report)
            self.assertIn("canvas.onmousemove", report)
            self.assertIn("tooltip-time", report)
            self.assertNotIn("https://", report)

    def test_history_aggregates_multiple_gpus(self):
        metrics = wm.normalize_metrics({
            "memory_total_bytes": 16 * 1024 ** 3,
            "memory_free_bytes": 6 * 1024 ** 3,
            "gpus": [
                {"memory_used": 2048, "memory_total": 8192, "utilization_gpu": 40,
                 "temperature": 50, "power_draw": 30},
                {"memory_used": 1024, "memory_total": 4096, "utilization_gpu": 75,
                 "temperature": 65, "power_draw": 20},
            ],
        })
        values = dict(zip(wm.HISTORY_COLUMNS, wm._history_values(metrics)))
        self.assertEqual(values["memory_used_bytes"], 10 * 1024 ** 3)
        self.assertEqual(values["gpu_memory_used_bytes"], 3 * 1024 ** 3)
        self.assertEqual(values["gpu_percent"], 75)
        self.assertEqual(values["gpu_temperature"], 65)
        self.assertEqual(values["gpu_power_watts"], 50)


class RuntimeCompatibilityTests(unittest.TestCase):
    def test_plugin_does_not_refresh_on_open(self):
        project_root = Path(__file__).resolve().parents[1]
        plugin = (project_root / "winbar.1m.py").read_text(encoding="utf-8")
        self.assertNotIn("swiftbar.refreshOnOpen", plugin)

    def test_macos_system_python_can_compile_plugin(self):
        interpreter = Path("/usr/bin/python3")
        if not interpreter.exists():
            self.skipTest("macOS system Python is unavailable")
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as cache_dir:
            env = dict(os.environ, PYTHONPYCACHEPREFIX=cache_dir)
            subprocess.run(
                [str(interpreter), "-m", "py_compile", "winbar_monitor.py", "winbar.1m.py"],
                cwd=project_root,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )

    def test_install_keeps_support_module_hidden(self):
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            config_path = Path(folder) / "test.env"
            config_path.write_text("WINBAR_SSH_ALIAS=test-monitor\n", encoding="utf-8")
            env = dict(os.environ)
            env["SWIFTBAR_PLUGIN_DIR"] = folder
            env["WINBAR_REFRESH_SECONDS"] = "7"
            env["WINBAR_CONFIG_FILE"] = str(config_path)
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
            installed_config = Path(folder) / ".winbar_lib" / ".winbar.env"
            self.assertTrue(plugin.exists())
            self.assertTrue(os.access(plugin, os.X_OK))
            self.assertTrue(support.exists())
            self.assertTrue(installed_config.exists())
            self.assertEqual(installed_config.stat().st_mode & 0o777, 0o600)
            self.assertFalse(os.access(support, os.X_OK))
            self.assertFalse((Path(folder) / "winbar_monitor.py").exists())

    def test_install_and_uninstall_are_reversible(self):
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            env = dict(os.environ, SWIFTBAR_PLUGIN_DIR=folder, WINBAR_REFRESH_SECONDS="9")
            subprocess.run([str(project_root / "install.sh")], cwd=project_root, env=env, check=True, capture_output=True, text=True)
            env["WINBAR_REFRESH_SECONDS"] = "11"
            subprocess.run([str(project_root / "install.sh")], cwd=project_root, env=env, check=True, capture_output=True, text=True)
            self.assertFalse((Path(folder) / "winbar.9s.py").exists())
            self.assertTrue((Path(folder) / "winbar.11s.py").exists())
            subprocess.run([str(project_root / "uninstall.sh")], cwd=project_root, env=env, check=True, capture_output=True, text=True)
            self.assertFalse((Path(folder) / "winbar.11s.py").exists())
            self.assertFalse((Path(folder) / ".winbar_lib" / "winbar_monitor.py").exists())


if __name__ == "__main__":
    unittest.main()
