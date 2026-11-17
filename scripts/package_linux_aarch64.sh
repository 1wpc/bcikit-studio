#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "aarch64" ]]; then
  echo "请在 Linux aarch64 设备上构建。" >&2
  exit 1
fi

uv run --no-sync python -m nuitka \
  --standalone \
  --enable-plugin=pyside6 \
  --output-dir=dist/linux-aarch64 \
  --output-filename=main.bin \
  --include-package=bcikit \
  --include-package=bcikit_studio \
  --include-package-data=bcikit_studio \
  --include-module=PySide6.QtOpenGL \
  --include-module=PySide6.QtOpenGLWidgets \
  --nofollow-import-to=scipy \
  --noinclude-qt-translations \
  main.py
echo "已生成：$project_root/dist/linux-aarch64/main.dist/main.bin"
