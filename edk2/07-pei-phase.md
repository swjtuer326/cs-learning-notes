# 07 — PEI 阶段：内存稀缺时代的策略

> PEI 是在 DDR 初始化之前的阶段。你只有几十 KB 的临时 RAM（Cache-as-RAM，CAR），却需要初始化内存控制器、构建内存描述 HOB、定位 DXE Core 映像。这篇讲 PEIM 怎么写、PPI 怎么用、HOB 如何从 PEI 流通到 DXE，以及"极简主义"到底意味着什么——**全部用可编译代码**。

## 1. PEI vs. DXE：根本区别

| | DXE Driver | PEIM |
|---|-----------|------|
| 内存 | 充足 DDR（GB 级） | CAR（几十 KB）→ 后期才得 DDR |
| TCB 范围 | 每个驱动独立栈 | 所有 PEIM 共享同一小段 CAR |
| 通信机制 | Protocol（GUID + 多实例 + 引用计数） | PPI（GUID + 单实例 + 无引用计数） |
| 库依赖 | `[LibraryClasses.common.DXE_DRIVER]` | `[LibraryClasses.common.PEIM]` |
| 输出 | Protocol 数据库（驱动栈） | HOB 列表（资源描述 + FV 位置） |

---

## 2. PPI：PEI 阶段的 Protocol 替代品

PPI 是 Protocol 的简化版本——单实例、无引用计数。调用方式几乎相同：

```c
// ===== 安装 PPI =====
STATIC MY_PPI  mMyPpiInstance = { .Version = 1, .GetSomething = MyGet };

STATIC EFI_PEI_PPI_DESCRIPTOR mPpiDesc = {
  EFI_PEI_PPI_DESCRIPTOR_PPI                      // 标志：这是一个 PPI 实例
    | EFI_PEI_PPI_DESCRIPTOR_TERMINATE_LIST,      // 标志：这是列表最后一个
  &gMyPpiGuid,                                    // GUID
  &mMyPpiInstance                                 // 实例指针
};

EFI_STATUS EFIAPI MyPeimEntryPoint (
  IN EFI_PEI_FILE_HANDLE FileHandle, IN CONST EFI_PEI_SERVICES **PeiServices)
{
  return PeiServices->InstallPpi (&mPpiDesc);
}

// ===== 查找 PPI =====
MY_PPI  *MyPpi;
EFI_STATUS Status = PeiServices->LocatePpi (
                       &gMyPpiGuid,       // GUID
                       0,                 // Instance (单例=0)
                       NULL,              // PPI Descriptor out（不需要时 NULL）
                       (VOID**)&MyPpi     // → PPI instance pointer
                       );
```

**为什么 PPI 不能多实例？** PEI 阶段代码空间在 CAR（几十 KB）——引入引用计数和多重查找校验会消耗宝贵的 KB 级内存，实现复杂度的代价远超收益。

---

## 3. HOB：PEI→DXE 的资源描述通道

PEI 阶段最重要的输出是 **HOB 列表**——一串单向链表，描述"什么内存可用"、"DXE 固件卷在哪"、"平台特定数据有哪些"：

```
HOB 列表（单向链表，每项有 Standard Header）：
Head → [PHIT] → [RES#1: DRAM 0x8000_0000] → [RES#2: MMIO 区域]
     → [RES#3: FV Region] → [Guid#1: HART_INFO 数据] → ...
```

每个 HOB 都有一个**标准头**：

```c
// 来自 MdePkg/Include/Pi/PiHob.h
typedef struct {
  UINT16  HobType;     // 节点类型: EFI_HOB_TYPE_RESOURCE_DESCRIPTOR / GUID_EXTENSION ...
  UINT16  HobLength;   // 整个 HOB 的字节大小
  UINT32  Reserved;    // 对齐用
} EFI_HOB_GENERIC_HEADER;
```

### 3.1 构建 Resource Descriptor HOB

描述一段内存区域（起始地址 + 长度 + 资源类型）：

```c
// 来自 MdeModulePkg/Library/HobLib/HobLib.c
// 内部实现: Allocate HOB node → fill EFI_HOB_RESOURCE_DESCRIPTOR → 链入 HOB list
VOID BuildResourceDescriptorHob (
  IN EFI_RESOURCE_TYPE            ResourceType,     // SystemMemory, MMIO, FirmwareDevice...
  IN EFI_RESOURCE_ATTRIBUTE_TYPE  ResourceAttribute,// PRESENT, INITIALIZED, TESTED...
  IN EFI_PHYSICAL_ADDRESS         PhysicalStart,    // 起始物理地址
  IN UINT64                       NumberOfBytes     // 长度 (bytes)
  );
```

### 3.2 构建 GUID HOB

传递任意自定义二进制数据（PEI→DXE）：

```c
VOID BuildGuidDataHob (
  IN EFI_GUID *Guid,              // 标识数据类型 (如 &gHartInfoHobGuid)
  IN VOID     *Data,              // 源数据指针
  IN UINTN     DataLength         // 数据长度
  );
```

---

## 4. 完整 PEIM 示例：解析 FDT 并构建 HOB

> **这是本篇的核心。** 以下是一个完整的 PEIM——它等待内存 PPI 就绪后，从 SEC 传来的 Handoff Block 获取 FDT 基址，遍历 `/memory` 节点构建 Resource HOB，遍历 `/cpus` 节点构建 GUID HOB，为 DXE 阶段构造 ACPI 表提供数据来源。

```c
// MyPlatformPkg/PlatformPei/FdtPeim.c
#include <PiPei.h>
#include <Library/PeiServicesLib.h>
#include <Library/HobLib.h>
#include <Library/DebugLib.h>
#include <Library/MemoryAllocationLib.h>
#include <Library/BaseMemoryLib.h>
#include <Library/PcdLib.h>

// ── 从 SEC Handoff Block 获取 FDT 基址 ──
// SEC_hob 在 PEI Core 获取 Hand-off Block 时隐式传递——PEIM 通过 HOB 查 FDT
STATIC VOID * GetFdtFromHob (VOID)
{
  VOID *HobList = GetHobList ();  // PEI Core 维护的 HOB list 根指针
  if (HobList == NULL) return NULL;

  // 遍历 HOB 列表——找到 EFI_HOB_TYPE_GUID_EXTENSION 中 gFdtHobGuid 对应的项
  EFI_PEI_HOB_POINTERS Hob;
  for (Hob.Raw = HobList; !END_OF_HOB_LIST (Hob); Hob.Raw = GET_NEXT_HOB (Hob)) {
    if (GET_HOB_TYPE (Hob) == EFI_HOB_TYPE_GUID_EXTENSION &&
        CompareGuid (&Hob.Guid->Name, &gFdtHobGuid))
    {
      // FDT HOB 的数据区: 第一个字段 = FDT 基址, 第二个 = 大小
      FDT_HOB_DATA *FdtHob = (FDT_HOB_DATA*)GET_GUID_HOB_DATA (Hob);
      DEBUG ((DEBUG_INFO, "PEI: FDT at 0x%lx, size=0x%lx\n",
              FdtHob->FdtBase, FdtHob->FdtSize));
      return (VOID*)FdtHob->FdtBase;
    }
  }

  DEBUG ((DEBUG_ERROR, "PEI: FDT HOB not found!\n"));
  return NULL;
}

// ── FDT 头与遍历原语（PEI 无 libfdt，需最小化实现） ──
// fdt_header: FDT magic (0xd00dfeed) + totalsize + structure/strings offsets
// 参考: linux/Documentation/devicetree/booting-without-of.rst
#pragma pack(1)
typedef struct {
  UINT32 Magic;             // 0xd00dfeed (大端)
  UINT32 TotalSize;         // DTB 总字节数 (大端)
  UINT32 OffDtStruct;       // 结构区 (token block) 的偏移 (大端)
  UINT32 OffDtStrings;      // 字符串块偏移 (大端)
  UINT32 OffMemRsvmap;      // reserved memory 偏移 (大端)
  UINT32 Version;           // 17 (大端)
  UINT32 LastCompVersion;   // 16 (大端)
} FDT_HEADER;
#pragma pack()

#define FDT_MAGIC  0xd00dfeed
#define FDT_BEGIN_NODE  0x00000001
#define FDT_END_NODE    0x00000002
#define FDT_PROP        0x00000003
#define FDT_NOP         0x00000004

STATIC UINT32 Fdt32ToCpu (UINT32 V) {
  // PEI 单字节处理大端 (lw + swap) — 简单实现
  UINT8 *B = (UINT8*)&V;
  UINT32 R;
  ((UINT8*)&R)[0] = B[3];  ((UINT8*)&R)[1] = B[2];
  ((UINT8*)&R)[2] = B[1];  ((UINT8*)&R)[3] = B[0];
  return R;
}
STATIC UINT64 Fdt64ToCpu (UINT64 V) {
  return ((UINT64)Fdt32ToCpu(V >> 32) << 32) | Fdt32ToCpu(V & 0xFFFFFFFF);
}

// ── 解析 /memory 节点中的 reg 属性，构建 Resource Hob ──
STATIC VOID ParseMemoryNode (
  IN UINT8 *Fdt, IN UINT8 *PropStart, IN UINT32 PropLen)
{
  // reg 属性格式: 每个条目 <base(U64) size(U64)> (addr-cells=2, size-cells=2)
  UINTN  NumEntries = PropLen / 16;  // 每个 region 占 16 字节
  UINT8 *Ptr = PropStart;

  for (UINTN i = 0; i < NumEntries; i++) {
    UINT64 Base = Fdt64ToCpu (*(UINT64*)Ptr);
    UINT64 Size = Fdt64ToCpu (*(UINT64*)(Ptr + 8));

    if (Base == 0 || Size == 0) { Ptr += 16; continue; }

    DEBUG ((DEBUG_INFO, "PEI: Memory region 0x%lx - 0x%lx (size=0x%lx)\n",
            Base, Base + Size, Size));

    BuildResourceDescriptorHob (
      EFI_RESOURCE_SYSTEM_MEMORY,
      (EFI_RESOURCE_ATTRIBUTE_PRESENT       // 此内存物理存在
       | EFI_RESOURCE_ATTRIBUTE_INITIALIZED // PEI 已完成初始化
       | EFI_RESOURCE_ATTRIBUTE_TESTED      // 内存测试通过
       | EFI_RESOURCE_ATTRIBUTE_WRITE_BACK_CACHEABLE),
      Base, Size
      );
    Ptr += 16;
  }
}

// ── 解析 /cpus 节点中的 cpu 子节点，构建 GUID HOB ──
typedef struct {
  UINT32 HartId;
  UINT32 AcpiUid;
  CHAR8  IsaString[128];
} PEI_HART_INFO;  // 注意：与 DXE HART_INFO (09 §5.2) 一致，版本兼容

STATIC VOID ParseCpuNode (
  IN UINT8 *Fdt, IN UINT8 *PropStart, IN UINT32 PropLen,
  IN PEI_HART_INFO *Hart, IN UINTN *HartIdx)
{
  // /cpus/cpu@N 节点的属性: reg = <HartId>; riscv,isa = "rv64..."
  // 在 FDT 结构区，cpu 节点的 reg 属性值是 HartId
  UINT64 HartId = Fdt64ToCpu (*(UINT64*)PropStart);

  Hart->HartId = (UINT32)HartId;
  Hart->AcpiUid = (UINT32)(*HartIdx);

  // ISA 字符串从 FDT strings 块获取 (见下面主循环中的逻辑)
  // 此处假定主循环已在扫描, Hart 结构已部分填充
}

// ── 主 PEIM 入口点 ──
EFI_STATUS EFIAPI FdtPeimEntryPoint (
  IN EFI_PEI_FILE_HANDLE FileHandle, IN CONST EFI_PEI_SERVICES **PeiServices)
{
  UINT8 *Fdt = (UINT8*)GetFdtFromHob ();
  if (Fdt == NULL) {
    DEBUG ((DEBUG_ERROR, "FDT not found in HOB list\n"));
    return EFI_NOT_FOUND;
  }

  // — 验证 FDT Header —
  FDT_HEADER *Hdr = (FDT_HEADER*)Fdt;
  if (Fdt32ToCpu (Hdr->Magic) != FDT_MAGIC) {
    DEBUG ((DEBUG_ERROR, "Invalid FDT magic: 0x%x\n", Hdr->Magic));
    return EFI_UNSUPPORTED;
  }
  DEBUG ((DEBUG_INFO, "PEI: FDT size=%d, struct_off=%d\n",
          Fdt32ToCpu (Hdr->TotalSize), Fdt32ToCpu (Hdr->OffDtStruct)));

  UINT8  *StructBlock = Fdt + Fdt32ToCpu (Hdr->OffDtStruct);
  UINT8  *StringsBlock = Fdt + Fdt32ToCpu (Hdr->OffDtStrings);
  UINT8  *Ptr = StructBlock;

  UINTN         HartIdx   = 0;
  PEI_HART_INFO HartBuffer[16];     // 栈上分配 (16 Harts = 2KB, PEI 可接受)
  BOOLEAN       InMemoryNode = FALSE;
  BOOLEAN       InCpuNode    = FALSE;
  CHAR8         NodeName[64];       // /cpus/cpu@N 中 N

  while (1) {
    UINT32 Token = Fdt32ToCpu (*(UINT32*)Ptr);
    Ptr += 4;

    if (Token == FDT_BEGIN_NODE) {
      // 节点名紧随 token (以 NUL 结尾的字符串)
      AsciiStrCpy (NodeName, (CHAR8*)Ptr);
      Ptr += AsciiStrLen ((CHAR8*)Ptr) + 1;

      if (AsciiStrCmp (NodeName, "memory") == 0) {
        InMemoryNode = TRUE;
      } else if (AsciiStrnCmp (NodeName, "cpu@", 4) == 0) {
        InCpuNode = TRUE;
        ZeroMem (&HartBuffer[HartIdx], sizeof (PEI_HART_INFO));
      }

    } else if (Token == FDT_END_NODE) {
      if (InCpuNode) {
        // cpu@N 子节点结束 → 完成此 Hart 的数据
        HartIdx++;
      }
      InMemoryNode = FALSE;
      InCpuNode    = FALSE;

    } else if (Token == FDT_PROP) {
      // 属性: UINT32 len + UINT32 nameoff → 值在 [Ptr+8]
      UINT32 PropLen = Fdt32ToCpu (*(UINT32*)Ptr);
      UINT32 NameOff = Fdt32ToCpu (*(UINT32*)(Ptr + 4));
      UINT8  *PropValue = Ptr + 8;
      CHAR8  *PropName  = (CHAR8*)(StringsBlock + NameOff);

      if (InMemoryNode && AsciiStrCmp (PropName, "reg") == 0) {
        ParseMemoryNode (Fdt, PropValue, PropLen);
      }

      if (InCpuNode) {
        PEI_HART_INFO *Hart = &HartBuffer[HartIdx];

        if (AsciiStrCmp (PropName, "reg") == 0) {
          Hart->HartId = (UINT32)Fdt32ToCpu (*(UINT32*)PropValue);
          Hart->AcpiUid = (UINT32)HartIdx;
        } else if (AsciiStrCmp (PropName, "riscv,isa") == 0) {
          AsciiStrnCpy (Hart->IsaString, (CHAR8*)PropValue, PropLen);
          Hart->IsaString[PropLen] = '\0';
        }
      }

      Ptr += 8 + ALIGN_VALUE (PropLen, 4);  // 属性值 4 字节对齐

    } else if (Token == FDT_NOP) {
      // NOP → skip

    } else if (Token == FDT_END_NODE) {
      // 已在上面处理了
    } else {
      // 未知 token → FDT 扫描错误
      DEBUG ((DEBUG_ERROR, "PEI: Bad FDT token 0x%x at offset %ld\n",
              Token, Ptr - StructBlock));
      break;
    }

    // FDT token 序列最后以 0x00000009 (FDT_END) 结束
  }

  // — 构建 GUID HOB，传递 Hart 信息给 DXE（供 ACPI 表构造使用） —
  //   对应 [09 §5.2](09-riscv-porting.md) 的 HART_INFO 结构
  if (HartIdx > 0) {
    BuildGuidDataHob (&gHartInfoHobGuid, HartBuffer,
                       HartIdx * sizeof (PEI_HART_INFO));
    DEBUG ((DEBUG_INFO, "PEI: Built HartInfo GUID Hob with %ld Harts\n", HartIdx));
  }

  // — 描述固件卷位置 (供 DXE Core 确定后续要读哪个 FV) —
  UINT64 FvBase = FixedPcdGet64 (PcdFlashFvMainBase);
  UINT64 FvSize = FixedPcdGet64 (PcdFlashFvMainSize);
  BuildResourceDescriptorHob (
    EFI_RESOURCE_FIRMWARE_DEVICE,
    (EFI_RESOURCE_ATTRIBUTE_PRESENT | EFI_RESOURCE_ATTRIBUTE_INITIALIZED),
    FvBase, FvSize
    );

  return EFI_SUCCESS;
}
```

### 4.1 对应 INF

```ini
# MyPlatformPkg/PlatformPei/FdtPeim.inf
[Defines]
  INF_VERSION    = 0x00010005
  BASE_NAME      = FdtPeim
  MODULE_TYPE    = PEIM
  ENTRY_POINT    = FdtPeimEntryPoint

[Packages]    MdePkg/MdePkg.dec  MdeModulePkg/MdeModulePkg.dec  MyPlatformPkg/MyPlatformPkg.dec

[LibraryClasses]
  PeiServicesLib  HobLib  DebugLib  BaseMemoryLib  PcdLib

[Pcd]
  gMyPlatformTokenSpaceGuid.PcdFlashFvMainBase
  gMyPlatformTokenSpaceGuid.PcdFlashFvMainSize

[Guids]
  gFdtHobGuid                               # SEC 放的 FDT 位置
  gHartInfoHobGuid                          # PEI → DXE Hart 数据

[Depex]
  TRUE
```

### 4.2 DXE 如何消费 PEI 传来的 HOB

在 DXE 阶段，`DxeMain` 把 HOB 列表转换为 UEFI 内存映射后，任何驱动都可以读取 GUID HOB：

```c
// MyPlatformPkg/AcpiTables/AcpiTableDxe.c  (延续 09 §5.6)
// 从 HOB 获取 Hart 信息，而非硬编码 mHarts[]
EFI_STATUS EFIAPI AcpiTableDxeEntryPoint (...)
{
  EFI_HOB_GUID_TYPE *GuidHob;
  HART_INFO          *Harts;
  UINTN               HartCount;

  // 查找 PEI 阶段构建的 Hart Info GUID HOB
  GuidHob = GetFirstGuidHob (&gHartInfoHobGuid);
  if (GuidHob == NULL) {
    DEBUG ((DEBUG_ERROR, "HartInfo GUID HOB not found!\n"));
    return EFI_NOT_FOUND;
  }

  Harts = (HART_INFO*)GET_GUID_HOB_DATA (GuidHob);
  // HOB 数据布局与 PEI_HART_INFO 一致 (字段顺序 + 类型宽度):
  //   UINT32 HartId; UINT32 AcpiUid; CHAR8 IsaString[128];
  HartCount = GET_GUID_HOB_DATA_SIZE (GuidHob) / sizeof (HART_INFO);

  DEBUG ((DEBUG_INFO, "DXE: Got %ld Harts from PEI HOB\n", HartCount));

  // 用 Harts + HartCount 构造 ACPI 表 (BuildRhct / BuildMadt)... 同 09 §5.6
  for (UINTN i = 0; i < HartCount; i++) {
    DEBUG ((DEBUG_INFO, "  Hart[%ld]: Id=%d ACPI_UID=%d ISA=\"%a\"\n",
            i, Harts[i].HartId, Harts[i].AcpiUid, Harts[i].IsaString));
  }
  // ... 调用 InstallAcpiTable
}
```

**这就是 PEI → DXE 的数据流全链路**：SEC 保留 FDT 位置 → PEI 解析并编码为 HOB → DXE 读取 HOB 构造 ACPI 表 → BDS 引导 OS → OS 消费 ACPI 表。

---

## 5. PPI 通知

PEI 也有通知机制，用于等待关键 PPI 就绪后再执行依赖逻辑：

```c
// 回调函数：内存 PPI 安装后触发
STATIC EFI_STATUS EFIAPI OnMemoryDiscoveredCallback (
  IN EFI_PEI_SERVICES           **PeiServices,
  IN EFI_PEI_NOTIFY_DESCRIPTOR  *NotifyDesc,
  IN VOID                       *Ppi)      // 指向 gEfiPeiMemoryDiscoveredPpiGuid 实例
{
  // 内存可用了——为 DXE 分配区域、构建 Resource HOB
  DEBUG ((DEBUG_INFO, "PEI: DDR discovered, building memory HOBs\n"));

  BuildResourceDescriptorHob (EFI_RESOURCE_SYSTEM_MEMORY,
    EFI_RESOURCE_ATTRIBUTE_PRESENT | EFI_RESOURCE_ATTRIBUTE_INITIALIZED |
    EFI_RESOURCE_ATTRIBUTE_TESTED,
    0x80000000, 0x40000000);  // 1GB DDR

  return EFI_SUCCESS;
}

// 注册通知
STATIC EFI_PEI_NOTIFY_DESCRIPTOR mMemoryNotifyList[] = {{
  EFI_PEI_PPI_DESCRIPTOR_NOTIFY_DISPATCH    // 模式: 首次就绪触发
    | EFI_PEI_PPI_DESCRIPTOR_TERMINATE_LIST,
  &gEfiPeiMemoryDiscoveredPpiGuid,           // 等待这个 PPI
  OnMemoryDiscoveredCallback                 // 就绪后调这个
}};

EFI_STATUS EFIAPI MyMemPeimEntryPoint (
  IN EFI_PEI_FILE_HANDLE FileHandle, IN CONST EFI_PEI_SERVICES **PeiServices)
{
  // 注册通知 → 返回 → 等内存 PPI 被安装后框架调 OnMemoryDiscoveredCallback
  return PeiServices->NotifyPpi (mMemoryNotifyList);
}
```

通知模式区别：

| 模式 | 触发时机 | 使用场景 |
|------|---------|---------|
| `NOTIFY_DISPATCH` | PPI 首次就绪 | "DDR 初始化完才能干活" |
| `NOTIFY_CALLBACK` | PPI 变成新值 (swap) | "PPI 实现被替换了，重新绑定新接口" |

---

## 6. "极简主义"的代码级含义

PEI "极简主义"不是口号，是具体的代码约束：

| 约束 | 为什么 | 违反的后果 |
|------|--------|----------|
| 禁止 `UINT8 Buf[4096]` | CAR 总容量 32~128KB，函数栈帧直接对应 CAR 消耗 | 栈溢出 → 静默覆盖 HOB/PCD 区域 → 接下来的 DXE 随机崩溃 |
| 避免递归 | PEI 栈深度受 CAR 严格限制，无栈保护 | 栈越界 → 覆盖 CAR 中其他 PEIM 数据 |
| 不初始化设备 | 设备寄存器写可能导致功耗/时序副作用，且 PEI 无 driver 模型 | 功耗不受控、与 DXE 驱动状态冲突 |
| 所有分配检查返回值 | 无 DDR 时 `AllocatePool` 返回 NULL 是预期情况 | 解引用 NULL → S-mode fault → OpenSBI 捕获 → 重启 |

---

**上一篇**：[06-事件 / TPL / DEPEX + OS 启动实战](./06-events-tpl-depex.md)  
**下一篇**：[08-构建系统深入](./08-build-system.md) — DEC/DSC/INF/FDF 的配合、库绑定与 AutoGen
