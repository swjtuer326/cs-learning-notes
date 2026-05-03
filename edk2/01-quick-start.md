# 快速上手：构建与运行

> 30 分钟内，从零开始构建并运行你的第一个 UEFI 固件。先动手，后面再细究原理。

### 关键术语

| 缩写 | 全称 | 含义 |
|------|------|------|
| QEMU | Quick Emulator | 开源硬件模拟器，用于固件开发调试 |
| OVMF | Open Virtual Machine Firmware | QEMU 虚拟机的 UEFI 固件实现 |
| RiscVVirt | RISC-V Virtual Platform | QEMU 的 RISC-V 虚拟机平台 |

---

## 1. 前置知识

| 需要了解 | 参考文档 |
|----------|----------|
| Linux 命令行基础 | — |
| Git 基本操作 | — |

---

## 2. 环境准备

### 2.1 安装依赖

```bash
# Ubuntu/Debian
sudo apt install build-essential uuid-dev iasl git \
    python3 python3-venv \
    qemu-system-misc

# RISC-V 交叉编译工具链
sudo apt install gcc-riscv64-unknown-elf
```

### 2.2 克隆源码

```bash
git clone https://github.com/tianocore/edk2.git
cd edk2
git submodule update --init
```

> `python3-distutils` 在 Python 3.12+ 中已被移除，EDK2 最新版本不再依赖它。如果遇到相关问题，确保使用最新源码。

---

## 3. 首次构建

EDK2 的构建分两部分：先编译一套 C 语言构建工具（BaseTools），再用这套工具编译固件本身。这是双阶段构建——用一套工具链编译另一套工具链的产物。

### 3.1 初始化环境

```bash
source edksetup.sh
```

这条命令做了三件事：

- 设置 `WORKSPACE` 环境变量指向 edk2 根目录
- 将 `BaseTools/BinWrappers/PosixLike` 加入 `PATH`，让后续能直接使用 `build` 命令
- 复制 `Conf/*.template` 到 `Conf/*.txt`（如果不存在），提供初始构建配置

### 3.2 编译构建工具

```bash
make -C BaseTools
```

BaseTools 包含 C 语言编写的二进制工具（GenFv、GenFfs、GenFw 等），负责生成最终的固件映像。这些工具和固件本身用的是不同构建系统，所以需要先单独编译一次。首次构建后，后续不需要重复此步骤。

### 3.3 构建 RISC-V 固件

```bash
build -p OvmfPkg/RiscVVirt/RiscVVirtQemu.dsc \
      -a RISCV64 \
      -b DEBUG \
      -t GCC \
      -n $(nproc)
```

**参数说明**：

| 参数 | 含义 |
|------|------|
| `-p` | 平台 DSC 文件——告诉构建系统"这个平台有哪些模块、用哪些库" |
| `-a` | 目标架构（RISCV64） |
| `-b` | 构建类型——DEBUG 含调试信息和 `DEBUG` 宏输出；RELEASE 做优化且消除调试代码 |
| `-t` | 工具链——GCC 即 GNU 编译器 |
| `-n` | 并发线程数（`$(nproc)` = 使用所有 CPU 核心） |

构建约 3-5 分钟。`DSC` 文件的角色以及所有参数的含义在 [04-构建系统深入](./04-build-system.md) 中有详细展开。

### 3.4 查看输出

```bash
ls Build/RiscVVirtQemu/DEBUG_GCC/FV/
```

你会看到：
- `RISCV_VIRT_CODE.fd` — 代码区（UEFI 固件本体，只读）
- `RISCV_VIRT_VARS.fd` — 变量存储区（可读写，用于保存启动顺序等 UEFI 变量）

---

## 4. 运行固件

### 4.1 填充 Flash 映像

QEMU virt 机器要求每个 pflash 分区至少 32MB，但构建产物只有 8MB：

```bash
truncate -s 32M Build/RiscVVirtQemu/DEBUG_GCC/FV/RISCV_VIRT_CODE.fd
truncate -s 32M Build/RiscVVirtQemu/DEBUG_GCC/FV/RISCV_VIRT_VARS.fd
```

`truncate -s` 不会真正写 32MB 数据——它只是扩展文件的逻辑大小，稀疏区域不占磁盘空间。

### 4.2 启动 QEMU

```bash
qemu-system-riscv64 \
    -machine virt \
    -m 256M \
    -bios default \
    -drive if=pflash,format=raw,file=Build/RiscVVirtQemu/DEBUG_GCC/FV/RISCV_VIRT_CODE.fd,readonly=on \
    -drive if=pflash,format=raw,file=Build/RiscVVirtQemu/DEBUG_GCC/FV/RISCV_VIRT_VARS.fd \
    -nographic
```

**参数说明**：

| 参数 | 含义 |
|------|------|
| `-machine virt` | RISC-V 虚拟机平台 |
| `-m 256M` | 256MB 内存 |
| `-bios default` | 使用 QEMU 自带的 OpenSBI 作为 M-mode 固件。OpenSBI 初始化后跳转到 pflash 中的 UEFI 固件 |
| `-drive if=pflash` | 指定 pflash 设备映射文件。CODE 区设 `readonly=on` 防止意外修改 |
| `-nographic` | 串口输出到当前终端 |

> 如果使用 `-bios none`，则不加载 M-mode 固件，UEFI 需要自行处理 M-mode 初始化——通常不推荐，除非你有自定义的 M-mode 固件。

### 4.3 你应该看到什么

启动后串口输出类似：

```
UEFI Interactive Shell v2.2
EDK II
UEFI v2.10 (EDK II, 0x00010000)
Mapping table
      BLK0: Alias(s):
...
Shell>
```

固件跑起来了。EDK2 的 `build` 命令默认将 UEFI Shell 打包进固件，所以你看到的就是 Shell 提示符。

### 4.4 在 Shell 中探索

```
# 查看系统信息
Shell> dh

# 查看已加载的驱动和协议
Shell> drivers

# 查看设备映射
Shell> map

# 浏览文件系统
Shell> fs0:
fs0:\> ls
```

### 4.5 退出 QEMU

按 `Ctrl+A` 然后按 `X`。

---

## 5. 修改并重新构建

### 5.1 修改调试输出级别

编辑 `OvmfPkg/RiscVVirt/RiscVVirtQemu.dsc`，找到：

```ini
gEfiMdePkgTokenSpaceGuid.PcdDebugPrintErrorLevel|0x80000047
```

改为：

```ini
gEfiMdePkgTokenSpaceGuid.PcdDebugPrintErrorLevel|0x80400047
```

这会额外启用 `DEBUG_VERBOSE`（`0x00400000`）级别的输出。该值是一个位掩码：`0x80000000` 部分表示 ERROR 级别常开，`0x00400000` = `DEBUG_VERBOSE`，尾部 `47` = `DEBUG_INFO` (0x40) | `DEBUG_LOAD` (0x04) | `DEBUG_WARN` (0x02) | `DEBUG_INIT` (0x01)。

### 5.2 重新构建

```bash
build -p OvmfPkg/RiscVVirt/RiscVVirtQemu.dsc \
      -a RISCV64 -b DEBUG -t GCC -n $(nproc)
```

EDK2 的构建系统有增量编译能力——只重新编译改动的模块，通常几秒到几十秒。

### 5.3 重新运行

重新构建后需要重新填充 Flash 映像（构建产物覆盖了旧文件），然后重复前面启动 QEMU 的命令。你会看到更多调试信息输出。

---

## 6. GDB 调试

快速启动 GDB 调试：

```bash
# 启动 QEMU（-s = GDB 端口 1234，-S = 启动时暂停）
qemu-system-riscv64 -machine virt -m 256M \
    -bios default \
    -drive if=pflash,format=raw,file=Build/RiscVVirtQemu/DEBUG_GCC/FV/RISCV_VIRT_CODE.fd,readonly=on \
    -drive if=pflash,format=raw,file=Build/RiscVVirtQemu/DEBUG_GCC/FV/RISCV_VIRT_VARS.fd \
    -nographic -s -S

# 连接 GDB
riscv64-unknown-elf-gdb \
    Build/RiscVVirtQemu/DEBUG_GCC/MdeModulePkg/Core/Dxe/DxeMain/DxeMain/DEBUG/DxeCore.dll

(gdb) set architecture riscv:rv64
(gdb) target remote :1234
(gdb) break DxeMain
(gdb) continue
```

更多调试技巧（关键断点、常见问题排查）见 [06-RISC-V 平台移植](./06-riscv-platform.md) 的调试章节。

---

## 7. 构建 x86 固件（对比）

作为参考，x86 的构建流程几乎一致：

```bash
build -p OvmfPkg/OvmfPkgX64.dsc -a X64 -b DEBUG -t GCC -n $(nproc)

qemu-system-x86_64 -m 2048 \
    -bios Build/OvmfX64/DEBUG_GCC/FV/OVMF.fd \
    -nographic
```

注意 x86 不需要 `truncate` 填充和 OpenSBI——x86 平台的 OVMF 固件自带模式切换逻辑。

---

## 8. 要点回顾

| 要点 | 说明 |
|------|------|
| 构建三步走 | `source edksetup.sh` 初始化环境 → `make -C BaseTools` 编译工具 → `build` 编译固件 |
| BaseTools 是先决条件 | 固件映像由 C 工具生成，这些工具需要先单独编译（首次） |
| RISC-V 固件依赖 OpenSBI | QEMU `-bios default` 自带 M-mode 固件，初始化后跳转 UEFI |
| pflash 需要填充到 32MB | `truncate -s 32M` 扩展文件大小，稀疏写不占空间 |
| 增量构建很快 | 只重新编译改动的模块 |
| GDB 调试需 `-s -S` | `-s` 开端口 1234，`-S` 启动暂停 |

---

## 参考资料

- [EDK2 Build Instructions](https://github.com/tianocore/edk2/blob/master/ReadMe.rst) — 官方构建说明
- [QEMU RISC-V Documentation](https://www.qemu.org/docs/master/system/target-riscv.html) — QEMU RISC-V 文档

---

**上一篇**：[00-全景地图](./00-overview.md) — 理解 EDK2 是什么
**下一篇**：[02-类型系统与编码规范](./02-type-system.md) — 读懂 EDK2 源码的基础
