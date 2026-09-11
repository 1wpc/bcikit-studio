# BCIKit 上位机协议文档入口

适用固件：ESP32 v0.7.0
原生协议版本：BCIKit Protocol v1  
面向对象：桌面端、移动端、数据采集服务、算法 SDK 开发者

自研上位机只需要实现 BCIKit 原生协议，不需要解析 OpenBCI 33 字节数据包。OpenBCI 相关接口是第三方兼容层，不是新软件的内部数据模型。

## 必读顺序

1. [BCIKit_Host_Integration_Guide_v1.md](./BCIKit_Host_Integration_Guide_v1.md)：从连接到显示第一条 EEG 波形的完整流程。
2. [BCIKit_WiFi_Transport_v1.md](./BCIKit_WiFi_Transport_v1.md)：自动协议会话、WiFi HTTP、TCP、UDP 行为。
3. [BCIKit_BLE_Transport_v1.md](./BCIKit_BLE_Transport_v1.md)：v0.7.0 已实现的 BLE 服务、双向控制、通知重组和会话规则。
4. [BCIKit_Raw_Stream_Protocol_v1.md](./BCIKit_Raw_Stream_Protocol_v1.md)：SAMPLE 二进制帧及 ADS1299 payload。
5. [BCIKit_Device_Control_Protocol_v1.md](./BCIKit_Device_Control_Protocol_v1.md)：二进制控制、设备信息和状态。
6. [BCIKit_Protocol_Test_Vectors_v1.md](./BCIKit_Protocol_Test_Vectors_v1.md)：跨语言实现必须通过的固定字节向量。
7. [../sdk/python/README.md](../sdk/python/README.md)：可运行的 Python 行为参考。

## v0.7.0 支持矩阵

| 能力 | WiFi TCP | WiFi UDP | BLE | UART0 |
| --- | --- | --- | --- | --- |
| BCIKit SAMPLE | 已实现，推荐 | 已实现 | 已实现，单板 250/500、双板 250 | 因 115200 带宽不足，不用于原生流 |
| BCIKit COMMAND | 已实现，与 SAMPLE 复用同一 TCP | 未实现 | 已实现，独立 Control RX | 已实现 |
| DEVICE_INFO / STATUS / ACK | 已实现 | 未实现 | 已实现，独立 Control TX | 已实现 |
| 连接和启停 | HTTP 负责建立目标，BCIKit COMMAND 负责会话 | HTTP | GATT 连接与首个有效 COMMAND | 文本或二进制命令 |
| 原生采样率 | 250/500 SPS，可无线配置 | 250/500 SPS，需先经 TCP/UART 配置 | 250/500，受 BLE 板数限制 | 可配置，但原生数据不从 UART 持续输出 |
| 板数 | 1..4，可无线配置 | 使用设备当前配置 | 1..2；双板仅 250 SPS | 1..4 |

这张表是上位机开发的实现边界。WiFi TCP 是双向 BCIKit 字节流，并支持首个有效协议会话自动认领和冲突隔离；BLE 使用同一帧编码，但将控制和采样放在不同 characteristic。UDP 仍是单向数据模式。

## 推荐首版上位机范围

- 桌面端先实现 WiFi TCP；移动端或近距无线场景可按 BLE Profile 实现 BLE。
- 默认从单板、8 通道、250 SPS、EEG SRB2、gain 24 起步，但通过原生 COMMAND 完成配置和读回确认。
- 完整实现增量解码、CRC、sequence 丢包检测、断线重连和原始数据保存。
- UI 中显示固件状态、有效帧数、CRC 错误、sequence gap、当前实际采样率。
- 所有滤波和显示使用原始数据的副本；落盘保留原始 `int24` 或完整 wire frame。

## 版本与兼容约定

- `protocol_version=1` 的字段偏移和现有枚举值不允许修改。
- 新命令、新 flag、新 capability 只能使用未分配的 bit 或 ID。
- 上位机遇到未知 frame type 时必须重新同步或按已知长度安全跳过，不能崩溃。
- 上位机以设备返回的实际字段为准，不能把 UI 选择值当作已生效配置。
- `GET_INFO.capabilities` bit 11 表示设备支持 WiFi BCIKit 双向控制。
- `GET_INFO.capabilities` bit 12 表示设备支持 WiFi 自动协议会话。
- `GET_INFO.capabilities` bit 13、14 分别表示支持 BLE BCIKit SAMPLE 与双向控制。
- 文档中的“已实现”指 v0.7.0 源码行为；“规划”不构成可调用接口。
