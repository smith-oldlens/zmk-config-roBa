#!/bin/bash
# ResolveAssist の Resolve 内スクリプトを DaVinci Resolve の Scripts メニューへ
# インストールする (macOS 用)。
#
# 使い方:  ./install_resolve_scripts.sh
set -euo pipefail

SRC_DIR="$(cd "$(dirname "$0")/resolve_scripts" && pwd)"
DEST_DIR="$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"

mkdir -p "$DEST_DIR"
cp "$SRC_DIR"/ResolveAssist_*.py "$DEST_DIR/"

echo "インストールしました:"
ls -1 "$DEST_DIR" | grep '^ResolveAssist_' | sed 's/^/  /'
echo ""
echo "DaVinci Resolve の Workspace > Scripts > Utility メニューから実行できます。"
echo "(メニューに出ない場合は Resolve を再起動してください)"
