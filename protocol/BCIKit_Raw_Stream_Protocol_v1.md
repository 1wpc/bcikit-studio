# BCIKit Raw Stream Protocol v1

版本：1  
状态：ESP32 固件 v0.7.0 实现规范
适用：USB CDC、TCP、WebSocket binary、BLE 分片后的重组数据

设备发现、配置、状态查询和 ACK 见 [BCIKit_Device_Control_Protocol_v1.md](./BCIKit_Device_Control_Protocol_v1.md)。WiFi TCP 链路会在同一字节流中复用 SAMPLE 和 control frame，协议库必须按 `frame_type` 分发；BLE 将它们放入独立的 Notify characteristic。UDP 仍只发送 SAMPLE。

## 1. 设计目标

该协议用于从主控传输完整 ADS1299 原始采样，支持 1 到 4 块 BCIKit AFE8：

- 保留每块 ADS1299 的 3 字节状态。
- 保留 24 位原始通道数据，不在 MCU 中强制转换为 float。
- 使用 32 位 sequence 和 64 位时间戳。
- 能够检测串口、网络或缓存导致的丢帧和损坏。
- 不受旧 OpenBCI 单板 33 字节格式限制。

## 2. 字节序

- 协议头中的多字节整数：little-endian。
- ADS1299 payload：保持芯片原始 MSB-first 顺序。
- CRC：按线上字节顺序计算。

## 3. Sample Frame

```text
Offset  Size  Field
0       2     sync = A5 5A
2       1     version = 01
3       1     type = 01 (sample)
4       2     flags, little-endian
6       4     sequence, little-endian
10      8     timestamp_us, little-endian
18      2     sample_rate_hz, little-endian
20      1     board_count
21      1     channel_count = board_count * 8
22      1     gain_code
23      1     reserved = 00
24      2     payload_len = board_count * 27
26      N     ADS1299 payload
26+N    2     CRC-16/CCITT-FALSE
```

总帧长度：

```text
total_bytes = 28 + 27 * board_count
```

| 板数 | 通道数 | payload | 协议总帧 |
| ---: | ---: | ---: | ---: |
| 1 | 8 | 27 | 55 bytes |
| 2 | 16 | 54 | 82 bytes |
| 3 | 24 | 81 | 109 bytes |
| 4 | 32 | 108 | 136 bytes |

## 4. 字段定义

### 4.1 flags

| Bit | 名称 | 含义 |
| ---: | --- | --- |
| 0 | `TIME_VALID` | timestamp 来自有效单调时钟 |
| 1 | `DROPPED_BEFORE` | 此帧前发生过队列或传输丢帧 |
| 2 | `REGISTER_VERIFIED` | 当前配置已完成读回验证 |
| 3 | `TEST_SIGNAL` | 当前使用内部测试信号 |
| 4 | `INPUT_SHORT` | 当前使用内部输入短接 |
| 5 | `LEADOFF_ENABLED` | lead-off comparator 已启用 |
| 6 | `PHYSICAL_ORDER` | payload 已被重排为物理顺序 |
| 7..15 | reserved | 必须为 0 |

默认建议 `PHYSICAL_ORDER=0`，保留原始 wire order，减少 MCU 内存搬运。上位机根据板数反转板块映射。

### 4.2 sequence

- 每产生一个 ADS1299 采样时递增 1。
- 从 0 开始，溢出后自然回绕。
- 即使传输层丢弃帧，采集侧 sequence 仍应继续递增。
- 上位机使用无符号 32 位差值检测丢帧。

### 4.3 timestamp_us

- 记录 DRDY 下降沿时间，而不是网络发送时间。
- 使用主控单调时钟，单位微秒。
- USB/Wi-Fi 发送延迟不应修改时间戳。

### 4.4 gain_code

与 ADS1299 CHnSET `GAIN[2:0]` 一致：

| code | gain |
| ---: | ---: |
| 0 | 1 |
| 1 | 2 |
| 2 | 4 |
| 3 | 6 |
| 4 | 8 |
| 5 | 12 |
| 6 | 24 |
| 7 | invalid/reserved |

当前菊花链硬件要求所有板、所有启用通道使用同一个配置。若未来支持逐通道不同增益，应升级协议版本或发送独立配置消息。

## 5. Payload 布局

每块板占 27 字节：

```text
board block:
  byte 0..2   status
  byte 3..5   CH1
  byte 6..8   CH2
  byte 9..11  CH3
  byte 12..14 CH4
  byte 15..17 CH5
  byte 18..20 CH6
  byte 21..23 CH7
  byte 24..26 CH8
```

状态高 4 位必须为 `0xC`。状态其余位包含 LOFF_STATP、LOFF_STATN 和 GPIO 状态。

默认 payload 使用 ADS1299 wire order。例如物理连接：

```text
Host -> Board A -> Board B -> Board C
```

payload 顺序是：

```text
Board C block, Board B block, Board A block
```

上位机映射：

```c
physical = board_count - 1 - wire;
global_channel = physical * 8 + local_channel;
```

## 6. CRC

使用 CRC-16/CCITT-FALSE：

```text
polynomial = 0x1021
initial    = 0xFFFF
reflect in = false
reflect out= false
xor out    = 0x0000
```

CRC 覆盖 `version` 字段开始到 payload 最后一个字节，不包含 2 字节 sync，也不包含 CRC 本身。CRC 在线上以 little-endian 发送。

参考实现：

```c
static uint16_t crc16_ccitt_false(const uint8_t *data, size_t len)
{
    uint16_t crc = 0xFFFFu;
    while (len--) {
        crc ^= (uint16_t)(*data++) << 8;
        for (unsigned i = 0; i < 8; ++i) {
            crc = (crc & 0x8000u)
                ? (uint16_t)((crc << 1) ^ 0x1021u)
                : (uint16_t)(crc << 1);
        }
    }
    return crc;
}
```

标准测试向量：

```text
ASCII input: "123456789"
expected CRC: 0x29B1
wire bytes: B1 29
```

## 7. 解码流程

1. 搜索 `A5 5A`。
2. 检查 version、type、board_count 和 payload_len。
3. 等待完整帧。
4. 校验 CRC。
5. 检查每个 27 字节板块的状态高 4 位。
6. 检查 sequence 连续性。
7. 根据 `PHYSICAL_ORDER` 决定是否反转板块顺序。
8. 将每个 24 位通道数据符号扩展为 `int32_t`。

CRC 失败后，从当前 sync 的第二个字节后重新搜索，避免错误长度造成长期失步。

## 8. 传输层要求

### USB CDC / UART

- 原样发送完整帧。
- UART 需要留出至少 2 倍持续带宽。
- 单板、250 SPS 的 BCIKit 协议流约为 `13.75 kB/s`，已经超过 115200 baud 的有效上限。ESP32 固件因此把 BCIKit 原生数据放在 WiFi 或 BLE；UART 115200 仅用于控制和 OpenBCI 单板 250 SPS 兼容流。

### TCP / WebSocket

- 一个协议帧可以对应一个 WebSocket binary message。
- TCP 接收方不能假设一次 read 对应一帧，必须按 sync 和长度重组。

### BLE

- 固件按协商 MTU 把连续字节流切为 Notification；没有额外的 fragment header 或 fragment index。
- 接收端必须对 Control TX 与 Sample TX 分别做增量重组，不能假设一个 Notification 对应一个协议帧。
- 当前 BLE V1 支持单板 250/500 SPS 和双板 250 SPS；其他组合应改用 WiFi TCP。
- 完整服务 UUID、会话与吞吐边界见 [BCIKit_BLE_Transport_v1.md](./BCIKit_BLE_Transport_v1.md)。

## 9. OpenBCI 兼容适配

需要兼容旧软件时，可将单板 payload 转换为：

```text
A0 + sample_number_u8 + 24 channel bytes + 6 aux bytes + C0
```

限制：

- 仅直接支持 8 通道。
- 3 字节 ADS1299 status 被丢弃。
- sample number 只有 8 位。
- 不包含可靠时间戳和 CRC。

因此 OpenBCI 包只能作为输出适配器，不能作为 BCIKit 固件内部数据结构。

ESP32 固件 v0.6.0 的 OpenBCI 模式固定为单板、8 通道，输出 33 字节标准 Cyton packet。传统 UART 建议 250 SPS，WiFi 兼容层支持 250/500/1000 SPS。多板仍使用 BCIKit Raw Stream v1；固件不会把多板数据冒充成 Cyton+Daisy 的交替/平均格式。

## 10. 协议验收

- 1/2/3/4 板帧长度正确。
- CRC 测试向量在 MCU、Python 和上位机实现中一致。
- sequence 回绕处理正确。
- 任意字节截断或插入后能重新同步。
- wire order 与 physical order 映射经过多板实物验证。
- 传输断连不会影响采集 sequence 和错误统计。
