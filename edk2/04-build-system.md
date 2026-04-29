# EDK2 构建系统深入

> 构建系统是固件开发中"最不有趣但最重要"的部分。理解它，你才能从"改别人的代码"进化到"创建自己的平台"。

### 关键术语

| 缩写 | 全称 | 含义 |
|------|------|------|
| DEC | Package Declaration | 包声明文件，定义包的公共接口 |
| DSC | Platform Description | 平台描述文件，定义构建配置 |
| INF | Information | 模块定义文件 |
| FDF | Flash Description File | 固件描述文件，定义 Flash 布局 |
| PCD | Platform Configuration Database | 平台配置数据库 |
| FV | Firmware Volume | 固件卷 |
| FFS | Firmware File System | 固件文件系统 |
| FD | Firmware Device | 完整固件映像 |
| AutoGen | Automatic Generation | 自动代码生成机制 |
| DEPEX | Dependency Expression | 依赖表达式 |

---

## 1. 构建系统全景

EDK2 的构建系统是一个**双层架构**：先用 make 编译构建工具（BaseTools），再用构建工具编译固件。

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#ECECFF", "primaryTextColor": "#333333", "primaryBorderColor": "#9370DB", "lineColor": "#666666", "secondaryColor": "#ffffde", "secondaryBorderColor": "#aaaa33", "tertiaryColor": "#f0f0f0", "fontFamily": "\"trebuchet ms\", verdana, arial, sans-serif"}}}%%
flowchart TD
    subgraph EnvInit["1. 环境初始化"]
        SourceEdk["source edksetup.sh"] --> SetWorkspace["设置 WORKSPACE"]
        SetWorkspace --> SourceBuildEnv["source BaseTools/BuildEnv"]
        SourceBuildEnv --> SetToolsPath["设置 EDK_TOOLS_PATH"]
        SetToolsPath --> AddPath["添加 BaseTools/Bin 到 PATH"]
        AddPath --> CopyTemplates["复制 Conf/*.template → Conf/*.txt"]
        CopyTemplates --> SaveConfig["保存配置到 BuildEnv.sh"]
    end

    subgraph BuildTools["2. 编译 BaseTools（首次）"]
        MakeBaseTools["make -C BaseTools"] --> CompileCTools["编译 C 工具"]
        CompileCTools --> OutputBin["输出到 BaseTools/Source/C/bin/"]
    end

    subgraph RunBuild["3. 执行构建"]
        BuildCmd["build 命令"] --> ParseConf["解析 target.txt, tools_def.txt"]
        ParseConf --> ParseMeta["解析 DSC/DEC/INF/FDF 元数据"]
        ParseMeta --> AutoGenCode["AutoGen 生成 .c/.h/Makefile"]
        AutoGenCode --> CallMake["调用 make 执行编译"]
        CallMake --> GenFdsImg["GenFds 生成 FD/FV 映像"]
        GenFdsImg --> GenReport["生成构建报告"]
    end

    EnvInit -->|首次或环境变更| BuildTools
    BuildTools --> RunBuild

    classDef phase1 fill:#d4edda,stroke:#28a745,color:#155724,stroke-width:2px
    classDef phase2 fill:#d1ecf1,stroke:#17a2b8,color:#0c5460,stroke-width:2px
    classDef phase3 fill:#fff3cd,stroke:#ffc107,color:#856404,stroke-width:2px
    class EnvInit phase1
    class BuildTools phase2
    class RunBuild phase3
```

> **设计背景 — 为什么是双层架构？** BaseTools 中的 C 工具（如 GenFv、VfrCompile）需要先编译才能使用，而这些工具的编译使用标准的 make 构建系统。固件的编译则使用 BaseTools 提供的 Python 构建引擎。这种分离让 BaseTools 可以独立更新，也避免了"鸡生蛋"问题——构建工具本身不需要 EDK2 的构建系统来编译。

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

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#ECECFF", "primaryTextColor": "#333333", "primaryBorderColor": "#9370DB", "lineColor": "#666666", "secondaryColor": "#ffffde", "secondaryBorderColor": "#aaaa33", "tertiaryColor": "#f0f0f0", "fontFamily": "\"trebuchet ms\", verdana, arial, sans-serif"}}}%%
flowchart LR
    SourceCode[/"源码 .c/.h/.S"/] --> ObjectFile["编译器 → .o 目标文件"]
    ObjectFile --> LinkedImage["链接器 → .dll/.so<br/>PE/COFF 或 ELF"]
    LinkedImage --> EfiFile["GenFw → .efi<br/>UEFI 可执行文件"]
    EfiFile --> SectionFile["GenSec → .section<br/>封装为 Section · 可选压缩"]
    SectionFile --> FfsFile["GenFfs → .ffs<br/>封装为 FFS 文件"]
    FfsFile --> FvImage["GenFv → .fv<br/>组装为固件卷"]
    FvImage --> FdImage["最终 → .fd<br/>完整固件映像"]

    classDef input fill:#d1ecf1,stroke:#17a2b8,color:#0c5460,stroke-width:2px
    classDef process fill:#cce5ff,stroke:#007bff,color:#004085,stroke-width:2px
    classDef output fill:#f8d7da,stroke:#dc3545,color:#721c24,stroke-width:2px
    class SourceCode input
    class ObjectFile,LinkedImage,EfiFile,SectionFile,FfsFile,FvImage process
    class FdImage output
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
| `AutoGen.h` | `Build/<Platform>/<Module>/DEBUG_GCC/` | 模块级别的宏和类型定义 |
| `AutoGen.c` | 同上 | PCD 初始化代码、ModuleInfo |
| `Makefile` | 同上 | 模块的编译规则 |
| `<Module>.depex` | 同上 | 依赖表达式字节码 |

> **设计背景 — 为什么需要 AutoGen？** EDK2 的元数据文件（DSC/DEC/INF）声明了模块的依赖、PCD 值、库绑定等信息，但 C 编译器不理解这些声明式格式。AutoGen 将元数据转换为 C 代码和 Makefile：`AutoGen.h` 包含 PCD 宏定义和库头文件包含；`AutoGen.c` 包含 PCD 初始值和模块信息字符串；`Makefile` 定义编译规则。这种"声明式元数据 + 自动代码生成"的模式让开发者只需关注"要构建什么"，而不需要手动编写样板代码。

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
TOOL_CHAIN_TAG        = GCC

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
DEBUG_GCC_RISCV64_CC_PATH = /usr/bin/riscv64-unknown-elf-gcc
DEBUG_GCC_RISCV64_CC_FLAGS = -g -Os -Wall -Werror ...
```

**支持的工具链**：

| 工具链标签 | 编译器 | 平台 |
|-----------|--------|------|
| GCC | GCC | Linux/macOS |
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
export GCC_RISCV64_PREFIX=/opt/riscv/bin/riscv64-unknown-elf-
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
  -t, --tagname=TOOL      工具链标签 (GCC/CLANGPDB/VS2022)

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
  genmake                仅生成 Makefile
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
      -t GCC \
      -n 8

# 仅构建某个模块
build -p OvmfPkg/RiscVVirt/RiscVVirtQemu.dsc \
      -a RISCV64 \
      -b DEBUG \
      -t GCC \
      -m MdeModulePkg/Universal/BdsDxe/BdsDxe.inf

# 带宏定义的构建
build -p OvmfPkg/RiscVVirt/RiscVVirtQemu.dsc \
      -a RISCV64 -b DEBUG -t GCC \
      -D SECURE_BOOT_ENABLE \
      -D TPM2_ENABLE

# 命令行设置 PCD
build -p OvmfPkg/RiscVVirt/RiscVVirtQemu.dsc \
      -a RISCV64 -b DEBUG -t GCC \
      --pcd "gUefiCpuPkgTokenSpaceGuid.PcdCpuRiscVMmuMaxSatpMode=9"
```

### 4.3 构建输出目录结构

```
Build/
└── RiscVVirtQemu/                    # 平台名
    └── DEBUG_GCC/                    # TARGET_TOOLCHAIN
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

> **设计背景 — DEC 的"头文件"角色**：DEC 文件与 C 语言的 `.h` 文件类比非常贴切。C 的 `.h` 声明了函数签名和类型，调用者只需要包含 `.h` 就能编译通过，不需要知道实现。同样，DEC 声明了包的公共接口（库类、GUID、PCD），其他包的模块只需要在 INF 中引用这个 DEC，就能使用这些接口。具体实现由 DSC 中的库绑定决定。这种"接口与实现分离"是 EDK2 包化管理的核心。

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

> **设计背景 — DSC 的"构建配置"角色**：DSC 文件回答了"如何构建这个平台"的所有问题：使用哪些库实现？PCD 值是什么？编译选项是什么？包含哪些模块？同一个包的模块可以通过不同的 DSC 文件构建出不同的平台配置。例如，MdeModulePkg 的模块在 OVMF 的 DSC 和真实硬件的 DSC 中使用不同的库绑定和 PCD 值。

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

> **设计背景 — FDF 的"链接脚本 + 分区表"角色**：FDF 做两件事：定义 Flash 的物理布局（哪些地址放什么）和指定模块放入哪个固件卷。这类似于嵌入式开发中的链接脚本（定义内存布局）和分区表（定义 Flash 分区）的组合。FDF 中的 `[FD]` 段定义完整的固件映像，`[FV]` 段定义固件卷的内容。构建系统根据 FDF 调用 GenFds 工具生成最终的 `.fd` 文件。

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
    python3 python3-venv \
    qemu-system-misc

# 安装 RISC-V 交叉编译工具链
sudo apt install gcc-riscv64-unknown-elf

# 克隆源码
git clone https://github.com/tianocore/edk2.git
cd edk2
git submodule update --init
```

> **注意**：`python3-distutils` 在 Python 3.12+ 中已被移除。如果你使用 Python 3.12 或更高版本，EDK2 的最新版本已经不再依赖 `distutils`。如果遇到相关问题，请确保使用最新版本的 EDK2 源码。

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
      -t GCC \
      -n $(nproc)

# 4. 查看输出
ls Build/RiscVVirtQemu/DEBUG_GCC/FV/RISCV_VIRT_CODE.fd
```

### 7.3 运行固件

```bash
# 填充 Flash 映像到 32MB（QEMU virt 要求）
truncate -s 32M Build/RiscVVirtQemu/DEBUG_GCC/FV/RISCV_VIRT_CODE.fd
truncate -s 32M Build/RiscVVirtQemu/DEBUG_GCC/FV/RISCV_VIRT_VARS.fd

# 使用 QEMU 运行（带 OpenSBI）
qemu-system-riscv64 \
    -machine virt \
    -m 256M \
    -bios default \
    -drive if=pflash,format=raw,file=Build/RiscVVirtQemu/DEBUG_GCC/FV/RISCV_VIRT_CODE.fd,readonly=on \
    -drive if=pflash,format=raw,file=Build/RiscVVirtQemu/DEBUG_GCC/FV/RISCV_VIRT_VARS.fd \
    -nographic

# 不使用 OpenSBI（UEFI 直接作为 payload）
qemu-system-riscv64 \
    -machine virt \
    -m 256M \
    -bios none \
    -drive if=pflash,format=raw,file=Build/RiscVVirtQemu/DEBUG_GCC/FV/RISCV_VIRT_CODE.fd,readonly=on \
    -drive if=pflash,format=raw,file=Build/RiscVVirtQemu/DEBUG_GCC/FV/RISCV_VIRT_VARS.fd \
    -nographic
```

> **设计背景 — `-bios default` vs `-bios none`**：QEMU 的 `-bios` 参数指定 M-mode 固件。`-bios default` 使用 QEMU 自带的 OpenSBI，它会初始化 M-mode 然后跳转到 pflash 指定的 UEFI 固件。`-bios none` 则不加载任何 M-mode 固件，UEFI 需要自行处理 M-mode 初始化（通常不推荐，除非你有自定义的 M-mode 固件）。对于 RiscVVirt 平台，推荐使用 `-bios default`。

### 7.4 调试固件

```bash
# 使用 GDB 调试
riscv64-unknown-elf-gdb Build/RiscVVirtQemu/DEBUG_GCC/MdeModulePkg/Core/Dxe/DxeMain/DxeMain/DEBUG/DxeCore.dll

# QEMU 端加 -s -S 参数（-s = GDB 端口 1234, -S = 启动时暂停）
qemu-system-riscv64 -machine virt -m 256M \
    -bios default \
    -drive if=pflash,format=raw,file=Build/RiscVVirtQemu/DEBUG_GCC/FV/RISCV_VIRT_CODE.fd,readonly=on \
    -drive if=pflash,format=raw,file=Build/RiscVVirtQemu/DEBUG_GCC/FV/RISCV_VIRT_VARS.fd \
    -nographic -s -S

# GDB 端
(gdb) set architecture riscv:rv64
(gdb) target remote :1234
(gdb) break DxeMain
(gdb) continue
```

## 8. CI 系统

EDK2 使用基于 Stuart/PyTool 的 CI 系统，配置在 `.pytool/CISettings.py` 中。

> **设计背景 — 为什么从传统 CI 迁移到 Stuart/PyTool？** EDK2 传统的 `build` 命令是单平台构建工具，而 CI 需要同时验证多个平台和架构的组合。Stuart/PyTool 是 TianoCore 开发的基于 Python 的构建/CI 框架，支持：多平台并行构建、依赖自动管理（nuget/pip）、细粒度的 CI 插件（编码规范、GUID 唯一性检查等）、以及与 Azure Pipelines/GitHub Actions 的集成。

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

**上一篇**：[03-启动流程详解](./03-boot-flow.md) — SEC→PEI→DXE→BDS
**下一篇**：[05-模块开发实战](./05-module-dev.md) — DXE 驱动、Protocol、PEIM
