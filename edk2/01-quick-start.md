# 快速上手：构建与运行

> 30 分钟内，从零开始构建并运行你的第一个 UEFI 固件。理论再好，不如亲手跑一遍。

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

> **注意**：`python3-distutils` 在 Python 3.12+ 中已被移除。EDK2 最新版本不再依赖它。如果遇到问题，确保使用最新源码。

---

## 3. 首次构建

### 3.1 初始化环境

```bash
source edksetup.sh
```

这条命令做了什么：
- 设置 `WORKSPACE` 环境变量指向 edk2 根目录
- 将 `BaseTools/BinWrappers/PosixLike` 加入 `PATH`
- 复制 `Conf/*.template` 到 `Conf/*.txt`（如果不存在）

### 3.2 编译构建工具

```bash
make -C BaseTools
```

首次构建需要编译 BaseTools 中的 C 工具（GenFv、GenFfs 等）。后续构建不需要重复此步骤。

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
| `-p` | 平台 DSC 文件（告诉构建系统"构建什么"） |
| `-a` | 目标架构 |
| `-b` | 构建类型（DEBUG 含调试信息，RELEASE 优化） |
| `-t` | 工具链（GCC = GNU 编译器） |
| `-n` | 并发线程数 |

构建过程约 3-5 分钟（取决于机器性能）。

### 3.4 查看输出

```bash
ls Build/RiscVVirtQemu/DEBUG_GCC/FV/
```

你会看到：
- `RISCV_VIRT_CODE.fd` — 代码区（UEFI 固件本体）
- `RISCV_VIRT_VARS.fd` — 变量存储区

---

## 4. 运行固件

### 4.1 填充 Flash 映像

QEMU virt 机器要求每个 pflash 至少 32MB，但构建产物只有 8MB，需要填充：

```bash
truncate -s 32M Build/RiscVVirtQemu/DEBUG_GCC/FV/RISCV_VIRT_CODE.fd
truncate -s 32M Build/RiscVVirtQemu/DEBUG_GCC/FV/RISCV_VIRT_VARS.fd
```

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
| `-bios default` | 使用 QEMU 自带的 OpenSBI（M-mode 固件） |
| `-drive if=pflash,format=raw` | 指定 pflash 设备，raw 格式避免警告 |
| `readonly=on` | CODE 区只读，防止意外修改 |
| `-nographic` | 串口输出到终端 |

> **设计背景 — `-bios default` vs `-bios none`**：`-bios default` 使用 QEMU 自带的 OpenSBI，它初始化 M-mode 后跳转到 UEFI 固件。`-bios none` 不加载 M-mode 固件，UEFI 需要自行处理 M-mode 初始化（通常不推荐）。

### 4.3 你应该看到什么

启动后，串口会输出类似：

```
UEFI Interactive Shell v2.2
EDK II
UEFI v2.10 (EDK II, 0x00010000)
Mapping table
      BLK0: Alias(s):
...
Shell>
```

恭喜，你的第一个 UEFI 固件跑起来了。

### 4.4 在 Shell 中探索

```
# 查看系统信息
Shell> dh

# 查看协议
Shell> drivers

# 查看设备
Shell> map

# 查看文件系统
Shell> fs0:
fs0:\> ls
```

### 4.5 退出 QEMU

按 `Ctrl+A` 然后按 `X` 退出 QEMU。

---

## 5. 修改并重新构建

### 5.1 修改调试输出级别

编辑 `OvmfPkg/RiscVVirt/RiscVVirtQemu.dsc`，找到：

```ini
gEfiMdePkgTokenSpaceGuid.PcdDebugPrintErrorLevel|0x80000047
```

改为：

```ini
gEfiMdePkgTokenSpaceGuid.PcdDebugPrintErrorLevel|0x80000447
```

这会启用更详细的调试输出。

### 5.2 重新构建

```bash
build -p OvmfPkg/RiscVVirt/RiscVVirtQemu.dsc \
      -a RISCV64 -b DEBUG -t GCC -n $(nproc)
```

增量构建通常只需几秒到几十秒。

### 5.3 重新运行

重新构建后需要重新填充 Flash 映像：

```bash
truncate -s 32M Build/RiscVVirtQemu/DEBUG_GCC/FV/RISCV_VIRT_CODE.fd
truncate -s 32M Build/RiscVVirtQemu/DEBUG_GCC/FV/RISCV_VIRT_VARS.fd

qemu-system-riscv64 \
    -machine virt -m 256M \
    -bios default \
    -drive if=pflash,format=raw,file=Build/RiscVVirtQemu/DEBUG_GCC/FV/RISCV_VIRT_CODE.fd,readonly=on \
    -drive if=pflash,format=raw,file=Build/RiscVVirtQemu/DEBUG_GCC/FV/RISCV_VIRT_VARS.fd \
    -nographic
```

你会看到更多调试信息输出。

---

## 6. GDB 调试

### 6.1 启动 QEMU（等待 GDB）

```bash
qemu-system-riscv64 \
    -machine virt -m 256M \
    -bios default \
    -drive if=pflash,format=raw,file=Build/RiscVVirtQemu/DEBUG_GCC/FV/RISCV_VIRT_CODE.fd,readonly=on \
    -drive if=pflash,format=raw,file=Build/RiscVVirtQemu/DEBUG_GCC/FV/RISCV_VIRT_VARS.fd \
    -nographic -s -S
```

`-s` = GDB 端口 1234，`-S` = 启动时暂停。

### 6.2 连接 GDB

```bash
riscv64-unknown-elf-gdb Build/RiscVVirtQemu/DEBUG_GCC/MdeModulePkg/Core/Dxe/DxeMain/DxeMain/DEBUG/DxeCore.dll

(gdb) set architecture riscv:rv64
(gdb) target remote :1234
(gdb) break DxeMain
(gdb) continue
```

> 详见 [06-RISC-V 平台移植](./06-riscv-platform.md) 的调试章节。

---

## 7. 构建 x86 固件（对比）

如果你想对比 x86 的构建流程：

```bash
# 构建 x86-64 OVMF 固件
build -p OvmfPkg/OvmfPkgX64.dsc -a X64 -b DEBUG -t GCC -n $(nproc)

# 运行
qemu-system-x86_64 \
    -m 2048 \
    -bios Build/OvmfX64/DEBUG_GCC/FV/OVMF.fd \
    -nographic
```

---

## 8. 要点回顾

| 要点 | 说明 |
|------|------|
| 构建三步走 | `source edksetup.sh` → `make -C BaseTools` → `build` |
| RISC-V 固件需要 OpenSBI 作为 M-mode 固件 | QEMU `-bios default` 自带 |
| pflash 需要填充到 32MB | `truncate -s 32M` 填充 |
| `-drive if=pflash,format=raw` 指定固件映像 | CODE 只读，VARS 可读写 |
| 增量构建很快 | 只重新编译改动的模块 |
| GDB 调试需要 `-s -S` 参数 | `-s` 开 GDB 端口，`-S` 启动暂停 |

---

## 参考资料

- [EDK2 Build Instructions](https://github.com/tianocore/edk2/blob/master/ReadMe.rst) — 官方构建说明
- [QEMU RISC-V Documentation](https://www.qemu.org/docs/master/system/target-riscv.html) — QEMU RISC-V 文档

---

**上一篇**：[00-全景地图](./00-overview.md) — EDK2 是什么
**下一篇**：[02-类型系统与编码规范](./02-type-system.md) — 读懂 EDK2 源码的基础
