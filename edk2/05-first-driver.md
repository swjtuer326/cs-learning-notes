# 05 — 写第一个 DXE 驱动

> 这篇不是"玩具 Protocol"教程。从最简 HelloWorld 开始，到写一个真正的 **NS16550 UART 硬件驱动**——包含 DriverBinding 三个回调、MMIO 寄存器读写、PCI 设备枚举。学完你能写出操作真实硬件的驱动。

## 1. Hello World（热身：15 行代码）

三个文件，验证"我写的驱动能被 Dispatcher 加载"：

```c
// MyPkg/Drivers/HelloWorldDxe/HelloWorldDxe.c
#include <Uefi.h>
#include <Library/UefiLib.h>
#include <Library/DebugLib.h>

EFI_STATUS EFIAPI HelloWorldEntryPoint (
  IN EFI_HANDLE ImageHandle, IN EFI_SYSTEM_TABLE *SystemTable)
{
  DEBUG ((DEBUG_INFO, "HelloWorld: loaded at Handle=%p\n", ImageHandle));
  return EFI_SUCCESS;
}
```

对应 `HelloWorldDxe.inf` 的 `MODULE_TYPE = DXE_DRIVER`，`[Depex] TRUE`。跑通之后，开始写真正做事的驱动。

---

## 2. 数据服务 Protocol（生产者-消费者，纯内存）

HelloWorld 驱动只是在入口点打了个日志。真实项目中的"第一次"通常是写一个配置/数据服务驱动——不碰硬件，但在 Handle 数据库里安装一个 Protocol：

```c
// MyPkg/Include/Protocol/MyProtocol.h
#define MY_PROTOCOL_GUID \
  { 0xABCD1234, 0x5678, 0x9ABC, { 0xDE, 0xF0, 0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC } }

typedef struct _MY_PROTOCOL MY_PROTOCOL;
struct _MY_PROTOCOL {
  UINT64 Version;
  EFI_STATUS (EFIAPI *GetData)(IN MY_PROTOCOL *This, IN UINTN Index, OUT UINT32 *Value);
  EFI_STATUS (EFIAPI *SetData)(IN MY_PROTOCOL *This, IN UINTN Index, IN UINT32 Value);
};
extern EFI_GUID gMyProtocolGuid;
```

**生产者**（安装 Protocol，暴露内部数据）：

```c
// MyPkg/Drivers/ProducerDxe/ProducerDxe.c
EFI_STATUS EFIAPI ProducerEntryPoint (EFI_HANDLE ImageHandle, EFI_SYSTEM_TABLE *SystemTable)
{
  static MY_PROTOCOL mProto = { .Version = 1, .GetData = MyGet, .SetData = MySet };
  return gBS->InstallProtocolInterface (&ImageHandle, &gMyProtocolGuid,
               EFI_NATIVE_INTERFACE, &mProto);
}
```

**消费者**（通过 GUID 找到 Protocol，调用方法）：

```c
// MyPkg/Drivers/ConsumerDxe/ConsumerDxe.c
EFI_STATUS EFIAPI ConsumerEntryPoint (EFI_HANDLE ImgH, EFI_SYSTEM_TABLE *ST)
{
  MY_PROTOCOL *Proto; UINT32 Val;
  if (!EFI_ERROR (gBS->LocateProtocol (&gMyProtocolGuid, NULL, (VOID**)&Proto))) {
    Proto->GetData (Proto, 0, &Val);
    DEBUG ((DEBUG_INFO, "Consumer: data[0] = %d\n", Val));
  }
  return EFI_SUCCESS;
}
```

这已经构成了一个完整的"服务提供 → 被调用"模型。但它不碰硬件。下一步：写一个真正读写 MMIO 寄存器的驱动。

---

## 3. 实战：NS16550 UART 串口驱动

目标：写一个 DXE 驱动，检测到 PCI UART 设备后，通过 MMIO 读写 NS16550 寄存器，发送字节流。

### 3.1 硬件背景

NS16550 兼容 UART 的核心寄存器（通过 MMIO/PIO 访问，偏移从 BAR0 开始）：

| 偏移 | Read | Write | 作用 |
|------|------|-------|------|
| `+0x00` | `RBR`（接收缓冲） | `THR`（发送缓冲） | 读写字节 |
| `+0x04` | `IER` | `IER` | 中断使能（TX/RX/Line） |
| `+0x08` | `IIR`（中断标识） | `FCR`（FIFO 控制） | FIFO 使能 + 清除 |
| `+0x14` | `LSR`（线路状态） | — | bit6 = "THR 空" |
| `+0x18` | `MSR`（调制解调）| — | 调制解调器状态 |

查 UART 手册或 `SerialPortLib` 源码可以随时查到寄存器含义，不需死记。

**发送一个字节的方法：**
1. 读 `LSR` 直到 bit6 (Transmitter Empty) = 1
2. 写字节到 `THR (0x00)`

### 3.2 驱动设计

使用标准 UEFI DriverBinding 三回调：

```c
// MyPkg/Include/Protocol/UartIo.h  —— 简化版 SerialIo
#define MY_UART_IO_PROTOCOL_GUID { ... }
struct _MY_UART_IO_PROTOCOL {
  EFI_STATUS (EFIAPI *WriteByte)(IN MY_UART_IO_PROTOCOL *This, IN UINT8 Byte);
  EFI_STATUS (EFIAPI *ReadByte) (IN MY_UART_IO_PROTOCOL *This, IN UINT8 *Byte);
};
extern EFI_GUID gMyUartIoProtocolGuid;
```

在 `MyPkg/MyPkg.dec` 中作 GUID 声明（不必担心值从哪来——static GUID 只用于 DXE 独立查询）：

```ini
[Protocols]
  gMyUartIoProtocolGuid = { 8A2B1C3D-4E5F-6A7B-8C9D-0E1F2A3B4C5D, {...} }
```

### 3.3 完整源码

```c
// MyPkg/Drivers/Uart16550Dxe/Uart16550Dxe.c
#include <Uefi.h>
#include <Library/UefiBootServicesTableLib.h>
#include <Library/DebugLib.h>
#include <Library/MemoryAllocationLib.h>
#include <Protocol/PciIo.h>
#include <Protocol/DriverBinding.h>
#include <Protocol/UartIo.h>

// -------------------------------------------
// 私有上下文：每个绑定的设备一个实例
// -------------------------------------------
typedef struct {
  UINT32              Signature;     // 魔数，用于 BASE_CR 校验（值为 'UART'）
  UINTN               MmioBase;      // BAR0 的 MMIO 虚拟地址
  MY_UART_IO_PROTOCOL UartIo;        // 暴露给消费者的方法表
  EFI_HANDLE          Controller;   // 绑定的设备 Handle
} UART_DEV;

#define UART_DEV_SIGNATURE  SIGNATURE_32('U','A','R','T')
#define UART_DEV_FROM_UIO(a)  CR(a, UART_DEV, UartIo, UART_DEV_SIGNATURE)

// —— NS16550 寄存器宏 ——
#define REG_THR  0x00
#define REG_IER  0x04
#define REG_IIR  0x08    // 读 (int id), FCR=写 (FIFO ctrl)
#define REG_LCR  0x0C
#define REG_MCR  0x10
#define REG_LSR  0x14
#define REG_MSR  0x18

// ===== 硬件访问原语（每个设备独立的 MMIO 基址） =====

STATIC UINT8 ReadReg8 (IN UART_DEV *Dev, IN UINTN Reg) {
  return MmioRead8 (Dev->MmioBase + Reg);
}
STATIC VOID WriteReg8 (IN UART_DEV *Dev, IN UINTN Reg, IN UINT8 Val) {
  MmioWrite8 (Dev->MmioBase + Reg, Val);
}
```

`MmioRead8` / `MmioWrite8` 来自 `BaseLib`——它们用标准的 `volatile UINT8*` 指针访问 MMIO 空间，编译器不会优化掉。

```c
// ===== Protocol 回调：WriteByte =====
EFI_STATUS EFIAPI UartWriteByte (IN MY_UART_IO_PROTOCOL *This, IN UINT8 Data)
{
  UART_DEV *Dev = UART_DEV_FROM_UIO (This);
  do { ; } while ((ReadReg8 (Dev, REG_LSR) & (1 << 6)) == 0);   // 等 TX_EMPTY
  WriteReg8 (Dev, REG_THR, Data);
  return EFI_SUCCESS;
}

// ===== Protocol 回调：ReadByte =====
EFI_STATUS EFIAPI UartReadByte (IN MY_UART_IO_PROTOCOL *This, OUT UINT8 *Data)
{
  UART_DEV *Dev = UART_DEV_FROM_UIO (This);
  if ((ReadReg8 (Dev, REG_LSR) & 1) == 0) return EFI_NOT_READY;  // 无数据
  *Data = ReadReg8 (Dev, REG_THR);  // RBR at offset 0
  return EFI_SUCCESS;
}

// ===== NS16550 初始化 =====
STATIC VOID Init16550 (IN UART_DEV *Dev)
{
  // 禁用中断（UEFI 用轮询模式）—— IER = 0 清全部中断使能
  WriteReg8 (Dev, REG_IER, 0x00);
  // 启用 FIFO，RX trigger = 14 bytes —— FCR = 0xC7: FIFO-en + RX/TX reset + RX@14
  WriteReg8 (Dev, REG_IIR, 0xC7);
  // 8N1, DLAB 清零 —— LCR = 0x03: 8bit 无奇偶 1 停止位
  WriteReg8 (Dev, REG_LCR, 0x03);
  // MCR = DTR|RTS 有效 —— MCR = 0x03: 始终准备好交换数据
  WriteReg8 (Dev, REG_MCR, 0x03);
}
```

初始化策略（轮询模式）：禁用中断、使能 FIFO、配置通讯参数、置位 DTR/RTS——全部通过 `MmioWrite8` 独立完成，不依赖 SBI 或 `SerialPortLib` 的任何调用。

```c
// ===== DriverBinding: Supported —— 检查设备兼容性 =====
EFI_STATUS EFIAPI UartSupported (
  IN EFI_DRIVER_BINDING_PROTOCOL *This,
  IN EFI_HANDLE Controller, IN EFI_DEVICE_PATH_PROTOCOL *RemainingPath)
{
  EFI_PCI_IO_PROTOCOL *PciIo;
  EFI_STATUS Status = gBS->OpenProtocol (Controller, &gEfiPciIoProtocolGuid,
                            (VOID**)&PciIo, This->DriverBindingHandle,
                            Controller, EFI_OPEN_PROTOCOL_BY_DRIVER);
  if (EFI_ERROR (Status)) return Status;

  // 读 PCI 配置空间：Class Code = 0x07_00_02（简易通讯 UART）
  union { UINT32 Raw; struct { UINT32 Prog:8, Sub:8, Base:8; } CC; } CCode;
  PciIo->Pci.Read (PciIo, EfiPciIoWidthUint8, 0x0B, 1, &CCode.CC.Base);
  PciIo->Pci.Read (PciIo, EfiPciIoWidthUint8, 0x0A, 1, &CCode.CC.Sub);
  PciIo->Pci.Read (PciIo, EfiPciIoWidthUint8, 0x09, 1, &CCode.CC.Prog);

  return (CCode.CC.Base == 0x07 && CCode.CC.Sub == 0x00) ? EFI_SUCCESS
                                                          : EFI_UNSUPPORTED;
}
```

`EfiPciIoWidthUint8` 表示每次 `PciIo->Pci.Read` 读取 1 字节（8 位）。PCI 配置空间读是抽象接口——PCI 总线提供的 `PciIo->Pci.Read` 会用 Port IO 或 MMCFG 对你透明地完成总线事务。

```c
// ===== DriverBinding: Start —— 绑定设备，安装 Protocol =====
EFI_STATUS EFIAPI UartStart (
  IN EFI_DRIVER_BINDING_PROTOCOL *This,
  IN EFI_HANDLE Controller, IN EFI_DEVICE_PATH_PROTOCOL *RemainingPath)
{
  EFI_STATUS Status;
  EFI_PCI_IO_PROTOCOL *PciIo;

  Status = gBS->OpenProtocol (Controller, &gEfiPciIoProtocolGuid,
                (VOID**)&PciIo, This->DriverBindingHandle,
                Controller, EFI_OPEN_PROTOCOL_GET_PROTOCOL);
  if (EFI_ERROR (Status)) return Status;

  // 分配私有上下文 —— UART_DEV 作为池分配，不进入栈（栈空间有限 + 全局上下文留存）
  UART_DEV *Dev = AllocateZeroPool (sizeof (UART_DEV));
  Dev->Signature   = UART_DEV_SIGNATURE;
  Dev->Controller  = Controller;
  Dev->UartIo.WriteByte = UartWriteByte;
  Dev->UartIo.ReadByte  = UartReadByte;

  // 读 BAR0 —— 偏移 0x10 是 BAR0（64bit BAR = 长描述；标准 BAR = 标准描述）
  UINT64 Bar0;  UINT8 BarReg = 0x10;
  PciIo->Pci.Read (PciIo, EfiPciIoWidthUint32, BarReg, 1, &Bar0);
  // BAR0 包含空间类型和长度信息，取 base 时要屏蔽低 3 位（地址=基于 BAR）
  UINT64 MmioPhys = Bar0 & ~0xFFF;  // 屏蔽类型位（bits 0-3: memory space indicator）

  // MMIO 映射：物理地址 → 虚拟地址（长度 4KB = 一个页面）
  EFI_GCD_MEMORY_SPACE_DESCRIPTOR Gcd;
  Status = gDS->AllocateMemorySpace (EfiGcdAllocateAddress, EfiGcdMemoryTypeMemoryMappedIo,
                                     0, SIZE_4KB, &MmioPhys, gImageHandle, NULL);
  if (EFI_ERROR (Status)) { FreePool (Dev); return Status; }
  Status = gDS->SetMemorySpaceAttributes (MmioPhys, SIZE_4KB, EFI_MEMORY_UC);
  Dev->MmioBase = (UINTN)(UINT64)MmioPhys;   // identity-mapped MMIO
  // 注：SEC/PEI 跳转后 MMU 已启用，phys == virt 是可以的

  Init16550 (Dev);

  // 在设备 Handle 上安装 Protocol → "我已准备好，谁来读？"
  Status = gBS->InstallProtocolInterface (&Controller,
                 &gMyUartIoProtocolGuid, EFI_NATIVE_INTERFACE, &Dev->UartIo);
  DEBUG ((DEBUG_INFO, "Uart16550: bound to Handle=%p MMIO=0x%x Status=%r\n",
          Controller, (UINT32)Dev->MmioBase, Status));

  return Status;
}

// ===== DriverBinding: Stop =====
EFI_STATUS EFIAPI UartStop (
  IN EFI_DRIVER_BINDING_PROTOCOL *This, IN EFI_HANDLE Controller,
  IN UINTN Children, IN EFI_HANDLE *ChildBuf)
{
  MY_UART_IO_PROTOCOL *Uio;
  EFI_STATUS Status = gBS->OpenProtocol (Controller, &gMyUartIoProtocolGuid,
                          (VOID**)&Uio, This->DriverBindingHandle,
                          Controller, EFI_OPEN_PROTOCOL_GET_PROTOCOL);
  if (EFI_ERROR (Status)) return Status;

  UART_DEV *Dev = UART_DEV_FROM_UIO (Uio);
  gBS->UninstallProtocolInterface (Controller, &gMyUartIoProtocolGuid, &Dev->UartIo);
  FreePool (Dev);
  return EFI_SUCCESS;
}
```

这段 `Start()` 就是真实设备驱动的初始化范式：配置空间读 → MMIO 映射 → 寄存器写 → Protocol 安装。整个启动流程只用到了 `PciIo` + `gDS` + `MemoryAllocationLib` + `MmioWrite8`——**没有任何 SBI 依赖**，在 RISC-V 和 x86 上完全相同。

```c
// ===== DriverBinding 实例（每个驱动>一个） =====
STATIC EFI_DRIVER_BINDING_PROTOCOL gUartBinding = {
  UartSupported, UartStart, UartStop,
  0x10, NULL, NULL    // Version, ImageHandle, DriverBindingHandle（编译时占位）
};

// ===== 入口点：安装 DriverBinding =====
EFI_STATUS EFIAPI Uart16550EntryPoint (
  IN EFI_HANDLE ImageHandle, IN EFI_SYSTEM_TABLE *SystemTable)
{
  return gBS->InstallProtocolInterface (&ImageHandle,
               &gEfiDriverBindingProtocolGuid,
               EFI_NATIVE_INTERFACE, &gUartBinding);
}
```

入口点不初始化硬件——只装 DriverBinding。硬件枚举留给 Dispatcher。这个驱动适用的 Module Type = `DXE_DRIVER`。

### 3.4 配置与编译

```ini
# MyPkg/Drivers/Uart16550Dxe/Uart16550Dxe.inf
[Defines]
  MODULE_TYPE = DXE_DRIVER
  ENTRY_POINT = Uart16550EntryPoint
  ...
[LibraryClasses]
  UefiDriverEntryPoint  UefiBootServicesTableLib
  DebugLib              BaseLib              MemoryAllocationLib
[Protocols]  gEfiPciIoProtocolGuid  gEfiDriverBindingProtocolGuid  gMyUartIoProtocolGuid
[Depex]      gEfiPciIoProtocolGuid
```

### 3.5 消费者测试

写一个简单的消费者驱动——关机前往串口写 "OK\n"：

```c
// MyPkg/Drivers/UartTestDxe/UartTestDxe.c
EFI_STATUS EFIAPI UartTestEntryPoint (...)
{
  EFI_HANDLE *Handles; UINTN Count;
  gBS->LocateHandleBuffer (ByProtocol, &gMyUartIoProtocolGuid, NULL, &Count, &Handles);
  for (UINTN i = 0; i < Count; i++) {
    MY_UART_IO_PROTOCOL *Io;
    if (!EFI_ERROR (gBS->HandleProtocol (Handles[i], &gMyUartIoProtocolGuid, (VOID**)&Io))) {
      Io->WriteByte (Io, 'O'); Io->WriteByte (Io, 'K'); Io->WriteByte (Io, '\n');
    }
  }
  return EFI_SUCCESS;
}
```

测试方法：QEMU 启动后串口应出现 "OK" 输出——验证从 PCI 枚举到 MMIO 写到 UART 硬件的完整链路。

---

## 4. 全局系统服务

所有驱动里都在用 `gBS->xxx` / `gST->yyy` / `gRT->...`，它们是全局快捷指针：

```
gST   = SystemTable                   → 入口点库初始化 (UefiLib)
gBS   = SystemTable->BootServices     → (UefiBootServicesTableLib)
gRT   = SystemTable->RuntimeServices  → (UefiRuntimeServicesTableLib)
```

前提：`INF` 中必须有对应库声明。入口点库在调用 `ENTRY_POINT` 前已把 `SystemTable` → `gST`。`UefiBootServicesTableLib` 从 `gST->BootServices` 提取成 `gBS`。

---

## 5. 文件清单：从零到运行

| 文件 | 作用 |
|------|------|
| `MyPkg/MyPkg.dec` | GUID / LibraryClass / PCD 声明 |
| `MyPkg/MyPkg.dsc` | 库绑定 + `[Components]` 三驱动列表 |
| `MyPkg/MyPkg.fdf` | `[FV]` 列出 HelloWorld + Producer + Consumer + Uart16550 + UartTest |
| `MyPkg/Include/Protocol/MyProtocol.h` | 方法签名 + GUID（MY_PROTOCOL） |
| `MyPkg/Include/Protocol/UartIo.h` | WriteByte / ReadByte + GUID（UartIo） |
| `MyPkg/Drivers/HelloWorldDxe/{.c,.inf}` | 加载验证 |
| `MyPkg/Drivers/ProducerDxe/{.c,.inf}` | 安装 PROTOCOL（数据） |
| `MyPkg/Drivers/ConsumerDxe/{.c,.inf}` | — LocateProtocol 调 GetData |
| `MyPkg/Drivers/Uart16550Dxe/{.c,.inf}` | **硬件驱动**：DriverBinding + MMIO |
| `MyPkg/Drivers/UartTestDxe/{.c,.inf}` | **测试驱动**：枚举 UartIo → 写 "OK\n" |

---

**上一篇**：[04-Handle / Protocol 核心模型](./04-handle-protocol.md)  
**下一篇**：[06-事件 / TPL / DEPEX — OS 引导实战](./06-events-tpl-depex.md)
