# BCIKit BLE Transport Profile v1

适用固件：ESP32 v0.7.0 及以上  
状态：已实现接口规范  
协议编码：[BCIKit Raw Stream Protocol v1](./BCIKit_Raw_Stream_Protocol_v1.md) 与 [Device Control Protocol v1](./BCIKit_Device_Control_Protocol_v1.md)

本页是自研上位机接入 BCIKit BLE 的实现合同。BLE 使用 BCIKit 原生二进制帧，不兼容也不模拟 OpenBCI Cyton 的蓝牙协议；需要 OpenBCI GUI 兼容性时，请继续使用 WiFi Direct。

## 1. 发现与 GATT 服务

设备以 BLE Peripheral 广播，名称为 `BCIKit-XXXX`，其中 `XXXX` 是芯片 MAC 的低 16 位十六进制表示。名称仅用于列表展示，连接后必须按下列 128-bit UUID 校验服务，避免把同名设备当作 BCIKit。

| 项目 | UUID | 属性 | 数据方向与用途 |
| --- | --- | --- | --- |
| BCIKit Service | `7d2ea28a-f7bd-485a-bd9d-92ad6ecfe93e` | Primary Service | BCIKit BLE v1 服务 |
| Control RX | `7d2ea28b-f7bd-485a-bd9d-92ad6ecfe93e` | Write, Write Without Response | Host → device：BCIKit COMMAND 帧 |
| Control TX | `7d2ea28c-f7bd-485a-bd9d-92ad6ecfe93e` | Notify | Device → host：ACK、DEVICE_INFO、DEVICE_STATUS |
| Sample TX | `7d2ea28d-f7bd-485a-bd9d-92ad6ecfe93e` | Notify | Device → host：SAMPLE |

固件请求 MTU 247；上位机应在连接后协商尽可能大的 MTU（建议至少 185，优先 247）。若中心设备只提供更小 MTU，固件仍会发送，但吞吐量下降，实时采样不保证稳定。

## 2. 字节流和分包规则

每个 characteristic 是各自独立的 BCIKit 帧字节流：Control TX 只含控制响应，Sample TX 只含 SAMPLE。所有 BCIKit 帧保持原有 `A5 5A`、长度与 CRC 格式，BLE **不**添加额外的帧类型、包序号或分片头。

- 一次 GATT Write 可以是半个 COMMAND、完整 COMMAND 或多个 COMMAND；上位机可任意切分写入。
- 一条 Notification 可以只含一帧的一部分，也可以包含多条连续的完整帧；接收端必须为 Control TX 和 Sample TX 分别维护增量解码缓冲区，不能假设“一个通知就是一帧”。
- 发送下一条命令前无需等待 Notification 边界；通过 BCIKit `request_id` 匹配 ACK。
- 收到 CRC 错误或非法长度时，按 Raw Stream Protocol v1 的同步规则在同一 characteristic 流内重新寻找 `A5 5A`；不要因此清空另一条 characteristic 的缓冲。

不需要、也不能在 BLE 层再对 SAMPLE 的 `sequence` 编号。`sequence` 仍用于检测真实的采样丢失或通知拥塞导致的缺帧。

## 3. 连接、会话和采样时序

```text
Host                                      ESP32
 |-- scan BCIKit Service ------------------>|
 |-- connect; request MTU; discover GATT -->|
 |-- subscribe Control TX + Sample TX ---->|
 |-- PING COMMAND via Control RX --------->|
 |<----------------------- ACK (Control TX) |
 |-- GET_INFO / STOP ---------------------->|
 |<--- ACK / DEVICE_INFO / ACK (Control TX) |
 |-- SET_* / GET_STATUS ------------------->|
 |<---- ACK / DEVICE_STATUS (Control TX)    |
 |-- START -------------------------------->|
 |<----------------------- ACK (Control TX) |
 |<================ SAMPLE (Sample TX) ====|
 |-- STOP --------------------------------->|
 |<----------------------- ACK (Control TX) |
 |-- disconnect --------------------------->|
```

订阅两个 Notify characteristic 后，第一条完整且 CRC 正确的 COMMAND（推荐 `PING`）会认领 BLE 会话。之后使用与 WiFi TCP 完全相同的 COMMAND payload、状态码和 `request_id`：

```text
PING -> GET_INFO -> STOP -> SET_OUTPUT_FORMAT(0)
-> SET_BOARD_COUNT -> SET_SAMPLE_RATE -> SET_PROFILE
-> SET_CHANNEL_MASK -> GET_STATUS -> START
```

配置、启停和错误处理规则以 Device Control Protocol v1 为准。UI 发出配置不代表已生效：必须等待匹配的 `ACK(STATUS_OK)`，并在关键配置后使用 `GET_STATUS` 读取设备实际状态。

## 4. 吞吐量边界

BLE V1 不实现重传、流量信用或记录回放。为使首版在常见手机与桌面蓝牙控制器上保持可用，固件只允许下列采样组合启动：

| AFE8 板数 | 可用采样率 | 说明 |
| ---: | --- | --- |
| 1 | 250、500 SPS | 支持 |
| 2 | 250 SPS | 支持 |
| 2 | 500 SPS | 拒绝 `START`，请改用 WiFi TCP |
| 3、4 | 任意 | 拒绝 `START`，请改用 WiFi TCP |

若设备端无法把通知排入 BLE 协议栈，当前帧会被计入固件的传输丢弃统计；上位机应同时监控 SAMPLE `sequence` gap。对持续数据记录、3/4 板或高吞吐场景，WiFi TCP 是正式推荐传输方式。

## 5. 与 WiFi/OpenBCI 的互斥规则

- BLE 只支持 `SET_OUTPUT_FORMAT(0)`，即 BCIKit Raw Stream v1；请求 OpenBCI Cyton 格式会得到 `ACK(STATUS_UNSUPPORTED)`。
- 首个有效 BLE COMMAND 认领会话后，WiFi `/tcp`、`/udp`、`/command`、`/stream/start` 等会返回 HTTP `409 BUSY: BLE session owns the device`。
- 已有 WiFi 数据目标或 WiFi 原生会话时，BLE COMMAND 得到 `ACK(STATUS_BUSY)`，不会夺取正在采集的会话。
- 单纯 BLE 已连接但尚未发送有效 COMMAND 时，WiFi 仍可正常建立会话；单纯创建 WiFi SoftAP 也不会阻止 BLE。
- BLE 链路断开会自动执行 STOP、丢弃尚未处理的 BLE 控制字节并释放会话，随后 WiFi 可以立即重新接管。

不要同时让 OpenBCI GUI、自研 WiFi 客户端和 BLE 客户端争用同一设备。上位机应将 `409` 或 `STATUS_BUSY` 明确显示为“另一传输会话正在使用设备”，而不是误报硬件掉线。

## 6. 上位机实现清单

1. 扫描时按 service UUID 过滤，展示设备名与 RSSI。
2. 连接后协商 MTU、发现 characteristic、**先订阅** Control TX 和 Sample TX，再发 PING。
3. 对三个方向分别建模：Control RX 写队列、Control TX 增量解码器、Sample TX 增量解码器。
4. 维护唯一非零 `request_id`；ACK 的 `request_id` 匹配请求，SAMPLE 不参与命令等待。
5. 显示协商 MTU、Notify 是否已订阅、有效帧数、CRC 错误、`sequence` gap、当前采样率和板数。
6. 断开、Notify 订阅被取消或 3 秒无有效 SAMPLE 时，终止本次采集并允许用户重连；不应向旧 characteristic 重复写入。
7. 首版无需 BLE 配对、绑定或应用层认证；产品上线前若采集敏感人体数据，应另行设计安全与授权方案。

## 7. 能力发现

`GET_INFO.capabilities` 的 bit 13（`0x00002000`）表示 `BLE_BCIKIT_STREAM`，bit 14（`0x00004000`）表示 `BLE_BCIKIT_CONTROL`。只有两位都存在时，客户端才应在 UI 中开放 BLE 原生采集；缺失时将设备视为旧固件，并给出升级提示。

本 Profile 不改变 BCIKit Protocol v1 的帧结构或既有枚举值；新增传输介质只通过 capability 位和本 GATT 映射声明。
