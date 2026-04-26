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

## 2. Protocol 开发

Protocol 是 DXE 阶段的核心通信机制。理解 Protocol 的安装、查找和使用是模块开发的关键。

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
  Print (Firmware Revision: 0x%x\n", gST->FirmwareRevision);
  return EFI_SUCCESS;
}
```

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

### 8.2 关键规则

1. **所有公共函数必须使用 EFIAPI**
2. **参数必须使用 IN/OUT/IN OUT/OPTIONAL 修饰**
3. **所有函数必须返回 EFI_STATUS**（除了 VOID 返回的函数）
4. **禁止使用 C 标准库函数**（使用 EDK2 的 BaseLib/BaseMemoryLib 替代）
5. **禁止使用浮点运算**（UEFI 环境不保证 FPU 可用）
6. **禁止使用全局变量初始化**（除 CONST 变量外，因为 .data 段可能不可写）
7. **Runtime Driver 必须正确处理虚拟地址转换**

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

---

**上一篇**：[02-build-system.md](02-build-system.md) — 构建系统深入
**下一篇**：[04-riscv-platform.md](04-riscv-platform.md) — RISC-V 与平台移植
