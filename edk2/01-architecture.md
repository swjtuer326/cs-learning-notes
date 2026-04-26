# EDK2 架构与核心概念

> 固件不是黑魔法，它只是在你以为计算机还没开机的时候，就已经跑完了一个操作系统。

## 1. 类型系统：一切代码的根基

EDK2 的类型系统定义在 `MdePkg/Include/Base.h` 中，通过 `ProcessorBind.h` 实现架构无关性。这是你写任何 EDK2 代码前必须理解的基础。

### 1.1 架构绑定机制

```
Base.h
  └── #include <ProcessorBind.h>    ← 架构相关，编译器根据目标架构选择
        ├── X64/ProcessorBind.h     ← x86-64
        ├── Ia32/ProcessorBind.h    ← IA-32
        ├── AArch64/ProcessorBind.h ← ARM64
        ├── RiscV64/ProcessorBind.h ← RISC-V 64
        └── LoongArch64/ProcessorBind.h ← 龙芯
```

`ProcessorBind.h` 的核心职责是定义 `UINTN`/`INTN`（与指针等宽的整数）和函数调用约定。在 RISC-V 64 上，`UINTN` 是 64 位。

### 1.2 核心数据类型

```c
// 固定宽度整数（与 Linux 内核的 u8/u16/u32/u64 对应）
UINT8, UINT16, UINT32, UINT64    // 无符号
INT8,  INT16,  INT32,  INT64     // 有符号

// 指针宽度整数（类似 Linux 的 unsigned long / long）
UINTN, INTN                       // 大小 = sizeof(void*)

// 布尔（注意：UEFI 的 BOOLEAN 是 1 字节，不是 C 的 int）
BOOLEAN                           // 必须为 TRUE (1) 或 FALSE (0)

// 字符
CHAR8                             // ASCII (1 字节)
CHAR16                            // UCS-2 (2 字节，UEFI 字符串编码)

// 物理地址
PHYSICAL_ADDRESS                  // UINT64，即使 32 位系统也是 64 位

// GUID（128 位唯一标识符，UEFI 的"万能钥匙"）
typedef struct {
  UINT32  Data1;
  UINT16  Data2;
  UINT16  Data3;
  UINT8   Data4[8];
} GUID;
```

### 1.3 函数参数修饰符

这是 UEFI 代码最显眼的风格特征——`IN`/`OUT`/`OPTIONAL`：

```c
EFI_STATUS
EFIAPI
SomeFunction (
  IN     EFI_HANDLE   Handle,        // 输入参数
  IN OUT UINTN        *BufferSize,   // 输入输出参数
  OUT    VOID         *Buffer,       // 输出参数
  IN     BOOLEAN      OptionalFlag  OPTIONAL  // 可选参数
  );
```

这些修饰符在编译时展开为空，纯粹是给人类看的文档。但它们在代码审查时极其有用——一眼就能看出参数的方向。

### 1.4 状态码体系

UEFI 的函数几乎都返回 `EFI_STATUS`（本质是 `UINTN`）：

```
编码规则：
  最高位 = 0 → 警告 (Warning)
  最高位 = 1 → 错误 (Error)

常用状态码：
  EFI_SUCCESS              (0)           // 成功，唯一没有设置最高位的"好消息"
  EFI_INVALID_PARAMETER    (0x80000002)  // 参数无效
  EFI_UNSUPPORTED          (0x80000003)  // 不支持
  EFI_DEVICE_ERROR         (0x80000007)  // 设备错误
  EFI_OUT_OF_RESOURCES     (0x80000009)  // 资源不足
  EFI_NOT_FOUND            (0x8000000E)  // 未找到
  EFI_ACCESS_DENIED        (0x8000000F)  // 访问拒绝
  EFI_SECURITY_VIOLATION   (0x8000001A)  // 安全违规
```

判断宏：`RETURN_ERROR(Status)` 检查最高位是否为 1。

### 1.5 实用宏

```c
// 从成员指针获取结构体指针（Linux 内核的 container_of）
BASE_CR(Record, TYPE, Field)

// 编译时断言（C11 _Static_assert 的 EDK2 封装）
STATIC_ASSERT(expression, message)

// 位掩码（BIT0 到 BIT63，寄存器操作必备）
BIT0, BIT1, BIT2, ... BIT63

// 大小常量（内存操作必备）
SIZE_1KB, SIZE_2KB, ... SIZE_8EB
BASE_1KB, BASE_2KB, ... BASE_8EB

// 对齐宏
ALIGN_VALUE(Value, Alignment)    // 向上对齐
IS_ALIGNED(Value, Alignment)     // 判断对齐
```

## 2. 启动阶段深度解析

### 2.1 ResetVector — 从硅片到软件的桥梁

ResetVector 是 CPU 上电后执行的第一段代码，通常是纯汇编。

**x86 的 ResetVector 流程**（`UefiCpuPkg/ResetVector/`）：

```
CPU 上电 → 0xFFFFFFF0 (4GB 顶部-16)
    │
    ├─ 1. EarlyInit16: 16 位实模式初始化
    ├─ 2. TransitionFromReal16To32BitFlat: 实模式 → 保护模式
    ├─ 3. SearchForBfvBase: 在顶部 16MB 空间搜索 BFV
    │      (每 4KB 对齐检查 FFS GUID)
    ├─ 4. SearchForSecEntryPoint: 在 BFV 中找 SEC Core
    │      (查找 EFI_FV_FILETYPE_SECURITY_CORE 类型文件)
    ├─ 5. Flat32ToFlat64: 32 位 → 64 位模式切换
    └─ 6. jmp esi: 跳转到 SEC Core 入口点
```

**RISC-V 的 ResetVector**（`OvmfPkg/RiscVVirt/Library/PlatformSecLib/SecEntry.S`）：

RISC-V 没有实模式/保护模式的概念，CPU 直接从 M-mode 启动，流程更简洁：

```
CPU 上电 → 0x1000 (QEMU virt 机器的复位向量)
    │
    ├─ 1. 设置栈指针
    ├─ 2. 保存 BootHartId 和 FdtPointer
    ├─ 3. 调用 SecEntry (C 函数)
    └─ 4. SecEntry → PlatformSecLib → PEI Core
```

### 2.2 SEC — 安全阶段

SEC 是第一个 C 代码阶段，源码位于 `UefiCpuPkg/SecCore/`。

**核心职责**：
1. 初始化临时 RAM（x86 用 CAR - Cache as RAM，RISC-V 用平台特定机制）
2. 设置 IDT/中断处理
3. 定位 PEI Core 入口点
4. 将控制权交给 PEI Core

**关键函数调用链**：

```
SecStartup()                          [UefiCpuPkg/SecCore/SecMain.c]
  ├─ ReportStatusCode (SEC 入口)
  ├─ InitializeFloatingPointUnits()
  ├─ InitializeIdt()
  ├─ 配置临时 RAM 栈
  └─ InitializeDebugAgent()
       └─ SecStartupPhase2()
            ├─ SecPlatformMain()       ← 平台特定初始化
            ├─ FindAndReportEntryPoints()  ← 定位 PEI Core
            └─ 跳转到 PEI Core
```

**SEC 注册的 PPI**（传递给 PEI Core）：
- `gEfiTemporaryRamDonePpiGuid` — 临时 RAM 禁用
- `gEfiSecPlatformInformationPpiGuid` — 平台信息
- `gPeiSecPerformancePpiGuid` — 性能数据

### 2.3 PEI — Pre-EFI 初始化阶段

PEI 是"内存初始化者"，源码位于 `MdeModulePkg/Core/Pei/`。

**核心职责**：
1. 调度 PEIM 模块（PEI Module）
2. 初始化永久内存（DDR）
3. 构建 HOB（Hand-Off Block）列表
4. 将控制权交给 DXE Core

**PEI 的两阶段运行**：

```
Phase 1: 临时 RAM 阶段（CAR/SRAM）
  ├─ PeiCore() 首次进入
  ├─ 初始化 PEI 服务表
  ├─ 建立 PPI 数据库
  ├─ 调度 PEIM（内存初始化 PEIM 最关键）
  └─ 永久内存可用后...

Phase 2: 永久内存阶段
  ├─ ShadowPeiCore(): 将 PEI Core 从 Flash 拷贝到内存
  ├─ PeiCore() 重新进入（OldCoreData != NULL）
  ├─ 迁移 PPI 数据库到内存
  ├─ 继续调度剩余 PEIM
  ├─ 构建 HOB 列表
  └─ 定位 DXE Core → 跳转
```

**PEI 服务表**（`gPs`）是 PEI 阶段的核心 API：

| 服务类别 | 关键接口 |
|----------|----------|
| PPI 管理 | `InstallPpi`, `ReInstallPpi`, `LocatePpi`, `NotifyPpi` |
| 启动模式 | `GetBootMode`, `SetBootMode` |
| HOB 管理 | `GetHobList`, `CreateHob` |
| 固件卷 | `FfsFindNextVolume`, `FfsFindNextFile`, `FfsFindSectionData` |
| 内存 | `InstallPeiMemory`, `AllocatePages`, `AllocatePool` |
| 状态码 | `ReportStatusCode` |

#### HOB（Hand-Off Block）

HOB 是 PEI 向 DXE 传递数据的核心机制，本质是一个单向链表：

```c
typedef struct {
  UINT16    HobType;       // HOB 类型
  UINT16    HobLength;     // 本 HOB 长度
  UINT32    Reserved;      // 保留
} EFI_HOB_GENERIC_HEADER;
```

**关键 HOB 类型**：

| HOB 类型 | 用途 |
|----------|------|
| `EFI_HOB_TYPE_HANDOFF` | PEI 到 DXE 的交接信息（包含 DXE Core 需要的所有信息） |
| `EFI_HOB_TYPE_MEMORY_ALLOCATION` | 内存分配描述 |
| `EFI_HOB_TYPE_RESOURCE_DESCRIPTOR` | 系统资源描述（物理内存范围） |
| `EFI_HOB_TYPE_GUID_EXTENSION` | 自定义数据（通过 GUID 区分） |
| `EFI_HOB_TYPE_FV` | 固件卷位置信息 |
| `EFI_HOB_TYPE_CPU` | CPU 信息（频率等） |

**RISC-V 特有的 HOB**：`RISCV_SEC_HANDOFF_HOB_GUID`，包含 `BootHartId` 和 `FdtPointer`。

#### PPI（PEIM-to-PEIM Interface）

PPI 是 PEI 阶段的模块间通信机制，类似 DXE 阶段的 Protocol，但更轻量：

```c
typedef struct _EFI_PEI_PPI_DESCRIPTOR {
  EFI_PEI_PPI_DESCRIPTOR_FLAGS  Flags;    // 安装/通知标志
  EFI_GUID                      *Guid;    // PPI 的 GUID
  VOID                          *Ppi;     // 指向 PPI 接口结构
} EFI_PEI_PPI_DESCRIPTOR;
```

PPI 与 Protocol 的关键区别：
- PPI 在临时 RAM 中，内存有限
- PPI 没有句柄（Handle）概念
- PPI 不支持打开/关闭协议的复杂语义
- PPI 支持通知机制（NotifyPPI），类似 DXE 的事件回调

### 2.4 DXE — 驱动执行环境

DXE 是 UEFI 启动中最重要、最复杂的阶段，源码位于 `MdeModulePkg/Core/Dxe/`。

**核心职责**：
1. 建立 EFI 系统表（Boot Services + Runtime Services）
2. 调度 DXE 驱动
3. 管理协议（Protocol）数据库
4. 管理事件（Event）和定时器
5. 管理内存映射（GCD）
6. 等待架构协议就绪后调用 BDS

**DxeMain() 初始化流程**：

```
DxeMain(HobStart)                    [MdeModulePkg/Core/Dxe/DxeMain/DxeMain.c]
  ├─ CoreInitializeMemoryServices()  ← 从 HOB 初始化内存
  ├─ CoreInitializeHandleServices()  ← 句柄/协议数据库
  ├─ CoreInitializeImageServices()   ← 镜像加载服务
  ├─ CoreInitializeGcdServices()     ← 全局一致性域
  ├─ 初始化事件/定时器
  ├─ 初始化 Runtime Services
  ├─ 进入 DXE Dispatcher 循环
  │    ├─ 调度 DXE 驱动
  │    └─ 检查架构协议是否就绪
  └─ gBds->Entry(gBds)              ← 进入 BDS
```

#### EFI 系统表

EFI 系统表是 DXE 阶段建立的核心数据结构，是所有 UEFI 应用程序和驱动的入口参数：

```
EFI_SYSTEM_TABLE
  ├── EFI_TABLE_HEADER (签名、版本、校验和)
  ├── FirmwareVendor, FirmwareRevision
  ├── EFI_HANDLE ConsoleInHandle → EFI_SIMPLE_TEXT_INPUT_PROTOCOL
  ├── EFI_HANDLE ConsoleOutHandle → EFI_SIMPLE_TEXT_OUTPUT_PROTOCOL
  ├── EFI_HANDLE StandardErrorHandle → EFI_SIMPLE_TEXT_OUTPUT_PROTOCOL
  ├── EFI_RUNTIME_SERVICES *RuntimeServices  ← OS 可用的运行时服务
  ├── EFI_BOOT_SERVICES *BootServices        ← 仅 Boot Services 阶段可用
  ├── UINTN NumberOfTableEntries
  └── EFI_CONFIGURATION_TABLE *ConfigurationTable  ← ACPI 表等
```

#### Boot Services（启动服务）

Boot Services 在 `ExitBootServices()` 调用后失效，是 UEFI 驱动的主要 API：

| 服务类别 | 关键接口 |
|----------|----------|
| 事件 | `CreateEvent`, `SetTimer`, `WaitForEvent`, `SignalEvent` |
| 内存 | `AllocatePages`, `FreePages`, `AllocatePool`, `FreePool` |
| 协议 | `InstallProtocolInterface`, `HandleProtocol`, `LocateHandle`, `OpenProtocol` |
| 镜像 | `LoadImage`, `StartImage`, `UnloadImage`, `Exit` |
| 杂项 | `Stall`, `SetWatchdogTimer`, `CopyMem`, `SetMem` |

#### Runtime Services（运行时服务）

Runtime Services 在 OS 运行期间仍然可用，是固件与 OS 的持久接口：

| 服务类别 | 关键接口 |
|----------|----------|
| 变量 | `GetVariable`, `SetVariable`, `GetNextVariableName` |
| 时间 | `GetTime`, `SetTime`, `GetWakeupTime` |
| 重置 | `ResetSystem` (冷启动/热启动/关机) |
| 虚拟内存 | `SetVirtualAddressMap`, `ConvertPointer` |
| 杂项 | `GetNextHighMonotonicCount`, `UpdateCapsule` |

#### Protocol（协议）

Protocol 是 DXE 阶段的核心通信机制，是 UEFI 的"面向对象"编程模型：

```c
// 安装 Protocol
EFI_STATUS
InstallProtocolInterface (
  IN OUT EFI_HANDLE  *Handle,       // 句柄（Protocol 的容器）
  IN     EFI_GUID    *Protocol,     // Protocol 的 GUID
  IN     EFI_INTERFACE_TYPE InterfaceType,
  IN     VOID        *Interface     // 指向 Protocol 接口结构
  );

// 查找 Protocol
EFI_STATUS
HandleProtocol (
  IN  EFI_HANDLE  Handle,
  IN  EFI_GUID    *Protocol,
  OUT VOID        **Interface
  );
```

**Protocol vs PPI 对比**：

| 特性 | PPI (PEI) | Protocol (DXE) |
|------|-----------|----------------|
| 存储位置 | 临时 RAM / 永久内存 | 永久内存 |
| 容器 | 无（全局 PPI 数据库） | Handle（句柄） |
| 生命周期 | PEI 阶段 | DXE → Runtime |
| 查找方式 | GUID | GUID + Handle |
| 通知机制 | NotifyPPI | Event + RegisterProtocolNotify |
| 复杂度 | 简单 | 复杂（Open/Close/Attributes） |

#### GCD（全局一致性域）

GCD 是 DXE 阶段的内存映射管理器，维护系统地址空间的统一视图：

```
GCD 管理的地址空间属性：
  - 内存类型：SystemMemory, MemoryMappedIo, Reserved, etc.
  - 内存属性：UC, WC, WT, WB (缓存属性)
  - 内存能力：ReadOnly, WriteOnly, ReadWrite, Executable
  - 内存状态：Allocated, Unallocated
```

GCD 的核心操作：
- `AddMemorySpace()` — 注册新的内存区域
- `AllocateMemorySpace()` — 分配内存区域
- `SetMemorySpaceAttributes()` — 设置内存属性（如缓存策略）
- `GetMemorySpaceMap()` — 获取完整内存映射

### 2.5 BDS — 启动设备选择

BDS 是 DXE 调度的最后一个阶段，源码位于 `MdeModulePkg/Universal/BdsDxe/`。

**BDS 的工作流程**：

```
BdsEntry()                           [MdeModulePkg/Universal/BdsDxe/BdsEntry.c]
  ├─ 填充 FirmwareVendor/Revision
  ├─ 验证 EFI 全局变量
  ├─ 连接控制台设备 (ConIn/ConOut/StdErr)
  ├─ 处理启动选项：
  │    ├─ DriverOrder → 加载驱动
  │    ├─ SysPrepOrder → 系统准备
  │    ├─ BootNext → 尝试一次性启动
  │    ├─ BootOrder → 按顺序尝试启动
  │    └─ PlatformRecovery → 平台恢复
  └─ 加载并启动 OS Loader
```

**启动选项存储在 UEFI 变量中**：

| 变量 | 含义 |
|------|------|
| `BootOrder` | 启动顺序列表（UINT16 数组） |
| `Boot####` | 具体启动选项（#### 是 BootOrder 中的编号） |
| `BootNext` | 下一次启动的选项编号（一次性） |
| `Timeout` | 启动菜单超时秒数 |

### 2.6 SMM — 系统管理模式

SMM 是 x86 特有的高权限执行模式，源码位于 `MdeModulePkg/Core/PiSmmCore/`。

**SMM 的关键特性**：
- 运行在独立的 SMRAM 中，OS 完全不可见
- 由 SMI（System Management Interrupt）触发进入
- 权限高于 OS 和 Hypervisor
- 用于实现安全策略、固件更新等

**SMM 的加载流程**：

```
DXE 阶段：
  ├─ SMM IPL (PiSmmIpl.c) 加载
  │    ├─ 定位 SMRAM 区域
  │    ├─ 将 SMM Core 加载到 SMRAM
  │    ├─ 安装 SMM Communication Protocol
  │    └─ 注册 SMI 处理器
  └─ SMM Core (PiSmmCore.c) 在 SMRAM 中初始化
       ├─ 建立 SMM 系统表 (SMST)
       ├─ 调度 SMM 驱动
       └─ 注册核心 SMI Handler
```

**SMM 系统表 (SMST)** 提供的服务：
- `SmmAllocatePool/SmmFreePool` — SMRAM 内存分配
- `SmmAllocatePages/SmmFreePages` — SMRAM 页面分配
- `SmiHandlerRegister/SmiHandlerUnRegister` — SMI 处理器注册
- `SmmInstallProtocolInterface` — SMM 协议管理
- `SmmStartupThisAp` — 多处理器 SMI 同步

> **RISC-V 注意**：RISC-V 没有 SMM。等价的安全执行环境是 M-mode（机器模式）和 TEE（Trusted Execution Environment）。StandaloneMmPkg 提供了架构无关的 MM 框架，可在 ARM TrustZone 或 RISC-V M-mode 上运行。

## 3. 库类体系

EDK2 的库类体系是其最优雅的设计之一——**接口与实现完全分离**。

### 3.1 核心概念

```
Library Class (接口)          Library Instance (实现)
┌─────────────────┐          ┌──────────────────────┐
│ BaseLib.h       │          │ BaseLib/X64/         │  ← x86-64 实现
│ (声明函数签名)   │          │ BaseLib/RiscV64/     │  ← RISC-V 实现
│                 │          │ BaseLib/AArch64/     │  ← ARM64 实现
└─────────────────┘          └──────────────────────┘
         ↑                            ↑
    DEC 文件声明                  DSC 文件绑定
    [LibraryClasses]             [LibraryClasses.XXX]
    BaseLib|Include/Library/BaseLib.h   BaseLib|MdePkg/Library/BaseLib
```

**绑定发生在 DSC 文件中**：

```ini
[LibraryClasses]
  BaseLib|MdePkg/Library/BaseLib/BaseLib.inf
  BaseMemoryLib|MdePkg/Library/BaseMemoryLibRepStr/BaseMemoryLibRepStr.inf
  DebugLib|MdePkg/Library/UefiDebugLibConOut/UefiDebugLibConOut.inf
```

同一个 Library Class 可以在不同模块类型中绑定不同实现：

```ini
[LibraryClasses.common.PEIM]
  MemoryAllocationLib|MdePkg/Library/PeiMemoryAllocationLib/PeiMemoryAllocationLib.inf

[LibraryClasses.common.DXE_DRIVER]
  MemoryAllocationLib|MdePkg/Library/UefiMemoryAllocationLib/UefiMemoryAllocationLib.inf
```

### 3.2 核心库类速查

| 库类 | 职责 | 关键接口 |
|------|------|----------|
| **BaseLib** | 基础运行时（位操作、字符串、数学、CPU 特定） | `BitFieldRead64`, `StrCmp`, `DivU64x64Remainder` |
| **BaseMemoryLib** | 内存操作（拷贝、填充、比较） | `CopyMem`, `SetMem`, `CompareMem`, `ZeroMem` |
| **DebugLib** | 调试输出 | `DEBUG`, `ASSERT`, `DEBUG_CODE` |
| **PrintLib** | 格式化输出 | `UnicodeVSPrint`, `AsciiVSPrint` |
| **IoLib** | I/O 端口和 MMIO 访问 | `MmioRead32`, `MmioWrite32`, `IoRead8` |
| **PcdLib** | PCD 访问 | `FixedPcdGet32`, `PcdGetPtr`, `PcdSetBoolS` |
| **UefiLib** | UEFI 便利函数 | `InitializeLib`, `UnicodeStrToAsciiStrS` |
| **UefiBootServicesTableLib** | 提供 gBS, gImageHandle, gST | 全局变量 |
| **UefiRuntimeServicesTableLib** | 提供 gRS | 全局变量 |
| **DevicePathLib** | 设备路径操作 | `IsDevicePathValid`, `DevicePathToString` |
| **HiiLib** | 人机接口基础设施 | `HiiAddPackages`, `HiiGetString` |

### 3.3 RISC-V 特有库类

| 库类 | 职责 | 实现位置 |
|------|------|----------|
| **BaseRiscVSbiLib** | SBI 调用封装 | `MdePkg/Library/BaseRiscVSbiLib/` |
| **RiscVMmuLib** | MMU 操作 | `UefiCpuPkg/Library/BaseRiscVMmuLib/` |

**BaseRiscVSbiLib 的关键接口**：

```c
// SBI ecall 封装
EFI_STATUS
SbiCall (
  IN  UINTN ExtId,     // SBI 扩展 ID
  IN  UINTN FuncId,    // 函数 ID
  IN  UINTN NumArgs,   // 参数数量 (0-6)
  ...
  );

// 常用封装
VOID SbiSetTimer(UINT64 Time);           // 设置定时器
EFI_STATUS SbiSystemReset(UINT32 Type);  // 系统重启
```

## 4. PCD（平台配置数据库）

PCD 是 EDK2 实现"配置与代码分离"的核心机制。

### 4.1 PCD 类型

| 类型 | 何时确定 | 可修改 | 典型用途 |
|------|----------|--------|----------|
| **FeatureFlag** | 编译时 | 否 | 功能开关（布尔值） |
| **FixedAtBuild** | 编译时 | 否 | 固定常量（基地址、大小） |
| **PatchableInModule** | 编译时 | 二进制修补 | 需要后期调整的参数 |
| **Dynamic** | 运行时 | 是 | 运行时配置 |
| **DynamicEx** | 运行时 | 是 | 跨包共享的动态 PCD |

### 4.2 PCD 的使用

```c
// 编译时 PCD（最高性能，直接内联常量）
UINT32 base = FixedPcdGet32(PcdPciExpressBaseAddress);

// 运行时 PCD（通过 PCD 协议/服务获取）
UINT32 size = PcdGet32(PcdMaxVariableSize);

// 设置运行时 PCD
PcdSet32S(PcdMaxVariableSize, newSize);

// FeatureFlag PCD（编译时决定代码是否包含）
if (FeaturePcdGet(PcdUgaConsumeSupport)) {
  // 这段代码在 PcdUgaConsumeSupport=FALSE 时不会被编译
}
```

### 4.3 RISC-V 相关 PCD

| PCD | 令牌空间 | 默认值 | 用途 |
|-----|----------|--------|------|
| `PcdRiscVFeatureOverride` | gEfiMdePkgTokenSpaceGuid | 0xFFFFFFFFFFFFFFFF | 覆盖 RISC-V CPU 特性自动检测 |
| `PcdCpuRiscVMmuMaxSatpMode` | gUefiCpuPkgTokenSpaceGuid | 10 | MMU SATP 最大模式 (Sv39=8, Sv48=9, Sv57=10) |

## 5. 固件卷与固件文件系统

### 5.1 固件卷（Firmware Volume）

固件卷是 EDK2 固件的基本存储单元，类似磁盘上的分区：

```
┌──────────────────────────────────────┐
│           Firmware Volume            │
│  ┌────────────────────────────────┐  │
│  │  EFI_FIRMWARE_VOLUME_HEADER    │  │
│  │  (签名、大小、属性、校验和)     │  │
│  ├────────────────────────────────┤  │
│  │  FFS File 1 (PEI Core)        │  │
│  │  ├─ EFI_FFS_FILE_HEADER       │  │
│  │  └─ Sections (PE32/TE)        │  │
│  ├────────────────────────────────┤  │
│  │  FFS File 2 (DXE Driver)      │  │
│  │  ├─ EFI_FFS_FILE_HEADER       │  │
│  │  └─ Sections (PE32/DEPEX)     │  │
│  ├────────────────────────────────┤  │
│  │  FFS File 3 (PEIM)            │  │
│  │  ...                          │  │
│  └────────────────────────────────┘  │
│  [Padding / Free Space]              │
└──────────────────────────────────────┘
```

### 5.2 FFS 文件类型

| 类型值 | 含义 | 阶段 |
|--------|------|------|
| 0x01 | RAW | 任意 |
| 0x02 | FREEFORM | 任意 |
| 0x03 | SECURITY_CORE | SEC |
| 0x04 | PEI_CORE | PEI |
| 0x05 | DXE_CORE | DXE |
| 0x06 | PEIM | PEI |
| 0x07 | DRIVER | DXE |
| 0x08 | COMBINED_PEIM_DRIVER | PEI+DXE |
| 0x09 | APPLICATION | DXE |
| 0x0B | FFS_PAD | 填充 |

### 5.3 Flash 布局（以 RiscVVirt 为例）

```
物理地址空间：
  0x20000000 ┌─────────────────────────┐
             │    PFLASH0 (CODE FD)     │
             │    8MB                   │
             │  ┌─────────────────────┐ │
             │  │ FV Recovery (PEI)   │ │
             │  │ FV DXE              │ │
             │  │ FV Boot Enforcer    │ │
             │  └─────────────────────┘ │
  0x20800000 ├─────────────────────────┤
             │    PFLASH1 (VARS FD)    │
             │    768KB                │
             │  ┌─────────────────────┐ │
             │  │ NV Variable Store   │ │
             │  │ FTW Working Block   │ │
             │  │ FTW Spare Block     │ │
             │  └─────────────────────┘ │
  0x220C0000 └─────────────────────────┘
```

## 6. 依赖表达式（DEPEX）

DEPEX (Dependency Expression) 是 EDK2 驱动调度的核心机制，声明模块运行的前提条件。

### 6.1 PEI 阶段的 DEPEX

PEI 的 DEPEX 基于 PPI：

```c
// 在 INF 文件中声明
[Depex]
  gEfiPeiMemoryDiscoveredPpiGuid AND gEfiPeiFirmwareVolumeInfoPpiGuid
```

含义：此 PEIM 只有在内存初始化 PPI 和 FV 信息 PPI 都安装后才可调度。

### 6.2 DXE 阶段的 DEPEX

DXE 的 DEPEX 基于 Protocol：

```c
// 在 INF 文件中声明
[Depex]
  gEfiPciRootBridgeIoProtocolGuid AND gEfiCpuArchProtocolGuid
```

### 6.3 DEPEX 操作码

| 操作码 | 含义 |
|--------|------|
| `BEFORE` | 在指定 GUID 的驱动之前调度 |
| `AFTER` | 在指定 GUID 的驱动之后调度 |
| `PUSH` | 压入 GUID |
| `AND` | 逻辑与 |
| `OR` | 逻辑或 |
| `NOT` | 逻辑非 |
| `TRUE` | 恒真 |
| `FALSE` | 恒假 |
| `END` | 表达式结束 |

**DEPEX 求值模型**：使用栈式求值器，类似逆波兰表达式。

---

**上一篇**：[00-overview.md](00-overview.md) — EDK2 全景地图
**下一篇**：[02-build-system.md](02-build-system.md) — 构建系统深入
