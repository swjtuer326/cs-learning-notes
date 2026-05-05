# 07 — PEI 阶段：内存稀缺时代的策略

> PEI 是在 DDR 初始化之前的阶段。你只有几十 KB 的临时 RAM（Cache-as-RAM，CAR），却需要初始化内存控制器、构建内存描述 HOB、定位 DXE Core 映像。这篇讲 PEIM 怎么写、PPI 怎么用，以及"极简主义"到底意味着什么。

## 1. PEI vs. DXE：根本区别

| | DXE Driver | PEIM |
|---|-----------|------|
| 内存 | 充足 DDR（GB 级） | CAR（Cache-as-RAM，几十 KB）→ 后期 DDR |
| Tcb 范围 | 每个驱动有自己的栈、自己的地址空间 | 所有 PEIM 共享同一小段 CAR |
| 通信机制 | Protocol（GUID + 多实例 + 引用计数） | PPI（GUID + 单实例 + 无引用计数） |
| 库依赖 | `[LibraryClasses.common.DXE_DRIVER]` | `[LibraryClasses.common.PEIM]` |
| 输出 | Protocol 数据库（驱动栈） | HOB 列表（资源描述 + 固件卷位置） |

## 2. PPI：PEI 阶段的 Protocol 替代品

PPI 是 Protocol 的简化版本——单实例、无引用计数。接口和用法几乎一致：

```c
// 安装 PPI
STATIC EFI_PEI_PPI_DESCRIPTOR mPpiDescriptor = {
  EFI_PEI_PPI_DESCRIPTOR_PPI | EFI_PEI_PPI_DESCRIPTOR_TERMINATE_LIST,
  &gMyPpiGuid, &mMyPpiInstance
};
PeiServices->InstallPpi (&mPpiDescriptor);

// 查找 PPI
MY_PPI  *MyPpi;
PeiServices->LocatePpi (&gMyPpiGuid, 0, NULL, (VOID**)&MyPpi);
```

为什么 PPI 不能多实例？PEI 阶段代码空间和管理开销都受 CAR 限制——引入引用计数和多重查找校验会消耗宝贵的 KB 级内存。

## 3. HOB：资源描述链

PEI 阶段最重要的输出是 **HOB 列表**——一串链表结构，描述了"什么内存可用"、"DXE 固件卷在哪"、"平台特定数据有哪些"：

```
HOB 列表（单向链表）：
Head → [PHIT] → [RES#1: DRAM 0x8000_0000] → [RES#2: MMIO] → 
       [RES#3: FV Region] → [Guid#1: 平台特定数据] → ...
```

每个 HOB 都有一个标准头（type + length），后面 Type 决定内容格式。构建 HOB：

```c
// Resource Descriptor HOB —— 描述一段内存区域
BuildResourceDescriptorHob (
  EFI_RESOURCE_SYSTEM_MEMORY,         // 类型
  (EFI_RESOURCE_ATTRIBUTE_PRESENT | ...), // 属性
  0x80000000,                         // 起始物理地址
  0x40000000                          // 长度
  );

// GUID HOB —— 传递任意自定义二进制数据
MyPlatformInfo  Info = { .BoardId = 7, .Rev = 2 };
BuildGuidDataHob (&gMyPlatformInfoGuid, &Info, sizeof (Info));
```

> DXE Core 启动后遍历 HOB 列表，将所有 Resource HOB 注册为 UEFI 内存映射条目。GUID HOB 中的自定义数据可以在 DXE 阶段通过 `GetFirstGuidHob` / `GetNextGuidHob` 读取。

## 4. 示例 PEIM

下面是一个完整的 PEIM——它等待内存发现 PPI 就绪后，读取 FDT 构建资源描述 HOB：

```c
// PlatformPei/PlatformPeim.c
#include <PiPei.h>
#include <Library/PeiServicesLib.h>
#include <Library/HobLib.h>
#include <Library/DebugLib.h>

EFI_STATUS EFIAPI InitializePlatformPeim (
  IN EFI_PEI_FILE_HANDLE FileHandle, IN CONST EFI_PEI_SERVICES **PeiServices)
{
  EFI_STATUS  Status;
  UINT64      FdtBase, DxeFvBase;
  UINTN       FdtSize, DxeFvSize;

  // 1. 读取 SEC 阶段传来的 HOB（FDT 基址、DXE FV 位置）
  GetHandoffDataFromHobStack (&FdtBase, &FdtSize, &DxeFvBase, &DxeFvSize);

  // 2. 解析 FDT，提取 /memory 节点的 reg 属性
  //    对于每条 memory region，调用 BuildResourceDescriptorHob
  ParseFdtMemoryNodes (FdtBase, FdtSize);

  // 3. 构建描述固件卷位置的 Resource HOB
  BuildResourceDescriptorHob (EFI_RESOURCE_FIRMWARE_DEVICE, ...);

  // 4. 构建 GUID HOB —— 传递平台特定数据给 DXE
  PlatformInfo  Info = { .PlatformType = TypeVirt, .MemSize = FdtMemSize };
  BuildGuidDataHob (&gPlatformInfoGuid, &Info, sizeof (Info));

  return EFI_SUCCESS;
}
```

PEIM 做的事：收集信息、转换格式、传下去。不初始化任何设备（设备驱动是 DXE 阶段的职责）。

## 5. PPI 通知

PEI 阶段的通知也有两种模式：

```c
// 模式一：NOTIFY_DISPATCH —— PPI 首次就绪时触发（最常用）
STATIC EFI_STATUS EFIAPI OnMemoryDiscovered (
  IN EFI_PEI_SERVICES **PeiSv, IN EFI_PEI_NOTIFY_DESCRIPTOR *Nd, IN VOID *Ppi)
{
  BuildResourceDescriptorHob (...);
  return EFI_SUCCESS;
}
STATIC EFI_PEI_NOTIFY_DESCRIPTOR mList[] = {
  { EFI_PEI_PPI_DESCRIPTOR_NOTIFY_DISPATCH | EFI_PEI_PPI_DESCRIPTOR_TERMINATE_LIST,
    &gEfiPeiMemoryDiscoveredPpiGuid, OnMemoryDiscovered }
};
PeiServicesNotifyPpi (mList);

// 模式二：NOTIFY_CALLBACK —— PPI 被重新安装（swap）时触发
// 标志位用 NOTIFY_CALLBACK 代替 NOTIFY_DISPATCH。用于 PPI 实现被替换的场景。
```

## 6. "极简主义"的实践含义

"PEI 阶段遵循极简主义"不是口号。它在代码层面意味着：

1. **禁止大局部数组**：`UINT8 Buf[4096]` 在 CAR 上是奢侈的——函数栈帧的大小直接对应 CAR 的消耗
2. **避免递归**：PEI 的栈深度受 CAR 严格限制，递归极易触发栈溢出而无任何保护
3. **不初始化设备**：设备驱动等待 DXE 阶段。PEIM 只收集硬件信息，不写设备寄存器
4. **所有分配可检查**：任何 `AllocatePages/AllocatePool` 调用后立即检查返回值——无休止的重试逻辑比直接失败更危险

---

**上一篇**：[06-事件 / TPL / DEPEX](./06-events-tpl-depex.md)  
**下一篇**：[08-构建系统深入](./08-build-system.md) — DEC/DSC/INF/FDF 的配合、库绑定与 AutoGen
