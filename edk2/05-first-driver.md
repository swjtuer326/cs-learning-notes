# 05 — 写第一个 DXE 驱动

> 前面讲了一堆概念：Handle、Protocol、DriverBinding。现在回到实践中——写一个能编译、能运行、能验证的完整驱动。从三个文件（INF + 头 + 源文件）到安装 Protocol 再到消费者找到它，每一步都有代码。

## 1. 最简驱动：Hello World

一个 DXE 驱动最少需要 3 个文件：

```
MyPkg/Drivers/HelloWorldDxe/
├── HelloWorldDxe.inf   # 模块元数据
├── HelloWorldDxe.h     # 头文件（Protocol GUID + 签名）
└── HelloWorldDxe.c     # 入口函数实现
```

### 1.1 INF：告诉构建系统"我是谁"

```ini
[Defines]
  INF_VERSION    = 0x00010005
  BASE_NAME      = HelloWorldDxe
  FILE_GUID      = A1B2C3D4-E5F6-7890-ABCD-EF1234567890
  MODULE_TYPE    = DXE_DRIVER
  VERSION_STRING = 1.0
  ENTRY_POINT    = HelloWorldEntryPoint

[Sources]
  HelloWorldDxe.c

[Packages]
  MdePkg/MdePkg.dec
  MyPkg/MyPkg.dec

[LibraryClasses]
  UefiDriverEntryPoint       # 提供 _ModuleEntryPoint → 调用你的 ENTRY_POINT
  UefiLib                    # Print / DEBUG 宏
  DebugLib                   # DEBUG 宏的实现后端

[Depex]
  TRUE                       # 无条件加载，不等待任何 Protocol
```

各字段的含义：
- `MODULE_TYPE = DXE_DRIVER`：决定链接 `UefiDriverEntryPoint` 库，该库提供 `(ImageHandle, *SystemTable) → EFI_STATUS` 的入口签名
- `ENTRY_POINT = HelloWorldEntryPoint`：你的实际入口函数名
- `[Depex] TRUE`：TRUE = 无条件加载。改为 GUID 表达式（如 `gEfiPciRootBridgeIoProtocolGuid AND gEfiCpuArchProtocolGuid`）则等待相关 Protocol 就绪后才调度

### 1.2 源码

```c
#include <Uefi.h>
#include <Library/UefiLib.h>
#include <Library/DebugLib.h>

EFI_STATUS
EFIAPI
HelloWorldEntryPoint (
  IN EFI_HANDLE        ImageHandle,
  IN EFI_SYSTEM_TABLE  *SystemTable
  )
{
  DEBUG ((DEBUG_INFO, "HelloWorldDxe: driver loaded at Handle=%p\n", ImageHandle));
  return EFI_SUCCESS;
}
```

### 1.3 注册到平台

在 DSC 中声明编译此模块，在 FDF 中声明打包到 Flash：

```ini
# MyPkg/MyPkg.dsc
[Components]
  MyPkg/Drivers/HelloWorldDxe/HelloWorldDxe.inf

# MyPkg/MyPkg.fdf
[FV.Main]
  INF MyPkg/Drivers/HelloWorldDxe/HelloWorldDxe.inf
```

DSC 决定编译，FDF 决定打入 Flash。两者缺一不可。

编译：`build -a RISCV64 -t GCC5 -b DEBUG -p MyPkg/MyPkg.dsc`  
验证：QEMU 启动后串口日志中搜索 `HelloWorldDxe`——DEBUG 宏会在该行打印。

---

## 2. 自定义 Protocol

Hello World 驱动只是在入口点打了个日志——有用但有限。真实的驱动要能与其他驱动交换数据。这就需要定义和安装 Protocol。

### 2.1 定义 Protocol

```c
// MyPkg/Include/Protocol/MyProtocol.h
#ifndef __MY_PROTOCOL_H__
#define __MY_PROTOCOL_H__

#define MY_PROTOCOL_GUID \
  { 0xABCD1234, 0x5678, 0x9ABC, { 0xDE, 0xF0, 0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC } }

typedef struct _MY_PROTOCOL MY_PROTOCOL;

struct _MY_PROTOCOL {
  UINT64    Version;
  EFI_STATUS (EFIAPI *GetData) (
    IN     MY_PROTOCOL  *This, IN UINTN Index, OUT UINT32 *Value);
  EFI_STATUS (EFIAPI *SetData) (
    IN     MY_PROTOCOL  *This, IN UINTN Index, IN UINT32  Value);
};

extern EFI_GUID gMyProtocolGuid;

#endif
```

在 DEC 中声明 GUID（构建系统需要知道 GUID 的存在和值）：

```ini
# MyPkg/MyPkg.dec
[Protocols]
  gMyProtocolGuid = { 0xABCD1234, 0x5678, 0x9ABC, { 0xDE, 0xF0, ... }}
```

### 2.2 安装 Protocol（生产者）

```c
// MyPkg/Drivers/ProducerDxe/ProducerDxe.c
#include <Uefi.h>
#include <Library/UefiBootServicesTableLib.h>
#include <Library/DebugLib.h>
#include <Protocol/MyProtocol.h>

STATIC EFI_STATUS EFIAPI MyGetData (
  IN MY_PROTOCOL *This, IN UINTN Index, OUT UINT32 *Value)
{
  static UINT32 mData[4] = { 100, 200, 300, 400 };
  if (Index >= 4) return EFI_INVALID_PARAMETER;
  *Value = mData[Index];
  return EFI_SUCCESS;
}

STATIC EFI_STATUS EFIAPI MySetData (
  IN MY_PROTOCOL *This, IN UINTN Index, IN UINT32 Value)
{
  static UINT32 mData[4];
  if (Index >= 4) return EFI_INVALID_PARAMETER;
  mData[Index] = Value;
  return EFI_SUCCESS;
}

STATIC MY_PROTOCOL mMyProtocol = {
  .Version  = 1,
  .GetData  = MyGetData,
  .SetData  = MySetData,
};

EFI_STATUS EFIAPI ProducerEntryPoint (
  IN EFI_HANDLE ImageHandle, IN EFI_SYSTEM_TABLE *SystemTable)
{
  EFI_STATUS Status = gBS->InstallProtocolInterface (
    &ImageHandle, &gMyProtocolGuid,
    EFI_NATIVE_INTERFACE, &mMyProtocol);
  DEBUG ((DEBUG_INFO, "Producer: Installed MyProtocol, Status=%r\n", Status));
  return Status;
}
```

### 2.3 查找并使用 Protocol（消费者）

```c
// MyPkg/Drivers/ConsumerDxe/ConsumerDxe.c
EFI_STATUS EFIAPI ConsumerEntryPoint (
  IN EFI_HANDLE ImageHandle, IN EFI_SYSTEM_TABLE *SystemTable)
{
  MY_PROTOCOL  *MyProto;
  UINT32        Value;
  EFI_STATUS    Status;

  Status = gBS->LocateProtocol (&gMyProtocolGuid, NULL, (VOID**)&MyProto);
  if (EFI_ERROR (Status)) {
    DEBUG ((DEBUG_ERROR, "Consumer: Protocol not found!\n"));
    return Status;
  }

  Status = MyProto->GetData (MyProto, 0, &Value);
  DEBUG ((DEBUG_INFO, "Consumer: Data[0] = %u, Status=%r\n", Value, Status));
  return EFI_SUCCESS;
}
```

注意消费者和生产者的代码放在完全不同的目录，不知对方存在。它们只有一个交集：`gMyProtocolGuid`。

**何时用 Protocol vs. DriverBinding？**

| 场景 | 用 |
|------|-----|
| 同一类型的多个设备（每个磁盘装一个 BlockIo，文件系统驱动枚举所有磁盘） | DriverBinding + Handle 列表 |
| 全局单例（时钟源、CPU Feature、调试接口） | Protocol + LocateProtocol |
| 服务实例（一个驱动想对外暴露一个配置读写接口） | Protocol（安装到自己 ImageHandle） |

---

## 3. 全局系统服务

你注意到所有代码里都在用 `gBS->xxx`，但从未声明过 `gBS`。它来自系统表——入口函数的 `SystemTable` 参数：

```c
// 三个全局宏，由各自的库构造函数初始化：
gST   // = SystemTable                   → 入口点库初始化，UefiLib 暴露
gBS   // = SystemTable->BootServices     → UefiBootServicesTableLib 暴露
gRT   // = SystemTable->RuntimeServices  → UefiRuntimeServicesTableLib 暴露
```

前提条件：**INF 中必须声明对应的 LibraryClasses**。`UefiDriverEntryPoint` 在调用你的入口函数前已把 `SystemTable` 存入 `gST`。`UefiBootServicesTableLib` 的构造函数从 `gST` 中提取 `BootServices` 字段赋值给 `gBS`。AutoGen 生成的 `ProcessLibraryConstructorList` 会按依赖顺序调用所有构造函数。

---

## 4. 文件清单：从零到运行状态的 7 步

以一个真实的 `MyPkg` 包为例，完成包含 3 个模块的完整流程：

| 文件 | 做什么 |
|------|--------|
| 1. `MyPkg/MyPkg.dec` | 声明 GUID（Protocol GUID、PCD TokenSpace） |
| 2. `MyPkg/MyPkg.dsc` | 库绑定 + `[Components]` 声明要构建的三驱动列表 |
| 3. `MyPkg/MyPkg.fdf` | Flash 分区 + `[FV]` 声明要打包到固件卷的驱动 |
| 4. `MyPkg/Include/Protocol/MyProtocol.h` | Protocol 方法签名 |
| 5. `MyPkg/Drivers/HelloWorldDxe/{.c, .inf}` | 日志打印（1 驱） |
| 6. `MyPkg/Drivers/ProducerDxe/{.c, .inf}` | 安装 Protocol + 数据提供（2 驱） |
| 7. `MyPkg/Drivers/ConsumerDxe/{.c, .inf}` | -- LocateProtocol + 数据消费（3 驱） |

在终端构建 `build -a RISCV64 -p MyPkg/MyPkg.dsc -t GCC5 -b DEBUG`；若成功则出现 Build Report 和 `0 Failures`。

---

**上一篇**：[04-Handle / Protocol 核心模型](./04-handle-protocol.md)  
**下一篇**：[06-事件 / TPL / DEPEX — 驱动间的时序与调度](./06-events-tpl-depex.md)
