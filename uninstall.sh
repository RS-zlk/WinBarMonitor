#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
CONFIG_FILE="${WINBAR_CONFIG_FILE:-${SCRIPT_DIR}/.winbar.env}"
ENV_PLUGIN_DIR_SET=${SWIFTBAR_PLUGIN_DIR+x}
ENV_PLUGIN_DIR=${SWIFTBAR_PLUGIN_DIR-}
ENV_REFRESH_SET=${WINBAR_REFRESH_SECONDS+x}
ENV_REFRESH=${WINBAR_REFRESH_SECONDS-}
if [ -f "$CONFIG_FILE" ]; then
  set -a
  . "$CONFIG_FILE"
  set +a
fi
if [ "$ENV_PLUGIN_DIR_SET" = x ]; then SWIFTBAR_PLUGIN_DIR=$ENV_PLUGIN_DIR; fi
if [ "$ENV_REFRESH_SET" = x ]; then WINBAR_REFRESH_SECONDS=$ENV_REFRESH; fi

PLUGIN_DIR="${SWIFTBAR_PLUGIN_DIR:-${HOME}/Library/Application Support/SwiftBar/Plugins}"
REFRESH_SECONDS="${WINBAR_REFRESH_SECONDS:-30}"
case "$REFRESH_SECONDS" in
  ''|0|*[!0-9]*) echo "WINBAR_REFRESH_SECONDS 必须是正整数" >&2; exit 2 ;;
esac
for plugin in "$PLUGIN_DIR"/winbar.*s.py "$PLUGIN_DIR"/winbar.1m.py "$PLUGIN_DIR"/winbar_monitor.py; do
  if [ -e "$plugin" ]; then rm -f "$plugin"; fi
done
rm -f "$PLUGIN_DIR/.winbar_lib/winbar_monitor.py" "$PLUGIN_DIR/.winbar_lib/.winbar.env"
rmdir "$PLUGIN_DIR/.winbar_lib" 2>/dev/null || true
echo "已移除 WinBarMonitor 插件文件（缓存和历史统计数据保留）。"
