#!/bin/sh
set -eu

PLUGIN_DIR="${SWIFTBAR_PLUGIN_DIR:-${HOME}/Library/Application Support/SwiftBar/Plugins}"
REFRESH_SECONDS="${WINBAR_REFRESH_SECONDS:-30}"
case "$REFRESH_SECONDS" in
  ''|0|*[!0-9]*) echo "WINBAR_REFRESH_SECONDS 必须是正整数" >&2; exit 2 ;;
esac
rm -f "$PLUGIN_DIR/winbar.${REFRESH_SECONDS}s.py" "$PLUGIN_DIR/winbar.1m.py" "$PLUGIN_DIR/winbar_monitor.py"
echo "已移除 WinBarMonitor 插件文件（缓存保留在 ~/.cache/winbar-monitor）。"
