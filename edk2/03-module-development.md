# EDK2 模块开发实战

> 写代码是工程师的本能，但在 EDK2 中写代码需要先理解它的"世界观"——模块、协议、库类、PCD 构成了一个精密的齿轮系统。

## 1. 模块开发基础

### 1.1 模块类型与入口点

EDK2 中每个模块都有一个明确的类型和入口点：

```c
// DXE Driver 入口点
EFI_STATUS
EFIAPI
MyDriverEntryPoint (
  IN EFI_HANDLE        ImageHandle,
  IN EFI_SYSTEM_TABLE  *SystemTable
  )
{
  // 初始化
  // 安装 Protocol
  // 注册事件回调
  return EFI_SUCCESS;
}
```

**入口点宏映射**（在 INF 文件中声明 `ENTRY_POINT = XxxInitialize`）：

| MODULE_TYPE | 入口点库 | 实际调用的函数 |
|-------------|----------|---------------|
| DXE_DRIVER | UefiDriverEntryPoint | `UefiDriverEntryPoint` → 你的函数 |
| UEFI_APPLICATION | UefiApplicationEntryPoint | `UefiApplicationEntryPoint` → 你的函数 |
| PEIM | PeimEntryPoint | `_ModuleEntryPoint` → 你的函数 |
| DXE_RUNTIME_DRIVER | UefiDriverEntryPoint | 同 DXE_DRIVER |

**EFIAPI 的含义**：指定使用 UEFI 调用约定（x86 上是 `__cdecl`，ARM/RISC-V 上是默认 AAPCS/LP64）。这是跨架构兼容的关键。

> **设计背景 — 为什么需要 EFIAPI？** 不同编译器和架构有不同的默认调用约定。例如，x86 上 Microsoft 编译器默认使用 `__cdecl`，而 GCC 可能使用其他约定；ARM 使用 AAPCS；RISC-V 使用 LP64。UEFI 规范要求所有跨模块调用的函数使用统一的调用约定，`EFIAPI` 就是这个统一的标记。没有它，不同编译器编译的模块之间调用可能因参数传递方式不同而崩溃。

### 1.2 模块的最小文件集

创建一个 DXE 驱动模块，最少需要 3 个文件：

```
MyDriver/
├── MyDriver.c      # 源码
├── MyDriver.h      # 头文件（可选但推荐）
└── MyDriver.inf    # 模块定义
```

### 1.3 第一个 DXE 驱动：Hello World

**MyDriver.inf**：

```ini
[Defines]
  INF_VERSION    = 0x00010005
  BASE_NAME      = MyDriver
  FILE_GUID      = 12345678-1234-1234-1234-123456789ABC
  MODULE_TYPE    = DXE_DRIVER
  VERSION_STRING = 1.0
  ENTRY_POINT    = MyDriverEntryPoint

[Sources]
  MyDriver.c

[Packages]
  MdePkg/MdePkg.dec

[LibraryClasses]
  UefiDriverEntryPoint
  UefiLib
  DebugLib

[Depex]
  TRUE
```

**MyDriver.c**：

```c
#include <Uefi.h>
#include <Library/UefiLib.h>
#include <Library/DebugLib.h>

EFI_STATUS
EFIAPI
MyDriverEntryPoint (
  IN EFI_HANDLE        ImageHandle,
  IN EFI_SYSTEM_TABLE  *SystemTable
  )
{
  DEBUG ((DEBUG_INFO, "MyDriver: Hello from DXE driver!\n"));
  return EFI_SUCCESS;
}
```

**将模块添加到平台 DSC**：

```ini
[Components]
  MyPkg/MyDriver/MyDriver.inf
```

**将模块添加到 FDF**（放入固件卷）：

```ini
[FV.FvMain]
  INF MyPkg/MyDriver/MyDriver.inf
```

> **注意**：DSC 中的 `[Components]` 决定模块是否被编译，FDF 中的 `INF` 决定编译后的模块是否被打包进固件映像。两者缺一不可——只在 DSC 中添加而不在 FDF 中添加，模块会被编译但不会出现在最终的 `.fd` 文件中。

## 2. Protocol 开发

Protocol 是 DXE 阶段的核心通信机制。理解 Protocol 的安装、查找和使用是模块开发的关键。

> **设计背景 — Protocol 的设计模式**：Protocol 实现了一种"发布-订阅"的松耦合模式。生产者驱动安装 Protocol，消费者驱动通过 GUID 查找 Protocol。双方不需要知道对方的存在，也不需要知道对方的具体实现。这种设计让 UEFI 的驱动模型具有极高的可扩展性——添加新功能只需要安装新的 Protocol，不需要修改已有代码。

### 2.1 定义自定义 Protocol

**MyProtocol.h**：

```c
#ifndef __MY_PROTOCOL_H__
#define __MY_PROTOCOL_H__

#define MY_PROTOCOL_GUID \
  { 0xABCD1234, 0x5678, 0x9ABC, { 0xDE, 0xF0, 0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC } }

typedef struct _MY_PROTOCOL MY_PROTOCOL;

struct _MY_PROTOCOL {
  UINT64    Version;
  EFI_STATUS (EFIAPI *GetData) (
    IN     MY_PROTOCOL  *This,
    IN     UINTN        Index,
    OUT    UINT32       *Value
    );
  EFI_STATUS (EFIAPI *SetData) (
    IN     MY_PROTOCOL  *This,
    IN     UINTN        Index,
    IN     UINT32       Value
    );
};

extern EFI_GUID gMyProtocolGuid;

#endif
```

**在 DEC 文件中声明 GUID**：

```ini
[Protocols]
  gMyProtocolGuid = { 0xABCD1234, 0x5678, 0x9ABC, { 0xDE, 0xF0, 0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC } }
```

> **Protocol 结构体的设计惯例**：第一个成员通常是 `This` 指针（类似 C++ 的 `this`），让成员函数可以回溯到 Protocol 实例。`Version` 字段允许消费者检查 Protocol 版本，实现向前兼容。所有成员函数都使用 `EFIAPI` 调用约定。

### 2.2 安装 Protocol

```c
#include "MyProtocol.h"

STATIC MY_PROTOCOL  mMyProtocol = {
  .Version  = 1,
  .GetData  = MyProtocolGetData,
  .SetData  = MyProtocolSetData,
};

EFI_STATUS
EFIAPI
MyDriverEntryPoint (
  IN EFI_HANDLE        ImageHandle,
  IN EFI_SYSTEM_TABLE  *SystemTable
  )
{
  EFI_STATUS  Status;

  Status = gBS->InstallProtocolInterface (
                  &ImageHandle,
                  &gMyProtocolGuid,
                  EFI_NATIVE_INTERFACE,
                  &mMyProtocol
                  );
  if (EFI_ERROR (Status)) {
    DEBUG ((DEBUG_ERROR, "MyDriver: Failed to install protocol - %r\n", Status));
    return Status;
  }

  return EFI_SUCCESS;
}
```

### 2.3 使用 Protocol

```c
EFI_STATUS
SomeOtherFunction (VOID)
{
  EFI_STATUS    Status;
  MY_PROTOCOL   *MyProto;

  Status = gBS->LocateProtocol (
                  &gMyProtocolGuid,
                  NULL,
                  (VOID **)&MyProto
                  );
  if (EFI_ERROR (Status)) {
    DEBUG ((DEBUG_ERROR, "MyProtocol not found - %r\n", Status));
    return Status;
  }

  UINT32 Value;
  Status = MyProto->GetData (MyProto, 0, &Value);
  return Status;
}
```

> **LocateProtocol vs HandleProtocol**：`LocateProtocol` 在全局范围内查找第一个匹配的 Protocol 实例，适用于"单例"Protocol。`HandleProtocol` 在指定 Handle 上查找 Protocol，适用于"多实例"Protocol（如多个磁盘设备各自安装 BlockIo Protocol）。如果你需要遍历所有安装了某个 Protocol 的 Handle，使用 `LocateHandle`。

### 2.4 Protocol 通知

当某个 Protocol 安装时自动收到通知，这是驱动间解耦的关键机制：

```c
STATIC EFI_EVENT  mProtocolRegistrationEvent;
STATIC VOID       *mProtocolRegistration;

VOID
EFIAPI
MyProtocolCallback (
  IN EFI_EVENT  Event,
  IN VOID       *Context
  )
{
  MY_PROTOCOL  *MyProto;
  EFI_STATUS   Status;

  Status = gBS->LocateProtocol (
                  &gMyProtocolGuid,
                  mProtocolRegistration,
                  (VOID **)&MyProto
                  );
  if (!EFI_ERROR (Status)) {
    DEBUG ((DEBUG_INFO, "MyProtocol installed, version=%lu\n", MyProto->Version));
  }
}

EFI_STATUS
EFIAPI
MyDriverEntryPoint (
  IN EFI_HANDLE        ImageHandle,
  IN EFI_SYSTEM_TABLE  *SystemTable
  )
{
  gBS->CreateEvent (
         EVT_NOTIFY_SIGNAL,
         TPL_CALLBACK,
         MyProtocolCallback,
         NULL,
         &mProtocolRegistrationEvent
         );

  gBS->RegisterProtocolNotify (
         &gMyProtocolGuid,
         mProtocolRegistrationEvent,
         &mProtocolRegistration
         );

  return EFI_SUCCESS;
}
```

> **设计背景 — Protocol 通知解决"先有鸡还是先有蛋"问题**：驱动 A 依赖驱动 B 安装的 Protocol，但 A 可能先于 B 被调度。DEPEX 可以确保 B 在 A 之前调度，但如果 A 需要在 B 的 Protocol 安装时立即执行某些操作（而非等到自己的入口点执行），Protocol 通知就是更好的选择。它让 A 可以在入口点注册回调，当 B 的 Protocol 安装时自动触发，实现真正的松耦合。

### 2.5 架构协议

架构协议是 DXE Core 等待的关键 Protocol，只有所有架构协议就绪后才会调用 BDS：

| 协议 | 提供者 | 用途 |
|------|--------|------|
| `EFI_SECURITY_ARCH_PROTOCOL` | SecurityPkg | 安全验证 |
| `EFI_SECURITY2_ARCH_PROTOCOL` | SecurityPkg | 安全验证（文件路径版） |
| `EFI_CPU_ARCH_PROTOCOL` | UefiCpuPkg | CPU 操作 |
| `EFI_METRONOME_ARCH_PROTOCOL` | 平台 | 精确延时 |
| `EFI_TIMER_ARCH_PROTOCOL` | 平台 | 定时器 |
| `EFI_BDS_ARCH_PROTOCOL` | MdeModulePkg | 启动设备选择 |
| `EFI_WATCHDOG_TIMER_ARCH_PROTOCOL` | 平台 | 看门狗 |

> **设计背景 — 为什么需要架构协议？** DXE Core 本身是架构无关的，它不知道如何操作 CPU、定时器或安全策略。架构协议是平台必须提供的"最低服务集"——DXE Core 通过 Protocol 接口使用这些服务，而不依赖具体实现。只有所有架构协议都安装后，DXE Core 才认为系统已经具备基本运行能力，可以进入 BDS 阶段。这是一种优雅的"依赖注入"模式。

## 3. 事件与定时器

### 3.1 事件类型

| 类型 | 触发方式 | 用途 |
|------|----------|------|
| `EVT_TIMER` | 定时器到期 | 周期性任务 |
| `EVT_NOTIFY_SIGNAL` | 手动 SignalEvent | Protocol 通知 |
| `EVT_NOTIFY_WAIT` | WaitForEvent 时触发 | 等待条件 |
| `EVT_SIGNAL_EXIT_BOOT_SERVICES` | ExitBootServices 时 | 清理资源 |
| `EVT_SIGNAL_VIRTUAL_ADDRESS_CHANGE` | SetVirtualAddressMap 时 | Runtime 地址转换 |

### 3.2 TPL（任务优先级）

TPL 是 UEFI 的事件优先级机制，类似中断优先级：

| TPL | 名称 | 用途 |
|-----|------|------|
| 0 | TPL_APPLICATION | 应用程序级别 |
| 4 | TPL_CALLBACK | 大多数驱动回调 |
| 8 | TPL_NOTIFY | 高优先级通知（如定时器） |
| 16 | TPL_HIGH_LEVEL | 最高优先级（不可抢占） |

> **设计背景 — 为什么 UEFI 使用 TPL 而非传统中断？** UEFI 运行在单核、协作式调度环境中，没有传统 OS 的中断和线程概念。TPL 是一种"软件中断屏蔽"机制：当执行在 TPL_CALLBACK 时，只有 TPL_NOTIFY 和 TPL_HIGH_LEVEL 的事件可以抢占。这比硬件中断简单得多，但足以保证固件阶段的并发安全。注意：`RaiseTPL` 只能提升不能降低，且必须在同一函数内恢复。

**规则**：
- TPL_APPLICATION 可以被任何事件抢占
- TPL_CALLBACK 适合大多数驱动操作
- TPL_NOTIFY 用于时间敏感的操作
- TPL_HIGH_LEVEL 用于临界区（此时中断被禁用）

```c
// 提升和恢复 TPL
EFI_TPL  OldTpl;

OldTpl = gBS->RaiseTPL (TPL_HIGH_LEVEL);
// 临界区操作
gBS->RestoreTPL (OldTpl);
```

### 3.3 ExitBootServices 事件

OS Loader 调用 `ExitBootServices()` 时，固件需要释放所有 Boot Services 资源。驱动应该注册此事件来清理：

```c
STATIC EFI_EVENT  mExitBootServicesEvent;

VOID
EFIAPI
OnExitBootServices (
  IN EFI_EVENT  Event,
  IN VOID       *Context
  )
{
  // 停止 DMA
  // 释放 Boot Services 内存
  // 将设备置于 OS 可接管的已知状态
}

// 在 EntryPoint 中注册
gBS->CreateEventEx (
       EVT_NOTIFY_SIGNAL,
       TPL_NOTIFY,
       OnExitBootServices,
       NULL,
       &gEfiEventExitBootServicesGuid,
       &mExitBootServicesEvent
       );
```

> **设计背景 — ExitBootServices 清理的重要性**：OS 内核启动后，Boot Services 的内存和事件系统不再可用。如果驱动不清理资源（如正在进行的 DMA 传输、未释放的中断等），OS 可能会踩到这些"幽灵"资源导致崩溃。`EVT_SIGNAL_EXIT_BOOT_SERVICES` 事件是驱动向 OS 交接控制权的最后机会。一个常见的错误是忘记停止 DMA——OS 启动后 DMA 仍在写入已被 OS 使用的内存区域。

## 4. PEIM 开发

### 4.1 PEIM 的特点

PEIM 是 PEI 阶段的模块，与 DXE 驱动有显著区别：

| 特性 | DXE Driver | PEIM |
|------|------------|------|
| 内存 | 充足的永久内存 | 早期只有临时 RAM |
| 通信 | Protocol | PPI |
| 调试 | DebugLib + ConOut | DebugLib + Serial |
| 服务 | Boot Services | PEI Services |
| 库绑定 | DSC [LibraryClasses.common.DXE_DRIVER] | DSC [LibraryClasses.common.PEIM] |

> **设计背景 — PEIM 的"极简主义"**：PEI 阶段的内存极其有限（可能只有 32-64KB 的 CAR/临时 RAM），PEIM 必须遵循"极简主义"原则：避免大数组、避免递归、避免动态内存分配（除非必要）。PEIM 的主要任务是初始化硬件和收集信息，真正的业务逻辑应该留到 DXE 阶段实现。

### 4.2 PEIM 示例

**MyPeim.inf**：

```ini
[Defines]
  INF_VERSION    = 0x00010005
  BASE_NAME      = MyPeim
  FILE_GUID      = 22345678-1234-1234-1234-123456789ABC
  MODULE_TYPE    = PEIM
  VERSION_STRING = 1.0
  ENTRY_POINT    = MyPeimEntryPoint

[Sources]
  MyPeim.c

[Packages]
  MdePkg/MdePkg.dec

[LibraryClasses]
  PeimEntryPoint
  PeiServicesLib
  DebugLib
  HobLib

[Ppis]
  gEfiPeiMemoryDiscoveredPpiGuid  ## CONSUMES

[Depex]
  gEfiPeiMemoryDiscoveredPpiGuid
```

**MyPeim.c**：

```c
#include <PiPei.h>
#include <Library/PeimEntryPoint.h>
#include <Library/PeiServicesLib.h>
#include <Library/DebugLib.h>
#include <Library/HobLib.h>

EFI_STATUS
EFIAPI
MyPeimEntryPoint (
  IN       EFI_PEI_FILE_HANDLE  FileHandle,
  IN CONST EFI_PEI_SERVICES     **PeiServices
  )
{
  DEBUG ((DEBUG_INFO, "MyPeim: Hello from PEI!\n"));

  BuildGuidDataHob (
    &gMyGuid,
    &MyData,
    sizeof (MyData)
    );

  return EFI_SUCCESS;
}
```

### 4.3 PPI 通知

PEI 阶段也有通知机制，类似 DXE 的 Protocol 通知：

```c
STATIC EFI_PEI_NOTIFY_DESCRIPTOR  mMyPpiNotifyList[] = {
  {
    (EFI_PEI_PPI_DESCRIPTOR_NOTIFY_DISPATCH | EFI_PEI_PPI_DESCRIPTOR_TERMINATE_LIST),
    &gEfiPeiMemoryDiscoveredPpiGuid,
    MyPpiCallback
  }
};

EFI_STATUS
EFIAPI
MyPpiCallback (
  IN EFI_PEI_SERVICES           **PeiServices,
  IN EFI_PEI_NOTIFY_DESCRIPTOR  *NotifyDescriptor,
  IN VOID                       *Ppi
  )
{
  DEBUG ((DEBUG_INFO, "Memory discovered!\n"));
  return EFI_SUCCESS;
}

// 在 EntryPoint 中注册
PeiServicesNotifyPpi (mMyPpiNotifyList);
```

> **PPI 通知的两种模式**：`EFI_PEI_PPI_DESCRIPTOR_NOTIFY_DISPATCH` 表示"调度通知"——PEI Dispatcher 在每个 PEIM 调度间隙检查是否有新的 PPI 通知需要触发。`EFI_PEI_PPI_DESCRIPTOR_NOTIFY_SWAP` 表示"交换通知"——在 PPI 被重新安装时触发（类似 DXE 的 `ReInstallProtocol`）。大多数场景使用 DISPATCH 模式。

## 5. Library 开发

### 5.1 设计 Library Class

**第一步：在 DEC 文件中声明库类**

```ini
[LibraryClasses]
  MyPlatformLib|Include/Library/MyPlatformLib.h
```

**第二步：定义头文件**

```c
// Include/Library/MyPlatformLib.h
#ifndef __MY_PLATFORM_LIB_H__
#define __MY_PLATFORM_LIB_H__

EFI_STATUS
EFIAPI
MyPlatformGetCpuFreq (
  OUT UINT64  *Frequency
  );

UINTN
EFIAPI
MyPlatformGetCpuCount (VOID);

#endif
```

### 5.2 实现 Library Instance

**MyPlatformLibDxe.inf**（DXE 版本）：

```ini
[Defines]
  BASE_NAME       = MyPlatformLibDxe
  MODULE_TYPE     = DXE_DRIVER
  LIBRARY_CLASS   = MyPlatformLib|DXE_DRIVER DXE_RUNTIME_DRIVER UEFI_DRIVER

[Sources]
  MyPlatformLibDxe.c

[Packages]
  MdePkg/MdePkg.dec
  MyPkg/MyPkg.dec

[LibraryClasses]
  UefiBootServicesTableLib
  DebugLib
```

**MyPlatformLibDxe.c**：

```c
#include <Base.h>
#include <Library/MyPlatformLib.h>
#include <Library/UefiBootServicesTableLib.h>
#include <Library/DebugLib.h>

EFI_STATUS
EFIAPI
MyPlatformGetCpuFreq (
  OUT UINT64  *Frequency
  )
{
  *Frequency = 1000000000ULL;
  return EFI_SUCCESS;
}

UINTN
EFIAPI
MyPlatformGetCpuCount (VOID)
{
  return 4;
}
```

### 5.3 在 DSC 中绑定

```ini
[LibraryClasses.common.DXE_DRIVER]
  MyPlatformLib|MyPkg/Library/MyPlatformLibDxe/MyPlatformLibDxe.inf

[LibraryClasses.common.PEIM]
  MyPlatformLib|MyPkg/Library/MyPlatformLibPei/MyPlatformLibPei.inf
```

**关键点**：`LIBRARY_CLASS = MyPlatformLib|DXE_DRIVER DXE_RUNTIME_DRIVER UEFI_DRIVER` 中的竖线后面声明了此库实例可用于哪些模块类型。

> **设计背景 — LIBRARY_CLASS 的模块类型限制**：为什么需要声明库实例可用于哪些模块类型？因为不同模块类型有不同的可用服务。例如，PEIM 不能使用 `gBS`（Boot Services），所以链接了 `UefiBootServicesTableLib` 的库实例不能用于 PEIM。`LIBRARY_CLASS` 的模块类型限制让构建系统在绑定时就能检测到不兼容的组合，而不是在运行时崩溃。

## 6. UEFI 应用程序开发

UEFI 应用程序是最容易上手的模块类型，不需要安装 Protocol 就能运行。

### 6.1 Hello World 应用

**HelloWorld.inf**：

```ini
[Defines]
  INF_VERSION    = 0x00010005
  BASE_NAME      = HelloWorld
  FILE_GUID      = 32345678-1234-1234-1234-123456789ABC
  MODULE_TYPE    = UEFI_APPLICATION
  VERSION_STRING = 1.0
  ENTRY_POINT    = UefiMain

[Sources]
  HelloWorld.c

[Packages]
  MdePkg/MdePkg.dec

[LibraryClasses]
  UefiApplicationEntryPoint
  UefiLib
```

**HelloWorld.c**：

```c
#include <Uefi.h>
#include <Library/UefiLib.h>

EFI_STATUS
EFIAPI
UefiMain (
  IN EFI_HANDLE        ImageHandle,
  IN EFI_SYSTEM_TABLE  *SystemTable
  )
{
  Print (L"Hello, UEFI World!\n");
  Print (L"Firmware Vendor: %s\n", gST->FirmwareVendor);
  Print (L"Firmware Revision: 0x%x\n", gST->FirmwareRevision);
  return EFI_SUCCESS;
}
```

> **注意**：UEFI 的 `Print` 函数使用 UCS-2 宽字符串，字符串字面量必须加 `L` 前缀（如 `L"Hello"`）。`%s` 格式化说明符对应 UCS-2 字符串（`CHAR16*`），`%a` 对应 ASCII 字符串（`CHAR8*`）。这是初学者最常犯的错误之一。

### 6.2 在 UEFI Shell 中运行

```bash
# 构建后，.efi 文件位于
# Build/<Platform>/DEBUG_GCC5/MdeModulePkg/Application/HelloWorld/HelloWorld/OUTPUT/HelloWorld.efi

# 在 UEFI Shell 中
fs0:
HelloWorld.efi
```

## 7. 调试技巧

### 7.1 DebugLib 使用

```c
// 调试级别（从低到高）
DEBUG_INIT      0x00000001
DEBUG_WARN      0x00000002
DEBUG_LOAD      0x00000004
DEBUG_FS        0x00000008
DEBUG_POOL      0x00000010
DEBUG_PAGE      0x00000020
DEBUG_INFO      0x00000040   ← 最常用
DEBUG_DISPATCH  0x00000080
DEBUG_VARIABLE  0x00000100
DEBUG_BM        0x00000400
DEBUG_BLKIO     0x00001000
DEBUG_NET       0x00004000
DEBUG_VERBOSE   0x00400000
DEBUG_ERROR     0x80000000   ← 错误信息

// 使用
DEBUG ((DEBUG_INFO, "MyDriver: Value = 0x%x\n", Value));
DEBUG ((DEBUG_ERROR, "MyDriver: Failed to allocate memory!\n"));

// 条件断言
ASSERT (Value != NULL);
ASSERT_EFI_ERROR (Status);
```

> **设计背景 — DEBUG 宏的编译时优化**：`DEBUG` 宏在 RELEASE 构建中会被编译器完全消除（零开销），在 DEBUG 构建中才输出日志。这是通过 `PcdDebugPrintErrorLevel` PCD 和编译器优化实现的——如果 `DEBUG_INFO` 不在 `PcdDebugPrintErrorLevel` 掩码中，编译器可以证明条件永远为假，从而消除整个 `DEBUG` 调用。`ASSERT` 在 RELEASE 构建中同样被消除。

### 7.2 PCD 控制调试级别

```ini
# 在 DSC 文件中设置调试输出级别
[PcdsFixedAtBuild]
  gEfiMdePkgTokenSpaceGuid.PcdDebugPrintErrorLevel|0x80000047
  # 0x80000047 = DEBUG_ERROR | DEBUG_WARN | DEBUG_LOAD | DEBUG_INFO | DEBUG_INIT
```

### 7.3 使用 GDB 调试

```bash
# 1. 构建 DEBUG 目标
build -p <DSC> -a RISCV64 -b DEBUG -t GCC5

# 2. 启动 QEMU（带 GDB 等待）
qemu-system-riscv64 -machine virt -m 2048 \
    -bios default \
    -pflash <CODE.fd> -pflash <VARS.fd> \
    -nographic -s -S

# 3. GDB 连接
riscv64-unknown-elf-gdb
(gdb) set architecture riscv:rv64
(gdb) target remote :1234

# 4. 设置断点
(gdb) break DxeMain
(gdb) break BdsEntry
(gdb) continue
```

### 7.4 日志输出

在 QEMU 中，`DEBUG` 宏的输出会通过串口输出：

```bash
# 将串口输出重定向到文件
qemu-system-riscv64 ... -serial file:uefi.log

# 或使用 stdio 模式
qemu-system-riscv64 ... -nographic  # 串口输出到终端
```

## 8. 编码规范要点

### 8.1 命名约定

| 类型 | 规范 | 示例 |
|------|------|------|
| 函数 | PascalCase | `InitializePlatform` |
| 宏 | UPPER_SNAKE_CASE | `MAX_BUFFER_SIZE` |
| 全局变量 | m 前缀 + PascalCase | `mDriverHandle` |
| 局部变量 | 小写 + 下划线 | `buffer_size` |
| 结构体类型 | _ 前缀 + PascalCase | `_MY_DRIVER_CONTEXT` |
| Protocol/PPI | g 前缀 | `gMyProtocolGuid` |
| PCD | Pcd 前缀 | `PcdMaxVariableSize` |

> **设计背景 — 命名前缀的意图**：`m` 前缀（member）标识模块级全局变量，`g` 前缀（global）标识跨模块共享的全局符号（如 GUID）。这种命名约定让代码审查者一眼就能区分变量的作用域，避免意外修改模块级状态。EDK2 的 ECC 工具会强制检查这些命名约定。

### 8.2 关键规则

1. **所有公共函数必须使用 EFIAPI**
2. **参数必须使用 IN/OUT/IN OUT/OPTIONAL 修饰**
3. **所有函数必须返回 EFI_STATUS**（除了 VOID 返回的函数）
4. **禁止使用 C 标准库函数**（使用 EDK2 的 BaseLib/BaseMemoryLib 替代）
5. **禁止使用浮点运算**（UEFI 环境不保证 FPU 可用）
6. **禁止使用全局变量初始化**（除 CONST 变量外，因为 .data 段可能不可写）
7. **Runtime Driver 必须正确处理虚拟地址转换**

> **设计背景 — 为什么禁止全局变量初始化？** UEFI 模块从 Flash 加载执行，Flash 是只读的。非 CONST 的初始化全局变量存储在 `.data` 段，需要加载器将其复制到可写内存并应用重定位。但在固件环境中（尤其是 PEI 阶段），可能没有可写内存来存放 `.data` 段。因此，EDK2 编码规范要求全局变量要么是 `CONST`（放在 `.rodata`/`.rdata`），要么在运行时赋值。`STATIC` 变量如果不加 `CONST`，也必须在代码中显式初始化（通常初始化为 0，由 `.bss` 段处理）。

### 8.3 禁止使用的 C 标准库函数及替代

| 禁止 | 替代 |
|------|------|
| `memcpy` | `CopyMem` |
| `memset` | `SetMem` / `ZeroMem` |
| `memcmp` | `CompareMem` |
| `strlen` | `StrLen` / `AsciiStrLen` |
| `strcpy` | `StrCpyS` / `AsciiStrCpyS` |
| `printf` | `Print` / `DEBUG` |
| `malloc`/`free` | `gBS->AllocatePool`/`FreePool` |
| `atoi`/`strtol` | `StrDecimalToUintn` / `StrHexToUintn` |

> **设计背景 — 为什么禁止 C 标准库？** UEFI 环境没有 OS 提供的 C 运行时（CRT）。`malloc` 需要 OS 的堆管理器，`printf` 需要 OS 的文件描述符，`strlen` 在 UEFI 中有 UCS-2 版本（`StrLen`）和 ASCII 版本（`AsciiStrLen`）。EDK2 通过 BaseLib 和 BaseMemoryLib 提供了精简的替代实现，这些库直接操作硬件，不依赖任何 OS 服务。

---

**上一篇**：[02-build-system.md](02-build-system.md) — 构建系统深入
**下一篇**：[04-riscv-platform.md](04-riscv-platform.md) — RISC-V 与平台移植
