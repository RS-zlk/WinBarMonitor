#!/bin/sh
set -eu

PLUGIN_DIR="${SWIFTBAR_PLUGIN_DIR:-${HOME}/Library/Application Support/SwiftBar/Plugins}"
mkdir -p "$PLUGIN_DIR"
cp "$(dirname "$0")/winbar_monitor.py" "$PLUGIN_DIR/winbar_monitor.py"
cp "$(dirname "$0")/winbar.1m.py" "$PLUGIN_DIR/winbar.1m.py"
chmod +x "$PLUGIN_DIR/winbar.1m.py"
echo "已安装到：$PLUGIN_DIR/winbar.1m.py"
