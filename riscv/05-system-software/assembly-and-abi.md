# 汇编与底层编程

> 汇编语言是理解硬件和操作系统的桥梁。对系统软件工程师而言，汇编不仅是"底层语言"，更是调试固件、分析编译器输出、优化关键路径的日常工具。
>
> **工程师视角**：当你在内核中遇到无法解释的崩溃，或需要验证编译器是否正确生成原子操作时，`objdump -d` 输出的汇编就是你的第一现场。

### 前置知识

| 需要了解 | 参考文档 |
|----------|----------|
| RISC-V 整数指令集（RV32I/RV64I）与编码 | [RV32I/RV64I 指令集详解](../02-isa/rv32i-rv64i-instructions.md) |
| 32 个通用寄存器的基本概念 | [体系结构基础](../01-basics/computer-architecture-fundamentals.md) |

---

## 1. RISC-V 汇编基础

### 1.1 汇编程序结构

```asm
    .section .text           # 代码段
    .global _start           # 入口点

_start:
    la    sp, _stack_top     # 初始化栈指针
    call  main               # 调用 main 函数
    j     .                  # 死循环（main 返回后）

    .section .data           # 数据段
msg:
    .string "Hello, RISC-V!\n"

    .section .bss            # BSS 段（未初始化数据）
    .space 4096              # 4KB 栈空间
_stack_top:
```

### 1.2 常用伪指令

RISC-V 汇编器提供了许多伪指令，它们会被展开为一条或多条真实指令：

| 伪指令 | 展开为 | 说明 |
|--------|--------|------|
| `li rd, imm` | `lui` + `addi` | 加载任意立即数 |
| `la rd, label` | `auipc` + `addi` | 加载地址（PC 相对） |
| `mv rd, rs` | `addi rd, rs, 0` | 寄存器复制 |
| `nop` | `addi x0, x0, 0` | 空操作 |
| `not rd, rs` | `xori rd, rs, -1` | 按位取反 |
| `neg rd, rs` | `sub rd, x0, rs` | 取负 |
| `seqz rd, rs` | `sltiu rd, rs, 1` | 等于零则置 1 |
| `snez rd, rs` | `sltu rd, x0, rs` | 不等于零则置 1 |
| `beqz rs, offset` | `beq rs, x0, offset` | 等于零则跳转 |
| `bnez rs, offset` | `bne rs, x0, offset` | 不等于零则跳转 |
| `j offset` | `jal x0, offset` | 无条件跳转 |
| `jal offset` | `jal x1, offset` | 调用（保存返回地址） |
| `jr rs` | `jalr x0, 0(rs)` | 寄存器跳转 |
| `ret` | `jalr x0, 0(ra)` | 函数返回 |
| `call offset` | `auipc ra, ...` + `jalr ra, ...` | 远程调用 |
| `tail offset` | `auipc t1, ...` + `jalr x0, ...` | 远程尾调用 |

### 1.3 li 伪指令的展开

```asm
# 小立即数（-2048 ~ 2047）
li  t0, 100           →  addi t0, x0, 100

# 中等立即数（高 20 位非零，低 12 位为零）
li  t0, 0x12345000    →  lui  t0, 0x12345

# 大立即数（需要两条指令）
li  t0, 0x12345678    →  lui  t0, 0x12345
                        addi t0, t0, 0x678

# 负数
li  t0, -1            →  addi t0, x0, -1
```

> **固件调试技巧**：在 OpenSBI 或 U-Boot 中设置内存映射寄存器时，常需要加载 64 位物理地址。如果地址超出 32 位范围，需用 `lui` + `slli` + `addi` 序列，或直接用 `lla`（本地加载地址）让链接器处理重定位。

---

## 2. 调用约定（ABI）

### 2.1 寄存器角色

```
┌─────────────────────────────────────────────────┐
│              RISC-V 调用约定                      │
├──────────┬──────┬────────────────────────────────┤
│ a0-a7    │ 参数 │ 前 8 个函数参数，a0-a1 也是返回值 │
│ t0-t6    │ 临时 │ Caller 保存，被调用者可自由修改   │
│ s0-s11   │ 保存 │ Callee 保存，被调用者必须保留     │
│ ra       │ 返回 │ Caller 保存，call 指令自动设置    │
│ sp       │ 栈   │ Callee 保存，必须 16 字节对齐     │
│ gp       │ 全局 │ 不在函数调用中使用                │
│ tp       │ 线程 │ 不在函数调用中使用                │
│ x0       │ 零   │ 硬连线为 0                       │
└──────────┴──────┴────────────────────────────────┘
```

### 2.2 栈帧布局

```
高地址
┌──────────────────────┐
│   调用者的栈帧        │
├──────────────────────┤ ← 调用者的 sp
│   参数 8+ (如果有的话) │   (a0-a7 放不下的参数)
├──────────────────────┤
│   返回地址 ra         │   (如果需要保存)
├──────────────────────┤
│   保存的 s0/fp        │
├──────────────────────┤ ← 当前函数的 fp (= s0)
│   局部变量            │
│   ...                 │
├──────────────────────┤
│   临时空间 / 对齐填充  │
├──────────────────────┤ ← 当前函数的 sp
│   (为调用子函数        │
│    准备的参数空间)      │
└──────────────────────┘
低地址

栈指针 sp 必须 16 字节对齐！
```

### 2.3 函数调用示例

```asm
# int add(int a, int b) { return a + b; }
add:
    add  a0, a0, a1    # a0 = a + b
    ret                 # 返回，a0 是返回值

# int sum_array(int *arr, int n) {
#     int sum = 0;
#     for (int i = 0; i < n; i++) sum += arr[i];
#     return sum;
# }
sum_array:
    addi  sp, sp, -16    # 分配栈帧
    sw    s0, 8(sp)      # 保存 s0
    sw    s1, 12(sp)     # 保存 s1
    mv    s0, a0         # s0 = arr
    mv    s1, a1         # s1 = n
    li    a0, 0          # sum = 0

.loop:
    beqz  s1, .done      # if (n == 0) break
    lw    t0, 0(s0)      # t0 = *arr
    add   a0, a0, t0     # sum += *arr
    addi  s0, s0, 4      # arr++
    addi  s1, s1, -1     # n--
    j     .loop

.done:
    lw    s0, 8(sp)      # 恢复 s0
    lw    s1, 12(sp)     # 恢复 s1
    addi  sp, sp, 16     # 释放栈帧
    ret                   # 返回，a0 = sum
```

### 2.4 中断处理中的调用约定

中断处理程序（trap handler）是一种特殊的"函数调用"——它不是由 `call` 指令进入，而是由硬件触发。这意味着：

- **ra 不会被自动保存**：中断返回使用 `mret/sret`，不是 `ret`
- **所有寄存器都必须保存**：因为中断可能发生在任何指令之间
- **栈必须对齐到 16 字节**：这是 RISC-V ABI 的硬性要求，即使在中断上下文中

```asm
# 简化的 M-mode trap 入口（保存全部寄存器）
_trap_entry:
    # 分配 256 字节的栈帧（32 个寄存器 × 8 字节）
    addi  sp, sp, -256
    sd    x1, 8(sp)      # ra
    sd    x2, 16(sp)     # sp（保存原始值）
    sd    x3, 24(sp)     # gp
    sd    x4, 32(sp)     # tp
    sd    x5, 40(sp)     # t0
    # ... 保存 x6-x31
    sd    x31, 248(sp)

    # 保存 CSR
    csrr  t0, mepc
    sd    t0, 0(sp)
    csrr  t0, mstatus
    sd    t0, 8(sp)      # 注意：覆盖 ra 的位置，实际实现需调整布局

    # 调用 C 处理函数
    mv    a0, sp         # 传递 pt_regs 指针
    call  do_trap

    # 恢复 CSR
    ld    t0, 0(sp)
    csrw  mepc, t0
    # ... 恢复所有寄存器
    addi  sp, sp, 256
    mret
```

> **关键提醒**：在 [Lab 1：裸机中断框架](../08-labs/lab01-baremetal-trap-handler.md) 中，你会看到一个完整的、生产级的 trap 入口实现，包括 sscratch 交换技巧。

---

## 3. 内联汇编

在 C 代码中嵌入 RISC-V 汇编：

### 3.1 基本语法

```c
asm volatile (
    "汇编指令模板"
    : 输出操作数   // 可选
    : 输入操作数   // 可选
    : 修改列表     // 可选
);
```

### 3.2 常用示例

```c
// 读取 CSR
static inline unsigned long read_csr_mstatus(void) {
    unsigned long val;
    asm volatile("csrr %0, mstatus" : "=r"(val));
    return val;
}

// 写入 CSR
static inline void write_csr_mstatus(unsigned long val) {
    asm volatile("csrw mstatus, %0" : : "r"(val));
}

// 原子交换
static inline int atomic_swap(int *addr, int newval) {
    int oldval;
    asm volatile(
        "amoswap.w %0, %2, (%1)"
        : "=r"(oldval)
        : "r"(addr), "r"(newval)
        : "memory"
    );
    return oldval;
}

// 内存屏障
static inline void fence(void) {
    asm volatile("fence" ::: "memory");
}

// 读取时间
static inline unsigned long read_time(void) {
    unsigned long val;
    asm volatile("rdtime %0" : "=r"(val));
    return val;
}

// 执行 WFI
static inline void wfi(void) {
    asm volatile("wfi");
}
```

### 3.3 约束字符

| 约束 | 含义 |
|------|------|
| `"r"` | 通用寄存器 |
| `"i"` | 立即数 |
| `"m"` | 内存操作数 |
| `"=r"` | 只写寄存器（输出） |
| `"+r"` | 读写寄存器（输入/输出） |

### 3.4 修改列表

| 修改 | 含义 |
|------|------|
| `"memory"` | 可能修改内存（编译器不能缓存内存值） |
| `"cc"` | 可能修改条件码（RISC-V 不常用） |
| `"t0", "t1"` | 可能修改特定寄存器 |

### 3.5 固件开发常用内联汇编模式

```c
// 读取 CPU 核心 ID（用于多核固件）
static inline unsigned long cpuid(void) {
    unsigned long id;
    asm volatile("csrr %0, mhartid" : "=r"(id));
    return id;
}

// 刷新指令缓存（自修改代码后必须调用）
static inline void fence_i(void) {
    asm volatile("fence.i" ::: "memory");
}

// 完整的内存屏障（用于设备寄存器同步）
static inline void fence_rw_rw(void) {
    // 确保所有之前的读写都在后续读写之前完成
    asm volatile("fence iorw, iorw" ::: "memory");
}

// 原子比较并交换（CAS）—— 锁-free 数据结构的基础
static inline int atomic_cas(int *ptr, int expected, int newval) {
    int result;
    asm volatile(
        "1: lr.w %0, (%1)\n"
        "   bne %0, %2, 2f\n"
        "   sc.w t0, %3, (%1)\n"
        "   bnez t0, 1b\n"
        "2:"
        : "=r"(result)
        : "r"(ptr), "r"(expected), "r"(newval)
        : "t0", "memory"
    );
    return result;
}

// 读取 cycle 计数（性能分析）
static inline unsigned long read_cycle(void) {
    unsigned long val;
    asm volatile("rdcycle %0" : "=r"(val));
    return val;
}
```

> **性能提示**：`rdcycle` 读取的是处理器周期计数器，受 DVFS 频率缩放影响；`rdtime` 读取的是平台级实时时钟（mtime），频率固定，适合跨核心比较时间戳。在 Linux 中，`rdtime` 被映射到 `vDSO`，用户态调用无需陷入内核。

---

## 4. 裸机编程（Bare-metal）

### 4.1 最小裸机程序

```c
// start.S - 汇编入口
.section .text
.global _start

_start:
    la    sp, _stack_top
    call  main
1:  wfi
    j     1b
```

```c
// main.c - C 入口
#include <stdint.h>

#define UART_BASE  0x10000000
#define UART_THR   (*(volatile uint8_t *)(UART_BASE + 0x00))

void uart_putc(char c) {
    UART_THR = c;
}

void uart_puts(const char *s) {
    while (*s) {
        uart_putc(*s++);
    }
}

int main(void) {
    uart_puts("Hello, bare-metal RISC-V!\n");
    while (1) {}
    return 0;
}
```

```ld
/* link.ld - 链接脚本 */
OUTPUT_ARCH("riscv")
ENTRY(_start)

MEMORY {
    RAM (rwx) : ORIGIN = 0x80000000, LENGTH = 128M
}

SECTIONS {
    .text : {
        *(.text.entry)
        *(.text .text.*)
    } > RAM

    .rodata : {
        *(.rodata .rodata.*)
    } > RAM

    .data : {
        *(.data .data.*)
    } > RAM

    .bss : {
        __bss_start = .;
        *(.bss .bss.*)
        *(COMMON)
        __bss_end = .;
    } > RAM

    . = ALIGN(16);
    . = . + 4096;
    _stack_top = .;
}
```

### 4.2 Makefile

```makefile
CROSS = riscv64-unknown-elf-
CC = $(CROSS)gcc
AS = $(CROSS)as
LD = $(CROSS)ld
OBJCOPY = $(CROSS)objcopy

CFLAGS = -march=rv64imac -mabi=lp64 -O2 -ffreestanding -nostdlib
ASFLAGS = -march=rv64imac

OBJS = start.o main.o

all: firmware.bin

start.o: start.S
	$(AS) $(ASFLAGS) -o $@ $<

main.o: main.c
	$(CC) $(CFLAGS) -c -o $@ $<

firmware.elf: $(OBJS) link.ld
	$(LD) -T link.ld -o $@ $(OBJS)

firmware.bin: firmware.elf
	$(OBJCOPY) -O binary $< $@

clean:
	rm -f *.o *.elf *.bin
```

### 4.3 QEMU 运行

```bash
# 运行裸机程序
qemu-system-riscv64 -machine virt -nographic -bios none -kernel firmware.bin

# 带 GDB 调试的启动
qemu-system-riscv64 -machine virt -nographic -bios none -kernel firmware.bin -S -gdb tcp::1234

# 多核启动（4 核）
qemu-system-riscv64 -machine virt -nographic -bios none -kernel firmware.bin -smp 4
```

> **调试技巧**：QEMU 的 `-bios none` 会跳过 OpenSBI，直接从 `0x80000000` 运行你的固件。如果你想在 OpenSBI 环境下测试，去掉 `-bios none`，QEMU 会自动加载默认的 OpenSBI。

---

## 5. 常用代码模式

### 5.1 自旋锁

```c
typedef volatile int spinlock_t;

void spin_lock(spinlock_t *lock) {
    int expected = 0;
    while (__atomic_exchange_n(lock, 1, __ATOMIC_ACQUIRE) != 0) {
        // 等待，可以加入 wfi 减少功耗
    }
}

void spin_unlock(spinlock_t *lock) {
    __atomic_store_n(lock, 0, __ATOMIC_RELEASE);
}
```

### 5.2 CSR 读写宏

```c
#define csr_read(csr)                              \
    ({                                             \
        register unsigned long __v;                \
        asm volatile("csrr %0, " #csr              \
                     : "=r"(__v));                 \
        __v;                                       \
    })

#define csr_write(csr, val)                        \
    ({                                             \
        unsigned long __v = (unsigned long)(val);  \
        asm volatile("csrw " #csr ", %0"           \
                     : : "r"(__v));                \
    })

#define csr_set(csr, val)                          \
    ({                                             \
        unsigned long __v = (unsigned long)(val);  \
        asm volatile("csrs " #csr ", %0"           \
                     : : "r"(__v));                \
    })

#define csr_clear(csr, val)                        \
    ({                                             \
        unsigned long __v = (unsigned long)(val);  \
        asm volatile("csrc " #csr ", %0"           \
                     : : "r"(__v));                \
    })

// 使用示例
unsigned long mstatus = csr_read(mstatus);
csr_set(mstatus, 0x8);    // 设置 MIE
csr_clear(mstatus, 0x8);  // 清除 MIE
```

### 5.3 延时函数

```c
static inline void delay(int count) {
    volatile int i;
    for (i = 0; i < count; i++)
        __asm__ volatile("nop");
}

// 基于mtime的精确延时
void udelay(unsigned int us) {
    unsigned long start = *(volatile unsigned long *)0x200BFF8; // mtime
    unsigned long delay_ticks = us * (10000000 / 1000000);     // 10MHz clock
    while ((*(volatile unsigned long *)0x200BFF8 - start) < delay_ticks)
        ;
}
```

### 5.4 多核启动序列（SMP Bring-up）

多核 RISC-V 系统中，通常由核心 0 负责初始化，其他核心处于等待状态：

```c
// 核心 0 的启动代码
void main(void) {
    unsigned long hartid = cpuid();

    if (hartid == 0) {
        // 主核心：初始化 UART、内存、中断控制器
        uart_init();
        plic_init();

        // 唤醒其他核心
        for (int i = 1; i < NUM_HARTS; i++) {
            // 通过 CLINT 发送软件中断唤醒核心 i
            *(volatile uint32_t *)(CLINT_BASE + 0x0000 + i * 4) = 1;
        }

        // 进入主循环
        scheduler_loop();
    } else {
        // 从核心：等待唤醒
        while (!is_core_ready(hartid)) {
            wfi();  // 低功耗等待
        }

        // 初始化自己的栈
        sp = get_stack_top(hartid);

        // 进入调度器
        scheduler_loop();
    }
}
```

> **实际案例**：在 [Lab 2：最小 SBI 实现](../08-labs/lab02-minimal-sbi.md) 中，你会看到 HSM（Hart State Management）扩展如何标准化多核启动流程。

---

## 6. 从 C 到汇编：编译器视角

理解编译器如何生成汇编，能帮助你写出更高效的 C 代码，也能在调试时快速定位问题。

### 6.1 查看编译器输出

```bash
# 生成汇编文件（带 C 代码注释）
riscv64-unknown-elf-gcc -S -O2 -fverbose-asm foo.c -o foo.s

# 反汇编 ELF 文件（最常用）
riscv64-unknown-elf-objdump -d firmware.elf

# 反汇编并显示源代码对应关系
riscv64-unknown-elf-objdump -d -l firmware.elf

# 查看特定函数的汇编
riscv64-unknown-elf-objdump -d firmware.elf | grep -A 20 "<my_function>:"
```

### 6.2 常见 C 代码模式与汇编对应

```c
// C 代码：结构体访问
struct device {
    volatile uint32_t ctrl;
    volatile uint32_t data;
};

void device_write(struct device *dev, uint32_t val) {
    dev->data = val;  // 编译为：sw a1, 4(a0)
}
```

```c
// C 代码：位操作（设备寄存器常用）
#define BIT(x) (1U << (x))

void set_bits(volatile uint32_t *reg, uint32_t mask) {
    *reg |= mask;  // 编译为：lw t0, 0(a0); or t0, t0, a1; sw t0, 0(a0)
}
```

> **优化提示**：对设备寄存器进行位操作时，编译器可能生成"读-改-写"序列。如果寄存器是写敏感的（写 1 清零），应该使用 `*((volatile uint32_t *)addr) = mask;` 直接写入，而不是 `|=`。

### 6.3 编译器优化与调试的平衡

| 优化级别 | 特点 | 适用场景 |
|----------|------|----------|
| `-O0` | 无优化，调试信息最准确 | 调试阶段 |
| `-O2` | 平衡优化，常用 | 发布版本 |
| `-O3` | 激进优化，可能增大代码体积 | 性能关键路径 |
| `-Os` | 优化代码体积 | 固件/嵌入式 |
| `-Og` | 调试友好的优化 | 推荐用于开发 |

```bash
# 开发时推荐：保留调试信息，适度优化
riscv64-unknown-elf-gcc -Og -g3 -march=rv64imac -mabi=lp64 ...
```

---

## 小结

| 要点 | 说明 |
|------|------|
| 伪指令简化编程 | li/la/mv/ret 等伪指令让汇编更易读 |
| 调用约定 | a0-a7 参数，s0-s11 保存，sp 16 字节对齐 |
| 中断上下文 | 需保存全部寄存器，使用 sscratch 交换栈指针 |
| 内联汇编 | 用于访问 CSR、原子操作、内存屏障 |
| 裸机编程 | 入口汇编 → C main → 链接脚本 → QEMU 运行 |
| CSR 宏 | 封装 csrr/csrw/csrs/csrc 为可读的宏 |
| 编译器视角 | objdump -d 是日常调试工具，-Og 是开发最优选 |

---

## 参考资料

- [RISC-V ELF psABI Specification](https://github.com/riscv-non-isa/riscv-elf-psabi-doc) — 函数调用约定与 ELF 结构定义
- [RISC-V Assembly Programmer's Manual](https://github.com/riscv-non-isa/riscv-asm-manual/blob/master/riscv-asm.md) — 汇编编程实践指南
- [GCC Inline Assembler Documentation](https://gcc.gnu.org/onlinedocs/gcc/Using-Assembly-Language-with-C.html) — 内联汇编语法详解

---

→ 下一节：[操作系统移植](./os-porting.md)
