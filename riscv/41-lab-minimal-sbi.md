# 实验二：最小 SBI 实现与跨模式调用

> 本实验实现一个最小化的 OpenSBI 替代品，展示 M-mode 与 S-mode 的交互机制。你将亲手搭建 M-mode 固件，初始化 S-mode 环境，并通过 SBI 接口为 S-mode 提供服务。

---

## 实验目标

1. 理解 M-mode 到 S-mode 的启动握手
2. 实现 PMP 配置，让 S-mode 安全访问内存
3. 实现中断委托（medeleg/mideleg）
4. 实现基础 SBI 调用（console putchar、set_timer）
5. 在 S-mode 运行一个 "Hello from S-mode" 程序

---

## 前置知识

- [特权模式与 CSR](../03-privileged/privileged-modes-and-csr.md)
- [启动流程](../03-privileged/boot-process.md)
- [实验一：裸机 Trap Handler](./lab01-baremetal-trap-handler.md)

---

## 1. 整体架构

```
┌─────────────────────────────────────────┐
│           M-mode (Firmware)             │
│  ┌─────────┐    ┌──────────────────┐   │
│  │ 复位入口 │ → │ PMP / 委托 / SBI │   │
│  └─────────┘    └──────────────────┘   │
│         ↓ ecall (SBI 调用)              │
│  ┌──────────────────────────────────┐  │
│  │      SBI Handler (M-mode)        │  │
│  │  - sbi_console_putchar           │  │
│  │  - sbi_set_timer                 │  │
│  │  - sbi_shutdown                  │  │
│  └──────────────────────────────────┘  │
└─────────────────────────────────────────┘
                    ↓ mret
┌─────────────────────────────────────────┐
│           S-mode (Payload)              │
│  ┌──────────────────────────────────┐  │
│  │  用户程序 / 简易内核              │  │
│  │  - 使用 ecall 请求 M-mode 服务   │  │
│  │  - 处理自己的异常/中断           │  │
│  └──────────────────────────────────┘  │
└─────────────────────────────────────────┘
```

---

## 2. M-mode 固件代码

### 2.1 启动与初始化（mfw.S）

```asm
/* mfw.S — M-mode Firmware */
    .section .text.init
    .global _start

    .equ SBI_CONSOLE_PUTCHAR, 0x01
    .equ SBI_SET_TIMER,       0x00
    .equ SBI_SHUTDOWN,        0x08

_start:
    /* 关闭中断 */
    csrw    mie, zero
    csrw    mstatus, zero

    /* 设置 M-mode 栈 */
    la      sp, _mstack_top

    /* 设置 trap 向量 */
    la      t0, m_trap_entry
    csrw    mtvec, t0

    /* ====== PMP：允许 S-mode 访问全部内存 ====== */
    /* PMP entry 0: 整个地址空间，NAPOT，R/W/X */
    li      t0, -1              /* 0xFFFFFFFFFFFFFFFF */
    srli    t0, t0, 2           /* 0x3FFFFFFFFFFFFFFF (NAPOT 编码) */
    csrw    pmpaddr0, t0
    li      t0, 0x1F            /* NAPOT | R | W | X */
    csrw    pmpcfg0, t0

    /* ====== 委托：把常见 trap 委托给 S-mode ====== */
    li      t0, (1 << 0)  |   /* instruction misaligned */
            (1 << 1)  |   /* instruction access fault */
            (1 << 2)  |   /* illegal instruction */
            (1 << 3)  |   /* breakpoint */
            (1 << 4)  |   /* load misaligned */
            (1 << 5)  |   /* load access fault */
            (1 << 6)  |   /* store/AMO misaligned */
            (1 << 7)  |   /* store/AMO access fault */
            (1 << 8)  |   /* U-mode ecall */
            (1 << 12) |   /* instruction page fault */
            (1 << 13) |   /* load page fault */
            (1 << 15)     /* store/AMO page fault */
    csrw    medeleg, t0

    li      t0, (1 << 1) |    /* S-mode software */
            (1 << 5) |    /* S-mode timer */
            (1 << 9)      /* S-mode external */
    csrw    mideleg, t0

    /* ====== 准备跳转到 S-mode ====== */
    /* 设置 mepc = S-mode 入口 */
    la      t0, s_payload
    csrw    mepc, t0

    /* 设置 mstatus.MPP = S-mode (01) */
    li      t0, (1 << 11)       /* MPP = 01 */
    csrw    mstatus, t0

    /* 设置 mscratch = M-mode 栈顶（用于 M-mode trap） */
    la      t0, _mstack_top
    csrw    mscratch, t0

    /* mret → 进入 S-mode */
    mret

/* ============================================================
 * M-mode Trap Handler: 处理 SBI 调用 (ecall from S-mode)
 * ============================================================ */
    .align  4
m_trap_entry:
    /* 保存 S-mode 上下文到 M-mode 栈 */
    addi    sp, sp, -256
    sd      ra, 0(sp)
    sd      a0, 32(sp)
    sd      a1, 40(sp)
    sd      a2, 48(sp)
    sd      a3, 56(sp)
    sd      a4, 64(sp)
    sd      a5, 72(sp)
    sd      a6, 80(sp)          /* fid */
    sd      a7, 88(sp)          /* eid */

    /* 检查是否是 ecall from S-mode */
    csrr    t0, mcause
    li      t1, 9               /* ecall from S-mode */
    bne     t0, t1, m_trap_unknown

    /* 分发 SBI 调用 */
    ld      a7, 88(sp)          /* eid */
    ld      a6, 80(sp)          /* fid */

    li      t0, SBI_CONSOLE_PUTCHAR
    beq     a7, t0, sbi_putchar

    li      t0, SBI_SET_TIMER
    beq     a7, t0, sbi_set_timer

    li      t0, SBI_SHUTDOWN
    beq     a7, t0, sbi_shutdown

    /* 未知 SBI 调用，返回错误 */
    li      a0, -1
    j       sbi_return

sbi_putchar:
    ld      a0, 32(sp)          /* char */
    /* 写入 QEMU UART0 (0x10000000) */
    li      t0, 0x10000000
    sb      a0, 0(t0)
    li      a0, 0               /* success */
    j       sbi_return

sbi_set_timer:
    ld      a0, 32(sp)          /* stime_value 低 32 */
    ld      a1, 40(sp)          /* stime_value 高 32 */
    /* 写入 CLINT mtimecmp（先写高 32 位再写低 32 位，避免中间值触发虚假中断） */
    li      t0, 0x2004000       /* mtimecmp for hart 0 */
    sw      a1, 4(t0)           /* 先写高 32 位 */
    sw      a0, 0(t0)           /* 再写低 32 位 */
    li      a0, 0
    j       sbi_return

sbi_shutdown:
    /* 写 QEMU sifive_test 寄存器触发关机 */
    li      t0, 0x100000
    li      t1, 0x5555
    sw      t1, 0(t0)
    j       sbi_shutdown        /* 应该不会执行到这里 */

m_trap_unknown:
    /* 未知 trap，停机调试 */
    j       m_trap_unknown

sbi_return:
    /* 返回值放入 a0/a1 */
    sd      a0, 32(sp)
    sd      a1, 40(sp)

    ld      ra, 0(sp)
    addi    sp, sp, 256

    /* mepc += 4，跳过 ecall */
    csrr    t0, mepc
    addi    t0, t0, 4
    csrw    mepc, t0

    mret

    .section .bss
    .align  16
_mstack:
    .space  8192
_mstack_top:
```

> **PMP 的 NAPOT 编码：** `pmpaddr = (addr >> 2) | ((size/4)-1)`。对于整个地址空间，`pmpaddr = 0x3FFFFFFFFFFFFFFF`。

### 2.2 链接脚本（注意 M-mode 和 S-mode 的内存布局）

```ld
/* linker.ld */
OUTPUT_ARCH(riscv)
ENTRY(_start)

MEMORY {
    RAM (rwx) : ORIGIN = 0x80000000, LENGTH = 128M
}

SECTIONS {
    . = ORIGIN(RAM);

    /* M-mode firmware */
    .text.mfw : {
        *(.text.init)
        *(.text.mfw)
    } > RAM

    .rodata.mfw : {
        *(.rodata.mfw)
    } > RAM

    .data.mfw : {
        *(.data.mfw)
    } > RAM

    .bss.mfw : {
        __mfw_bss_start = .;
        *(.bss.mfw)
        *(COMMON)
        __mfw_bss_end = .;
    } > RAM

    /* S-mode payload 放在 1MB 偏移处 */
    . = ORIGIN(RAM) + 0x100000;
    s_payload = .;

    .text.spayload : {
        *(.text.spayload)
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

---

## 3. S-mode Payload

```asm
/* spayload.S — S-mode Payload */
    .section .text.spayload
    .global s_payload

s_payload:
    /* S-mode 入口 */
    /* 设置 S-mode 栈 */
    la      sp, __stack_top

    /* 设置 S-mode trap 向量 */
    la      t0, s_trap_entry
    csrw    stvec, t0

    /* 清空 BSS */
    la      t0, __bss_start
    la      t1, __bss_end
1:
    bge     t0, t1, 2f
    sd      zero, 0(t0)
    addi    t0, t0, 8
    j       1b
2:

    call    s_main

3:
    wfi
    j       3b

/* S-mode trap handler */
    .align  4
s_trap_entry:
    /* 简单处理：直接停机，本实验重点不在 S-mode trap */
    j       s_trap_entry
```

```c
/* s_main.c — S-mode 主程序 */
#include <stdint.h>

/* SBI 调用封装 */
static inline long sbi_call(long eid, long fid,
                            long arg0, long arg1, long arg2,
                            long arg3, long arg4, long arg5) {
    register long a0 __asm__("a0") = arg0;
    register long a1 __asm__("a1") = arg1;
    register long a2 __asm__("a2") = arg2;
    register long a3 __asm__("a3") = arg3;
    register long a4 __asm__("a4") = arg4;
    register long a5 __asm__("a5") = arg5;
    register long a6 __asm__("a6") = fid;
    register long a7 __asm__("a7") = eid;

    __asm__ volatile("ecall"
                     : "+r"(a0), "+r"(a1)
                     : "r"(a2), "r"(a3), "r"(a4), "r"(a5),
                       "r"(a6), "r"(a7)
                     : "memory");
    return a0;
}

#define SBI_CONSOLE_PUTCHAR 0x01
#define SBI_SET_TIMER       0x00
#define SBI_SHUTDOWN        0x08

void sbi_putchar(char c) {
    sbi_call(SBI_CONSOLE_PUTCHAR, 0, c, 0, 0, 0, 0, 0);
}

void sbi_puts(const char *s) {
    while (*s) {
        sbi_putchar(*s++);
    }
}

void sbi_shutdown(void) {
    sbi_call(SBI_SHUTDOWN, 0, 0, 0, 0, 0, 0, 0);
}

void s_main(void) {
    sbi_puts("Hello from S-mode!\n");
    sbi_puts("Requesting shutdown...\n");
    sbi_shutdown();

    /* 不应该执行到这里 */
    while (1) { __asm__ volatile("wfi"); }
}
```

---

## 4. Makefile

```makefile
CROSS_COMPILE ?= riscv64-unknown-elf-
CC      = $(CROSS_COMPILE)gcc
LD      = $(CROSS_COMPILE)ld
OBJCOPY = $(CROSS_COMPILE)objcopy
OBJDUMP = $(CROSS_COMPILE)objdump

CFLAGS  = -march=rv64imac -mabi=lp64 -mcmodel=medany \
          -ffreestanding -nostdlib -nostartfiles \
          -O2 -Wall -g

LDFLAGS = -T linker.ld

OBJS    = mfw.o spayload.o s_main.o

all: firmware.elf

firmware.elf: $(OBJS)
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $^

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
	rm -f *.o firmware.elf

.PHONY: all dump run debug clean
```

---

## 5. 运行验证

```bash
make run
```

期望输出：
```
Hello from S-mode!
Requesting shutdown...
```

然后 QEMU 自动退出。

---

## 6. 进阶：添加 S-mode 中断委托验证

修改 M-mode 固件，在初始化后触发一个 S-mode 定时器中断：

```c
/* 在 mfw 初始化后，mret 之前添加 */
void setup_s_timer(void) {
    /* 设置 mtimecmp，让 S-mode 很快收到定时器中断 */
    volatile uint64_t *mtimecmp = (uint64_t *)0x2004000;
    volatile uint64_t *mtime = (uint64_t *)0x200BFF8;
    *mtimecmp = *mtime + 1000;  /* 很快触发 */
}
```

在 S-mode 添加定时器中断处理：

```asm
s_trap_entry:
    /* 保存上下文 */
    addi    sp, sp, -256
    /* ... save regs ... */

    csrr    t0, scause
    li      t1, (1 << 63) | 5   /* S-mode timer interrupt */
    beq     t0, t1, s_timer_irq

    /* 其他 trap 处理 */
    j       s_trap_halt

s_timer_irq:
    /* 处理定时器中断 */
    /* 重新设置定时器... */
    /* ... restore regs ... */
    sret
```

---

## 7. 与真实 OpenSBI 的对比

| 特性 | 本实验最小 SBI | 真实 OpenSBI |
|------|---------------|--------------|
| 代码量 | ~200 行汇编 | 数万行 C/汇编 |
| 支持的 SBI 扩展 | 3 个 | 20+ 个 |
| 多核支持 | 无 | 完整 HSM 扩展 |
| 设备树 | 无 | 完整 FDT 解析和传递 |
| 平台抽象 | 无 | 平台驱动框架 |
| 安全性 | 基础 PMP | 完整的 PMP 分区 |

> 本实验剥离了所有复杂性，让你看清 M/S 模式交互的本质。

---

## 小结

| 要点 | 说明 |
|------|------|
| M→S 跳转 | 设置 `mepc`、`mstatus.MPP`、PMP、委托，然后 `mret` |
| PMP 必须配置 | 否则 S-mode 访问任何内存都会触发 access fault |
| 委托减少 M-mode 负担 | 常见异常/中断直接由 S-mode 处理 |
| SBI 是 M/S 契约 | ecall 是调用方式，EID/FID 是接口编号 |
| 返回值 | a0=错误码，a1=返回值（如有） |

→ 下一实验：[实验三：Sv39 页表建立与缺页处理](./lab03-sv39-page-table.md)
