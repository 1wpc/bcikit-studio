# BCIKit 上位机集成指南 v1

适用固件：ESP32 v0.7.0
推荐首版链路：WiFi Direct + HTTP 建链 + TCP BCIKit 双向控制与采样；BLE 见独立 Profile
原生协议版本：1

## 1. 最小可用目标

首个上位机版本应完成这条闭环：

```text
连接热点 -> 创建 TCP listener -> 配置 ESP32 目标 -> 原生握手/配置 -> 启动采集
-> 增量解码 -> CRC/sequence 检查 -> int24 转换 -> 波形显示/落盘
-> 停止采集 -> 释放目标
```

不要在第一版同时实现 OpenBCI、UDP、多板、滤波器编辑器和复杂分析。先把原始采集、重连和数据完整性做扎实。

## 2. 推荐软件分层

```text
UI / Session
    |
DeviceSession state machine
    |-- HTTP control client
    |-- TCP listener + bidirectional BCIKit socket
    |-- BCIKit incremental decoder
    |-- integrity monitor (CRC / sequence / flags)
    |-- raw recorder
    `-- processing pipeline -> display / filters / algorithms
```

网络读取线程只负责收字节和解帧，不在该线程做滤波、绘图或磁盘压缩。有效帧进入有界队列；队列满时必须计数并提示“上位机处理丢帧”，不能静默覆盖。

## 3. 会话状态机

建议状态：

| 状态 | 含义 |
| --- | --- |
| `DISCONNECTED` | 未连接设备热点或 HTTP 不可达 |
| `DISCOVERING` | 查询 `192.168.4.1/board` |
| `LISTENING` | 本地 TCP listener 已建立 |
| `CONFIGURING` | 正在 `POST /tcp output=auto` 并以 PING 认领会话、执行 GET_INFO/SET_* |
| `READY` | ESP32 已反向连接、配置读回成功，尚未采样 |
| `STREAMING` | 正在收 SAMPLE |
| `STOPPING` | 正在停止并释放目标 |
| `ERROR` | 超时、CRC 连续错误、断线或固件错误 |

所有失败路径最终都要关闭 socket，并尽力发送 STOP，最后 `DELETE /tcp`。冲突时的 HTTP `409` 表示 OpenBCI 或另一 BCIKit 会话正在占用设备，上位机不得强行调用兼容层 `/stream/stop` 破坏对方会话。重连必须新建 decoder，避免把上一条连接残留的半帧拼到新会话。

## 4. 接收与增量解码

TCP 没有消息边界。解码器至少完成：

1. 在缓存中搜索 `A5 5A` 并确认 `version=1`。
2. 读取 `frame_type`。SAMPLE 为 `0x01`；control frame 为 `0x02..0x05`。
3. SAMPLE 缓存不足 26 字节时等待，读取 offset 24 的 `payload_len`，计算 `total_len = 28 + payload_len`。
4. control frame 缓存不足 12 字节时等待，读取 offset 10 的 `payload_len`，计算 `total_len = 14 + payload_len`。
5. 等待完整帧并校验 CRC-16/CCITT-FALSE。
6. SAMPLE 校验 `board_count=1..4`、`channel_count=board_count*8`、`payload_len=board_count*27`。
7. SAMPLE 校验每块 27 字节 block 的状态首字节高四位为 `0xC`。
8. control frame 按 `request_id` 交给等待请求，SAMPLE 交给数据流水线。
9. CRC 或格式失败时从当前 sync 的第二个字节之后重新搜索。

不要假设一次 socket read 就是一帧，也不要按 55 字节固定切包；多板帧长度不同，控制帧也会随时出现在相同字节流中。

## 5. 通道数据转换

每个通道是 3 字节大端有符号整数：

```text
raw24 = (b0 << 16) | (b1 << 8) | b2
if raw24 & 0x800000:
    raw32 = raw24 - 0x1000000
else:
    raw32 = raw24
```

当前内部参考电压 4.5 V、gain 24 时，显示用微伏换算：

```text
microvolts = raw32 * 4.5 / 24 / (2^23 - 1) * 1,000,000
           = raw32 * 0.0223517444553
```

原始文件建议保存 `raw32`、帧元数据和设备配置，不要只保存经过滤波的浮点值。若未来 gain 可变，应使用帧中的 `gain_code` 和会话配置换算，不能写死 24。

## 6. 通道和板卡顺序

每块板 block 为：3 字节状态 + CH1..CH8 各 3 字节。当前 payload 保留 ADS1299 wire order。

若物理链为：

```text
Host -> Board A -> Board B -> Board C
```

线上 block 顺序是 C、B、A。当前 `PHYSICAL_ORDER` flag 为 0，上位机显示物理顺序时使用：

```text
physical_board = board_count - 1 - wire_board
global_channel = physical_board * 8 + local_channel
```

单板时 wire/physical 相同。多板 UI 上线前必须用每块板不同测试信号或逐块短接做实物映射验证。

## 7. sequence、时间戳和丢帧

### sequence

- `sequence` 是无符号 32 位计数，允许自然回绕。
- 它在固件采集路径中持续递增，停止/启动不保证归零。
- 固件启动采样后会丢弃最初若干稳定帧，因此第一条收到的 sequence 不保证为 0。
- 计算差值：`delta = (current - previous) & 0xFFFFFFFF`。
- `delta=1` 连续；`delta>1` 表示中间缺少 `delta-1` 帧；重复 sequence 在 TCP 中不应出现。

### timestamp_us

- 时间戳来自 ESP32 单调微秒时钟，记录 DRDY 下降沿，不是网络到达时间。
- 设备重启后重新起算，不能直接作为 UTC 或本地时间。
- 文件中同时保存 host 会话开始 UTC、host 单调时间和设备 `timestamp_us`。
- 绘图横轴优先使用设备时间；网络抖动不应改变样本时间间隔。

### 丢帧信号

以下任一条件都应增加会话质量告警：

- sequence gap。
- `DROPPED_BEFORE` flag。
- CRC 错误。
- 本地处理队列溢出。
- TCP 断开或在预期三个采样周期内没有新帧。

CRC 错误表示字节流损坏；sequence gap 表示采集或传输路径丢失。二者必须分别统计。

## 8. 采样质量指标

会话页至少显示：

| 指标 | 计算 |
| --- | --- |
| 有效帧数 | CRC、长度和状态均通过的帧 |
| 有效采样时长 | 首末设备时间戳差 |
| 实际采样率 | `(valid_frames-1) / duration` |
| sequence 丢帧数 | 所有正向 gap 累计 |
| CRC 错误数 | 解码器累计 |
| 格式错误/丢弃字节 | 重同步累计 |
| 最大到达间隔 | host receive monotonic 的最大间隔 |
| 设备丢帧标记数 | `DROPPED_BEFORE` 出现次数 |

250 SPS 下设备时间戳间隔约 4000 us，500 SPS 下约 2000 us。不要用网络包到达间隔判断真实采样率。

## 9. 数据保存建议

推荐同时保存两层：

1. 原始层：未经修改的完整 BCIKit frame，便于未来重新解码和审计。
2. 索引层：解码后的 sequence、timestamp、flags、通道 `int32`、会话元数据。

会话元数据至少包括：

- 软件版本、协议版本、固件版本（能取得时）。
- device ID/MAC、采样率、板数、通道掩码、profile、gain。
- host 操作系统、会话开始/结束 UTC。
- 总帧数、丢帧、CRC、重连次数和结束原因。
- 原始文件 SHA-256。

不要在原始记录文件里覆盖或插值缺失样本。算法需要等间隔数据时，在派生数据层显式补值并保留 mask。

## 10. 断线和恢复

- HTTP 可达但数据 socket 断开：停止旧会话，重新 listener -> `POST /tcp` -> start。
- HTTP 也不可达：提示检查 WiFi 是否仍连接 `OpenBCI-XXXX`。
- 连续 CRC 错误：清空至下一个 sync；超过阈值后重连并保留错误诊断数据。
- 应用重启：旧 TCP 断开后固件会自动释放；若暂时收到 `409`，短暂退避并重试，不要调用 `/stream/stop` 抢占可能属于其他软件的会话。
- 设备重启可通过热点暂时消失、TCP 断开、timestamp/sequence 回退综合识别。

自动重连应有上限和退避，例如 0.5 s、1 s、2 s，共三次；之后交由用户操作，避免无限循环掩盖硬件问题。

## 11. 原生握手与配置策略

TCP accept 完成后，上位机使用递增的 `request_id` 执行：

```text
PING("app-version")
GET_INFO                 -> 检查 protocol_version 和 capability bit 11/12
STOP
SET_OUTPUT_FORMAT(0)
SET_BOARD_COUNT(N)
SET_SAMPLE_RATE(250|500)
SET_PROFILE(eeg|diff|test|short)
SET_CHANNEL_MASK(mask)
GET_STATUS               -> 所有字段必须与请求配置一致
START
```

每条请求设置独立超时并通过 `request_id` 匹配响应。START ACK 和首个 SAMPLE 可能交错；接收线程必须始终运行，不能由请求函数独占 socket 读取。配置失败时保留 ACK status 和最近 DEVICE_STATUS 作为诊断信息。

默认值仍是单板、250 SPS、EEG SRB2、八通道、gain 24，但上位机应显式设置并读回，而不是依赖默认值。

## 12. 上位机验收清单

- 固定测试向量全部通过。
- WiFi PING、GET_INFO、全部 SET_*、GET_STATUS、START、STOP 往返通过。
- 不支持 capability bit 11 的旧固件会得到明确升级提示。
- v0.6.0 自动模式必须返回 capability bit 12；需要兼容 v0.5.0 时，上位机可在 `output=auto` 被 506 拒绝后回退到显式 `output=bcikit`。
- 支持 BLE 的 v0.7.0 必须同时返回 capability bit 13、14；BLE 端按 [BCIKit_BLE_Transport_v1.md](./BCIKit_BLE_Transport_v1.md) 单独验证分包重组、Notify 订阅与断开释放。
- 随机 1..N 字节切块输入仍能解出同样帧。
- 帧前插入日志/垃圾字节可恢复同步。
- CRC 错误后一帧能恢复。
- sequence `0xFFFFFFFF -> 0` 不误报大规模丢帧。
- TCP 一次收到多帧、半帧和断在 CRC 中间均正确。
- 保存文件能重新解码并得到相同通道值。
- 运行至少 30 分钟并报告有效采样率、gap、CRC 和重连次数。
- 防火墙拒绝反向 TCP 时能给出明确提示。
- 停止、拔电、WiFi 断开和应用强退后都能再次建立会话。
