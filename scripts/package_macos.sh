#!/bin/zsh

set -euo pipefail

script_dir="${0:A:h}"
project_root="${script_dir:h}"
app_path="$project_root/dist/macos/BCIKit Studio.app"
plist_path="$app_path/Contents/Info.plist"

cd "$project_root"
uv run pyside6-deploy -c pysidedeploy.spec --force

plutil -replace CFBundleDisplayName -string "BCIKit Studio" "$plist_path"
plutil -replace CFBundleName -string "BCIKit Studio" "$plist_path"
plutil -replace CFBundleIdentifier -string "org.bcikit.studio" "$plist_path"
plutil -replace NSBluetoothAlwaysUsageDescription -string \
  "BCIKit Studio 使用蓝牙连接 BCIKit 采集设备并接收脑电数据。" "$plist_path"

# 首版不使用 Developer ID；修改 Info.plist 后重新施加本机临时签名。
codesign --force --deep --sign - "$app_path"
codesign --verify --deep --strict "$app_path"

echo "已生成：$app_path"
file "$app_path/Contents/MacOS/main"
