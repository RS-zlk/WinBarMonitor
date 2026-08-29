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
PLUGIN_NAME="winbar.${REFRESH_SECONDS}s.py"
LIB_DIR="$PLUGIN_DIR/.winbar_lib"
mkdir -p "$PLUGIN_DIR"
mkdir -p "$LIB_DIR"
for old_plugin in "$PLUGIN_DIR"/winbar.*s.py "$PLUGIN_DIR"/winbar.1m.py; do
  if [ -e "$old_plugin" ] && [ "$old_plugin" != "$PLUGIN_DIR/$PLUGIN_NAME" ]; then
    rm -f "$old_plugin"
  fi
done
cp "$SCRIPT_DIR/winbar_monitor.py" "$LIB_DIR/winbar_monitor.py"
cp "$SCRIPT_DIR/winbar.1m.py" "$PLUGIN_DIR/$PLUGIN_NAME"
if [ -f "$CONFIG_FILE" ]; then
  cp "$CONFIG_FILE" "$LIB_DIR/.winbar.env"
  chmod 600 "$LIB_DIR/.winbar.env"
else
  rm -f "$LIB_DIR/.winbar.env"
fi
chmod 644 "$LIB_DIR/winbar_monitor.py"
chmod +x "$PLUGIN_DIR/$PLUGIN_NAME"
echo "已安装到：${PLUGIN_DIR}/${PLUGIN_NAME}（每 ${REFRESH_SECONDS} 秒刷新）"
