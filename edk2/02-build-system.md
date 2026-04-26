# EDK2 构建系统深入

> 构建系统是固件开发中"最不有趣但最重要"的部分。理解它，你才能从"改别人的代码"进化到"创建自己的平台"。

## 1. 构建系统全景

EDK2 的构建系统是一个**双层架构**：先用 make 编译构建工具（BaseTools），再用构建工具编译固件。

```
┌───────────────────────────────────────────────────────┐
│                  固件构建流程                           │
│                                                       │
│  1. 环境初始化                                         │
│     $ . edksetup.sh                                   │
│     ├── 设置 WORKSPACE, PYTHON_COMMAND                │
│     ├── source BaseTools/BuildEnv                     │
│     │   ├── 设置 EDK_TOOLS_PATH                      │
│     │   ├── 将 BaseTools/Bin 添加到 PATH              │
│     │   └── 复制 Conf/*.template → Conf/*.txt        │
│     └── 保存配置到 Conf/BuildEnv.sh                   │
│                                                       │
│  2. 编译 BaseTools（首次）                              │
│     $ make -C BaseTools                               │
│     ├── 编译 C 工具 (GenFv, GenFfs, VfrCompile...)   │
│     └── 输出到 BaseTools/Source/C/bin/               │
│                                                       │
│  3. 执行构建                                           │
│     $ build -p <DSC> -a <ARCH> -b <TARGET> -t <TOOL> │
│     ├── 解析 Conf/target.txt, Conf/tools_def.txt     │
│     ├── 解析 DSC/DEC/INF/FDF 元数据文件               │
│     ├── AutoGen: 自动生成 .c/.h/Makefile              │
│     ├── 调用 make 执行编译                             │
│     ├── GenFds: 生成固件映像 (FD/FV/Capsule)          │
│     └── 生成构建报告                                   │
└───────────────────────────────────────────────────────┘
```

## 2. BaseTools 详解

### 2.1 目录结构

```
BaseTools/
├── Bin/                     # 预编译二进制工具
├── BinWrappers/
│   ├── PosixLike/           # Linux/macOS 工具包装脚本
│   │   ├── build            # → build.py
│   │   ├── GenFds           # → GenFds.py
│   │   └── GenFv            # → GenFv C 工具
│   └── WindowsLike/         # Windows .bat 包装脚本
├── Conf/
│   ├── build_rule.template  # 构建规则模板
│   ├── target.template      # 构建目标配置模板
│   └── tools_def.template   # 工具链定义模板
├── Plugin/                  # 构建插件
│   ├── LinuxGccToolChain/   # Linux GCC 工具链
│   └── WindowsVsToolChain/  # Windows VS 工具链
├── Scripts/                 # 辅助脚本
├── Source/
│   ├── C/                   # C 语言构建工具
│   │   ├── GenFv/           # 固件卷生成
│   │   ├── GenFfs/          # FFS 文件生成
│   │   ├── GenSec/          # Section 生成
│   │   ├── GenFw/           # 固件生成（含 ELF→COFF 转换）
│   │   ├── GenCrc32/        # CRC32 生成
│   │   ├── VfrCompile/      # VFR 表单编译器
│   │   ├── LzmaCompress/    # LZMA 压缩
│   │   ├── BrotliCompress/  # Brotli 压缩
│   │   ├── TianoCompress/   # Tiano 压缩
│   │   ├── EfiRom/          # EFI ROM 生成
│   │   ├── DevicePath/      # 设备路径工具
│   │   └── VolInfo/         # 卷信息工具
│   └── Python/              # Python 构建引擎
│       ├── build/           # ★ 核心 build 命令
│       ├── AutoGen/         # 自动代码生成
│       ├── GenFds/          # FD 生成子系统
│       ├── Workspace/       # 元数据解析 (DSC/DEC/INF)
│       └── Common/          # 公共工具库
├── BuildEnv                 # Unix 环境初始化脚本
└── Edk2ToolsBuild.py        # PyTool 方式编译 BaseTools
```

### 2.2 C 工具功能详解

| 工具 | 输入 | 输出 | 核心功能 |
|------|------|------|----------|
| **GenFv** | FFS 文件 | .fv 固件卷 | 将多个 FFS 文件组装成固件卷，添加 FV 头部 |
| **GenFfs** | Section 文件 | .ffs 文件 | 将多个 Section 组装成 FFS 文件，添加文件头部 |
| **GenSec** | 原始数据 | .section | 将数据封装成 Section，支持压缩和 GUIDed 封装 |
| **GenFw** | ELF/COFF | .efi/.bin | 固件镜像生成，含 ELF→COFF 转换、重定位处理 |
| **GenCrc32** | 任意文件 | 带校验和文件 | 计算 CRC32 校验和 |
| **VfrCompile** | .vfr 文件 | .h/.i | 编译 VFR 表单定义为字节码 |
| **LzmaCompress** | 任意文件 | .lzma | LZMA 压缩（高压缩比） |
| **BrotliCompress** | 任意文件 | .brotli | Brotli 压缩 |
| **TianoCompress** | 任意文件 | .tiano | Tiano 自定义压缩 |

**工具链的数据流**：

```
源码 (.c/.h/.S)
  → 编译器 → .o 目标文件
  → 链接器 → .dll/.so (PE/COFF 或 ELF)
  → GenFw → .efi (UEFI 可执行文件)
  → GenSec → .section (封装为 Section，可选压缩)
  → GenFfs → .ffs (封装为 FFS 文件)
  → GenFv → .fv (组装为固件卷)
  → 最终 → .fd (完整固件映像)
```

### 2.3 Python 构建引擎

Python 构建引擎是整个构建系统的"大脑"，核心模块：

```
build.py (入口)
├── AutoGen/
│   ├── WorkspaceAutoGen     # 工作区级代码生成
│   ├── PlatformAutoGen      # 平台级代码生成
│   ├── ModuleAutoGen        # 模块级代码生成
│   ├── AutoGenWorker        # 多进程并行生成
│   ├── GenMake              # Makefile 生成
│   ├── GenC                 # C 代码生成（PCD、ModuleInfo 等）
│   └── GenDepex             # 依赖表达式生成
├── Workspace/
│   └── WorkspaceDatabase    # 元数据数据库（DSC/DEC/INF 解析）
├── GenFds/
│   └── GenFds               # 固件描述文件生成
└── BuildReport              # 构建报告生成
```

**AutoGen 生成的关键文件**：

| 生成文件 | 位置 | 用途 |
|----------|------|------|
| `AutoGen.h` | `Build/<Platform>/<Module>/DEBUG_GCC5/` | 模块级别的宏和类型定义 |
| `AutoGen.c` | 同上 | PCD 初始化代码、ModuleInfo |
| `Makefile` | 同上 | 模块的编译规则 |
| `<Module>.depex` | 同上 | 依赖表达式字节码 |

## 3. 配置文件详解

### 3.1 target.txt — 构建目标配置

文件位置：`Conf/target.txt`（由 `BaseTools/Conf/target.template` 生成）

```ini
# 要构建的平台 DSC 文件
ACTIVE_PLATFORM       = OvmfPkg/RiscVVirt/RiscVVirtQemu.dsc

# 构建目标类型: DEBUG, RELEASE, NOOPT
TARGET                = DEBUG

# 目标架构: IA32, X64, AARCH64, RISCV64, LOONGARCH64
TARGET_ARCH           = RISCV64

# 工具链定义文件
TOOL_CHAIN_CONF       = Conf/tools_def.txt

# 使用的工具链标签
TOOL_CHAIN_TAG        = GCC5

# 构建规则文件
BUILD_RULE_CONF       = Conf/build_rule.txt

# 并发线程数
MAX_CONCURRENT_THREAD_NUMBER = 8
```

**优先级**：命令行参数 > target.txt > DSC 文件

### 3.2 tools_def.txt — 工具链定义

文件位置：`Conf/tools_def.txt`（由 `BaseTools/Conf/tools_def.template` 生成）

这是 EDK2 构建系统中最庞大的配置文件，定义了所有支持的编译器工具链。

**配置格式**：

```
TARGET_TOOLCHAIN_ARCH_COMMANDTYPE_ATTRIBUTE = <value>
```

例如：
```
DEBUG_GCC5_RISCV64_CC_PATH = /usr/bin/riscv64-unknown-elf-gcc
DEBUG_GCC5_RISCV64_CC_FLAGS = -g -Os -Wall -Werror ...
```

**支持的工具链**：

| 工具链标签 | 编译器 | 平台 |
|-----------|--------|------|
| GCC5 | GCC (带 LTO) | Linux/macOS |
| GCCNOLTO | GCC (无 LTO) | Linux/macOS |
| CLANGPDB | Clang (PDB 调试) | 全平台 |
| CLANGDWARF | Clang (DWARF 调试) | 全平台 |
| VS2022 | Visual Studio 2022 | Windows |
| VS2019 | Visual Studio 2019 | Windows |
| XCODE5 | Xcode | macOS |

**RISC-V 交叉编译工具链配置**：

```bash
# 安装 RISC-V 交叉编译工具链
sudo apt install gcc-riscv64-unknown-elf

# 或使用自定义路径
export GCC5_RISCV64_PREFIX=/opt/riscv/bin/riscv64-unknown-elf-
```

### 3.3 build_rule.txt — 构建规则

文件位置：`Conf/build_rule.txt`（由 `BaseTools/Conf/build_rule.template` 生成）

定义了如何将各种源文件编译为目标文件：

```ini
[C-Code-File]
    <InputFile>
        ?.c
    <OutputFile>
        $(OUTPUT_DIR)(+)${s_dir}(+)${f_base}.o
    <Command>
        "$(CC)" $(CC_FLAGS) -c -o ${dst} ${src}
```

支持的文件类型：C-Code-File, Assembly-Code-File, Vfr-Code-File, Unicode-Text-File 等。

## 4. build 命令详解

### 4.1 常用参数

```bash
build [选项]

必需参数：
  -p, --platform=FILE     平台 DSC 文件
  -a, --arch=ARCH         目标架构 (IA32/X64/AARCH64/RISCV64/LOONGARCH64)
  -b, --buildtarget=TYPE  构建目标 (DEBUG/RELEASE/NOOPT)
  -t, --tagname=TOOL      工具链标签 (GCC5/CLANGPDB/VS2022)

可选参数：
  -m, --module=FILE       仅构建指定模块 INF
  -n, --thread=NUM        并发线程数
  -D, --define=MACRO      宏定义 (NAME[=VALUE])
  --pcd=PCD               命令行设置 PCD (TokenSpace.PcdName=Value)
  --hash                  启用基于哈希的构建缓存
  -v, --verbose           详细输出
  -s, --silent            静默模式
  -k, --skip-autogen      跳过 AutoGen

构建目标：
  all                     完整构建（默认）
  genc                    仅生成 C 代码
  genmake                 仅生成 Makefile
  modules                 编译所有模块
  fds                     生成固件映像
  clean                   清理构建产物
  cleanall                清理所有（含 AutoGen）
  run                     构建并运行（仅 EmulatorPkg）
```

### 4.2 典型构建命令

```bash
# 构建 QEMU RISC-V UEFI 固件
build -p OvmfPkg/RiscVVirt/RiscVVirtQemu.dsc \
      -a RISCV64 \
      -b DEBUG \
      -t GCC5 \
      -n 8

# 仅构建某个模块
build -p OvmfPkg/RiscVVirt/RiscVVirtQemu.dsc \
      -a RISCV64 \
      -b DEBUG \
      -t GCC5 \
      -m MdeModulePkg/Universal/BdsDxe/BdsDxe.inf

# 带宏定义的构建
build -p OvmfPkg/RiscVVirt/RiscVVirtQemu.dsc \
      -a RISCV64 -b DEBUG -t GCC5 \
      -D SECURE_BOOT_ENABLE \
      -D TPM2_ENABLE

# 命令行设置 PCD
build -p OvmfPkg/RiscVVirt/RiscVVirtQemu.dsc \
      -a RISCV64 -b DEBUG -t GCC5 \
      --pcd "gUefiCpuPkgTokenSpaceGuid.PcdCpuRiscVMmuMaxSatpMode=9"
```

### 4.3 构建输出目录结构

```
Build/
└── RiscVVirtQemu/                    # 平台名
    └── DEBUG_GCC5/                   # TARGET_TOOLCHAIN
        ├── FV/                       # 固件卷输出
        │   ├── RISCV_VIRT.fd         # ★ 完整固件映像
        │   ├── RISCV_VIRT_CODE.fd    # CODE FD
        │   ├── RISCV_VIRT_VARS.fd    # VARS FD
        │   └── Ffs/                  # 各模块的 FFS 文件
        ├── MdeModulePkg/             # 各包的模块输出
        │   └── Universal/
        │       └── BdsDxe/
        │           └── BdsDxe/
        │               ├── DEBUG/    # 目标文件
        │               ├── AutoGen.h # 自动生成的头文件
        │               ├── AutoGen.c # 自动生成的 C 文件
        │               ├── Makefile  # 模块 Makefile
        │               └── OUTPUT/   # 最终输出 (.efi, .depex)
        └── BuildReport.txt           # 构建报告
```

## 5. 元数据文件格式

### 5.1 DEC 文件（包声明）

DEC 文件定义包的公共接口，是包的"头文件"。

```ini
[Defines]
  DEC_SPECIFICATION   = 0x00010005
  PACKAGE_NAME        = MdePkg
  PACKAGE_GUID        = 1E73767F-8F52-4603-AEB4-F29B510B6766
  PACKAGE_VERSION     = 1.08

[Includes]
  Include                              # 通用包含路径

[Includes.RISCV64]
  Include/RiscV64                      # RISC-V 特定包含路径

[LibraryClasses]
  BaseLib|Include/Library/BaseLib.h    # 库类名|头文件路径

[LibraryClasses.RISCV64]
  BaseRiscVSbiLib|Include/Library/BaseRiscVSbiLib.h

[Guids]
  gEfiEventExitBootServicesGuid = { 0x2AB5D321, 0xDE8F, 0x4828, ... }

[Ppis]
  gEfiPeiMemoryDiscoveredPpiGuid = { 0xF894643D, 0xC449, 0x42D1, ... }

[Protocols]
  gEfiLoadedImageProtocolGuid = { 0x5B1B31A1, 0x9562, 0x11D2, ... }

[PcdsFeatureFlag]
  gEfiMdePkgTokenSpaceGuid.PcdUgaConsumeSupport|TRUE|BOOLEAN|0x00010139

[PcdsFixedAtBuild]
  gEfiMdePkgTokenSpaceGuid.PcdMaximumUnicodeStringLength|1000000|UINT32|0x00000001
```

### 5.2 DSC 文件（平台描述）

DSC 文件定义如何构建一个平台，是"构建配置"的核心。

```ini
[Defines]
  DSC_SPECIFICATION    = 0x00010005
  PLATFORM_NAME        = RiscVVirtQemu
  PLATFORM_GUID        = ...
  PLATFORM_VERSION     = 1.0
  DSC_NAME             = RiscVVirtQemu
  SUPPORTED_ARCHITECTURES = RISCV64
  BUILD_TARGETS        = DEBUG|RELEASE|NOOPT
  SKUID_IDENTIFIER     = DEFAULT
  FLASH_DEFINITION     = OvmfPkg/RiscVVirt/RiscVVirtQemu.fdf

[BuildOptions]
  # 全局编译选项
  GCC:*_*_*_CC_FLAGS = -Wno-unused-but-set-variable

[LibraryClasses]
  # 库类绑定（接口 → 实现）
  BaseLib|MdePkg/Library/BaseLib/BaseLib.inf
  BaseMemoryLib|MdePkg/Library/BaseMemoryLibRepStr/BaseMemoryLibRepStr.inf
  DebugLib|MdePkg/Library/UefiDebugLibConOut/UefiDebugLibConOut.inf
  PcdLib|MdePkg/Library/BasePcdLibNull/BasePcdLibNull.inf

[LibraryClasses.common.PEIM]
  # PEI 阶段专用库绑定
  MemoryAllocationLib|MdePkg/Library/PeiMemoryAllocationLib/PeiMemoryAllocationLib.inf
  HobLib|MdePkg/Library/PeiHobLib/PeiHobLib.inf

[LibraryClasses.common.DXE_DRIVER]
  # DXE 阶段专用库绑定
  MemoryAllocationLib|MdePkg/Library/UefiMemoryAllocationLib/UefiMemoryAllocationLib.inf
  HobLib|MdePkg/Library/DxeHobLib/DxeHobLib.inf

[PcdsFixedAtBuild]
  # 平台特定的 PCD 值
  gEfiMdePkgTokenSpaceGuid.PcdDebugPrintErrorLevel|0x80000047

[Components]
  # 要构建的模块列表
  MdeModulePkg/Core/Pei/PeiCore.inf
  MdeModulePkg/Core/Dxe/DxeMain.inf
  MdeModulePkg/Universal/BdsDxe/BdsDxe.inf
  UefiCpuPkg/CpuDxeRiscV64/CpuDxeRiscV64.inf
```

### 5.3 INF 文件（模块定义）

INF 文件描述一个模块的所有信息。

```ini
[Defines]
  INF_VERSION          = 0x00010005
  BASE_NAME            = BdsDxe
  FILE_GUID            = 634337E7-5E5B-4E7A-8B70-939C1C67ECD0
  MODULE_TYPE          = DXE_DRIVER
  VERSION_STRING       = 1.0
  ENTRY_POINT          = BdsInitialize

[Sources]
  BdsEntry.c
  Bds.h
  FrontPage.c
  FrontPage.h
  Language.c
  LanguageData.c

[Sources.RISCV64]
  # RISC-V 特定源文件（如果有）

[Packages]
  MdePkg/MdePkg.dec
  MdeModulePkg/MdeModulePkg.dec

[LibraryClasses]
  BaseLib
  BaseMemoryLib
  DebugLib
  UefiLib
  UefiBootServicesTableLib
  UefiRuntimeServicesTableLib
  DevicePathLib
  PcdLib

[Pcd]
  gEfiMdePkgTokenSpaceGuid.PcdPlatformBootTimeOut

[Protocols]
  gEfiBdsArchProtocolGuid                  ## PRODUCES
  gEfiLoadedImageProtocolGuid              ## CONSUMES
  gEfiSimpleFileSystemProtocolGuid         ## CONSUMES

[Depex]
  TRUE
```

**MODULE_TYPE 取值**：

| 类型 | 阶段 | 入口点宏 |
|------|------|----------|
| SEC | SEC | 无（汇编入口） |
| PEIM | PEI | `_ModuleEntryPoint` |
| PEI_CORE | PEI | `_ModuleEntryPoint` |
| DXE_DRIVER | DXE | `_ModuleEntryPoint` |
| DXE_CORE | DXE | `_ModuleEntryPoint` |
| DXE_RUNTIME_DRIVER | DXE/RT | `_ModuleEntryPoint` |
| DXE_SAL_DRIVER | DXE/RT (IA64) | `_ModuleEntryPoint` |
| DXE_SMM_DRIVER | SMM | `_ModuleEntryPoint` |
| UEFI_DRIVER | DXE | `_ModuleEntryPoint` |
| UEFI_APPLICATION | DXE | `_ModuleEntryPoint` |
| MM_STANDALONE | MM | `_ModuleEntryPoint` |
| MM_CORE_STANDALONE | MM | `_ModuleEntryPoint` |

### 5.4 FDF 文件（固件描述）

FDF 文件定义 Flash 布局和固件卷内容。

```ini
[Defines]
  # Flash 布局常量
  DEFINE CODE_BASE_ADDRESS   = 0x20000000
  DEFINE CODE_SIZE           = 0x00800000
  DEFINE VARS_BASE_ADDRESS   = 0x22000000
  DEFINE VARS_SIZE           = 0x000C0000

[FV.FvRecovery]
  # PEI 阶段固件卷
  FvAlignment        = 16
  ERASE_POLARITY     = 1
  MEMORY_MAPPED      = TRUE
  STICKY_WRITE       = TRUE
  LOCK_CAP           = TRUE
  LOCK_STATUS        = TRUE
  WRITE_DISABLED_CAP = TRUE
  WRITE_ENABLED_CAP  = TRUE

  # 包含的模块
  INF MdeModulePkg/Core/Pei/PeiCore.inf
  INF UefiCpuPkg/SecCore/SecCore.inf
  INF MdeModulePkg/Universal/PCD/Pei/Pcd.inf
  INF MdeModulePkg/Core/DxeIplPeim/DxeIpl.inf

[FV.FvMain]
  # DXE 阶段固件卷
  FvAlignment        = 16

  INF MdeModulePkg/Core/Dxe/DxeMain.inf
  INF MdeModulePkg/Universal/BdsDxe/BdsDxe.inf
  INF UefiCpuPkg/CpuDxeRiscV64/CpuDxeRiscV64.inf

[FD.RISCV_VIRT]
  # 完整固件映像定义
  BaseAddress   = $(CODE_BASE_ADDRESS)
  Size          = $(CODE_SIZE)
  ErasePolarity = 1

  # 区域定义
  0x00000000|$(FV_SIZE)
  FV = FvRecovery
  FV = FvMain
```

## 6. AutoGen 机制

AutoGen 是 EDK2 构建系统最精巧的设计——根据元数据文件自动生成代码。

### 6.1 AutoGen.h

每个模块都会生成 `AutoGen.h`，包含：

```c
// 模块信息
#define MODULE_NAME   "BdsDxe"
#define MODULE_GUID   0x634337E7, 0x5E5B, 0x4E7A, ...

// PCD 宏定义（编译时 PCD 直接展开为常量）
#define _PCD_VALUE_PcdPlatformBootTimeOut  10
#define FixedPcdGet32(TokenName)  _PCD_VALUE_##TokenName

// 包含路径
#include <Base.h>
#include <Library/BaseLib.h>
// ... 所有 [LibraryClasses] 中声明的库类头文件
```

### 6.2 AutoGen.c

```c
// PCD 初始化（运行时 PCD 的初始值）
GLOBAL_REMOVE_IF_UNREFERENCED const UINT32 _gPcd_FixedAtBuild_PcdPlatformBootTimeOut = 10;

// 模块信息字符串
GLOBAL_REMOVE_IF_UNREFERENCED CHAR8 *gEfiCallerBaseName = "BdsDxe";

// 入口点注册
EFI_STATUS EFIAPI _ModuleEntryPoint(...);
```

### 6.3 依赖表达式生成

INF 文件中的 `[Depex]` 段被编译为字节码：

```
源码: gEfiPciRootBridgeIoProtocolGuid AND gEfiCpuArchProtocolGuid
字节码: PUSH <GUID1> PUSH <GUID2> AND END
```

DXE Dispatcher 使用栈式求值器执行这些字节码来决定驱动调度顺序。

## 7. 实战：构建 RISC-V QEMU 固件

### 7.1 环境准备

```bash
# 安装依赖
sudo apt install build-essential uuid-dev iasl git \
    python3 python3-distutils python3-setuptools \
    qemu-system-misc

# 安装 RISC-V 交叉编译工具链
sudo apt install gcc-riscv64-unknown-elf

# 克隆源码
git clone https://github.com/tianocore/edk2.git
cd edk2
git submodule update --init
```

### 7.2 构建步骤

```bash
# 1. 初始化环境
source edksetup.sh

# 2. 编译 BaseTools（首次）
make -C BaseTools

# 3. 构建 RISC-V QEMU 固件
build -p OvmfPkg/RiscVVirt/RiscVVirtQemu.dsc \
      -a RISCV64 \
      -b DEBUG \
      -t GCC5 \
      -n $(nproc)

# 4. 查看输出
ls Build/RiscVVirtQemu/DEBUG_GCC5/FV/RISCV_VIRT.fd
```

### 7.3 运行固件

```bash
# 使用 QEMU 运行
qemu-system-riscv64 \
    -machine virt \
    -m 2048 \
    -bios none \
    -pflash Build/RiscVVirtQemu/DEBUG_GCC5/FV/RISCV_VIRT_CODE.fd \
    -pflash Build/RiscVVirtQemu/DEBUG_GCC5/FV/RISCV_VIRT_VARS.fd \
    -nographic

# 或使用默认的 OpenSBI + UEFI 组合
qemu-system-riscv64 \
    -machine virt \
    -m 2048 \
    -bios default \
    -pflash Build/RiscVVirtQemu/DEBUG_GCC5/FV/RISCV_VIRT_CODE.fd \
    -pflash Build/RiscVVirtQemu/DEBUG_GCC5/FV/RISCV_VIRT_VARS.fd \
    -nographic
```

### 7.4 调试固件

```bash
# 使用 GDB 调试
riscv64-unknown-elf-gdb Build/RiscVVirtQemu/DEBUG_GCC5/MdeModulePkg/Core/Dxe/DxeMain/DxeMain/DEBUG/DxeCore.dll

# GDB 连接 QEMU（QEMU 端加 -s -S 参数）
qemu-system-riscv64 -machine virt -m 2048 \
    -bios default \
    -pflash ... \
    -nographic -s -S

# GDB 端
(gdb) target remote :1234
(gdb) break DxeMain
(gdb) continue
```

## 8. CI 系统

EDK2 使用基于 Stuart/PyTool 的 CI 系统，配置在 `.pytool/CISettings.py` 中。

### 8.1 CI 执行流程

```bash
# 安装 PyTool
pip install edk2-pytool-extensions edk2-pytool-library

# 环境设置
stuart_setup -c .pytool/CISettings.py

# 依赖更新
stuart_update -c .pytool/CISettings.py

# CI 构建
stuart_ci_build -c .pytool/CISettings.py \
    -t DEBUG -a RISCV64 \
    -p OvmfPkg/RiscVVirt
```

### 8.2 CI 插件

| 插件 | 功能 |
|------|------|
| CompilerPlugin | 编译测试 |
| DscCompleteCheck | DSC 完整性检查 |
| GuidCheck | GUID 唯一性检查 |
| DependencyCheck | 跨包依赖检查 |
| LibraryClassCheck | 库类声明检查 |
| LicenseCheck | 许可证检查 |
| SpellCheck | 拼写检查 |
| EccCheck | 编码标准检查 |
| UncrustifyCheck | 代码格式检查 |

---

**上一篇**：[01-architecture.md](01-architecture.md) — 架构与核心概念
**下一篇**：[03-module-development.md](03-module-development.md) — 模块开发实战
