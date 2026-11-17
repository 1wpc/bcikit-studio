#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
executable="$project_root/dist/linux-aarch64/main.dist/main.bin"
desktop_file="$HOME/Desktop/BCIKit Studio.desktop"

if [[ ! -x "$executable" ]]; then
  echo "未找到 Linux 构建产物：$executable" >&2
  echo "请先执行 ./scripts/package_linux_aarch64.sh" >&2
  exit 1
fi

mkdir -p "$HOME/Desktop"
printf '%s\n' \
  '[Desktop Entry]' \
  'Type=Application' \
  'Name=BCIKit Studio' \
  'Comment=BCIKit EEG acquisition workstation' \
  "Exec=$executable" \
  "Path=$project_root" \
  "Icon=$project_root/src/bcikit_studio/resources/logo.svg" \
  'Terminal=false' \
  'Categories=Science;Education;' \
  > "$desktop_file"
chmod 755 "$desktop_file"
echo "已创建桌面入口：$desktop_file"
