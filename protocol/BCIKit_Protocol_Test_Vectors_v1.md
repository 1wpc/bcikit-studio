# BCIKit Protocol v1 固定测试向量

适用：所有语言的编码器、解码器、移动端和桌面端 SDK  
十六进制字节按线上顺序排列。

## 1. CRC 标准向量

```text
input ASCII: 31 32 33 34 35 36 37 38 39
CRC value:   0x29B1
wire bytes:  B1 29
```

算法为 CRC-16/CCITT-FALSE：poly `0x1021`、init `0xFFFF`、不反射、xorout `0x0000`。

## 2. PING COMMAND

字段：

```text
request_id = 0x12345678
command    = 0x0B PING
status     = 0
payload    = ASCII "hello"
```

完整 19 字节：

```text
A5 5A 01 80 78 56 34 12 0B 00 05 00 68 65 6C 6C 6F 5B 66
```

CRC 数值为 `0x665B`，线上 little-endian 为 `5B 66`。

## 3. PING ACK

设备成功回显同一 request ID 和 payload：

```text
A5 5A 01 04 78 56 34 12 0B 00 05 00 68 65 6C 6C 6F 80 CC
```

CRC 数值为 `0xCC80`，线上为 `80 CC`。

## 4. 单板 SAMPLE

期望字段：

```text
flags          = 0x0005 (TIME_VALID | REGISTER_VERIFIED)
sequence       = 7
timestamp_us   = 123456789
sample_rate_hz = 250
board_count    = 1
channel_count  = 8
gain_code      = 6 (gain 24)
status         = C0 00 00
channels       = [1, -2, 3, -4, 5, -6, 7, -8]
```

完整 55 字节：

```text
A5 5A 01 01 05 00 07 00 00 00 15 CD 5B 07 00 00
00 00 FA 00 01 08 06 00 1B 00 C0 00 00 00 00 01
FF FF FE 00 00 03 FF FF FC 00 00 05 FF FF FA 00
00 07 FF FF F8 96 9D
```

最后 CRC 数值为 `0x9D96`，线上为 `96 9D`。

## 5. 必须通过的分块测试

同一 SAMPLE 分别按以下方式喂给增量解码器，结果必须完全相同：

- 一次输入 55 字节。
- 每次输入 1 字节。
- `5 + 14 + 36` 字节。
- 前置任意 ASCII 日志后再输入。
- 两帧拼接后一次输入。
- 第一帧 CRC 损坏，紧接一帧有效数据。

损坏帧不能输出为有效样本；后一帧必须能够重新同步。

## 6. 跨语言一致性

每个 SDK 的 CI 至少断言：

- 上述 PING 编码字节完全一致。
- CRC 测试向量一致。
- SAMPLE 解码得到精确的有符号整数，尤其是负数。
- little-endian 头字段和 big-endian ADS1299 channel 不混淆。
- sequence 回绕按无符号 32 位处理。

