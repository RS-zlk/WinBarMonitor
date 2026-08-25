#!/bin/sh
set -eu

PLUGIN_DIR="${SWIFTBAR_PLUGIN_DIR:-${HOME}/Library/Application Support/SwiftBar/Plugins}"
REFRESH_SECONDS="${WINBAR_REFRESH_SECONDS:-30}"
case "$REFRESH_SECONDS" in
  ''|0|*[!0-9]*) echo "WINBAR_REFRESH_SECONDS 必须是正整数" >&2; exit 2 ;;
esac
PLUGIN_NAME="winbar.${REFRESH_SECONDS}s.py"
LIB_DIR="$PLUGIN_DIR/.winbar_lib"
mkdir -p "$PLUGIN_DIR"
mkdir -p "$LIB_DIR"
cp "$(dirname "$0")/winbar_monitor.py" "$LIB_DIR/winbar_monitor.py"
cp "$(dirname "$0")/winbar.1m.py" "$PLUGIN_DIR/$PLUGIN_NAME"
chmod 644 "$LIB_DIR/winbar_monitor.py"
chmod +x "$PLUGIN_DIR/$PLUGIN_NAME"
echo "已安装到：${PLUGIN_DIR}/${PLUGIN_NAME}（每 ${REFRESH_SECONDS} 秒刷新）"
