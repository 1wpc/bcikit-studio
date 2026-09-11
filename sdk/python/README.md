# BCIKit Python 参考 SDK

这里的 Python 实现是其他语言上位机 SDK 的行为参考，不是完整的信号处理库。

## 文件

| 文件 | 用途 |
| --- | --- |
| `bcikit_protocol.py` | CRC、COMMAND 编码、SAMPLE/control 增量解码、int24 解析 |
| `bcikit_wifi_smoke.py` | WiFi TCP 双向控制与数据链路实机冒烟测试 |
| `bcikit_serial_smoke.py` | UART 二进制 GET_INFO/GET_STATUS/STOP 测试 |
| `tests/test_bcikit_protocol.py` | 固定帧、随机分块、CRC 恢复等单元测试 |

## 运行测试

在项目根目录执行：

```bash
PYTHONPATH=sdk/python python3 -m unittest discover -s sdk/python/tests -v
```

只使用 Python 标准库，当前应通过 6 项测试。

## WiFi 实机测试

电脑先连接设备热点，然后执行：

```bash
PYTHONPATH=sdk/python python3 sdk/python/bcikit_wifi_smoke.py \
  --device 192.168.4.1 --output auto --seconds 10
```

脚本会：

1. 请求 `/board` 和 `/all`。
2. 在电脑上建立随机 TCP listener。
3. `POST /tcp`，使用 `output=auto` 建立待识别会话。
4. 接受 ESP32 的反向 TCP 连接。
5. 在同一 socket 上以 CRC 正确的 PING 自动认领 BCIKit，再验证 GET_INFO、STOP、SET_* 和 GET_STATUS。
6. 用不同端口尝试建立 OpenBCI 目标，确认返回 `409 BUSY` 且当前会话没有被抢占。
7. 使用原生 START 启动采样并统计有效 BCIKit 帧。
8. 使用原生 STOP 停止采样并删除 TCP 目标。

如果 `POST /tcp` 返回 504，优先检查本机防火墙是否允许 Python 接收专用网络的入站连接。

## UART 控制测试

需要 pyserial：

```bash
python3 -m pip install pyserial
PYTHONPATH=sdk/python python3 sdk/python/bcikit_serial_smoke.py \
  /dev/cu.usbserial-XXXX
```

UART 固定为 115200。该脚本验证二进制控制面，不通过 UART 持续接收 BCIKit 原生采样数据。

## 移植规则

开发 Swift、Dart、Kotlin、C# 或 Rust SDK 时，以 `bcikit_protocol.py` 和 `protocol/BCIKit_Protocol_Test_Vectors_v1.md` 为共同基准。不要从 OpenBCI 解码器复制数据结构，也不要假设 TCP read 与协议帧一一对应。生产实现应由单一接收循环解码全部 frame，再按 `frame_type` 和 `request_id` 分发，不能让多个请求线程并发读取同一 socket。
