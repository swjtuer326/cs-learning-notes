# 实验一：从零实现裸机 Trap Handler

> 本实验带你从复位向量开始，手写一个可在 QEMU 上运行的 M-mode 中断/异常处理框架。通过实战理解 trap 的硬件行为、上下文保存与恢复、以及嵌套中断的处理。

---

## 实验目标

1. 理解复位后 CPU 的初始状态
2. 手写汇编级别的 trap entry 与 exit
3. 实现上下文保存/恢复（全部通用寄存器；浮点上下文的保存作为进阶练习——需配合 `mstatus.FS` 状态字段）
4. 处理定时器中断，实现周期性的 "tick"
5. （进阶）支持中断嵌套

---

## 前置知识

- [特权模式与 CSR](./03-privileged-modes-and-csr.md)
- [中断与异常处理](./04-interrupts-and-exceptions.md)

---

## 1. 实验环境

### 1.1 工具链

```bash
# Ubuntu/Debian
sudo apt install gcc-riscv64-unknown-elf qemu-system-misc

# 验证
riscv64-unknown-elf-gcc --version
qemu-system-riscv64 --version
```

### 1.2 目录结构

```
lab01/
├── start.S          # 汇编入口、trap handler
├── main.c           # C 语言主逻辑
├── linker.ld        # 链接脚本
├── Makefile         # 构建
└── README.md        # 本文件
```

---

## 2. 链接脚本：告诉链接器内存布局

```ld
/* linker.ld */
OUTPUT_ARCH(riscv)
ENTRY(_start)

MEMORY {
    RAM (rwx) : ORIGIN = 0x80000000, LENGTH = 128M
}

SECTIONS {
    . = ORIGIN(RAM);

    .text : {
        *(.text.init)
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

    __stack_top = ORIGIN(RAM) + LENGTH(RAM);
}
```

> **关键点：**
> - `ORIGIN = 0x80000000` 是 QEMU `virt` 机器的 DRAM 起始地址
> - `__stack_top` 放在 RAM 最高地址，栈向低地址增长
> - `ENTRY(_start)` 指定入口符号

---

## 3. 汇编入口：从复位到 C 世界

```asm
/* start.S */
    .section .text.init
    .global _start
    .global trap_entry

    /* 宏：保存通用寄存器到栈 */
    .macro SAVE_REGS
    addi    sp, sp, -256
    sd      ra, 0(sp)
    sd      t0, 8(sp)
    sd      t1, 16(sp)
    sd      t2, 24(sp)
    sd      a0, 32(sp)
    sd      a1, 40(sp)
    sd      a2, 48(sp)
    sd      a3, 56(sp)
    sd      a4, 64(sp)
    sd      a5, 72(sp)
    sd      a6, 80(sp)
    sd      a7, 88(sp)
    sd      t3, 96(sp)
    sd      t4, 104(sp)
    sd      t5, 112(sp)
    sd      t6, 120(sp)
    sd      s0, 128(sp)
    sd      s1, 136(sp)
    sd      s2, 144(sp)
    sd      s3, 152(sp)
    sd      s4, 160(sp)
    sd      s5, 168(sp)
    sd      s6, 176(sp)
    sd      s7, 184(sp)
    sd      s8, 192(sp)
    sd      s9, 200(sp)
    sd      s10, 208(sp)
    sd      s11, 216(sp)
    sd      gp, 224(sp)
    sd      tp, 232(sp)
    .endm

    /* 宏：恢复通用寄存器 */
    .macro RESTORE_REGS
    ld      ra, 0(sp)
    ld      t0, 8(sp)
    ld      t1, 16(sp)
    ld      t2, 24(sp)
    ld      a0, 32(sp)
    ld      a1, 40(sp)
    ld      a2, 48(sp)
    ld      a3, 56(sp)
    ld      a4, 64(sp)
    ld      a5, 72(sp)
    ld      a6, 80(sp)
    ld      a7, 88(sp)
    ld      t3, 96(sp)
    ld      t4, 104(sp)
    ld      t5, 112(sp)
    ld      t6, 120(sp)
    ld      s0, 128(sp)
    ld      s1, 136(sp)
    ld      s2, 144(sp)
    ld      s3, 152(sp)
    ld      s4, 160(sp)
    ld      s5, 168(sp)
    ld      s6, 176(sp)
    ld      s7, 184(sp)
    ld      s8, 192(sp)
    ld      s9, 200(sp)
    ld      s10, 208(sp)
    ld      s11, 216(sp)
    ld      gp, 224(sp)
    ld      tp, 232(sp)
    addi    sp, sp, 256
    .endm

_start:
    /* 1. 关闭中断 */
    csrw    mie, zero
    csrw    mstatus, zero

    /* 2. 设置栈指针 */
    la      sp, __stack_top

    /* 3. 清空 BSS */
    la      t0, __bss_start
    la      t1, __bss_end
1:
    bge     t0, t1, 2f
    sd      zero, 0(t0)
    addi    t0, t0, 8
    j       1b
2:

    /* 4. 设置 trap 向量（Direct 模式） */
    la      t0, trap_entry
    csrw    mtvec, t0

    /* 5. 设置 mscratch = 备用栈（用于嵌套中断） */
    la      t0, __stack_top
    li      t1, 8192
    sub     t0, t0, t1          /* 预留 8KB 主栈，其余给 trap */
    csrw    mscratch, t0

    /* 6. 初始化定时器 */
    call    timer_init

    /* 7. 使能 M-mode 全局中断和定时器中断 */
    li      t0, (1 << 3)              /* mstatus.MIE = 1 */
    csrw    mstatus, t0
    li      t0, (1 << 7)              /* mie.MTIE = 1 (M-mode timer interrupt) */
    csrw    mie, t0

    /* 8. 进入 C 代码 */
    call    main

    /* main 返回后停机 */
3:
    wfi
    j       3b

/* ============================================================
 * Trap Entry
 * ============================================================ */
    .align  4
trap_entry:
    /* 切换 sp 到 trap 栈 */
    csrrw   sp, mscratch, sp

    SAVE_REGS

    /* 读取异常信息作为 C 函数参数 */
    csrr    a0, mcause
    csrr    a1, mepc
    csrr    a2, mtval

    call    trap_handler

    /* 如果 C handler 修改了 mepc，需要写回 */
    csrw    mepc, a0

    RESTORE_REGS

    /* 恢复原始 sp */
    csrrw   sp, mscratch, sp
    mret
```

> **设计要点：**
> - `csrrw sp, mscratch, sp` 原子交换 sp 与 mscratch，实现栈切换
> - 保存所有寄存器（包括 caller-saved 和 callee-saved），因为中断可能发生在任何位置
> - `trap_handler` 的返回值写入 `mepc`，允许 C 代码修改返回地址

---

## 4. C 语言：Trap 分发与定时器

```c
/* main.c */
#include <stdint.h>

#define CLINT_BASE      0x2000000UL
#define CLINT_MTIME     (CLINT_BASE + 0xBFF8)
#define CLINT_MTIMECMP  (CLINT_BASE + 0x4000)

static volatile uint64_t g_ticks = 0;

/* 从设备寄存器读取 64-bit 值（注意：mtime 可能不是原子 64-bit 访问） */
static uint64_t read_mtime(void) {
    volatile uint32_t *lo = (volatile uint32_t *)CLINT_MTIME;
    volatile uint32_t *hi = (volatile uint32_t *)(CLINT_MTIME + 4);
    uint32_t h1, h2, l;
    do {
        h1 = *hi;
        l  = *lo;
        h2 = *hi;
    } while (h1 != h2);
    return ((uint64_t)h1 << 32) | l;
}

static void write_mtimecmp(uint64_t val) {
    volatile uint32_t *lo = (volatile uint32_t *)CLINT_MTIMECMP;
    volatile uint32_t *hi = (volatile uint32_t *)(CLINT_MTIMECMP + 4);
    /* 先写高 32 位为一个极大值，防止中间触发 */
    *hi = 0xFFFFFFFF;
    *lo = (uint32_t)val;
    *hi = (uint32_t)(val >> 32);
}

void timer_init(void) {
    uint64_t now = read_mtime();
    /* 假设 10MHz，100000 = 10ms */
    write_mtimecmp(now + 100000);
}

/* 返回新的 mepc */
uint64_t trap_handler(uint64_t mcause, uint64_t mepc, uint64_t mtval) {
    int is_interrupt = (mcause >> 63) & 1;
    int cause_code   = mcause & 0x7FF;

    if (is_interrupt) {
        switch (cause_code) {
        case 7:  /* M-mode timer interrupt */
            g_ticks++;
            /* 重新设置定时器 */
            write_mtimecmp(read_mtime() + 100000);

            /* 每 100 个 tick 打印一次（模拟串口输出） */
            if (g_ticks % 100 == 0) {
                /* 这里可以调用 UART 驱动输出 */
                __asm__ volatile("ebreak");  /* 在 QEMU 中触发断点，方便调试 */
            }
            break;
        case 3:  /* M-mode software interrupt */
            break;
        case 11: /* M-mode external interrupt */
            break;
        default:
            break;
        }
    } else {
        /* 异常处理 */
        switch (cause_code) {
        case 11: /* M-mode ecall */
            mepc += 4;  /* 跳过 ecall */
            break;
        case 3:  /* ebreak */
            /* 断点，通常由调试器处理 */
            break;
        case 2:  /* illegal instruction */
            /* 可以模拟未实现指令 */
            mepc += 4;
            break;
        default:
            /* 未知异常，进入死循环方便调试 */
            while (1) { __asm__ volatile("wfi"); }
        }
    }

    return mepc;
}

int main(void) {
    /* 主循环：低功耗等待中断 */
    while (1) {
        __asm__ volatile("wfi");
    }
    return 0;
}
```

> **mtime 读取的陷阱：** 32-bit 系统访问 64-bit mtime 需要分两次读取。如果低 32 位刚好在读取时溢出，高 32 位会变化。上面的循环读取直到高 32 位稳定，是标准的处理方式。

---

## 5. Makefile

```makefile
# Makefile
CROSS_COMPILE ?= riscv64-unknown-elf-
CC      = $(CROSS_COMPILE)gcc
OBJCOPY = $(CROSS_COMPILE)objcopy
OBJDUMP = $(CROSS_COMPILE)objdump

CFLAGS  = -march=rv64imac -mabi=lp64 -mcmodel=medany \
          -ffreestanding -nostdlib -nostartfiles \
          -O2 -Wall -Wextra -g

LDFLAGS = -T linker.ld

OBJS    = start.o main.o

all: firmware.elf firmware.bin

firmware.elf: $(OBJS)
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $^

firmware.bin: firmware.elf
	$(OBJCOPY) -O binary $< $@

%.o: %.S
	$(CC) $(CFLAGS) -c -o $@ $<

%.o: %.c
	$(CC) $(CFLAGS) -c -o $@ $<

dump: firmware.elf
	$(OBJDUMP) -d $<

run: firmware.elf
	qemu-system-riscv64 \
		-machine virt -nographic -bios none \
		-kernel firmware.elf

debug: firmware.elf
	qemu-system-riscv64 \
		-machine virt -nographic -bios none \
		-kernel firmware.elf -S -gdb tcp::1234

clean:
	rm -f $(OBJS) firmware.elf firmware.bin

.PHONY: all dump run debug clean
```

---

## 6. 运行与调试

```bash
# 编译
make

# 直接运行（无输出，因为没接 UART，但可以用 GDB 观察）
make run

# 反汇编查看代码
make dump

# GDB 调试（另一个终端）
make debug
# 新终端：
riscv64-unknown-elf-gdb firmware.elf \
    -ex "target remote localhost:1234" \
    -ex "break trap_handler" \
    -ex "continue"
```

---

## 7. 进阶：支持中断嵌套

默认情况下，进入 trap 后 `mstatus.MIE` 被硬件清零，禁止中断。要实现嵌套：

```asm
    .align  4
trap_entry_nested:
    csrrw   sp, mscratch, sp
    SAVE_REGS

    /* 重新使能中断（允许更高优先级中断抢占） */
    csrr    t0, mstatus
    ori     t0, t0, (1 << 3)    /* MIE = 1 */
    csrw    mstatus, t0

    csrr    a0, mcause
    csrr    a1, mepc
    csrr    a2, mtval
    call    trap_handler
    csrw    mepc, a0

    /* 关闭中断，防止恢复上下文时被打断 */
    csrr    t0, mstatus
    andi    t0, t0, ~(1 << 3)   /* MIE = 0 */
    csrw    mstatus, t0

    RESTORE_REGS
    csrrw   sp, mscratch, sp
    mret
```

> **注意：** 嵌套中断需要确保 `mscratch` 指向的 trap 栈足够大（容纳多层的上下文），或者为每个中断优先级分配独立的栈。

---

## 8. 思考题

1. 为什么 `mscratch` 要在初始化时设置，而不是在 trap entry 里动态分配？
2. 如果 `trap_handler` 是 C 函数，C 编译器会自动保存/恢复 callee-saved 寄存器（s0-s11），那为什么 trap entry 还需要保存 s0-s11？
3. 在嵌套中断场景中，如果 `mscratch` 只有一个，第二层中断会覆盖第一层的 `sp` 吗？如何解决？
4. 为什么 `write_mtimecmp` 要先写高 32 位为 `0xFFFFFFFF`？

---

## 小结

| 要点 | 说明 |
|------|------|
| 复位状态 | M-mode，中断全关，PC=0x1000（QEMU virt 复位向量，ROM 跳转到 0x80000000） |
| Trap 栈切换 | `csrrw sp, mscratch, sp` 原子交换 |
| 上下文保存 | 保存所有通用寄存器，共 256 字节 |
| 定时器中断 | CLINT mtimecmp，注意 64-bit 写入顺序 |
| 嵌套中断 | 手动设置 `MIE=1`，恢复前清零 |
| 调试技巧 | `ebreak` 触发 QEMU 断点，GDB remote 调试 |

→ 下一实验：[实验二：最小 SBI 实现与跨模式调用](./41-lab-minimal-sbi.md)
