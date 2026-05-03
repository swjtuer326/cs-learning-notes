# EDK2 模块开发实战

> 前面学完了类型系统和构建流程。这一篇回到工程实践：写一个真正的 DXE 驱动，安装 Protocol，处理事件，以及理解 TPL 优先级系统。

### 关键术语

| 缩写 | 全称 | 含义 |
|------|------|------|
| DEPEX | Dependency Expression | 依赖表达式，编译为字节码由 DXE Dispatcher 执行 |
| TPL | Task Priority Level | 任务优先级级别，UEFI 的软件中断嵌套机制 |

> Protocol、PPI、HOB、PCD、INF/DSC/FDF 等术语已在 [00-全景地图](./00-overview.md) 和 [02-类型系统](./02-type-system.md) 中定义。

---

## 1. 前置知识

| 需要了解 | 参考文档 |
|----------|----------|
| EDK2 类型系统与编码规范 | [02-类型系统与编码规范](./02-type-system.md) |
| 启动流程与各阶段职责 | [03-启动流程详解](./03-boot-flow.md) |
| DSC/INF/FDF 元数据文件格式 | [04-构建系统深入](./04-build-system.md) |

---

## 2. 模块开发的"世界观"

在 EDK2 中写代码和写普通 C 程序有三点根本区别：

1. **没有 `main()`**——每种模块类型有不同的入口函数签名（`UefiMain`、`MyDriverEntryPoint` 等），由 INF 的 `MODULE_TYPE` 决定链接哪个入口点库。
2. **不能调 C 标准库**——没有 `printf`/`malloc`/`memcpy`，用 `DEBUG`/`AllocatePool`/`CopyMem` 替代（详见 [02-类型系统](./02-type-system.md) §8.3）。
3. **模块间通信通过 GUID 驱动**——Protocol 和 PPI 是唯一的信息交换机制，双方只通过 GUID 互相发现。

下面按"写一个驱动→安装 Protocol→订阅事件→写 PEIM→写 Library"这条线展开。

---

## 3. DXE 驱动开发

### 3.1 最简驱动：Hello World

一个 DXE 驱动最少 3 个文件：

```
MyDriver/
├── MyDriver.c      # 源码
├── MyDriver.h      # 头文件（推荐）
└── MyDriver.inf    # 模块定义
```

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

> `UefiDriverEntryPoint` 库负责在 Dispatcher 加载驱动后调用你的 `MyDriverEntryPoint`。入口点库的完整映射表见 [04-构建系统](./04-build-system.md) §5.3。

**注册到平台**：
- DSC 的 `[Components]` 添加 `MyPkg/MyDriver/MyDriver.inf`
- FDF 的 `[FV.*]` 添加 `INF MyPkg/MyDriver/MyDriver.inf`

两者缺一不可——DSC 决定编译，FDF 决定打包进 Flash（[04-构建系统](./04-build-system.md) §5.4）。

### 3.2 入口函数的两个参数

入口函数拿到两个关键参数：

| 参数 | 核心用途 |
|------|----------|
| `ImageHandle` | 当前驱动自己的 Handle。安装 Protocol 时通常用此处传入（也可建新的 Handle） |
| `SystemTable` | 全局 `gST` 指针——包含 Boot Services (`gBS`)、Runtime Services、GUID 表 |

全局宏（由 `UefiBootServicesTableLib` 库提供）：

```c
gST   // = SystemTable（由入口点库初始化）
gBS   // = SystemTable->BootServices  (UefiBootServicesTableLib)
gRT   // = SystemTable->RuntimeServices (UefiRuntimeServicesTableLib)
```

---

## 4. Protocol 开发

Protocol 是 DXE 阶段的核心通信机制——生产者安装 Protocol，消费者通过 GUID 查找。双方零耦合。

### 4.1 定义自定义 Protocol

**MyProtocol.h**（定义 GUID + 方法签名）：

```c
#ifndef __MY_PROTOCOL_H__
#define __MY_PROTOCOL_H__

#define MY_PROTOCOL_GUID \
  { 0xABCD1234, 0x5678, 0x9ABC, { 0xDE, 0xF0, 0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC } }

typedef struct _MY_PROTOCOL MY_PROTOCOL;

struct _MY_PROTOCOL {
  UINT64    Version;                          // 1 = 可版本检查
  EFI_STATUS (EFIAPI *GetData) (             // 2 = 方法签名，返回 EFI_STATUS + EFIAPI
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
```

在 DEC 文件中声明 GUID：

```ini
[Protocols]
  gMyProtocolGuid = { 0xABCD1234, 0x5678, 0x9ABC, { ... }}
```

### 4.2 安装 Protocol

```c
// ---- 回调实现 ----
EFI_STATUS EFIAPI MyProtocolGetData (
  IN     MY_PROTOCOL  *This, IN UINTN Index, OUT UINT32 *Value)
{
  *Value = mInternalData[Index];  // 从内部数组读数据
  return EFI_SUCCESS;
}

EFI_STATUS EFIAPI MyProtocolSetData (
  IN     MY_PROTOCOL  *This, IN UINTN Index, IN UINT32 Value)
{
  mInternalData[Index] = Value;
  return EFI_SUCCESS;
}

// ---- 组装 Protocol 实例 ----
STATIC UINT32       mInternalData[64];

STATIC MY_PROTOCOL  mMyProtocol = {
  .Version  = 1,
  .GetData  = MyProtocolGetData,
  .SetData  = MyProtocolSetData,
};

// ---- 入口函数中安装 ----
EFI_STATUS EFIAPI MyDriverEntryPoint (
  IN EFI_HANDLE ImageHandle, IN EFI_SYSTEM_TABLE *SystemTable)
{
  EFI_STATUS Status;

  Status = gBS->InstallProtocolInterface (
                  &ImageHandle,          // 安装到当前驱动的 Handle
                  &gMyProtocolGuid,      // GUID
                  EFI_NATIVE_INTERFACE,  // 接口类型
                  &mMyProtocol           // 实例指针
                  );
  if (EFI_ERROR (Status)) {
    return Status;
  }
  return EFI_SUCCESS;
}
```

安装完成后，其他任何驱动都可以通过 GUID 找到这个 Protocol。

### 4.3 查找和使用 Protocol

按范围从大到小，有三种查找方式：

| API | 查找范围 | 适用场景 |
|-----|---------|---------|
| `LocateProtocol(&Guid, NULL, &Ptr)` | 全局第一个匹配实例 | 单例 Protocol（如 CPU Arch Protocol） |
| `LocateHandleBuffer(ByProtocol, &Guid, NULL, &Count, &Handles)` | 找到所有安装了此 Protocol 的 Handle 列表 | 多实例 Protocol（如每个磁盘安一个 BlockIo Protocol） |
| `HandleProtocol(Handle, &Guid, &Ptr)` | 指定 Handle 上的实例 | 已知 Handle 时使用 |

消费者示例：

```c
MY_PROTOCOL  *MyProto;
Status = gBS->LocateProtocol (&gMyProtocolGuid, NULL, (VOID**)&MyProto);
if (!EFI_ERROR (Status)) {
    UINT32 Value;
    MyProto->GetData (MyProto, 0, &Value);
}
```

### 4.4 Protocol 通知回调

依赖驱动 A 可能先于 提供驱动 B 被加载。Protocol 通知回调解决了这个时序问题——在 Protocol 安装的瞬间触发回调。

```c
// 模块级变量（在入口函数中初始化）
STATIC VOID      *mRegistration;  // 用于后续 LocateProtocol 的注册句柄
STATIC EFI_EVENT  mEvent;         // 事件对象，退注册用

// 入口函数中注册通知
EfiCreateProtocolNotifyEvent (
  &gMyProtocolGuid,          // 等待的 GUID
  TPL_CALLBACK,              // 回调时的 TPL
  MyProtocolCallback,        // void EFIAPI (*)(Event, Context)
  NULL,                      // Context
  &mRegistration,            // → void*: 用于 LocateProtocol
  &mEvent                    // → 退注册用
);

VOID EFIAPI MyProtocolCallback (IN EFI_EVENT Event, IN VOID *Context) {
    MY_PROTOCOL *MyProto;
    Status = gBS->LocateProtocol (&gMyProtocolGuid, mRegistration, (VOID**)&MyProto);
    if (!EFI_ERROR (Status)) {
        // MyProto 刚刚被安装，现在可用
    }
}
```

---

## 5. 事件与 TPL

### 5.1 事件类型

事件是 UEFI 的通知机制——等待条件为真时执行回调：

| 事件类型 | 触发条件 |
|----------|----------|
| `EVT_TIMER` | 定时器到期 |
| `EVT_NOTIFY_SIGNAL` | 手动 `SignalEvent()` 或 Protocol 通知时 |
| `EVT_NOTIFY_WAIT` | `WaitForEvent()` 返回时 |
| `EVT_SIGNAL_EXIT_BOOT_SERVICES` | OS Loader 调用 `ExitBootServices()` 时 |
| `EVT_SIGNAL_VIRTUAL_ADDRESS_CHANGE` | Runtime 驱动地址转换时 |

### 5.2 TPL（任务优先级）

UEFI 的运行环境是单线程协作调度的，不像 Linux 有内核线程和中断。TPL 是 UEFI 版的"中断优先级"——高 TPL 可以抢占低 TPL。

| TPL | 名称 | 典型场景 |
|------|------|----------|
| 0 | TPL_APPLICATION | 普通代码执行 |
| 4 | TPL_CALLBACK | 大多数驱动回调 |
| 8 | TPL_NOTIFY | 定时器、高优先级通知 |
| 16 | TPL_HIGH_LEVEL | 临界区（此时中断被完全禁用） |

核心规则：**执行在 TPL = N 时，只有 TPL > N 的事件回调可以抢占你**。

提升 TPL 来保护临界区：

```c
EFI_TPL  OldTpl;
OldTpl = gBS->RaiseTPL (TPL_HIGH_LEVEL);
// ↑ 临界区：任何 TPL ≤ 15 的事件都不会抢占此代码
gBS->RestoreTPL (OldTpl);
```

> TPL 只能提升不能降低，且必须在同一函数内恢复。`RaiseTPL` 不匹配 `RestoreTPL` 会导致事件处理永久阻塞。

### 5.3 ExitBootServices 清理

OS Loader 调用 `ExitBootServices()` 意味着固件向 OS 交接控制权。此后所有 Boot Services 失效。驱动需要在此事件中清理资源：

```c
STATIC EFI_EVENT  mEbsEvent;  // 模块级变量

// 入口函数中注册
EfiCreateEventEx (EVT_NOTIFY_SIGNAL, TPL_NOTIFY,
                  OnExitBootServices, NULL,
                  &gEfiEventExitBootServicesGuid, &mEbsEvent);

VOID EFIAPI OnExitBootServices (IN EFI_EVENT Event, IN VOID *Context) {
    // 1. 停止所有正在进行的 DMA
    // 2. 将设备置于 OS 可接收的已知状态
    // 3. 释放 Boot Services 内存
}
```

DMA 忘记停止而 OS 启动后继续写入 OS 内存区域，是 UEFI 到 OS 交接阶段最隐蔽的崩溃原因。

---

## 6. PEIM 开发

PEIM 运行在 PEI 阶段，与 DXE 驱动的核心区别：

| | DXE Driver | PEIM |
|---|------------|------|
| 内存 | 充足的 DDR（GB 级） | 早期只有 CAR/临时 RAM（KB 级） |
| 模块间通信 | Protocol（GUID + 多实例 + 引用计数） | PPI（GUID + 单实例 + 无引用计数） |
| 使用的调试输出 | DebugLib（任意后端） | DebugLib + 串口（SBI console 或 UART） |
| 库绑定段 | `[LibraryClasses.common.DXE_DRIVER]` | `[LibraryClasses.common.PEIM]` |

PEIM 必须遵循"极简主义"——避免大数组、递归、不必要的动态分配。收集信息即可，业务逻辑留到 DXE 阶段。

### 6.1 示例 PEIM

**MyPeim.inf**：

```ini
[Defines]
  INF_VERSION    = 0x00010005
  BASE_NAME      = MyPeim
  FILE_GUID      = 22345678-1234-1234-1234-123456789ABC
  MODULE_TYPE    = PEIM
  ENTRY_POINT    = MyPeimEntryPoint

[Sources]   MyPeim.c
[Packages]  MdePkg/MdePkg.dec
[LibraryClasses]
  PeimEntryPoint
  PeiServicesLib
  DebugLib
  HobLib

[Ppis]
  gEfiPeiMemoryDiscoveredPpiGuid  ## CONSUMES   # 等内存可用

[Depex]
  gEfiPeiMemoryDiscoveredPpiGuid                 # 条件: 内存可用后才被调度
```

**MyPeim.c**：

```c
#include <PiPei.h>
#include <Library/DebugLib.h>
#include <Library/HobLib.h>

// 需要传递给 DXE 的数据（GUID 和结构体均已在 DEC 中声明）
EFI_GUID  gMyGuid = { 0xAABBCCDD, ... };  // 在 DEC 的 [Guids] 段中定义

STATIC struct { UINT32 Foo; UINT32 Bar; } MyData = { .Foo = 42, .Bar = 7 };

EFI_STATUS EFIAPI
MyPeimEntryPoint (
  IN       EFI_PEI_FILE_HANDLE  FileHandle,
  IN CONST EFI_PEI_SERVICES     **PeiServices
  )
{
  DEBUG ((DEBUG_INFO, "MyPeim: Hello from PEI!\n"));

  // 构建 HOB——将 MyData 的副本传到 DXE 阶段
  BuildGuidDataHob (&gMyGuid, &MyData, sizeof (MyData));

  return EFI_SUCCESS;
}
```

### 6.2 PPI 通知

PEI 阶段的 PPI 通知有两种模式：

| 模式 | 触发时机 |
|------|----------|
| `NOTIFY_DISPATCH` | PEI Dispatcher 在每个 PEIM 调度间隙检查是否有新 PPI 就绪 |
| `NOTIFY_SWAP` | PPI 被重新安装时触发（类似 DXE 的 ReInstallProtocol） |

```c
// 回调函数：当 gEfiPeiMemoryDiscoveredPpiGuid 就绪时被 Dispatcher 调用
STATIC EFI_STATUS EFIAPI MemoryDiscoveredCallback (
  IN       EFI_PEI_SERVICES **PeiServices,
  IN       EFI_PEI_NOTIFY_DESCRIPTOR *NotifyDescriptor,
  IN       VOID *Ppi)
{
  DEBUG ((DEBUG_INFO, "MyPeim: 内存已就绪，可以创建 HOB 了\n"));
  // 此时真正的 DDR 已经可用，可以构建资源描述 HOB 了
  BuildResourceDescriptorHob (...);
  return EFI_SUCCESS;
}

// 通知列表
STATIC EFI_PEI_NOTIFY_DESCRIPTOR mPpiNotifyList[] = {
  { EFI_PEI_PPI_DESCRIPTOR_NOTIFY_DISPATCH | EFI_PEI_PPI_DESCRIPTOR_TERMINATE_LIST,
    &gEfiPeiMemoryDiscoveredPpiGuid, MemoryDiscoveredCallback }
};

// 入口函数中注册
PeiServicesNotifyPpi (mPpiNotifyList);
```

---

## 7. Library 开发

Library Class/Instance 的分离是 EDK2 多态机制的基础。理解"接口 vs 实现"这一点，才能理解为什么同一个驱动能在不同平台编译而不改代码。

### 7.1 定义 Library Class

**第一步：在 DEC 中声明地址**

```ini
[LibraryClasses]
  MyPlatformLib|Include/Library/MyPlatformLib.h
```

**第二步：接口头文件**

```c
// Include/Library/MyPlatformLib.h
#ifndef __MY_PLATFORM_LIB_H__
#define __MY_PLATFORM_LIB_H__

EFI_STATUS EFIAPI MyPlatformGetCpuFreq (OUT UINT64 *Frequency);
UINTN      EFIAPI MyPlatformGetCpuCount (VOID);
#endif
```

### 7.2 实现 Library Instance

```ini
# MyPlatformLibDxe.inf
[Defines]
  LIBRARY_CLASS = MyPlatformLib|DXE_DRIVER DXE_RUNTIME_DRIVER UEFI_DRIVER
```

`LIBRARY_CLASS = 名字|可用模块类型列表`——竖线后限制了此实例可用于哪些 Module Type。例如 PEI 专用的实例写 `MyLib|PEIM`。如果写错了模块类型，构建系统在绑定阶段就会报错（而非到运行时崩溃）。

### 7.3 在 DSC 中绑定

```ini
[LibraryClasses.common.DXE_DRIVER]
  MyPlatformLib|MyPkg/Library/MyPlatformLibDxe/MyPlatformLibDxe.inf

[LibraryClasses.common.PEIM]
  MyPlatformLib|MyPkg/Library/MyPlatformLibPei/MyPlatformLibPei.inf
```

---

## 8. UEFI 应用程序

UEFI 应用是最简单的模块类型——不安装 Protocol，在 UEFI Shell 中直接被调用运行。

```c
#include <Uefi.h>
#include <Library/UefiLib.h>

EFI_STATUS EFIAPI UefiMain (
  IN EFI_HANDLE ImageHandle,
  IN EFI_SYSTEM_TABLE *SystemTable
  )
{
  Print (L"Hello, UEFI World!\n");
  Print (L"Firmware Vendor: %s\n", gST->FirmwareVendor);
  return EFI_SUCCESS;
}
```

> `%s` = UCS-2 字符串 (`CHAR16*`)，`%a` = ASCII 字符串 (`CHAR8*`)。这是 UEFI 应用开发最常见的问题。见 [02-类型系统](./02-type-system.md) §3。

---

## 9. 调试技巧

### 9.1 DEBUG 宏

`DEBUG`、`ASSERT` 与调试级别的定义详见 [02-类型系统](./02-type-system.md) §9。

### 9.2 GDB 调试

快速 GDB 启调用和关键断点见 [01-快速上手](./01-quick-start.md) §6 和 [06-RISC-V 平台移植](./06-riscv-platform.md) §8。

### 9.3 日志输出

QEMU 运行时，`DEBUG` 宏通过串口输出：

```bash
qemu-system-riscv64 ... -nographic          # 串口 → 终端
qemu-system-riscv64 ... -serial file:uefi.log  # 串口 → 文件
```

---

## 10. 要点回顾

| 要点 | 说明 |
|------|------|
| 入口函数签名取决于 MODULE_TYPE | INF 的 `MODULE_TYPE` 决定链接哪个入口点库，不同入口点库提供不同的函数签名 |
| Protocol = GUID + 方法签名 | 生产者安装，消费者通过 GUID 查找。三种查找方式：`LocateProtocol`（单例）、`LocateHandleBuffer`（多实例）、`HandleProtocol`（指定 Handle） |
| Protocol 通知解决时序问题 | 依赖驱动通过回调获知 Protocol 安装，而不依赖 Dispatcher 的调度顺序 |
| TPL 是 UEFI 的软件中断屏蔽机制 | 高 TPL 抢占低 TPL；`RaiseTPL` 要 restore；TPL_HIGH_LEVEL 禁用中断 |
| PEIM 遵循极简主义 | CAR 只有几十 KB，避免大数组和递归。收集信息即可，业务在 DXE 做 |
| Library = DEC 声明 + INF 实现 + DSC 绑定 | 同一 Library Class 仅在 DSC 中改一行就切到不同平台实现 |

---

## 11. 编码规范

命名词典、IN/OUT 修饰符、EFIAPI、禁用 C 标准库等规范见 [02-类型系统](./02-type-system.md) §4~§8。写每个回调时参考即可。

---

**上一篇**：[04-构建系统深入](./04-build-system.md) — 元数据与 AutoGen
**下一篇**：[06-RISC-V 平台移植](./06-riscv-platform.md) — SBI、MMU 与新 SoC 移植
