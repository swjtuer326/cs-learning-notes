# EDK2 学习笔记

> 从零到能写驱动、移植平台的 EDK2 学习路径。面向 RISC-V 固件开发者。

### 关键术语

| 缩写 | 全称 | 含义 |
|------|------|------|
| UEFI | Unified Extensible Firmware Interface | 统一可扩展固件接口，替代传统 BIOS 的标准 |
| PI | Platform Initialization | 平台初始化规范，定义固件内部架构 |
| EDK2 | EFI Development Kit II | UEFI/PI 规范的开源参考实现 |

---

## 学习路线

每篇文档围绕一个核心问题展开，按"为什么 → 是什么 → 怎么用"递进：

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#ECECFF", "primaryTextColor": "#333333", "primaryBorderColor": "#9370DB", "lineColor": "#666666", "secondaryColor": "#ffffde", "secondaryBorderColor": "#aaaa33", "tertiaryColor": "#f0f0f0", "fontFamily": "\"trebuchet ms\", verdana, arial, sans-serif"}}}%%
flowchart LR
    P1["01-why-uefi<br/>为什么存在"]
    P2["02-boot-sequence<br/>完整启动流程"]
    P3["03-quick-start<br/>先跑起来"]
    P4["04-handle-protocol<br/>核心通信模型"]
    P5["05-first-driver<br/>写第一个驱动"]
    P6["06-events-tpl-depex<br/>时序与调度"]
    P7["07-pei-phase<br/>PEI 阶段"]
    P8["08-build-system<br/>构建系统"]
    P9["09-riscv-porting<br/>RISC-V 移植实战"]

    P1 --> P2 --> P3 --> P4 --> P5 --> P6 --> P7 --> P8 --> P9

    classDef phase1 fill:#d4edda,stroke:#28a745,color:#155724,stroke-width:2px
    classDef phase2 fill:#d1ecf1,stroke:#17a2b8,color:#0c5460,stroke-width:2px
    classDef phase3 fill:#fff3cd,stroke:#ffc107,color:#856404,stroke-width:2px
    class P1,P2,P3 phase1
    class P4,P5,P6,P7 phase2
    class P8,P9 phase3
```

## 文档索引

| 序号 | 文档 | 核心问题 | 概要 | 建议学时 |
|------|------|----------|------|----------|
| 01 | [为什么存在 UEFI](./01-why-uefi.md) | BIOS 有什么问题？ | BIOS 的四个局限、UEFI 的设计哲学、两套规范的分工 | 0.5h |
| 02 | [一次完整启动](./02-boot-sequence.md) | 按电源后发生了什么？ | SEC→PEI→DXE→BDS→RT 各阶段职责与输入输出 | 1h |
| 03 | [先跑起来](./03-quick-start.md) | 怎么看到第一个日志？ | clone、构建、QEMU 运行、DEBUG 输出解读 | 1h |
| 04 | [Handle / Protocol 核心模型](./04-handle-protocol.md) | Handle 和 Protocol 怎么配合？ | Handle 数据库、安装/查找、DriverBinding 三回调 | 2h |
| 05 | [写第一个 DXE 驱动](./05-first-driver.md) | 怎么写出能编译能运行的驱动？ | INF→入口点→安装 Protocol→消费者查找 | 2h |
| 06 | [事件 / TPL / DEPEX](./06-events-tpl-depex.md) | 驱动间的时序依赖怎么处理？ | 事件类型、Protocol 通知回调、TPL 心智模型、ExitBootServices 交接 | 2h |
| 07 | [PEI 阶段](./07-pei-phase.md) | 几十 KB 内存里怎么初始化 DDR？ | CAR 限制、PPI、HOB 列表、极简主义实践 | 1.5h |
| 08 | [构建系统深入](./08-build-system.md) | DEC/DSC/INF/FDF 怎么协作？ | 四种元数据文件分工、Library 绑定、AutoGen 代码生成 | 1.5h |
| 09 | [RISC-V 平台移植实战](./09-riscv-porting.md) | RISC-V SoC 怎么适配 UEFI？ | SBI 调用、MMU 配置、ACPI 表生成、完整移植模板 | 3h |

---

## 依赖管理

本项目使用 Git Submodule 管理 EDKII 源码：

```bash
# 初始化 submodule
git submodule update --init --recursive

# 更新 submodule 到最新版本
git submodule update --remote edk2/edk2-src

# 更新到特定版本
cd edk2/edk2-src
git checkout <tag-or-commit>
```

---

## 源码阅读导航

| 包 | 路径 | 职责 | 对应文档 |
|----|------|------|----------|
| **MdePkg** | `edk2-src/MdePkg/` | 类型定义、库类声明、UEFI/PI 头文件 | 04-handle-protocol |
| **MdeModulePkg** | `edk2-src/MdeModulePkg/` | PEI Core、DXE Core、通用驱动 | 02-boot-sequence |
| **UefiCpuPkg** | `edk2-src/UefiCpuPkg/` | CPU 驱动、异常处理、MMU | 09-riscv-porting |
| **OvmfPkg** | `edk2-src/OvmfPkg/` | QEMU 虚拟机平台（含 RiscVVirt） | 09-riscv-porting |
| **BaseTools** | `edk2-src/BaseTools/` | 构建工具集 | 08-build-system |
| **DynamicTablesPkg** | `edk2-src/DynamicTablesPkg/` | 动态 ACPI 表生成 | 09-riscv-porting |

---

## 官方文档

| 文档 | 用途 | 阶段 |
|------|------|------|
| [EDK II Build Specification](https://tianocore-docs.github.io/) | 构建系统详解 | 学完 08 后 |
| [EDK II Module Writer's Guide](https://tianocore-docs.github.io/) | 模块开发指南 | 学完 05 后 |
| [UEFI Specification 2.10](https://uefi.org/specs/UEFI/2.10/) | OS 与固件的接口规范 | 按需查阅 |
| [RISC-V UEFI 规范](https://github.com/riscv-non-isa/riscv-uefi) | RISC-V UEFI 协议 | 学完 09 后 |
