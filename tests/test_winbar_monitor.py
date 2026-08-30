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

    def test_menu_bar_display_modes_and_chinese_settings_menu(self):
        metrics = wm.normalize_metrics({
            "cpu_percent": 12,
            "network_rx_bps": 2 * 1024 ** 2,
            "gpus": [{"utilization_gpu": 84, "memory_used": 2048, "memory_total": 8192,
                      "temperature": 56, "power_draw": 145}],
        })
        self.assertIn("GPU 84%", wm._menu_bar_line(metrics, "gpu"))
        self.assertIn("CPU 12%", wm._menu_bar_line(metrics, "cpu"))
        self.assertIn("显存 2.0 GB / 8.0 GB", wm._menu_bar_line(metrics, "vram"))
        self.assertNotIn("🟢", wm._menu_bar_line(metrics, "gpu"))
        self.assertNotIn("🖥", wm._menu_bar_line(metrics, "gpu"))
        self.assertIn("GPU 56°C", wm._menu_bar_line(metrics, "temperature"))
        self.assertIn("GPU 145 W", wm._menu_bar_line(metrics, "power"))
        self.assertIn("网络下载速率", wm._display_settings_menu(Path("/tmp/settings.json"), "gpu")[7])
        self.assertIn("⚙️ 菜单栏显示设置", wm.render(metrics, menu_bar_mode="gpu"))

    def test_menu_bar_mode_is_saved_locally(self):
        with tempfile.TemporaryDirectory() as folder:
            settings_path = Path(folder) / "settings.json"
            self.assertEqual(wm.cli_main(["--set-menu-bar-mode", "power", "--settings-path", str(settings_path)]), 0)
            self.assertEqual(wm._read_settings(settings_path), {"menu_bar_mode": "power"})
            self.assertEqual(wm.cli_main(["--set-menu-bar-mode", "not-a-mode"]), 1)

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

    def test_windows_file_source_configuration_is_loaded(self):
        with patch.dict(os.environ, {
            "WINBAR_DATA_SOURCE": "windows-files",
            "WINBAR_REMOTE_DATA_DIR": r"D:\Monitoring Data",
            "WINBAR_WINDOWS_SAMPLE_SECONDS": "30",
        }, clear=False):
            config = wm.Config.from_env()
        self.assertEqual(config.data_source, "windows-files")
        self.assertEqual(config.remote_data_dir, r"D:\Monitoring Data")
        self.assertEqual(config.windows_sample_seconds, 30)

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

    def test_windows_file_snapshot_is_normalized_with_source_timestamp(self):
        payload = json.dumps({
            "schema_version": 1,
            "collected_at": 1_700_000_000.5,
            "metrics": {"hostname": "LAPTOP", "cpu_percent": "31", "gpus": []},
        })
        config = wm.Config(data_source="windows-files", remote_data_dir=r"C:\ProgramData\WinBarMonitor")
        with patch("winbar_monitor._run_remote_powershell", return_value=payload) as runner:
            metrics, collected_at = wm.collect_windows_file(config)
        self.assertEqual(metrics["hostname"], "LAPTOP")
        self.assertEqual(metrics["cpu_percent"], 31)
        self.assertEqual(collected_at, 1_700_000_000.5)
        self.assertIn("ReadAllText", runner.call_args.args[1])
        self.assertNotIn("Get-CimInstance", runner.call_args.args[1])

    def test_windows_history_skips_invalid_lines_and_orders_samples(self):
        older = json.dumps({"schema_version": 1, "collected_at": 100,
                             "metrics": {"hostname": "PC", "cpu_percent": 1}})
        newer = json.dumps({"schema_version": 1, "collected_at": 200,
                             "metrics": {"hostname": "PC", "cpu_percent": 2}})
        config = wm.Config(data_source="windows-files")
        with patch("winbar_monitor._run_remote_powershell", return_value=f"not-json\n{newer}\n{older}\n"):
            samples = wm.read_windows_history(config, 50)
        self.assertEqual([(sample[0]["cpu_percent"], sample[1]) for sample in samples], [(1, 100), (2, 200)])

    def test_windows_history_gap_is_backfilled_before_current_sample(self):
        with tempfile.TemporaryDirectory() as folder:
            config = wm.Config(
                data_source="windows-files",
                history_path=Path(folder) / "history.sqlite3",
                retention_days=30,
                windows_sample_seconds=30,
            )
            metrics = wm.normalize_metrics({"hostname": "PC", "cpu_percent": 20, "gpus": []})
            buffered = [
                (wm.normalize_metrics({"hostname": "PC", "cpu_percent": 10, "gpus": []}), 100),
                (wm.normalize_metrics({"hostname": "PC", "cpu_percent": 15, "gpus": []}), 130),
            ]
            with patch("winbar_monitor.read_windows_history", return_value=buffered) as reader:
                wm.record_windows_history(config, metrics, 160)
            self.assertTrue(reader.called)
            with closing(sqlite3.connect(config.history_path)) as database:
                values = database.execute("SELECT collected_at FROM samples ORDER BY collected_at").fetchall()
            self.assertEqual(values, [(100.0,), (130.0,), (160.0,)])

            with patch("winbar_monitor.read_windows_history") as reader:
                wm.record_windows_history(config, metrics, 190)
            reader.assert_not_called()

    def test_windows_history_backfill_error_keeps_current_snapshot_visible(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = wm.Config(
                data_source="windows-files",
                cache_path=root / "cache.json",
                history_path=root / "history.sqlite3",
                report_path=root / "history.html",
            )
            metrics = wm.normalize_metrics({"hostname": "PC", "cpu_percent": 20, "gpus": []})
            with patch.object(wm.Config, "from_env", return_value=config):
                with patch("winbar_monitor.collect_windows_file", return_value=(metrics, 1_000)):
                    with patch("winbar_monitor.read_windows_history", side_effect=RuntimeError("history offline")):
                        with patch("builtins.print") as printer:
                            wm.main()
            self.assertIn("🟢 在线", printer.call_args.args[0])
            self.assertIn("历史记录失败：history offline", printer.call_args.args[0])

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


class LowUsageAlertTests(unittest.TestCase):
    @staticmethod
    def _metrics(*, gpu_utilization=3, memory_used=400, memory_total=10_000):
        return wm.normalize_metrics({
            "hostname": "TRAINING-PC",
            "gpus": [{
                "name": "GPU A",
                "utilization_gpu": gpu_utilization,
                "memory_used": memory_used,
                "memory_total": memory_total,
            }],
        })

    def test_low_usage_alert_notifies_once_and_rearms_after_activity(self):
        alert = wm.LowUsageAlertConfig(
            enabled=True, gpu_utilization_threshold=5, vram_utilization_threshold=10,
            duration_seconds=120,
        )
        with tempfile.TemporaryDirectory() as folder:
            state_path = Path(folder) / "alert-state.json"
            first, notify = wm.update_low_usage_alert(state_path, alert, self._metrics(), 1_000)
            self.assertFalse(notify)
            self.assertEqual(first.low_since, 1_000)

            due, notify = wm.update_low_usage_alert(state_path, alert, self._metrics(), 1_120)
            self.assertTrue(notify)
            self.assertFalse(due.notified)
            wm.mark_low_usage_alert_notified(state_path, alert)
            notified, notify = wm.update_low_usage_alert(state_path, alert, self._metrics(), 1_180)
            self.assertFalse(notify)
            self.assertTrue(notified.notified)

            active, notify = wm.update_low_usage_alert(
                state_path, alert, self._metrics(gpu_utilization=36), 1_200)
            self.assertFalse(notify)
            self.assertIsNone(active.low_since)
            restarted, notify = wm.update_low_usage_alert(state_path, alert, self._metrics(), 1_300)
            self.assertFalse(notify)
            self.assertEqual(restarted.low_since, 1_300)
            _, notify = wm.update_low_usage_alert(state_path, alert, self._metrics(), 1_420)
            self.assertTrue(notify)

    def test_missing_gpu_reading_cannot_extend_low_usage_timer(self):
        alert = wm.LowUsageAlertConfig(enabled=True, duration_seconds=120)
        with tempfile.TemporaryDirectory() as folder:
            state_path = Path(folder) / "alert-state.json"
            wm.update_low_usage_alert(state_path, alert, self._metrics(), 1_000)
            unknown, notify = wm.update_low_usage_alert(
                state_path, alert, wm.normalize_metrics({"gpus": [{"utilization_gpu": "N/A"}]}), 1_050)
            self.assertFalse(notify)
            self.assertIsNone(unknown.low_since)
            _, notify = wm.update_low_usage_alert(state_path, alert, self._metrics(), 1_120)
            self.assertFalse(notify)

    def test_one_gpu_with_high_vram_prevents_an_alert_on_multi_gpu_hosts(self):
        alert = wm.LowUsageAlertConfig(enabled=True, gpu_utilization_threshold=5,
                                       vram_utilization_threshold=10, duration_seconds=60)
        metrics = wm.normalize_metrics({"gpus": [
            {"utilization_gpu": 2, "memory_used": 1_600, "memory_total": 10_000},
            {"utilization_gpu": 2, "memory_used": 0, "memory_total": 10_000},
        ]})
        with tempfile.TemporaryDirectory() as folder:
            status, notify = wm.update_low_usage_alert(Path(folder) / "alert-state.json", alert, metrics, 1_000)
        self.assertFalse(notify)
        self.assertIsNone(status.low_since)
        self.assertEqual(status.vram_utilization, 16)

    def test_disabling_an_alert_clears_an_existing_low_usage_timer(self):
        enabled = wm.LowUsageAlertConfig(enabled=True, duration_seconds=120)
        disabled = wm.LowUsageAlertConfig(enabled=False, duration_seconds=120)
        with tempfile.TemporaryDirectory() as folder:
            state_path = Path(folder) / "alert-state.json"
            wm.update_low_usage_alert(state_path, enabled, self._metrics(), 1_000)
            wm.update_low_usage_alert(state_path, disabled, self._metrics(), 1_050)
            restarted, notify = wm.update_low_usage_alert(state_path, enabled, self._metrics(), 1_120)
            self.assertFalse(notify)
        self.assertEqual(restarted.low_since, 1_120)

    def test_reset_low_usage_alert_clears_a_reconnect_observation_window(self):
        alert = wm.LowUsageAlertConfig(enabled=True, duration_seconds=120)
        with tempfile.TemporaryDirectory() as folder:
            state_path = Path(folder) / "alert-state.json"
            wm.update_low_usage_alert(state_path, alert, self._metrics(), 1_000)
            wm.reset_low_usage_alert(state_path)
            restarted, notify = wm.update_low_usage_alert(state_path, alert, self._metrics(), 1_300)
        self.assertFalse(notify)
        self.assertEqual(restarted.low_since, 1_300)

    def test_alert_settings_preserve_menu_bar_setting_and_can_be_toggled(self):
        with tempfile.TemporaryDirectory() as folder:
            settings_path = Path(folder) / "settings.json"
            alert = wm.LowUsageAlertConfig(enabled=True, gpu_utilization_threshold=7,
                                           vram_utilization_threshold=12, duration_seconds=90)
            wm.save_low_usage_alert_config(settings_path, alert)
            wm.save_menu_bar_mode(settings_path, "power")
            self.assertEqual(wm._read_settings(settings_path)["menu_bar_mode"], "power")
            self.assertEqual(wm._low_usage_alert_config(wm._read_settings(settings_path)["low_usage_alert"]), alert)
            self.assertEqual(wm.cli_main(["--set-low-usage-alert-enabled", "false", "--settings-path", str(settings_path)]), 0)
            self.assertFalse(wm._low_usage_alert_config(wm._read_settings(settings_path)["low_usage_alert"]).enabled)

    def test_saved_alert_settings_override_local_config_but_not_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            folder_path = Path(folder)
            config_path = folder_path / ".winbar.env"
            settings_path = folder_path / "settings.json"
            config_path.write_text(
                f"WINBAR_SETTINGS_PATH={settings_path}\n"
                "WINBAR_LOW_USAGE_ALERT_ENABLED=true\n"
                "WINBAR_LOW_USAGE_GPU_THRESHOLD=4\n",
                encoding="utf-8",
            )
            wm.save_low_usage_alert_config(settings_path, wm.LowUsageAlertConfig(
                enabled=False, gpu_utilization_threshold=8, vram_utilization_threshold=15,
                duration_seconds=180,
            ))
            with patch.dict(os.environ, {"WINBAR_CONFIG_PATH": str(config_path)}, clear=True):
                config = wm.Config.from_env()
            self.assertFalse(config.low_usage_alert.enabled)
            self.assertEqual(config.low_usage_alert.gpu_utilization_threshold, 8)
            with patch.dict(os.environ, {
                "WINBAR_CONFIG_PATH": str(config_path), "WINBAR_LOW_USAGE_GPU_THRESHOLD": "3",
            }, clear=True):
                config = wm.Config.from_env()
            self.assertEqual(config.low_usage_alert.gpu_utilization_threshold, 3)

    def test_cli_saves_arbitrary_custom_thresholds_without_a_dialog(self):
        with tempfile.TemporaryDirectory() as folder:
            settings_path = Path(folder) / "settings.json"
            result = wm.cli_main([
                "--set-low-usage-alert", "true", "6.5", "18", "90",
                "--settings-path", str(settings_path),
            ])
            saved = wm._low_usage_alert_config(wm._read_settings(settings_path)["low_usage_alert"])
        self.assertEqual(result, 0)
        self.assertEqual(saved, wm.LowUsageAlertConfig(enabled=True, gpu_utilization_threshold=6.5,
                                                        vram_utilization_threshold=18, duration_seconds=90))

    def test_one_dialog_parses_all_custom_rule_values(self):
        with patch("winbar_monitor._prompt_with_osascript", return_value="6.5, 18, 1.5") as prompt:
            alert = wm._prompt_low_usage_alert_config(wm.LowUsageAlertConfig())
        self.assertEqual(alert, wm.LowUsageAlertConfig(enabled=True, gpu_utilization_threshold=6.5,
                                                        vram_utilization_threshold=18, duration_seconds=90))
        self.assertIn("GPU 阈值 %, 显存阈值 %, 持续时间", prompt.call_args.args[0])

    def test_render_exposes_configure_action_and_detector_status(self):
        alert = wm.LowUsageAlertConfig(enabled=True, duration_seconds=300)
        status = wm.LowUsageAlertStatus(monitoring=True, low_since=900,
                                        max_gpu_utilization=3, vram_utilization=4)
        rendered = wm.render(self._metrics(), collected_at=1_000, low_usage_alert=alert,
                             low_usage_alert_status=status)
        self.assertIn("⏰ 任务完成提醒", rendered)
        self.assertIn("状态 · 低占用计时中 · 1 分钟 40 秒 / 5 分钟", rendered)
        self.assertIn("--快速预设", rendered)
        self.assertIn("--输入自定义规则并启用…", rendered)
        self.assertIn("param3=--configure-low-usage-alert", rendered)
        self.assertIn("param3=--set-low-usage-alert", rendered)
        self.assertIn("--关闭提醒", rendered)

    def test_alert_submenu_keeps_a_constant_parent_and_child_count(self):
        def section(alert):
            lines = wm.render(self._metrics(), low_usage_alert=alert).splitlines()
            start = lines.index("⏰ 任务完成提醒")
            return lines[start:lines.index("---", start)]

        disabled = section(wm.LowUsageAlertConfig(enabled=False))
        enabled = section(wm.LowUsageAlertConfig(enabled=True))
        direct_children = lambda lines: [line for line in lines if line.startswith("--") and not line.startswith("---") and not line.startswith("----")]
        self.assertEqual(disabled[0], enabled[0])
        self.assertEqual(len(direct_children(disabled)), len(direct_children(enabled)))

    def test_notification_uses_native_macos_notification(self):
        alert = wm.LowUsageAlertConfig(enabled=True, duration_seconds=300)
        status = wm.LowUsageAlertStatus(max_gpu_utilization=2, vram_utilization=4)
        with patch("winbar_monitor.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            wm.send_low_usage_notification("TRAINING-PC", alert, status)
        command = run.call_args.args[0]
        self.assertEqual(command[:2], ["/usr/bin/osascript", "-e"])
        self.assertIn("display notification", command[2])
        self.assertIn("任务可能已完成", command[2])


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
            self.assertIn('"twoHours":{"series"', report)
            self.assertIn('"bucket_seconds":60', report)
            self.assertIn('"bucket_seconds":300', report)
            self.assertIn('data-range="twoHours" class="active">2 小时', report)
            self.assertIn("render('twoHours');", report)
            self.assertIn('id="current-cards"', report)
            self.assertIn("<canvas id=\"util\"", report)
            self.assertIn("className='tooltip'", report)
            self.assertIn("canvas.onmousemove", report)
            self.assertIn("tooltip-time", report)
            self.assertIn("hasTimeGap", report)
            self.assertIn("started=false", report)
            self.assertIn("fixedMax,nearest,gapSeconds", report)
            self.assertIn("fixedMax,null,gapSeconds", report)
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
    def test_windows_powershell_sources_are_ascii_for_windows_powershell_5(self):
        project_root = Path(__file__).resolve().parents[1]
        for script in (project_root / "windows").glob("*.ps1"):
            with self.subTest(script=script.name):
                self.assertTrue(script.read_bytes().isascii())

    def test_windows_collector_uses_a_real_backup_path_for_atomic_replace(self):
        project_root = Path(__file__).resolve().parents[1]
        collector = (project_root / "windows" / "collect-winbar.ps1").read_text(
            encoding="ascii")
        self.assertIn("File]::Replace($temporary, $Path, $backup, $true)", collector)
        self.assertNotIn("File]::Replace($temporary, $Path, $null, $true)", collector)

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
