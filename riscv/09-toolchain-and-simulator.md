# 工具链与模拟器

> 工欲善其事，必先利其器。掌握 RISC-V 的交叉编译工具链和模拟器，是实际开发的前提。

## 为什么重要

一套正确配置的工具链是你进入 RISC-V 世界的第一道门。选错 `-march` 会让编译器生成目标 CPU 不支持的指令（比如在 RV64IMAC 上生成浮点指令）；选错 `-mabi` 会导致函数调用的参数传递约定不一致；忘记 `-mcmodel=medany` 则会让内核在虚拟地址高位访问时链接错误。这些编译选项不是"可选的微调"——它们决定了你的固件能不能跑。

工具链的另一半是"看"——当固件在开发板上卡死、串口无输出时，QEMU 的 `-d int` 日志能告诉你异常原因和触发地址；Spike 的逐指令模拟能验证 ISA 正确性；GDB + QEMU stub 的远程调试能让你单步跟踪启动代码。本章覆盖 GCC 编译选项矩阵、QEMU 系统/用户态模拟、Spike ISA 验证、gem5 性能模拟、OpenOCD JTAG 调试、实用工具（objdump/readelf/nm）和一个完整的固件调试流程图。读完本章，你将拥有一套从"编译→运行→调试→性能分析"的完整工具链技能。

> **工程师视角**：工具链不仅是"编译代码"，更是定位问题的显微镜。当内核在目标板上 panic，而串口只输出乱码时，QEMU + GDB 的组合能让你在宿主机上精确复现和调试；当怀疑编译器生成错误指令时，`objdump` 是你的仲裁者。

## 学习目标

- 根据目标平台选择正确的 `-march`、`-mabi`、`-mcmodel` 组合
- 区分裸机工具链（`riscv64-unknown-elf-`）和 Linux 工具链（`riscv64-unknown-linux-gnu-`）
- 使用 QEMU 的 `-d int`/`-d in_asm` 日志定位异常根因
- 通过 GDB 远程调试在 QEMU 上运行的裸机程序
- 对比 Spike（ISA 参考模型）和 QEMU（全系统模拟）的适用场景
- 使用 objdump/readelf/nm 检查 ELF 文件的段表和符号
- 搭建基于 QEMU 的自动化测试脚本

### 前置知识

| 需要了解 | 参考文档 |
|----------|----------|
| RISC-V ISA 模块化扩展组合与 Profile | [RISC-V 概览](../01-basics/riscv-overview.md) |
| 汇编基本语法 | [汇编与 ABI](../05-system-software/assembly-and-abi.md) |

---

## 1. 交叉编译工具链

### 1.1 工具链选择

| 工具链 | 前缀 | 来源 | 说明 |
|--------|------|------|------|
| **GCC (riscv-gnu-toolchain)** | `riscv64-unknown-elf-` | 社区维护 | 裸机开发 |
| **GCC (Linux)** | `riscv64-unknown-linux-gnu-` | 社区维护 | Linux 用户态开发 |
| **LLVM/Clang** | 无需前缀 | LLVM 项目 | 统一工具链，多架构支持 |

### 1.2 安装工具链

```bash
# 方式 1：包管理器安装（Ubuntu/Debian）
sudo apt install gcc-riscv64-unknown-elf     # 裸机
sudo apt install gcc-riscv64-linux-gnu       # Linux
sudo apt install qemu-system-misc            # QEMU

# 方式 2：从源码编译（推荐，版本最新）
git clone https://github.com/riscv/riscv-gnu-toolchain
cd riscv-gnu-toolchain

# 裸机工具链
./configure --prefix=/opt/riscv --with-arch=rv64imac --with-abi=lp64
make -j$(nproc)

# Linux 工具链
./configure --prefix=/opt/riscv --with-arch=rv64gc --with-abi=lp64d
make linux -j$(nproc)
```

### 1.3 常用编译选项

| 选项 | 说明 | 示例 |
|------|------|------|
| `-march=rv64imac` | 指定 ISA 扩展 | rv32imac, rv64gc |
| `-mabi=lp64` | 指定 ABI | lp64, lp64d (双精度浮点) |
| `-mcmodel=medany` | 代码模型（内核用） | medlow, medany |
| `-ffreestanding` | 无标准库 | 裸机开发必须 |
| `-nostdlib` | 不链接标准库 | 裸机开发必须 |
| `-nostartfiles` | 不使用标准启动文件 | 自定义入口 |

### 1.4 ISA 与 ABI 的对应关系

| -march | -mabi | 说明 |
|--------|-------|------|
| rv32i | ilp32 | 最小 32 位 |
| rv32imac | ilp32 | 嵌入式常用 |
| rv32imafdc | ilp32d | 全功能 32 位 |
| rv64imac | lp64 | 64 位，无浮点 ABI |
| rv64gc | lp64d | 64 位，双精度浮点 ABI |

> **gc = imafdc_zicsr_zifencei**，G 是 IMAFDZicsr_Zifencei 的缩写。

### 1.5 固件与内核的编译选项差异

| 场景 | 推荐选项 | 原因 |
|------|----------|------|
| **Boot ROM** | `-march=rv64imac -mabi=lp64 -mcmodel=medany -Os` | 最小体积，位置无关 |
| **OpenSBI** | `-march=rv64imafdc -mabi=lp64d -mcmodel=medany` | 需要浮点保存/恢复 |
| **Linux 内核** | `-march=rv64imafdc -mabi=lp64d -mcmodel=medany` | 内核虚拟地址高半部分 |
| **用户态程序** | `-march=rv64gc -mabi=lp64d -mcmodel=medlow` | 低地址，直接寻址 |

> **关键区别**：`-mcmodel=medany` 允许代码和数据引用整个 64 位地址空间（使用 `auipc` + `addi`），这是内核必须的；`-mcmodel=medlow` 假设所有符号都在 2GB 范围内（使用 `lui` + `addi`），生成更小更快的代码。

---

## 2. QEMU

### 2.1 QEMU RISC-V 模式

| 模式 | 命令 | 用途 |
|------|------|------|
| **系统模拟** | `qemu-system-riscv64` | 运行完整 OS |
| **用户态模拟** | `qemu-riscv64` | 运行 RISC-V Linux 程序 |

### 2.2 常用 QEMU 命令

```bash
# 运行 Linux（完整启动链）
qemu-system-riscv64 \
    -machine virt \
    -nographic \
    -bios default \              # 使用 OpenSBI
    -kernel vmlinux \            # Linux 内核
    -initrd rootfs.cpio \        # 根文件系统
    -append "root=/dev/ram console=ttyS0" \
    -smp 2 \                     # 2 个 CPU
    -m 2G                        # 2GB 内存

# 运行裸机程序
qemu-system-riscv64 \
    -machine virt \
    -nographic \
    -bios none \
    -kernel firmware.elf

# 启用 GDB 调试
qemu-system-riscv64 \
    -machine virt \
    -nographic \
    -bios none \
    -kernel firmware.elf \
    -S \                         # 启动时暂停
    -gdb tcp::1234               # GDB 端口

# 用户态模拟
qemu-riscv64 ./hello_riscv      # 运行 RISC-V 可执行文件
qemu-riscv64 -L /opt/riscv/sysroot ./hello_riscv  # 指定 sysroot
```

### 2.3 QEMU virt 机器的内存映射

```
QEMU virt 机器地址映射:

0x00000000 - 0x000000FF  Debug
0x00001000 - 0x0000FFFF  MROM (复位向量在 0x1000)
0x00100000 - 0x00100FFF  Test
0x00101000 - 0x00101FFF  RTC
0x02000000 - 0x0200FFFF  CLINT (mtime/mtimecmp/msip)
0x0C000000 - 0x0FFFFFFF  PLIC
0x10000000 - 0x100000FF  UART0
0x10001000 - 0x10001FFF  VirtIO
0x80000000 - ...          DRAM
```

### 2.4 GDB 调试

```bash
# 终端 1：启动 QEMU（带 -S -gdb）
qemu-system-riscv64 -machine virt -nographic -bios none -kernel fw.elf -S -gdb tcp::1234

# 终端 2：启动 GDB
riscv64-unknown-elf-gdb fw.elf
(gdb) target remote :1234
(gdb) break _start
(gdb) continue
(gdb) info registers
(gdb) x/10i $pc
(gdb) stepi
```

#### GDB 常用命令速查

| 命令 | 作用 | 固件调试场景 |
|------|------|-------------|
| `info registers` | 查看所有寄存器 | 检查 a0/a1 启动参数 |
| `info registers mcause mepc mtval` | 查看异常 CSR | 定位 panic 原因 |
| `x/10gx $sp` | 查看栈内容 | 检查栈溢出或损坏 |
| `x/20i $pc` | 反汇编当前位置 | 确认代码执行路径 |
| `set $pc = 0x80000000` | 修改 PC | 跳过故障指令继续调试 |
| `monitor reset` | 复位目标 | 重新启动调试会话 |

> **TUI 模式**：`riscv64-unknown-elf-gdb -tui fw.elf` 可以开启分屏界面，同时显示源代码和汇编，调试体验大幅提升。

---

## 3. Spike（RISC-V ISA 模拟器）

Spike 是 RISC-V 官方的 ISA 模拟器，专注于指令集正确性验证：

```bash
# 安装
git clone https://github.com/riscv-software-src/riscv-isa-sim
cd riscv-isa-sim
mkdir build && cd build
../configure --prefix=/opt/spike
make -j$(nproc)
make install

# 运行
spike pk hello                    # 运行程序（pk = proxy kernel）
spike -m2 +disk=rootfs.img pk vmlinux  # 运行 Linux
spike --isa=rv64gc pk hello       # 指定 ISA
spike -d hello                    # 调试模式
```

### Spike vs QEMU

| 特性 | Spike | QEMU |
|------|-------|------|
| **定位** | ISA 参考模拟器 | 全系统模拟器 |
| **速度** | 慢（~10 MIPS） | 快（~1000 MIPS） |
| **准确性** | 最高（官方参考） | 高 |
| **外设支持** | 最小 | 丰富（VirtIO 等） |
| **调试** | 基础 | 完善（GDB stub） |
| **适用场景** | ISA 验证、教学 | 开发、测试、CI |

---

## 4. gem5（性能模拟器）

gem5 是学术界广泛使用的系统级模拟器，支持详细的时序建模：

```bash
# 安装
git clone https://github.com/gem5/gem5
cd gem5
scons build/RISCV/gem5.opt -j$(nproc)

# 运行
./build/RISCV/gem5.opt configs/example/se.py \
    --cpu-type=DerivO3CPU \
    --cmd=/path/to/binary
```

| 特性 | 说明 |
|------|------|
| **详细时序模型** | 可以模拟流水线、Cache、分支预测器 |
| **可配置 CPU** | AtomicSimpleCPU, TimingSimpleCPU, DerivO3CPU |
| **可配置 Cache** | L1/L2 大小、关联度、替换策略 |
| **统计输出** | CPI, Cache miss rate, 分支预测准确率等 |
| **适用场景** | 学术研究、架构探索 |

---

## 5. OpenOCD + JTAG 调试

### 5.1 OpenOCD 配置

```bash
# 安装
sudo apt install openocd

# 启动（以 SiFive HiFive1 为例）
openocd -f interface/ftdi/olimex-arm-usb-tiny-h.cfg \
        -f target/riscv.cfg
```

### 5.2 GDB 连接

```bash
riscv64-unknown-elf-gdb firmware.elf
(gdb) target extended-remote :3333
(gdb) monitor reset halt
(gdb) load
(gdb) break main
(gdb) continue
```

---

## 6. 实用工具

### 6.1 objdump 反汇编

```bash
# 反汇编 ELF 文件
riscv64-unknown-elf-objdump -d firmware.elf

# 反汇编特定段
riscv64-unknown-elf-objdump -d -j .text firmware.elf

# 显示段信息
riscv64-unknown-elf-objdump -h firmware.elf
```

### 6.2 readelf 查看 ELF 信息

```bash
# 查看 ELF 头
riscv64-unknown-elf-readelf -h firmware.elf

# 查看段表
riscv64-unknown-elf-readelf -S firmware.elf

# 查看符号表
riscv64-unknown-elf-readelf -s firmware.elf
```

### 6.3 nm 查看符号

```bash
# 查看所有符号
riscv64-unknown-elf-nm firmware.elf | sort

# 查看未定义符号
riscv64-unknown-elf-nm -u firmware.elf
```

### 6.4 固件调试实战流程

当固件在 QEMU 或真实硬件上崩溃时，按以下流程诊断：

```
1. 确认崩溃现象
   └── 串口无输出？→ 检查链接脚本入口地址、QEMU -kernel 参数
   └── 乱码？→ 检查波特率、时钟配置
   └── 输出一半卡住？→ 可能是 trap 循环，查看 mcause

2. 提取关键信息
   └── 读取 mcause, mepc, mtval（通过 GDB 或 OpenSBI 控制台）
   └── mcause = 0x2 (非法指令) → 检查 mepc 指向的指令
   └── mcause = 0xf (存储页错误) → 检查 mtval 的地址和页表

3. 复现与定位
   └── QEMU: 添加 -d int 查看中断/异常日志
   └── QEMU: 添加 -d in_asm 查看执行轨迹
   └── GDB: 在 mepc 附近设置断点，单步跟踪

4. 修复验证
   └── 修改代码 → 重新编译 → 测试
   └── 如果是硬件问题，检查设备树地址、PMP 配置
```

> **QEMU 调试日志示例**：
> ```bash
> qemu-system-riscv64 -machine virt -nographic -kernel fw.elf -d int
> # 输出：riscv_cpu_do_interrupt: hart:0, async:0, cause: 0x0000000000000002, epc: 0x0000000080001040
> # 表示核心 0 发生了同步异常（async:0），非法指令（cause:2），在地址 0x80001040
> ```

---

## 7. 持续集成与自动化测试

对于系统软件工程师，工具链的自动化是效率的关键：

```bash
# 使用 QEMU 进行自动化测试的脚本示例
#!/bin/bash
set -e

# 编译固件
make clean && make

# 运行测试，设置 10 秒超时
timeout 10 qemu-system-riscv64 \
    -machine virt \
    -nographic \
    -bios none \
    -kernel firmware.elf \
    -serial stdio | tee qemu.log

# 检查预期输出
if grep -q "TEST PASSED" qemu.log; then
    echo "✓ 测试通过"
    exit 0
else
    echo "✗ 测试失败"
    exit 1
fi
```

> **CI 建议**：GitHub Actions 或 GitLab CI 中可以使用 `qemu-system-riscv64` 运行回归测试，确保每次代码提交都不会破坏已有的 Lab 案例。

---

## 参考资料

- [RISC-V GNU Toolchain (GitHub)](https://github.com/riscv-collab/riscv-gnu-toolchain) — 官方工具链源码与构建指南
- [GCC RISC-V Options Documentation](https://gcc.gnu.org/onlinedocs/gcc/RISC-V-Options.html) — GCC RISC-V 编译选项详解
- [LLVM RISC-V Backend Documentation](https://llvm.org/docs/RISCVUsage.html) — Clang/LLVM 的 RISC-V 后端文档
- [gem5 — A Modular Platform Simulation Framework](https://www.gem5.org/) — RISC-V 微架构模拟器
- [QEMU RISC-V System Emulation](https://www.qemu.org/docs/master/system/riscv/) — QEMU RISC-V 系统模拟文档
- [OpenOCD RISC-V Target Driver](https://openocd.org/doc/html/Architecture-and-Core-Commands.html) — JTAG 调试工具配置

## 小结

| 工具 | 用途 | 命令前缀 |
|------|------|----------|
| GCC | 交叉编译 | riscv64-unknown-elf- |
| QEMU | 全系统模拟 | qemu-system-riscv64 |
| Spike | ISA 验证 | spike |
| gem5 | 性能模拟 | gem5.opt |
| OpenOCD | JTAG 调试 | openocd |
| GDB | 源码级调试 | riscv64-unknown-elf-gdb |

| 调试场景 | 推荐工具组合 |
|----------|-------------|
| 启动失败 | QEMU `-d int` + `mcause` 分析 |
| 随机崩溃 | GDB + QEMU stub，断点跟踪 |
| 性能问题 | `rdcycle` 计数 + gem5 模拟 |
| 指令验证 | Spike + QEMU 交叉对比 |

→ 下一节：[硬件平台与前沿方向](../07-practice/hardware-platforms.md)
