# EDK2 全景地图

> 从 BIOS 到 UEFI，从闭源到开源，固件开发正在经历一场静悄悄的革命。EDK2 是这场革命的核心战场。

## 1. 为什么你需要了解 EDK2

如果你是一名系统软件工程师，尤其是从事 RISC-V SoC 固件和内核开发，EDK2 是你绕不开的基础设施。原因很简单：

- **UEFI 是事实标准**：从服务器到嵌入式，UEFI 已经取代传统 BIOS 成为固件接口标准
- **EDK2 是 UEFI 的参考实现**：Intel 开源，社区维护，工业界广泛使用
- **RISC-V 服务器需要 UEFI**：RISC-V 服务器生态正在快速成熟，UEFI + ACPI 是服务器启动的标配
- **固件是安全的第一道防线**：Secure Boot、TPM、Measured Boot 都在固件层实现

**一句话总结**：不懂 EDK2，你的 RISC-V SoC 就是一块没有灵魂的硅片。

## 2. EDK2 是什么

EDK II (EFI Development Kit II) 是一个现代化的、跨平台的固件开发环境，实现了 UEFI (Unified Extensible Firmware Interface) 和 PI (Platform Initialization) 规范。

### 2.1 规范体系

理解 EDK2 必须先理解它实现的规范体系，这是整个知识体系的骨架：

```
┌─────────────────────────────────────────────────────┐
│                    UEFI 规范                          │
│  (OS 与固件的接口：Boot Services, Runtime Services,   │
│   Protocols, 变量服务, GPT 分区, 网络栈...)          │
├─────────────────────────────────────────────────────┤
│                    PI 规范                            │
│  (固件内部架构：SEC → PEI → DXE → BDS → TSL → RT)  │
│  ├─ PI PEI 规范 (PEI Core, PPI, HOB)                │
│  ├─ PI DXE 规范 (DXE Core, Protocol, 驱动模型)       │
│  └─ PI SMM 规范 (SMM Core, SMI Handler)             │
├─────────────────────────────────────────────────────┤
│                  ACPI 规范                            │
│  (OS 与硬件的接口：电源管理, 设备描述, 中断路由...)    │
└─────────────────────────────────────────────────────┘
```

**UEFI vs PI 的区别**（这是初学者最容易混淆的点）：
- **UEFI 规范**定义的是固件暴露给 OS 的接口（对外的契约）
- **PI 规范**定义的是固件内部各阶段之间的接口（对内的契约）
- EDK2 同时实现了两者

### 2.2 EDK2 的核心设计哲学

| 设计哲学 | 体现 |
|----------|------|
| **模块化** | 每个功能是一个独立的 Module（.inf 描述），可独立编译 |
| **包化管理** | 相关模块组织成 Package（.dec 描述），包是发布和版本管理的基本单位 |
| **接口与实现分离** | Library Class（接口）vs Library Instance（实现），DSC 中做绑定 |
| **数据驱动** | PCD (Platform Configuration Database) 实现配置与代码分离 |
| **声明式构建** | DSC/FDF 声明"要构建什么"，构建系统自动解决依赖和生成代码 |

## 3. 源码目录全景

EDK2 源码树庞大但组织有序。以下是按功能分类的目录地图：

### 3.1 核心框架包（必须掌握）

| 包 | 路径 | 职责 | 重要程度 |
|----|------|------|----------|
| **MdePkg** | `MdePkg/` | 模块开发环境：类型定义、库类声明、UEFI/PI 规范头文件 | ⭐⭐⭐⭐⭐ |
| **MdeModulePkg** | `MdeModulePkg/` | 核心实现：PEI Core、DXE Core、SMM Core、通用驱动 | ⭐⭐⭐⭐⭐ |

### 3.2 CPU 与平台包

| 包 | 路径 | 职责 | 重要程度 |
|----|------|------|----------|
| **UefiCpuPkg** | `UefiCpuPkg/` | CPU 驱动：CPU DXE、异常处理、SMM CPU、定时器、MMU | ⭐⭐⭐⭐ |
| **OvmfPkg** | `OvmfPkg/` | QEMU 虚拟机平台（含 RiscVVirt 子目录） | ⭐⭐⭐⭐ |
| **ArmPkg** | `ArmPkg/` | ARM 架构支持 | ⭐⭐⭐ |

### 3.3 功能包

| 包 | 路径 | 职责 |
|----|------|------|
| **CryptoPkg** | `CryptoPkg/` | 加密库（基于 OpenSSL/MbedTLS） |
| **SecurityPkg** | `SecurityPkg/` | 安全功能（TPM、Secure Boot） |
| **NetworkPkg** | `NetworkPkg/` | 网络协议栈（TCP/IP、HTTP、PXE） |
| **ShellPkg** | `ShellPkg/` | UEFI Shell |
| **FatPkg** | `FatPkg/` | FAT 文件系统驱动 |

### 3.4 特殊用途包

| 包 | 路径 | 职责 |
|----|------|------|
| **StandaloneMmPkg** | `StandaloneMmPkg/` | 独立 MM 框架（ARM TrustZone 安全环境） |
| **DynamicTablesPkg** | `DynamicTablesPkg/` | 动态 ACPI 表生成（含 RISC-V ACPI 表） |
| **IntelFsp2Pkg** | `IntelFsp2Pkg/` | Intel FSP（固件支持包） |
| **UefiPayloadPkg** | `UefiPayloadPkg/` | Universal Payload 入口 |
| **EmulatorPkg** | `EmulatorPkg/` | 宿主机模拟器（开发调试用） |

### 3.5 构建系统

| 路径 | 职责 |
|------|------|
| `BaseTools/` | 构建工具集（C 工具 + Python 构建引擎） |
| `Conf/` | 构建配置（由 BaseTools/Conf/*.template 生成） |

## 4. 启动流程全景

这是理解 EDK2 最重要的心智模型——从按下电源键到 OS 启动的完整旅程：

```
  CPU 上电/复位
       │
       ▼
  ┌──────────┐    纯汇编，CPU 复位向量 (0xFFFFFFF0 on x86)
  │ResetVector│    搜索 BFV → 定位 SEC Core → 跳转
  └────┬─────┘
       │
       ▼
  ┌──────────┐    第一个 C 代码阶段
  │   SEC    │    初始化临时 RAM (CAR) → 设置 IDT → 定位 PEI Core
  └────┬─────┘
       │
       ▼
  ┌──────────┐    初始化永久内存 → 调度 PEIM → 构建 HOB
  │   PEI    │    Shadow 自身到内存 → 定位 DXE Core → 跳转
  └────┬─────┘
       │
       ▼
  ┌──────────┐    建立 EFI 服务表 → 调度 DXE 驱动 → 等待架构协议就绪
  │   DXE    │    加载 SMM Core 到 SMRAM → 调用 BDS Entry
  └────┬─────┘
       │
       ▼
  ┌──────────┐    枚举启动设备 → 连接控制台 → 加载 OS 引导程序
  │   BDS    │    处理 BootOrder/BootNext 变量
  └────┬─────┘
       │
       ▼
  ┌──────────┐    OS Loader 执行 → 调用 ExitBootServices
  │   TSL    │    固件从 Boot Services 过渡到 Runtime Services
  └────┬─────┘
       │
       ▼
  ┌──────────┐    仅保留 Runtime Services（变量服务、时间、重置）
  │    RT    │    OS 内核接管系统
  └──────────┘

  ┌──────────┐    (独立运行) SMI 中断触发 → 进入 SMM
  │   SMM    │    运行在隔离的 SMRAM 中，OS 不可见
  └──────────┘
```

**RISC-V 的差异**：RISC-V 没有 x86 的实模式/保护模式切换，也没有 SMM。RISC-V 的启动从 M-mode 开始，通过 SBI (Supervisor Binary Interface) 与上层交互。SEC 阶段直接在 M-mode 运行，后续阶段切换到 S-mode。

## 5. 核心文件类型速查

EDK2 有自己独特的元数据文件体系，这是理解项目的钥匙：

| 文件类型 | 扩展名 | 作用 | 类比 |
|----------|--------|------|------|
| **DEC** | `.dec` | 包声明：定义包的公共接口（库类、GUID、PCD） | C 的 `.h` 文件 |
| **DSC** | `.dsc` | 平台描述：定义如何构建一个平台（库绑定、PCD 值、模块列表） | Makefile / CMakeLists |
| **INF** | `.inf` | 模块定义：描述一个模块的源码、依赖、入口点 | `.c` 文件 + 编译信息 |
| **FDF** | `.fdf` | 固件描述：定义 Flash 布局和固件卷内容 | 链接脚本 + 分区表 |
| **UNI** | `.uni` | Unicode 字符串资源（多语言支持） | `.po` / `.resx` |
| **VFR** | `.vfr` | Visual Form Representation（BIOS Setup 界面） | HTML 表单 |
| **CI** | `.ci.yaml` | 包的 CI 配置 | `.github/workflows/` |

**文件之间的依赖关系**：

```
DEC (包接口定义)
 ├── 被 DSC 引用（指定使用哪些包）
 ├── 被 INF 引用（声明依赖哪些包的接口）
 └── 被 FDF 引用（引用 GUID 定义）

DSC (平台构建配置)
 ├── 引用 DEC（指定包依赖）
 ├── 引用 INF（指定要构建的模块）
 ├── 引用 FDF（指定固件布局）
 └── 设置 PCD 值

INF (模块定义)
 ├── 引用 DEC（声明包依赖）
 └── 被 DSC 引用（被包含在平台构建中）

FDF (固件布局)
 ├── 引用 DEC（使用 GUID）
 ├── 引用 INF（指定模块放入哪个 FV）
 └── 被 DSC 引用（关联平台构建）
```

## 6. 官方文档导航

TianoCore 官方文档体系庞大，按学习阶段推荐阅读顺序：

### 6.1 入门必读（第一阶段）

| 文档 | 内容 | 优先级 |
|------|------|--------|
| EDK II Build Specification | 构建系统详解 | ⭐⭐⭐⭐⭐ |
| EDK II DEC Specification | 包声明文件格式 | ⭐⭐⭐⭐⭐ |
| EDK II INF Specification | 模块定义文件格式 | ⭐⭐⭐⭐⭐ |
| EDK II DSC Specification | 平台描述文件格式 | ⭐⭐⭐⭐ |
| EDK II FDF Specification | 固件描述文件格式 | ⭐⭐⭐⭐ |

### 6.2 进阶必读（第二阶段）

| 文档 | 内容 | 优先级 |
|------|------|--------|
| EDK II PCD Specification | 平台配置数据库 | ⭐⭐⭐⭐ |
| EDK II Module Writer's Guide | 模块开发指南 | ⭐⭐⭐⭐⭐ |
| EDK II C Coding Standards | C 编码规范 | ⭐⭐⭐ |
| Understanding UEFI Secure Boot Chain | 安全启动链 | ⭐⭐⭐⭐ |

### 6.3 高级专题（第三阶段）

| 文档 | 内容 | 优先级 |
|------|------|--------|
| EDK II VFR Specification | BIOS Setup 界面开发 | ⭐⭐⭐ |
| EDK II Secure Coding Guide | 安全编码指南 | ⭐⭐⭐⭐ |
| EDK II Minimum Platform Specification | 最小平台规范 | ⭐⭐⭐⭐ |
| Understanding the Trusted Boot Chain | 可信启动链 | ⭐⭐⭐⭐ |

**文档获取**：所有文档可在 https://tianocore-docs.github.io/ 获取 HTML/PDF 版本。

## 7. 学习路线图

针对 RISC-V 固件开发者的推荐学习路线：

```
Phase 1: 建立心智模型（1-2 周）
├── 理解 UEFI/PI 规范体系
├── 掌握启动流程全景
├── 熟悉源码目录结构
└── 理解 DEC/DSC/INF/FDF 文件关系

Phase 2: 构建与运行（1-2 周）
├── 搭建构建环境
├── 构建 OvmfPkg/RiscVVirt（QEMU RISC-V）
├── 在 QEMU 中运行 RISC-V UEFI 固件
└── 使用 GDB 调试固件

Phase 3: 核心概念深入（2-4 周）
├── MdePkg 类型系统与库类体系
├── PEI Core 源码分析（HOB、PPI、调度器）
├── DXE Core 源码分析（Protocol、事件、GCD）
└── BDS 启动流程分析

Phase 4: 模块开发实战（2-4 周）
├── 编写简单的 DXE 驱动
├── 编写 PEIM 模块
├── 使用 PCD 做平台配置
└── Library Class 设计与实现

Phase 5: RISC-V 平台移植（4-8 周）
├── 分析 OvmfPkg/RiscVVirt 架构
├── 理解 RISC-V SEC/PEI 初始化流程
├── RISC-V MMU 与页表配置
├── RISC-V ACPI 表生成（DynamicTablesPkg）
└── 为新 SoC 创建平台包
```

## 8. 关键术语表

| 术语 | 全称 | 含义 |
|------|------|------|
| UEFI | Unified Extensible Firmware Interface | 统一可扩展固件接口 |
| PI | Platform Initialization | 平台初始化规范 |
| SEC | Security Phase | 安全阶段（启动第一阶段） |
| PEI | Pre-EFI Initialization | PEI 阶段（内存初始化） |
| DXE | Driver Execution Environment | DXE 阶段（驱动执行环境） |
| BDS | Boot Device Selection | 启动设备选择 |
| TSL | Transient System Load | 过渡系统加载 |
| RT | Runtime | 运行时 |
| SMM | System Management Mode | 系统管理模式（x86 特有） |
| MM | Management Mode | 管理模式（SMM 的架构无关抽象） |
| PPI | PEIM-to-PEIM Interface | PEI 阶段模块间接口 |
| Protocol | - | DXE 阶段模块间接口 |
| HOB | Hand-Off Block | 阶段间数据传递结构 |
| PCD | Platform Configuration Database | 平台配置数据库 |
| FV | Firmware Volume | 固件卷 |
| FFS | Firmware File System | 固件文件系统 |
| BFV | Boot Firmware Volume | 启动固件卷 |
| CAR | Cache-as-RAM | 缓存作为 RAM（SEC/PEI 早期临时内存） |
| SBI | Supervisor Binary Interface | RISC-V 特权层二进制接口 |
| FDT | Flattened Device Tree | 扁平化设备树 |
| GCD | Global Coherency Domain | 全局一致性域（内存映射管理） |

---

**下一篇**：[01-architecture.md](01-architecture.md) — 架构与核心概念深入
