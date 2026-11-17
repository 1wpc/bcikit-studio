# BCIKit Studio 使用、构建与安装指南

BCIKit Studio 是 BCIKit 的桌面上位机，用于教学、科研和原型开发场景下的 EEG 采集、
记录、回放和导出。它不是医疗器械，也不提供医学诊断结论。

本文面向两类读者：希望从源码运行或打包的开发者，以及使用已打包应用的采集用户。

## 1. 当前范围

- 采集传输：BCIKit Wi-Fi TCP、BCIKit BLE v1、树莓派本机 TCP。
- 硬件规模：1–4 块 AFE8 板，即 8–32 通道；板数由用户按实际串联装配选择，并在连接后由
  设备状态读回校验。
- Wi-Fi：支持 250/500 SPS。
- BLE：单板支持 250/500 SPS，双板只支持 250 SPS；三、四板或双板 500 SPS 必须使用
  Wi-Fi。
- Apple Silicon macOS `.app` 与 Raspberry Pi OS 64 位本机构建路径。Windows 可以从源码
  运行和本机构建 `.exe`，但当前尚未提供签名的 Windows 安装包。

模拟设备仅用于仓库的自动化测试，不会出现在交付版界面。

## 2. 使用前准备

### 2.1 安全

人体采集必须使用电池或合规隔离供电。采集期间断开 USB、示波器地、接地充电器等可能引入
非隔离回路的连接。本软件只记录数据；电极佩戴、电气安全和实验伦理由使用者负责。

### 2.2 设备与网络

不要同时让 OpenBCI GUI、其他 Wi-Fi 客户端或其他 BLE 客户端连接同一设备。BCIKit 的 Wi-Fi
和 BLE 采集会话互斥；出现“设备正被另一协议会话占用”时，先停止另一端会话后再连接。

Wi-Fi 采集前，在系统网络设置中手动连接设备热点 `OpenBCI-XXXX`。该热点可能没有互联网；
应保持该连接，并暂时关闭会改写 `192.168.4.x` 路由的 VPN。Wi-Fi 采集由设备反向连接电脑的
临时 TCP 端口，因此首次使用时应允许系统防火墙放行 BCIKit Studio 的入站连接。

蓝牙设备会显示为 `BCIKit-XXXX`，并需要 v0.7.0 或更高版本固件。macOS 第一次扫描会请求
蓝牙权限；Windows 需要开启系统蓝牙并允许桌面应用使用蓝牙。

## 3. 安装和启动

### 3.1 使用 macOS `.app`

当前发布包面向 Apple Silicon Mac。

1. 将 `BCIKit Studio.app` 拖入“应用程序”。
2. 双击启动；首次蓝牙扫描时允许蓝牙权限。
3. 若 macOS 因本机临时签名而阻止启动，请在 Finder 中按住 Control 点按应用并选择“打开”，再
   在确认框中打开。仅应对从可信来源取得的构建这样操作。
4. 在首页选择板数、采样率、PGA 增益和 Wi-Fi 或蓝牙连接方式。

发布包目前不含 Developer ID 签名、公证和 DMG 安装器；这不影响从源码构建或在本机运行，
但组织分发前应自行完成签名与公证流程。

### 3.2 从源码启动（macOS、Windows 与 Linux ARM64）

要求：Git、[uv](https://docs.astral.sh/uv/)、Python 3.12。不要使用 macOS 自带 Python
3.9，也不要用 Python 3.13 创建本项目环境。

```bash
git clone https://github.com/1wpc/bcikit-studio.git
cd bcikit-studio
uv python install 3.12
uv sync --extra dev
uv run bcikit-studio
```

Windows PowerShell 使用相同命令；若系统未将 `uv` 加入 `PATH`，请重新打开 PowerShell 后再执行。
首次同步会下载 Qt、科学计算和打包依赖，耗时取决于网络环境。

## 4. 从源码构建可分发程序

每个平台都必须在该平台本机构建，不能把 macOS `.app` 直接转换为 Windows `.exe`，也不能反向
转换。构建前建议先执行 `uv sync --extra dev`。

### 4.1 macOS Apple Silicon `.app`

需要 Xcode Command Line Tools（可通过 `xcode-select --install` 安装）。在仓库根目录运行：

```bash
./scripts/package_macos.sh
```

产物为：

```text
dist/macos/BCIKit Studio.app
```

脚本会调用 `pyside6-deploy`/Nuitka、写入蓝牙权限说明并施加本机临时签名。构建完成后应在一台
未使用开发环境的 Mac 上验证启动、Wi-Fi/BLE 连接、记录和回放。

### 4.2 Windows `.exe`

需要 Windows 10/11、Python 3.12、uv，以及 Visual Studio 2022 Build Tools 的 C++ 构建工具。
建议在干净的 PowerShell 中执行：

```powershell
git clone <你的 BCIKit 仓库地址>
Set-Location BCIKit
uv python install 3.12
uv sync --extra dev
New-Item -ItemType Directory -Force .build\windows | Out-Null
Push-Location .build\windows
uv run --project ..\.. pyside6-deploy ..\..\main.py --force --name "BCIKit Studio" --mode standalone --extra-modules "QtOpenGL,QtOpenGLWidgets"
Pop-Location
```

`pyside6-deploy` 会在完成信息中打印生成的 `.exe` 或部署目录。使用 `standalone` 模式时，必须
将该目录内的全部文件一起分发，不能只复制 `.exe`。Windows 构建目前是源码构建路径，尚未在
本仓库提供已签名安装器；分发前应至少在一台未安装 Python 的 Windows 机器测试启动、Windows
防火墙放行、记录和回放。

若只需本机使用，不必生成 `.exe`，直接执行 `uv run bcikit-studio` 即可。

### 4.3 树莓派 5 本机工作站

要求：Raspberry Pi OS 64 位桌面版、树莓派 5 上已正确连接 AFE8/ADS1299、SPI0 与
2.048 MHz CLK 已启用，并已安装运行 `bcikit-acquisition.service`。采集服务安装及接线
步骤见 [树莓派固件仓库](https://github.com/1wpc/bcikit_firmware_rpi5)。Studio 与
采集服务必须在**同一台树莓派**，本机入口固定连接 `127.0.0.1:6677`，不需要切换 Wi-Fi。
Raspberry Pi OS Bookworm 的 glibc 2.36 使用 Qt/PySide6 6.7.3 的 ARM64 轮子；其他平台
仍使用原有 Qt 版本范围。

```bash
git clone https://github.com/1wpc/bcikit-studio.git
cd bcikit-studio
uv python install 3.12
uv sync --extra dev
./scripts/package_linux_aarch64.sh
./scripts/install_linux_desktop.sh
./dist/linux-aarch64/main.dist/main.bin
```

`standalone` 产物是整个 `main.dist` 目录；不能只复制 `main.bin`。也可不打包，执行
`uv run bcikit-studio`。首页选择“树莓派本机”，并选择实际板数、采样率及增益；芯片诊断
页也有同机连接入口。本机入口与其他连接方式共用握手、状态读回、采集、记录及诊断。
只有在人体与电极完全断开时，才可用
`uv run python scripts/smoke_local_pi.py --electrodes-disconnected` 做短时数据通路检查。

本机 TCP 只解决软件进程间通信，**不等于人体电气隔离**。人体采集须先完成合规隔离供电
和安全验证；未完成前仅可在电极与人体断开的条件下做台架测试。服务仅监听本机，若需要
局域网远程控制，必须另行设计认证、访问控制和网络部署；不要直接暴露本机端口。

## 5. 首页与连接

首页不是实时采集页的平级导航；它只用于选择配置、建立会话和打开已有记录。连接成功且设备
配置读回后，应用才会进入实时采集工作区。

### 5.1 采集配置

选择实际装配的板数与采样率：一块板对应 8 个通道。软件不会猜测串联板数；若选择与硬件不符，
连接阶段的状态读回会报错或采集结果不可信。

PGA 增益默认为 24×，可选 1×、2×、4×、6×、8×、12×、24×。该选项对所有板的启用通道统一
生效，不能逐通道设置。非 24× 需要固件 v0.7.13 及以上；旧固件仍可按 24× 连接。Studio 会先
检查设备能力位，再发送 `SET_GAIN`，从 `GET_STATUS` 核对读回值，并按每个采样帧携带的实际
`gain_code` 换算微伏。设备重启后恢复默认 24×，需要重新连接以应用所选档位。

### 5.2 Wi-Fi 连接

点击“开始连接”后确认安全提示。Studio 会访问设备 `192.168.4.1`、建立本机 TCP listener，
并完成 `PING → GET_INFO → STOP → SET_* → GET_STATUS → START`。配置以设备读回值为准。

### 5.3 蓝牙连接

点击“扫描蓝牙设备”，从列表选择目标 `BCIKit-XXXX`。Studio 会校验 BCIKit BLE 服务与控制、
采样特征，订阅两个通知通道后再启动同一套 BCIKit 命令握手。蓝牙适合单板或双板 250 SPS 的
近距离采集；长期记录、多板和高吞吐采集优先使用 Wi-Fi。

### 5.4 树莓派本机连接

仅 Linux 版显示“树莓派本机”按钮。先确认 `systemctl is-active bcikit-acquisition.service`
为 `active`，再连接；Studio 通过本机 TCP 完成同一套 BCIKit 命令握手，不访问 ESP32 热点。
如提示无法连接，请检查服务、SPI0 和 ADS1299 时钟；若服务正常但 START 失败，再看
`journalctl -u bcikit-acquisition.service` 和设备状态计数。

### 5.5 芯片诊断

首页“芯片诊断”可通过 Wi-Fi 或蓝牙连接。连接后可：

- 读取当前配置、ADS1299 ID、寄存器校验和设备错误计数；
- 清零设备计数；
- 切换 ADS1299 内部测试信号；
- 切换输入短接；
- 恢复 EEG SRB2 模式。

切换模式会执行 `STOP → SET_PROFILE → GET_STATUS → START`，且记录进行中不允许切换。内部测试
与输入短接只用于硬件检查，恢复 EEG SRB2 后再连接人体电极。

从诊断页返回连接页会断开当前设备，避免出现后台仍占用设备而首页看似未连接的状态。

## 6. 实时采集工作区

连接后，波形区默认显示全部协商通道。每条波形被限制在自己的通道轨道内，横线用于分隔相邻
通道，避免大幅度信号覆盖其他通道。

- **显示**：`原始波形` 是默认值；`EEG 清晰` 使用显示副本上的 1–45 Hz 带通与 50 Hz notch，
  不修改原始记录。
- **窗口**：选择 2、5、10 或 30 秒的显示时间范围。
- **幅度**：选择统一 ±100 µV、统一 ±50 µV 或自动独立缩放。
- **查看 / 通道**：多板时可按板查看，也可保留“全部通道”；通道菜单用于单独隐藏或显示通道。
- **暂停画面**：只停止图形刷新，不停止设备采样，也不停止记录；再次点击“继续画面”恢复显示。
- **开始记录 / 停止记录**：记录原始有效 BCIKit 帧。录制期间可添加 Marker；停止记录后会写入
  完整性统计和 SHA-256。
- **添加 Marker**：写入设备时间、主机 UTC 时间、标签和备注，不会改动 EEG 样本。
- **统计卡片**：显示实际采样率、有效帧、sequence 丢帧、CRC 错误和界面队列丢帧。
- **实时分析**：可隐藏或显示 PSD、主峰和 Delta/Theta/Alpha/Beta/Gamma 相对频段能量。它是最近
  数据的可视化分析，不是电极阻抗测量，也不是医学判断。
- **断开**：正常关闭会话并回到首页。设备失联时，Studio 会尝试处理连接；清理超过 4 秒后会
  强制释放本机资源，使连接按钮恢复可用。设备本身无响应时，仍可能需要等待固件超时或重启设备。

## 7. 记录、回放与导出

记录按会话目录保存，原始文件不会被滤波、显示或导出操作覆盖：

```text
YYYY-MM-DD_HH-mm-ss_名称/
  raw.bciraw
  session.json
  events.csv
  logs.txt
```

数据目录由操作系统决定。通常是：

- macOS：`~/Library/Application Support/BCIKit Studio/sessions/`
- Windows：`%LOCALAPPDATA%\BCIKit Studio\sessions\`

“历史记录”显示会话时长、采样率、通道数、Marker 数量、状态、gap 和 CRC。双击会话进入回放；
可播放、暂停、拖动时间轴、调整窗口并跳转到 Marker。

回放页支持三类导出，均不会修改源会话：

- 微伏 CSV；
- 原始 24 位计数值 CSV；
- BDF+：EEG 通道保持数字量，Marker 写入 BDF+ 注释。

BDF+ 只能表达连续固定采样率数据。若会话存在 sequence gap、设备时间戳不连续、通道数/采样率/
增益变化，Studio 会拒绝导出 BDF+，请保留并使用原始 `raw.bciraw` 会话。

## 8. 日志与常见问题

通过菜单“视图 → 显示运行日志”打开全局日志。提交 issue 时，请附上复现步骤、系统版本、固件版本、
连接方式、板数/采样率，以及问题发生前后约 30 行日志；不要上传包含敏感受试者信息的记录。

| 现象 | 首先检查 |
| --- | --- |
| `No route to host` / 无法访问 `192.168.4.1` | Mac/Windows 是否真的连接 `OpenBCI-XXXX`；关闭冲突 VPN；设备是否上电。 |
| Wi-Fi 提示反向连接失败 | 防火墙是否阻止入站连接；设备和电脑是否处于同一热点网络。 |
| 蓝牙扫描不到设备 | 固件是否为 v0.7.0+；系统蓝牙与应用权限是否开启；设备是否正在被另一会话占用。 |
| `超过 3 秒未收到采样帧` | 蓝牙控制命令已连接但 SAMPLE 通知没有到达；检查固件 BLE Sample TX、ADS1299 DRDY 和板数/采样率限制。 |
| `not railed` | 这是 OpenBCI GUI 的电极输入未饱和提示，不等于 BCIKit Studio 的连接状态；仍需检查电极、参考和偏置连接。 |
| 连接页按钮持续灰色 | 等待最多 4 秒的本机清理；若日志显示强制释放后仍无法连接，停止其他客户端、重启设备，并附上日志提交 issue。 |

## 9. 贡献者验证

提交前运行：

```bash
uv run pytest
uv run ruff check .
uv run mypy
```

测试、虚拟数据和打包成功只证明相应的软件层；它们不能替代真实设备、ADS1299 内部测试信号或人体
采集验证。请在 issue/PR 中清楚区分已完成的单元测试、虚拟传输测试、真实硬件测试和人体实验。
