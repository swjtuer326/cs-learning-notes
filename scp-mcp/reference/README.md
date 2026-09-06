# reference — SCP 专题参考规范

本目录存放学习 SCP(System Control Processor)固件所需的**外部规范/手册**,与源码克隆 `src/SCP-firmware` 配合使用。笔记正文以此处文件 + 仓库内 `doc/` 文档为事实来源,而不是凭空讲原理。

## 已放置

| 文件 | 说明 | 来源 |
|---|---|---|
| `DEN0056F_System_Control_and_Management_Interface_v4.0.pdf` | SCMI v4.0 规范。SCP 对外暴露的核心接口:电源域 / 性能 / 时钟 / 传感器 / 复位 / 电压域 / 遥测等协议;含 transport 描述(邮箱机制) | developer.arm.com,用户手动下载 |
| `DEN0022F.b_Power_State_Coordination_Interface.pdf` | PSCI v1.3 规范(Version 1.3, issue F.b)。OS → ATF → SCP 电源状态协调链路的上一环,便于理解 SCP 的调用方 | developer.arm.com,用户手动下载 |
| `neoverse_v2_technical_overview_102759_relc_03_en.pdf` | Arm Neoverse V2 参考设计技术总览(2022)——Neoverse RD 平台(RD-V2)的 CPU/互联/电源背景,与 Morello 的 N2 同族 | developer.arm.com,用户手动下载 |

## 可选补充(官网动态下载,浏览器手动获取)

协议层已闭环,以下按"读源码时需要"排序,可后续按需补充:

| 规范 | 文档号 | 链接 | 与 SCP 的关系 |
|---|---|---|---|
| Arm CoreLink MHU-320AE Message Handling Unit TRM | 107612 | https://developer.arm.com/documentation/107612 | SCMI 的硬件邮箱。SCP 源码 `module/mhu`、`mhu2`、`mhu3` 直接对应该硬件,读 transport 代码时对照。注:MHUv2 架构规范在 System IP 文档中按需提供 |
| Armv8-M Architecture Reference Manual | DDI0553 | https://developer.arm.com/documentation/ddi0553/latest | 可选。SCP 传统跑在 Cortex-M(`arch/arm/arm-m`)上,理解其异常 / 特权 / 中断模型用;新引入 AArch64 支持(`arch/arm/aarch64`) |

## 仓库内文档索引(克隆自带,不要复制进 reference)

源码克隆位于 `scp/src/SCP-firmware`(main = `0a5b4b58`;基线版本 v2.16.0)。关键文档:

- `user_guide.md` —— 构建/运行 SCP 与 MCP 固件(环境要求、三选一编译器)
- `doc/framework.md` —— 框架指南(模块、事件、消息模型)
- `doc/deferred_response_architecture.md` —— 延迟响应架构
- `doc/build_system.md` / `doc/build_configurations.md` —— 构建系统与配置
- `doc/architecture_support.md` —— 架构支持(arm-m 各 Cortex-M;新引入 AArch64)
- `doc/glossary.md` —— 术语表
- `doc/scp_firmware_threat_model.md` —— 威胁模型
- `change_log.md` —— 版本演进

产品目录 `product/` 现仅保留 juno / morello / totalcompute 三个参考平台(上游近期精简)。模块级说明在各 `module/<name>/doc/`(如 SCMI 各协议、mhu/mhu2/mhu3)。