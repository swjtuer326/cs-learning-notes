# 03 — 先跑起来

> 理解概念后，立刻上手。用 RISC-V QEMU 为目标：一条命令构建，一条命令运行，看到 UEFI Shell 提示符。这篇只讲实践，不讲原理。

## 1. 环境要求

| 工具 | 版本要求 | 安装 |
|------|---------|------|
| GCC RISC-V 交叉编译器 | riscv64-unknown-elf 或 riscv64-linux-gnu | `apt install gcc-riscv64-unknown-elf` |
| QEMU | 6.0+（RISC-V virt 机器） | `apt install qemu-system-misc` |
| Python 3 | 3.6+（构建脚本） | 系统自带 |
| NASM / iasl | 非必需（RISC-V 不用） | 仅 x86 平台需要 |

## 2. 构建

```bash
# 1. 克隆 EDK2
git clone https://github.com/tianocore/edk2.git edk2
cd edk2
git submodule update --init --recursive

# 2. 初始化构建环境
make -C BaseTools                    # 编译构建工具（只需要一次）
export EDK_TOOLS_PATH=$PWD/BaseTools

# 3. 设置环境变量 + 构建 RISC-V QEMU 平台
. edksetup.sh
build -p OvmfPkg/RiscVVirt/RiscVVirtQemu.dsc \
      -a RISCV64 -t GCC5 -b DEBUG
```

各参数的含义：
- `-p <dsc>`：指定平台描述文件（定义库绑定、PCD 值、包含哪些模块）
- `-a RISCV64`：目标架构。EDK2 支持 IA32/X64/AARCH64/ARM/RISCV64
- `-t GCC5`：编译器标签（GCC 5+ 兼容）。也可用 CLANG38
- `-b DEBUG`：构建类型。DEBUG 输出详细日志；RELEASE 消除调试代码缩小体积

构建耗时 ≈ 3-5 分钟。产物位置：`Build/RiscVVirtQemu/DEBUG_GCC5/FV/`

## 3. 运行

```bash
qemu-system-riscv64 \
  -machine virt -m 1G -smp 4 \
  -bios default \
  -drive file=Build/RiscVVirtQemu/DEBUG_GCC5/FV/RISCV_VIRT_CODE.fd,format=raw,if=pflash \
  -drive file=Build/RiscVVirtQemu/DEBUG_GCC5/FV/RISCV_VIRT_VARS.fd,format=raw,if=pflash \
  -nographic
```

两个 pflash 文件：
- `CODE.fd`：只读，包含 UEFI 可执行代码（SEC+PEI+DXE+BDS）
- `VARS.fd`：可读写，存储 UEFI 变量（NVRAM，例如 BootOrder、SecureBoot 密钥）

## 4. 理解日志输出

启动过程中的每个 `DEBUG` 宏都通过 SBI 串口（M-mode OpenSBI → S-mode UEFI）输出。关键行：

```
SecCoreStartupWithStack()  ← SEC 阶段：汇编入口跳到 C，临时栈建立完毕
PeiCore()                  ← PEI Core 启动，开始调度 PEIM
DxeMain()                  ← DXE Core 启动，HOB 列表转换为 UEFI 内存映射
BdsEntry()                 ← BDS 启动，开始按 BootOrder 加载 OS Loader
```

如果最后停在 `Shell>` 提示符，说明找不到 Boot#### 选项——正常现象。QEMU 的 VARS.fd 初始为空，还没有安装 OS。

## 5. GDB 调试

```bash
# 终端 1：QEMU 启动并暂停 (-S)，开放 GDB 端口 (-s = :1234)
qemu-system-riscv64 ... -nographic -s -S

# 终端 2：GDB 连接
riscv64-unknown-elf-gdb
(gdb) set architecture riscv:rv64
(gdb) target remote :1234
(gdb) break SecEntry           # SEC 汇编入口第一条指令
(gdb) break DxeMain            # DXE Core 入口
(gdb) break BdsEntry           # BDS 入口
(gdb) continue
```

---

**上一篇**：[02-一次完整启动](./02-boot-sequence.md)  
**下一篇**：[04-Handle / Protocol — 核心通信模型](./04-handle-protocol.md)
