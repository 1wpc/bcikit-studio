# BCIKit WiFi Transport Profile v1

适用固件：ESP32 v0.7.0
状态：已实现接口规范  
推荐数据传输：TCP  
设备地址：`192.168.4.1`

本页只描述自研上位机使用 BCIKit 二进制采样帧时需要的 WiFi 行为。虽然部分 HTTP 路径沿用兼容层名称，但 `output="bcikit"` 或 `output="auto"` 被原生命令认领后，线上数据是 BCIKit Raw Stream Protocol v1，不是 OpenBCI 数据。

## 1. 网络模型

- ESP32 建立开放 SoftAP：`OpenBCI-XXXX`。
- ESP32 IP、网关和 HTTP API 地址：`192.168.4.1:80`。
- 电脑通常由 DHCP 获得 `192.168.4.x` 地址。
- 上位机先在电脑本地监听一个 TCP 端口，再把电脑 IP 和端口通过 HTTP 告诉 ESP32。
- ESP32 主动连接上位机监听端口。`output="bcikit"` 时，这条 TCP 连接是双向 BCIKit 字节流。

这里不是“上位机连接 ESP32 固定数据端口”的模型。

## 2. 推荐 TCP 连接时序

```text
Host                              ESP32
 |-- connect WiFi AP -------------->|
 |-- GET /board ------------------->|
 |<------------- HTTP 200 JSON -----|
 |-- listen(host_ip, random_port)   |
 |-- POST /tcp output=auto -------->|
 |<==== ESP32 connects to listener ==|
 |<------------- HTTP 200 JSON -----|
 |==== PING (自动认领 BCIKit) ======>|
 |<================ ACK ==============|
 |==== GET_INFO / STOP ==============>|
 |<=== ACK / DEVICE_INFO / ACK ======|
 |==== SET_* / GET_STATUS ===========>|
 |<=== ACK / DEVICE_STATUS ==========|
 |================ START ============>|
 |<======= ACK + continuous SAMPLE ===|
 |================ STOP =============>|
 |<================ ACK ==============|
 |-- DELETE /tcp ------------------>| 
```

监听 socket 必须在 `POST /tcp` 前建立。固件会在处理该请求时同步尝试连接；若电脑防火墙拦截或监听尚未开始，请求返回 504。

## 3. 设备探测

### `GET /board`

用于确认设备 HTTP 服务存活。当前响应示例：

```json
{
  "board_connected": true,
  "board_type": "cyton",
  "num_channels": 8,
  "gains": [24,24,24,24,24,24,24,24]
}
```

其中 `board_type` 是兼容层字段。自研软件只用它判断 HTTP 服务可达；产品型号、固件版本和能力以 TCP `GET_INFO` 返回的 DEVICE_INFO 为准。

### `GET /all`

返回热点 IP、MAC、名称、空闲堆、兼容层版本和 latency。字段可用于诊断，不建议作为长期数据模型。

## 4. 配置 TCP 数据目标

### 请求

`POST http://192.168.4.1/tcp`

```json
{
  "ip": "192.168.4.2",
  "port": 45123,
  "output": "auto",
  "delimiter": false,
  "latency": 10000
}
```

字段：

| 字段 | 类型 | 要求 |
| --- | --- | --- |
| `ip` | string | 必填，上位机在 ESP32 SoftAP 网段内的本地 IPv4 |
| `port` | integer | 必填，1..65535，必须已处于 listen 状态 |
| `output` | string | 推荐 `auto`；也可显式使用 `bcikit` |
| `delimiter` | boolean | 必须为 `false`；`true` 会在每帧后附加 CRLF |
| `latency` | integer | 可选，50..1000000 us；当前仅保存和回报，不控制聚合 |

### 成功响应

```json
{
  "connected": true,
  "delimiter": false,
  "ip": "192.168.4.2",
  "output": "auto",
  "port": 45123,
  "latency": 10000
}
```

数据 socket 是连续、双向 TCP 字节流。`output="auto"` 时，上位机必须在 5 秒内发送一条完整且 CRC 正确的 BCIKit COMMAND，推荐首先发送 PING；设备由此认领 BCIKit 会话并写回响应。一次 `recv()` 可能包含半帧、一帧或多帧，必须交给同一个增量解码器并按 `frame_type` 分发。

## 5. 原生握手、启停和释放

TCP accept 完成后建议依次发送：

```text
PING
GET_INFO
STOP
SET_OUTPUT_FORMAT(0)
SET_BOARD_COUNT(1..4)
SET_SAMPLE_RATE(250 or 500)
SET_PROFILE
SET_CHANNEL_MASK
GET_STATUS
START
```

每个 COMMAND 使用唯一的非零 `request_id`。上位机持续读取整个流，通过 `request_id` 匹配响应；不能在发送命令后只阻塞等待“下一帧”，因为 START 后 SAMPLE 可以和 ACK 交错。

原生 `STOP` 是推荐停止方式。HTTP 路径保留为连接引导、兼容和异常恢复接口：

| 请求 | 成功 | 行为 |
| --- | --- | --- |
| `GET /stream/start` | `200 OK` | HTTP 兼容启用；原生客户端使用 START COMMAND |
| `GET /stream/stop` | `200 OK` | HTTP 恢复接口；原生客户端使用 STOP COMMAND |
| `DELETE /tcp` | `200` + JSON | 先停止采样，再断开并清除 TCP 目标 |

推荐关闭顺序：发送 STOP 并收到 ACK，再 `DELETE /tcp`，最后关闭本地 listener/data socket。v0.6.1 起不依赖该顺序才能恢复：热点客户端离开、TCP 断开或连续发送失败都会触发自动停止和释放；同一客户端的新监听端口会安全替换旧目标。不同客户端仍不能抢占有效会话。

## 6. UDP 可选模式

`POST /udp` 使用与 TCP 相同字段，可增加：

```json
{"redundancy": true}
```

- 每个 UDP datagram 包含一个完整 BCIKit SAMPLE 帧。
- `redundancy=false` 时每帧发送一次。
- `redundancy=true` 时同一帧连续发送三份；上位机必须按 `sequence` 去重。
- UDP 不保证到达、顺序或唯一性。
- `DELETE /udp` 停止采样并清除目标。

首版专业上位机建议使用 TCP；只有在明确需要低延迟并实现了乱序、去重和丢包统计后再开放 UDP。

## 7. HTTP 错误处理

| HTTP 状态 | 典型原因 | 上位机动作 |
| ---: | --- | --- |
| 403 | 缺少或无法解析字段 | 记录请求体并提示客户端参数错误 |
| 409 | 已有另一个协议会话占用设备 | 不覆盖当前会话；提示关闭另一软件后重试 |
| 503 | 固件网络互斥锁繁忙 | 退避 200 ms 后有限次重试 |
| 504 | ESP32 无法连接电脑监听端口 | 检查 listener、本机 IP、专用网络防火墙 |
| 506 | `output` 无效或模式应用失败 | TCP 使用 `auto`/`bcikit`；停止后重试 |
| 500 | 无法启动采样 | 检查是否已配置目标、ADS 初始化和串口日志 |

HTTP 超时建议 5 秒。控制请求最多重试 2 次；重试前先确认是否已经产生副作用，例如 `/stream/start` 可能已经成功。

## 8. 原生控制、自动会话与 BLE 边界

`output="bcikit"` 的 TCP 连接支持全部 Device Control Protocol v1 命令：

- `PING`、`GET_INFO`、`GET_STATUS`
- `STOP`、`START`
- `SET_OUTPUT_FORMAT`、`SET_SAMPLE_RATE`、`SET_BOARD_COUNT`
- `SET_PROFILE`、`SET_CHANNEL_MASK`、`RESET_STATS`

DEVICE_INFO 的 capabilities bit 11（`0x00000800`）表示支持此功能。上位机必须检查能力位，旧固件没有该能力时应提示升级，而不是静默改用 OpenBCI `/command`。

capabilities bit 12（`0x00001000`）表示支持自动协议会话。自动会话规则：

- OpenBCI 的 `output="raw"`、`POST /command` 或 `GET /stream/start` 认领 OpenBCI。
- TCP `output="auto"` 建立的是待识别会话；首个完整、CRC 正确的 BCIKit COMMAND 认领 BCIKit。
- 完全相同的 POST 重试是幂等的；同一热点客户端的新端口 POST 视为显式重连，固件先停止采集、关闭旧目标，再建立新目标。
- 不同客户端请求仍返回 `409 BUSY`，不会覆盖正在使用的目标。由于同一台电脑上的不同应用共享 IP，用户应避免同时运行 OpenBCI GUI 和 BCIKit Studio。
- 待识别会话 5 秒未握手、热点无客户端持续 1 秒、TCP 断开、连续 3 次发送失败或删除目标时，固件停止采集并释放会话。正常停止采集不会仅因固定空闲时间而删除仍有效的TCP目标。
- UDP 没有上行字节流，不支持 `output="auto"`，必须显式选择格式。
- BLE 会话已被有效 COMMAND 认领时，WiFi 建链、控制与启动请求返回 HTTP `409 BUSY: BLE session owns the device`；BLE 的完整规则见 [BCIKit BLE Transport Profile v1](./BCIKit_BLE_Transport_v1.md)。

约束：

- WiFi BCIKit 连接上的 `SET_OUTPUT_FORMAT` 只接受值 0。
- 配置类命令在 streaming 状态返回 `BUSY`，必须先 STOP。
- `SET_SAMPLE_RATE` 接受 250/500；1000 SPS 只属于 OpenBCI BrainFlow 兼容路径。
- TCP 支持双向控制；UDP 仍只发送 SAMPLE，不接收 COMMAND。
- 控制响应和 SAMPLE 都是完整 BCIKit 帧，单次写入受固件网络互斥锁保护，不会在字节级交叉。

## 9. 安全边界

当前热点无密码、协议无认证和加密，适合实验室近距离开发，不适合不可信网络或敏感数据的正式部署。上位机应清楚显示当前连接的是开放热点；涉及人体采集数据时，默认本地存储并由用户明确授权导出。
