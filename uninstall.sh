#!/bin/sh
set -eu

PLUGIN_DIR="${SWIFTBAR_PLUGIN_DIR:-${HOME}/Library/Application Support/SwiftBar/Plugins}"
rm -f "$PLUGIN_DIR/winbar.1m.py" "$PLUGIN_DIR/winbar_monitor.py"
echo "已移除 WinBarMonitor 插件文件（缓存保留在 ~/.cache/winbar-monitor）。"
