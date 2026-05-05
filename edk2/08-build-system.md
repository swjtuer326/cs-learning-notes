# 08 — 构建系统深入

> 之前写驱动时，DEC/DSC/INF/FDF 只是"照搬模板"。这篇讲清它们的配合机制：四种元数据文件各自解决什么问题，Library Class/Instance 的绑定意味着什么，以及 AutoGen 如何把"声明"翻译成"可编译的 C 代码"。

## 1. 四种元数据文件的分工

EDK2 的构建是**声明式**的——你告诉构建系统"要编译什么"，而不是"怎么编译"。四种元数据文件各司其职，范围层层收窄：

| 文件 | 作用域 | 核心问题 |
|------|--------|----------|
| **DEC** | 包级（Package） | 这个包对外暴露什么？（Protocol GUID、PCD 定义、Library Class 声明） |
| **DSC** | 平台级（Platform） | 这个平台用哪些 PCD 值、Library Instance、包含哪些模块？ |
| **INF** | 模块级（Module） | 这个模块的源码列表、需要的 Library Class、依赖的 Protocol、DEPEX |
| **FDF** | 固件级（Firmware） | Flash 怎么分区？哪个模块放进哪个固件卷（FV）？空间有多大？ |

四种文件的关系：DEC 声明"有什么可用"→ DSC 选择"用什么实现"→ INF 描述"一个模块用了什么"→ FDF 指定"这些东西在 Flash 上的物理位置"。

## 2. DEC：包声明

```ini
# MyPkg/MyPkg.dec
[Defines]
  DEC_SPECIFICATION = 0x0001001B
  PACKAGE_NAME      = MyPkg
  PACKAGE_GUID      = XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX  # uuidgen 生成

[Includes]
  Include                  # 公开头文件目录

[LibraryClasses]
  MyPlatformLib|Include/Library/MyPlatformLib.h  # 声明 Library Class

[Protocols]
  gMyProtocolGuid = { 0xABCD1234, 0x5678, { ... }}

[PcdsFixedAtBuild]
  gMyPkgTokenSpaceGuid.PcdMemoryBase|0x80000000|UINT64|0x00000001
```

DEC 的作用是让**其他包**知道这个包的存在和它提供什么。其他包的 INF 在 `[Packages]` 中声明 `MyPkg/MyPkg.dec` 后，就能引用其中的 GUID、PCD、Library Class。

## 3. DSC：平台配置

DSC 是构建的"总控制器"——决定平台用哪些库实现、PCD 值是多少、包含哪些模块：

```ini
# MyPkg/MyPkg.dsc
[Defines]
  PLATFORM_NAME                  = MyRiscVPlatform
  PLATFORM_GUID                  = ...
  SUPPORTED_ARCHITECTURES        = RISCV64
  BUILD_TARGETS                  = DEBUG|RELEASE
  FLASH_DEFINITION               = MyPkg/MyPkg.fdf

[LibraryClasses]
  DebugLib|MdePkg/Library/BaseDebugLibSerialPort/BaseDebugLibSerialPort.inf
  # ↑ 库绑定：所有模块的 DebugLib 用"串口输出"实现
  #   如果改成 BaseDebugLibNull → 所有 DEBUG 语句被消除

[LibraryClasses.common.DXE_DRIVER]
  UefiBootServicesTableLib|MdePkg/Library/UefiBootServicesTableLib/...

[PcdsFixedAtBuild]
  gEfiMdePkgTokenSpaceGuid.PcdDebugPrintErrorLevel|0x8000000F

[Components]
  MyPkg/Drivers/ProducerDxe/ProducerDxe.inf
  MyPkg/Drivers/ConsumerDxe/ConsumerDxe.inf
```

LibraryClasses 的绑定是按**模块类型**分的——`[LibraryClasses.common.SEC]` 为 SEC 绑定，`[LibraryClasses.common.DXE_DRIVER]` 为 DXE 驱动绑定。同一个 Library Class（如 SerialPortLib）可以对应不同的 Instance，分别用于 SEC（XIP 版）和 DXE（RAM 版）。

## 4. INF：模块定义

```ini
[Defines]
  INF_VERSION    = 0x00010005
  BASE_NAME      = MyDriver
  FILE_GUID      = ...
  MODULE_TYPE    = DXE_DRIVER       # ① 决定入口函数签名
  ENTRY_POINT    = MyEntryPoint     #    见下表

[Sources]      MyDriver.c
[Packages]     MdePkg/MdePkg.dec   MyPkg/MyPkg.dec
[LibraryClasses]
  UefiDriverEntryPoint             # ② 提供 ImageHandle + SystemTable 的库
  UefiBootServicesTableLib         # ③ 提供 gBS 全局指针
  DebugLib                         # ④ 提供 DEBUG 宏

[Protocols]
  gMyProtocolGuid                   # 声明"我会安装/使用这个 Protocol"

[Depex]  TRUE                       # ⑤ 依赖表达式字节码
```

| MODULE_TYPE | 入口函数签名 | 阶段 |
|-------------|------------|------|
| SEC | 无（汇编入口） | SEC |
| PEIM | `(FileHandle, **PeiServices) → EFI_STATUS` | PEI |
| PEI_CORE | 同上 | PEI |
| DXE_DRIVER | `(ImageHandle, *SystemTable) → EFI_STATUS` | DXE |
| UEFI_DRIVER | 同上 | DXE |
| DXE_RUNTIME_DRIVER | 同上 | DXE/RT |
| UEFI_APPLICATION | 同上 | DXE（Shell 中运行） |

> DXE_DRIVER、UEFI_DRIVER、DXE_RUNTIME_DRIVER 入口签名相同，区别在于：① 可用的 Library Class 绑定不同（DSC 中的 `[LibraryClasses.common.DXE_DRIVER]` / `.DXE_RUNTIME_DRIVER`）；② DXE Runtime Driver 在 ExitBootServices 后仍可运行（通过 Runtime Services）。

## 5. FDF：Flash 布局

```ini
[FD.Main]
  BaseAddress   = 0x20000000        # Flash 物理基址
  Size          = 0x00800000        # 8MB
  BlockSize     = 0x00001000        # 擦除块大小

[FD.Main]
  0x00000000|0x00800000
  FV = CODE                         # 固件卷 CODE 占整个 Flash

[FV.CODE]
  BlockSize     = 0x00001000
  FvNameGuid    = ...

  INF MdeModulePkg/Core/Dxe/DxeMain.inf
  INF MyPkg/Drivers/ProducerDxe/ProducerDxe.inf
  INF MyPkg/Drivers/ConsumerDxe/ConsumerDxe.inf
```

FDF 决定两件事：① Flash 设备的物理分区（`[FD]`），② 每个固件卷（`[FV]`）里放哪些模块的映像。DSC 决定编译，FDF 决定打包——缺一个你的模块就不在最终固件里。

## 6. AutoGen：元数据→C 代码

构建分两个阶段：先编译 `BaseTools`（C 工具：GenFw, GenFfs, GenFds），再通过 Python 构建引擎解析元数据。AutoGen 是其中的核心——它将 INF/DSC 的声明翻译成代码和 Makefile：

```
INF 中的 [LibraryClasses]
  ↓ AutoGen
AutoGen.h:  #include <Library/UefiBootServicesTableLib.h>
           #include <Library/DebugLib.h>

INF 中的 [Pcd]
  ↓ AutoGen
AutoGen.h:  #define _PCD_TOKEN_PcdDebugPrintErrorLevel  0U
           #define _PCD_SIZE_PcdDebugPrintErrorLevel 8
           #define _PCD_GET_MODE_32_PcdDebugPrintErrorLevel ...

INF 中的 [Depex]
  ↓ AutoGen
<Module>.depex: PUSH GUID1 PUSH GUID2 AND END  (字节码嵌入 .efi)
```

Autogen.h 包含 PCD 宏和库头文件引用，Autogen.c 包含 PCD 常量和模块信息，Makefile 定义编译命令。整个过程是自动的——你修改 INF 后重跑 `build`，AutoGen 会重新生成。

---

## 7. 常见构建命令

```bash
build -a RISCV64 -p MyPkg/MyPkg.dsc -t GCC5 -b DEBUG
# = "为 RISCV64 架构，用 MyPkg.dsc 平台，GCC5 工具链，DEBUG 配置，全构建"

build -a RISCV64 -p MyPkg/MyPkg.dsc -t GCC5 -m MyPkg/Drivers/MyDriver/MyDriver.inf
# 同上，但只编译指定模块（开发快速迭代时用）

build clean && build ...  # 全量重构建
```

## 8. 完整示例：UART16550 驱动的四种元数据

以 [05 §3](05-first-driver.md) 的 NS16550 UART 驱动为例，四个文件如何配合：

### MyPkg.dec（包声明——定义"有什么可用"）

```ini
[Defines]
  DEC_SPECIFICATION = 0x0001001B
  PACKAGE_NAME      = MyPkg
  PACKAGE_GUID      = 3F7E8D9A-B1C2-4D5E-6F7A-8B9C0D1E2F3A

[Includes]
  Include                        # 公开头文件路径 (MyPkg/Include/ → MyPkg/Include/Protocol/UartIo.h)

[Protocols]
  gMyProtocolGuid          = { 0xABCD1234, 0x5678, 0x9ABC, { 0xDE, 0xF0, 0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC }}
  gMyUartIoProtocolGuid    = { 0x8A2B1C3D, 0x4E5F, 0x6A7B, { 0x8C, 0x9D, 0x0E, 0x1F, 0x2A, 0x3B, 0x4C, 0x5D }}

[PcdsFixedAtBuild]
  gMyPkgTokenSpaceGuid.PcdUartDefaultBaudRate|115200|UINT32|0x00000001
```

### MyPkg.dsc（平台配置——决定"用什么实现"）

```ini
[Defines]
  PLATFORM_NAME           = MyPlatform
  SUPPORTED_ARCHITECTURES = RISCV64
  BUILD_TARGETS           = DEBUG|RELEASE
  FLASH_DEFINITION        = MyPkg/MyPkg.fdf

[LibraryClasses]
  DebugLib|MdePkg/Library/BaseDebugLibSerialPort/BaseDebugLibSerialPort.inf
  BaseLib|MdePkg/Library/BaseLib/BaseLib.inf
  MemoryAllocationLib|MdePkg/Library/UefiMemoryAllocationLib/UefiMemoryAllocationLib.inf

[LibraryClasses.common.DXE_DRIVER]
  UefiDriverEntryPoint|MdePkg/Library/UefiDriverEntryPoint/UefiDriverEntryPoint.inf
  UefiBootServicesTableLib|MdePkg/Library/UefiBootServicesTableLib/UefiBootServicesTableLib.inf

[PcdsFixedAtBuild]
  gEfiMdePkgTokenSpaceGuid.PcdDebugPrintErrorLevel|0x8000000F  # DEBUG_INFO + 以上

[Components]
  MyPkg/Drivers/HelloWorldDxe/HelloWorldDxe.inf
  MyPkg/Drivers/ProducerDxe/ProducerDxe.inf
  MyPkg/Drivers/ConsumerDxe/ConsumerDxe.inf
  MyPkg/Drivers/Uart16550Dxe/Uart16550Dxe.inf
  MyPkg/Drivers/UartTestDxe/UartTestDxe.inf
```

### Uart16550Dxe.inf（模块定义——"这个模块需要什么"）

```ini
[Defines]
  INF_VERSION    = 0x00010005
  BASE_NAME      = Uart16550Dxe
  MODULE_TYPE    = DXE_DRIVER           # 决定入口签名 (ImageHandle + SystemTable)
  ENTRY_POINT    = Uart16550EntryPoint

[Sources]    Uart16550Dxe.c

[Packages]   MdePkg/MdePkg.dec  MyPkg/MyPkg.dec

[LibraryClasses]
  UefiDriverEntryPoint                   # → 入口点设 gST
  UefiBootServicesTableLib               # → 入口点设 gBS
  DebugLib                               # → DEBUG 宏
  BaseLib                                # → MmioRead8 / MmioWrite8
  MemoryAllocationLib                    # → AllocateZeroPool

[Protocols]
  gEfiPciIoProtocolGuid                  # 消费: 读 BAR、查 Class Code
  gEfiDriverBindingProtocolGuid          # 安装: 声明自己是 DriverBinding 驱动
  gMyUartIoProtocolGuid                  # 安装: 暴露 UART 写/读接口

[Depex]   gEfiPciIoProtocolGuid          # PCI 总线已在位 → 才有 PciIo 可用
```

### MyPkg.fdf（Flash 布局——"这些 .efi 放在 Flash 哪个位置"）

```ini
[FD.Main]
  BaseAddress = 0x20000000 | Size = 0x00800000    # 8MB Flash
  BlockSize    = 0x00001000                       # 4KB erase block

  # 分区: 起始偏移 | 大小
  0x00000000|0x00800000
  FV = CODE                                      # 固件卷 CODE 占整个 Flash

[FV.CODE]
  FvNameGuid  = 7C1E8B3A-9D4F-5A6B-8C9D-0E1F2A3B4C5D
  BlockSize   = 0x00001000      # 必须与 FD BlockSize 一致

  # ── 固件卷中的模块列表（构建系统按此顺序打包） ──
  INF MdeModulePkg/Core/Dxe/DxeMain.inf               # DXE Core 最先
  INF MyPkg/Drivers/HelloWorldDxe/HelloWorldDxe.inf
  INF MyPkg/Drivers/ProducerDxe/ProducerDxe.inf         # 安装 MY_PROTOCOL
  INF MyPkg/Drivers/ConsumerDxe/ConsumerDxe.inf         # 用通知回调等待 MY_PROTOCOL
  INF MyPkg/Drivers/Uart16550Dxe/Uart16550Dxe.inf       # UART 硬件驱动
  INF MyPkg/Drivers/UartTestDxe/UartTestDxe.inf         # 测试: 枚举 UART → 写 "OK\n"
```

**数据流向回顾**：DEC 声明 GUID "存在" → INF 引用 GUID "我要用/我安装了" → DSC 绑定实现在 "用哪个库" → FDF 分配 Flash 位置 "在哪执行"。四个文件缺一不可——DSC 不加 `[Components]` 你的模块不会被编译，FDF 不加 `INF` 编译出的 .efi 不会打包进 ROM。

---

## 9. CI 系统

EDK2 源码仓库使用基于 **Stuart/PyTool** 的 CI 框架（`.pytool/CISettings.py`）在多平台上自动构建并检查 GUID 唯一性、库声明合法性、编码格式等。参与上游开发时，通过插件链式执行 `stuart_setup` → `stuart_update` → `stuart_ci_build` 确保改动不破坏现有平台。

---

**上一篇**：[07-PEI 阶段](./07-pei-phase.md)  
**下一篇**：[09-RISC-V 平台移植实战](./09-riscv-porting.md) — SBI、MMU、ACPI 与完整移植流程
