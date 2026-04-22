# 汇编与底层编程

> 汇编语言是理解硬件和操作系统的桥梁。掌握 RISC-V 汇编、调用约定和裸机编程，是系统软件工程师的必备技能。

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
qemu-system-riscv64 -machine virt -nographic -bios none -kernel firmware.bin
```

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

---

## 小结

| 要点 | 说明 |
|------|------|
| 伪指令简化编程 | li/la/mv/ret 等伪指令让汇编更易读 |
| 调用约定 | a0-a7 参数，s0-s11 保存，sp 16 字节对齐 |
| 内联汇编 | 用于访问 CSR 和特殊指令 |
| 裸机编程 | 入口汇编 → C main → 链接脚本 → QEMU 运行 |
| CSR 宏 | 封装 csrr/csrw/csrs/csrc 为可读的宏 |

→ 下一节：[操作系统移植](./os-porting.md)
