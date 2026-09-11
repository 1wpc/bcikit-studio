# BCIKit OpenBCI WiFi Compatibility v0.6.0

本兼容层用于让 ESP32-DevKitC 版 BCIKit 在 WiFi Direct 模式下表现为 OpenBCI WiFi Shield，同时保留 BCIKit 自有二进制协议。

## 默认网络参数

| 项目 | 固件值 | 官方兼容依据 |
| --- | --- | --- |
| SoftAP SSID | `OpenBCI-XXXX` | 官方 Shield 使用 MAC 后四位生成名称 |
| SoftAP 密码 | 无 | 官方 WiFi Direct 默认开放热点 |
| SoftAP IP/网关 | `192.168.4.1` | 官方 WiFi Direct 固定地址 |
| HTTP API | TCP 80 | 官方 REST server |
| SSDP | `239.255.255.250:1900` | GUI/Node driver 自动发现 |
| SSDP ST | `urn:schemas-upnp-org:device:Basic:1` | OpenBCI Node WiFi driver 搜索目标 |
| latency 默认值 | `10000 us` | 官方固件默认值 |

## 官方连接时序

OpenBCI GUI/driver 在电脑上建立 TCP Server 或 UDP 接收端，然后调用 ESP32 HTTP API：

1. SSDP 发现 `OpenBCI-XXXX`，或用户输入 `192.168.4.1`。
2. `GET /board` 和 `GET /all` 获取 Cyton、8 通道、gain 24 等信息。
3. `POST /tcp` 或 `POST /udp` 告诉 ESP32 电脑的 IP 和端口。
4. ESP32 主动连接 TCP Server，或保存 UDP 目标地址。
5. `POST /command` 发送 `b`，或 `GET /stream/start` 开始采集。
6. 设备持续发送数据；`s` 或 `/stream/stop` 停止。

这与“电脑主动连接 ESP32 固定数据端口”的普通 TCP Server 模型不同。

## HTTP 接口

### 信息

- `GET /board`：返回 `board_connected=true`、`board_type=cyton`、8 通道和 8 个 gain 24。
- `GET /all`：返回热点 IP、MAC、名称、版本、通道数和 latency。
- `GET /version`：返回兼容层版本。
- `GET /description.xml`：SSDP 设备描述。

### 数据目标

`POST /tcp` 请求体：

```json
{
  "ip": "192.168.4.2",
  "port": 12345,
  "output": "raw",
  "delimiter": false,
  "latency": 10000
}
```

`POST /udp` 字段相同，并支持 `{"redundancy":true}`，表示每个 UDP 数据报重复发送三次。`DELETE /tcp` 或 `DELETE /udp` 清除目标。

### 输出格式

- `output="raw"`：OpenBCI Cyton 33-byte packet，`A0 + sample + 24 EEG + 6 AUX + C0`。
- `output="bcikit"`：BCIKit 扩展，发送 BCIKit Raw Stream v1 完整帧。
- `output="auto"`：BCIKit 扩展；TCP 建立后等待有效 BCIKit COMMAND，OpenBCI 官方客户端无需使用。
- `output="json"`：当前未实现，明确返回错误，不会假装输出 JSON。

v0.5.0 起，`output="bcikit"` 的 TCP 连接同时接收 BCIKit Device Control Protocol v1 COMMAND；v0.6.0 增加 `auto` 和首会话锁定。该能力属于 BCIKit 原生扩展，不属于 OpenBCI Shield API；`output="raw"` 时不解析 BCIKit COMMAND。

### 控制

- `POST /command`，请求体 `{"command":"b"}` 或其他 OpenBCI 命令。
- `GET /stream/start`、`GET /stream/stop`。
- `GET /latency`、`POST /latency`。

采样率命令支持 `~~`、`~6`（250 SPS）、`~5`（500 SPS）和 `~4`（1000 SPS）。1000 SPS 使用 ADS1299 `CONFIG1=0x94` 真实配置，供当前 BrainFlow Cyton WiFi 初始化使用；其他采样率命令返回失败。

## 当前兼容边界

- 已实现 WiFi Direct、SSDP、mDNS、HTTP、TCP、UDP、UDPx3 和 raw 数据。
- 当前仅模拟 Cyton 8 通道，不模拟 Cyton+Daisy。
- 尚未实现官方 JSON 纳伏数据、MQTT、Station 配网网页和 OTA。
- `latency` 字段会保存并回报；raw 数据当前按采样到达立即发送，不人为延迟或聚合。
- OpenBCI 会话存在时，BCIKit 的冲突连接返回 `409 BUSY`；反向 TCP 断开或删除目标后自动释放，不影响下一次使用 BCIKit。
- ESP32 同时保留 UART 输出，便于台架诊断；正式无线测试仍需测量长时间丢包率。
