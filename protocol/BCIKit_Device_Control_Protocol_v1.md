# BCIKit Device Control Protocol v1

版本：1  
状态：ESP32 固件 v0.7.0 实现规范
线格式适用：UART、USB CDC、TCP、WebSocket binary、BLE GATT
当前固件实现：UART0 115200 baud、BCIKit WiFi TCP、BCIKit BLE

## 1. 目的

本协议是 BCIKit 专业上位机和采集设备之间的机器控制接口。它与 `BCIKit Raw Stream Protocol v1` 共用 `A5 5A` 同步字和 CRC 算法，但使用独立帧类型。

> 实现边界：WiFi 在 `output="bcikit"` 或自动认领后的 TCP 连接上接收 COMMAND，并在同一连接返回 ACK、DEVICE_INFO、DEVICE_STATUS 和 SAMPLE；BLE 在 Control RX 接收 COMMAND、Control TX 发送控制帧、Sample TX 发送 SAMPLE。WiFi UDP 仍然只发送 SAMPLE。连接方式见 [BCIKit_WiFi_Transport_v1.md](./BCIKit_WiFi_Transport_v1.md) 与 [BCIKit_BLE_Transport_v1.md](./BCIKit_BLE_Transport_v1.md)。

目标：

- 上位机能够发现设备型号、固件版本和能力。
- 每条命令都有 32 位 `request_id`，支持异步响应匹配。
- 采集中允许查询信息、状态和停止采集。
- 配置失败返回明确错误码，不依赖解析人类日志。
- BCIKit sample、status、ack 可以在同一字节流中安全复用。
- OpenBCI 兼容模式不取代本控制协议；专业上位机应使用 BCIKit 模式。

## 2. Control Frame

```text
Offset  Size  Field
0       2     sync = A5 5A
2       1     protocol_version = 01
3       1     frame_type
4       4     request_id, little-endian
8       1     code
9       1     status
10      2     payload_len, little-endian
12      N     payload
12+N    2     CRC-16/CCITT-FALSE, little-endian
```

总长度：

```text
total_bytes = 14 + payload_len
```

CRC 覆盖 offset 2 开始至 payload 最后一个字节，不包括 sync 和 CRC 本身。参数与 Raw Stream v1 相同：多项式 `0x1021`、初值 `0xFFFF`、不反射、xor out `0x0000`。

## 3. Frame Type

| 值 | 名称 | 方向 |
| ---: | --- | --- |
| `0x01` | SAMPLE | Device -> Host |
| `0x02` | DEVICE_INFO | Device -> Host |
| `0x03` | DEVICE_STATUS | Device -> Host |
| `0x04` | ACK | Device -> Host |
| `0x05` | EVENT | Device -> Host，预留 |
| `0x80` | COMMAND | Host -> Device |

`SAMPLE` 使用 Raw Stream v1 的 sample 专用布局，不使用本页的 control frame 布局。其余类型使用上面的 control frame。

## 4. COMMAND

COMMAND 帧中：

- `code` 是 command ID。
- `status` 必须为 `0`。
- `request_id` 由上位机产生；同一连接上未完成的请求不能重复。
- 当前固件最大命令 payload 为 32 字节。

| Command ID | 名称 | Payload | 说明 |
| ---: | --- | --- | --- |
| `0x01` | GET_INFO | 空 | 返回 DEVICE_INFO |
| `0x02` | GET_STATUS | 空 | 返回 DEVICE_STATUS |
| `0x03` | START | 空 | 开始采集 |
| `0x04` | STOP | 空 | 停止采集 |
| `0x05` | SET_OUTPUT_FORMAT | `u8 format` | 0=BCIKit，1=OpenBCI Cyton |
| `0x06` | SET_SAMPLE_RATE | `u16 Hz` | 当前为 250 或 500 |
| `0x07` | SET_BOARD_COUNT | `u8 count` | 1..4 |
| `0x08` | SET_PROFILE | `u8 profile` | 0=EEG SRB2，1=差分，2=内部测试，3=输入短接 |
| `0x09` | SET_CHANNEL_MASK | `u8 mask` | bit0..7 对应 CH1..CH8 |
| `0x0A` | RESET_STATS | 空 | 清空错误和采集计数，不重置 sequence |
| `0x0B` | PING | 0..32 字节 | ACK 原样回显 payload |

修改采样率、板数、profile、通道掩码和输出格式前应先 STOP。采集中发送这些命令返回 `BUSY`。

在 BCIKit WiFi TCP 连接上，`SET_OUTPUT_FORMAT` 只接受 `0=BCIKit`。若切换成 OpenBCI，control frame 将与无同步字的兼容数据混流，因此固件返回 `INVALID_VALUE`。需要 OpenBCI 时应删除当前 TCP 目标并重新 `POST /tcp`，设置 `output="raw"`。

BCIKit 原生模式支持 1..4 板和 250/500 SPS。`SET_SAMPLE_RATE` 只接受 250/500；OpenBCI WiFi 兼容层为了 BrainFlow 初始化另行支持 1000 SPS，但该能力不属于自研上位机的 v1 原生控制合同。

OpenBCI Cyton 输出固定要求：

```text
board_count = 1
```

UART 无网络目标时只有 OpenBCI 单板 250 SPS 数据流满足 115200 baud 带宽。自研上位机不应依赖这一兼容模式。

## 5. ACK

ACK 帧中：

- `code` 回显 command ID。
- `status` 是处理结果。
- PING 成功时 payload 原样回显；其他首版命令 ACK payload 为空。

| status | 名称 | 含义 |
| ---: | --- | --- |
| `0` | OK | 成功 |
| `1` | INVALID_COMMAND | 未知 command ID |
| `2` | INVALID_LENGTH | payload 长度错误 |
| `3` | INVALID_VALUE | 参数值或参数组合不支持 |
| `4` | BUSY | 当前采集中，必须先 STOP |
| `5` | APPLY_FAILED | ADS1299 写入或读回验证失败 |
| `6` | UNSUPPORTED | 当前硬件或固件不支持 |
| `7` | CRC_ERROR | 预留；CRC 错误帧当前直接丢弃并重新同步 |

START 的 ACK 和首个 SAMPLE 都是合法完整帧。上位机必须按 `frame_type` 分发，不应假定 ACK 一定早于第一个 SAMPLE 到达。

## 6. DEVICE_INFO payload

固定 24 字节：

```text
Offset  Size  Field
0       1     firmware_major
1       1     firmware_minor
2       1     firmware_patch
3       1     protocol_version
4       1     hardware_type (1 = ESP32-DevKitC + AFE8)
5       4     capabilities, little-endian
9       1     max_board_count
10      1     sample_rate_mask (bit0=250, bit1=500)
11      1     output_format_mask (bit0=BCIKit, bit1=OpenBCI)
12      4     serial_baud, little-endian
16      8     device_id, little-endian (ESP32 eFuse MAC)
```

Capabilities：

| Bit | 功能 |
| ---: | --- |
| 0 | BCIKit Raw Stream v1 |
| 1 | OpenBCI Cyton 8ch 输出 |
| 2 | 1..4 板菊花链 |
| 3 | DRDY 时间戳 |
| 4 | CRC-16 |
| 5 | 内部测试信号 |
| 6 | 输入短接 |
| 7 | 通道启用掩码 |
| 8 | 二进制控制协议 |
| 9 | OpenBCI WiFi Shield HTTP/SSDP 兼容层 |
| 10 | WiFi TCP/UDP 上的 BCIKit 原生数据帧 |
| 11 | WiFi TCP 上的 BCIKit 原生双向控制 |
| 12 | WiFi TCP 自动协议识别与首会话锁定 |

## 7. DEVICE_STATUS payload

固定 54 字节：

```text
Offset  Size  Field
0       1     acquisition_state (0=ready, 1=streaming)
1       1     output_format
2       1     profile
3       1     board_count
4       2     sample_rate_hz
6       1     gain_code
7       1     channel_enabled_mask
8       1     register_verified
9       1     ads1299_id
10      4     next_sequence
14      8     uptime_us
22      4     drdy_count
26      4     frame_count
30      4     frame_sync_error
34      4     spi_error
38      4     drdy_timeout
42      4     queue_overrun
46      4     transport_drop
50      4     register_verify_error
```

所有多字节整数均为 little-endian。

## 8. 推荐 WiFi TCP 上位机握手

```text
create TCP listener
POST /tcp with output=auto
accept ESP32 reverse TCP connection
PING -> CRC-valid COMMAND claims BCIKit session
GET_INFO
    -> require capabilities bit 11
STOP
SET_OUTPUT_FORMAT(BCIKit)
SET_BOARD_COUNT
SET_SAMPLE_RATE
SET_PROFILE
SET_CHANNEL_MASK
GET_STATUS
START
    -> receive SAMPLE and control responses on the same TCP stream
```

`DEVICE_STATUS.ads_id` 在单板模式必须为 `0x3E`。共享 `CS/DIN` 的多板
daisy-chain 不支持 multiple readback，因此多板模式下该字段只保留最近一次直接
ID 读值，允许为 `0x00`；上位机必须改用 `register_verified`，并在 START 后校验
每个 SAMPLE 中全部 `27 * board_count` 数据块的 `0xC` 状态头。

UART 使用同一 COMMAND 编码，波特率为 115200。UART 适合生产测试和故障恢复；专业上位机的正常无线会话使用上述 TCP 流程。

上位机应持续检查：

- SAMPLE sequence 连续性。
- CRC。
- `DROPPED_BEFORE` 标志。
- 每块 ADS1299 status 高 4 位是否为 `0xC`。
- 定期 GET_STATUS 得到的错误计数是否增加。

START 的 ACK 与第一条 SAMPLE 可能先后交错；不得使用“下一帧一定是 ACK”的同步读取方式。应持续解码所有帧，按 `frame_type` 分发，并用 `request_id` 匹配控制响应。

## 9. 兼容性原则

- 新固件增加 command 时只能追加新的 command ID。
- 不得改变 v1 已定义字段的偏移和含义。
- 未识别 frame type 时，上位机按长度跳过并继续同步。
- 固件升级后，上位机先检查 protocol version 和 capabilities，再决定显示哪些功能。
- 人类文本命令只用于台架调试，不是专业上位机 API。
