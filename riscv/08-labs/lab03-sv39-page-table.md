# 实验三：Sv39 页表建立与缺页处理

> 本实验在实验二的基础上，为 S-mode 启用 Sv39 虚拟内存。你将亲手建立页表、处理缺页异常，并理解 Linux 早期页表建立的核心逻辑。

---

## 实验目标

1. 建立 Sv39 三级页表，实现恒等映射（Identity Mapping）
2. 启用 MMU（写 satp + sfence.vma）
3. 处理缺页异常（Page Fault），动态分配页表
4. 实现用户态/内核态的地址空间隔离

---

## 前置知识

- [内存管理](../03-privileged/memory-management.md)
- [实验二：最小 SBI 实现](./lab02-minimal-sbi.md)

---

## 1. Sv39 页表回顾

```
虚拟地址 (39-bit):
  38    30 29    21 20    12 11         0
┌─────────┬─────────┬─────────┬───────────┐
│  VPN[2] │  VPN[1] │  VPN[0] │ Page Offset│
│  9 bits │  9 bits │  9 bits │  12 bits   │
└─────────┴─────────┴─────────┴───────────┘

页表项 (PTE):
  63  54 53  10 9  8  7  6  5  4  3  2  1  0
┌─────┬─────────┬───┬───┬───┬───┬───┬───┬───┐
│  0  │   PPN   │ R │ W │ X │ U │ G │ A │ D │ V │
└─────┴─────────┴───┴───┴───┴───┴───┴───┴───┘
```

---

## 2. 页表建立代码

```c
/* vm.c — 虚拟内存管理 */
#include <stdint.h>

#define PAGE_SIZE       4096
#define PAGE_SHIFT      12
#define PTE_V           (1 << 0)
#define PTE_R           (1 << 1)
#define PTE_W           (1 << 2)
#define PTE_X           (1 << 3)
#define PTE_U           (1 << 4)
#define PTE_G           (1 << 5)
#define PTE_A           (1 << 6)
#define PTE_D           (1 << 7)

#define PTE_PPN_SHIFT   10

/* 页表区域：从 0x80200000 开始，预留 1MB */
static uint64_t *next_page = (uint64_t *)0x80200000;

static uint64_t *alloc_page(void) {
    uint64_t *p = next_page;
    next_page += PAGE_SIZE / sizeof(uint64_t);
    /* 清零页 */
    for (int i = 0; i < PAGE_SIZE / sizeof(uint64_t); i++) {
        p[i] = 0;
    }
    return p;
}

/* 创建 PTE：PPN + flags */
static inline uint64_t make_pte(uint64_t paddr, uint64_t flags) {
    return ((paddr >> PAGE_SHIFT) << PTE_PPN_SHIFT) | flags;
}

/* 从虚拟地址提取 VPN */
static inline uint64_t vpn(uint64_t vaddr, int level) {
    return (vaddr >> (PAGE_SHIFT + level * 9)) & 0x1FF;
}

/* 建立映射：vaddr → paddr */
void map_page(uint64_t *root, uint64_t vaddr, uint64_t paddr, uint64_t flags) {
    uint64_t *table = root;

    for (int level = 2; level > 0; level--) {
        uint64_t idx = vpn(vaddr, level);
        uint64_t pte = table[idx];

        if (!(pte & PTE_V)) {
            /* 分配下一级页表 */
            uint64_t *new_table = alloc_page();
            table[idx] = make_pte((uint64_t)new_table, PTE_V);
            pte = table[idx];
        }

        /* 提取下一级页表地址 */
        table = (uint64_t *)(((pte >> PTE_PPN_SHIFT) << PAGE_SHIFT));
    }

    /* 第 0 级：叶子节点 */
    uint64_t idx0 = vpn(vaddr, 0);
    table[idx0] = make_pte(paddr, flags | PTE_V | PTE_A | PTE_D);
}

/* 查询映射（用于调试） */
uint64_t walk_page(uint64_t *root, uint64_t vaddr) {
    uint64_t *table = root;
    for (int level = 2; level >= 0; level--) {
        uint64_t idx = vpn(vaddr, level);
        uint64_t pte = table[idx];
        if (!(pte & PTE_V)) {
            return 0;  /* 未映射 */
        }
        if (pte & (PTE_R | PTE_W | PTE_X)) {
            /* 叶子节点 */
            uint64_t ppn = (pte >> PTE_PPN_SHIFT) & ((1ULL << 44) - 1);
            uint64_t offset = vaddr & (PAGE_SIZE - 1);
            return (ppn << PAGE_SHIFT) | offset;
        }
        table = (uint64_t *)(((pte >> PTE_PPN_SHIFT) << PAGE_SHIFT));
    }
    return 0;
}
```

---

## 3. S-mode 启用 MMU

```asm
/* spayload.S — 启用 MMU 后进入 C */
s_payload:
    la      sp, __stack_top

    /* 建立早期页表 */
    call    setup_vm

    /* 写 satp：MODE=Sv39(8), ASID=0, PPN=root>>12 */
    la      t0, early_pgdir
    srli    t0, t0, 12          /* PPN */
    li      t1, (8ULL << 60)    /* MODE = Sv39 */
    or      t0, t0, t1
    csrw    satp, t0

    /* 刷新 TLB */
    sfence.vma

    /* 现在可以安全地使用虚拟地址了 */
    /* 跳转到高地址（如果做了内核映射） */
    la      t0, s_main_high
    jr      t0

s_main_high:
    call    s_main
1:
    wfi
    j       1b
```

```c
/* vm.c continued */

/* 早期页表根节点 */
static uint64_t early_pgdir[512] __attribute__((aligned(PAGE_SIZE)));

void setup_vm(void) {
    /* 清零根页表 */
    for (int i = 0; i < 512; i++) {
        early_pgdir[i] = 0;
    }

    /* 恒等映射：0x80000000 → 0x80000000，1GB 超级页 */
    /* 使用 1GB 超级页（Giga Page），减少页表层级 */
    uint64_t vaddr = 0x80000000ULL;
    uint64_t paddr = 0x80000000ULL;
    uint64_t flags = PTE_R | PTE_W | PTE_X;

    /* 对于 1GB 超级页，直接在 L2 建立叶子节点 */
    uint64_t idx2 = vpn(vaddr, 2);
    early_pgdir[idx2] = make_pte(paddr, flags | PTE_V | PTE_A | PTE_D);
    /* 注意：超级页需要 PTE 的 R/W/X 非零，且 level=1/2 时即为叶子 */

    /* 也可以映射 UART 区域（0x10000000）用于调试 */
    map_page(early_pgdir, 0x10000000ULL, 0x10000000ULL,
             PTE_R | PTE_W | PTE_X);
}
```

> **超级页（Super Page）：** 在 L2（或 L1）直接建立叶子节点，跳过下级页表。1GB 超级页只需一个 PTE，TLB 压力最小，是内核早期映射的首选。

---

## 4. 缺页异常处理

```c
/* trap.c — S-mode trap handler */

#define CAUSE_LOAD_PAGE_FAULT   13
#define CAUSE_STORE_PAGE_FAULT  15
#define CAUSE_INST_PAGE_FAULT   12

/* 简化的页分配器 */
static uint64_t next_free_page = 0x80300000;

static uint64_t alloc_free_page(void) {
    uint64_t p = next_free_page;
    next_free_page += PAGE_SIZE;
    /* 清零 */
    uint64_t *ptr = (uint64_t *)p;
    for (int i = 0; i < PAGE_SIZE / 8; i++) {
        ptr[i] = 0;
    }
    return p;
}

uint64_t s_trap_handler(uint64_t scause, uint64_t sepc, uint64_t stval) {
    int is_interrupt = (scause >> 63) & 1;
    int code = scause & 0x7FF;

    if (!is_interrupt && (code == CAUSE_LOAD_PAGE_FAULT ||
                          code == CAUSE_STORE_PAGE_FAULT ||
                          code == CAUSE_INST_PAGE_FAULT)) {
        /* 缺页异常：动态分配物理页并建立映射 */
        uint64_t vaddr = stval & ~(PAGE_SIZE - 1);
        uint64_t paddr = alloc_free_page();

        map_page(early_pgdir, vaddr, paddr, PTE_R | PTE_W | PTE_U);

        /* 刷新 TLB */
        __asm__ volatile("sfence.vma %0, zero" :: "r"(vaddr));

        return sepc;  /* 重新执行触发异常的指令 */
    }

    /* 其他异常：停机调试 */
    while (1) { __asm__ volatile("wfi"); }
}
```

---

## 5. 用户态地址空间隔离

```c
/* 创建独立的用户页表 */
void create_user_page_table(uint64_t *root) {
    /* 用户代码段映射 */
    map_page(root, 0x10000, alloc_free_page(), PTE_R | PTE_X | PTE_U);

    /* 用户数据段/栈映射 */
    map_page(root, 0x7FFFF000, alloc_free_page(), PTE_R | PTE_W | PTE_U);
}

/* 切换到用户态 */
void enter_user_mode(void) {
    /* 设置用户页表 */
    uint64_t *user_pgdir = alloc_page();
    create_user_page_table(user_pgdir);

    uint64_t satp_val = (8ULL << 60) | (((uint64_t)user_pgdir) >> 12);
    __asm__ volatile("csrw satp, %0" :: "r"(satp_val));
    __asm__ volatile("sfence.vma");

    /* 设置 sstatus: SPP=0 使 sret 返回 U-mode */
    uint64_t sstatus;
    __asm__ volatile("csrr %0, sstatus" : "=r"(sstatus));
    sstatus &= ~(1UL << 8);   /* SPP = 0 → sret 返回 U-mode */
    __asm__ volatile("csrw sstatus, %0" :: "r"(sstatus));

    /* 设置 sepc = 用户入口 */
    __asm__ volatile("csrw sepc, %0" :: "r"(0x10000));

    /* sret → 进入 U-mode */
    __asm__ volatile("sret");
}
```

---

## 6. 验证页表建立

```c
void test_vm(void) {
    /* 测试 1：恒等映射 */
    uint64_t pa = walk_page(early_pgdir, 0x80000000);
    if (pa == 0x80000000) {
        sbi_puts("Identity map OK\n");
    }

    /* 测试 2：缺页分配 */
    volatile int *test_ptr = (int *)0x90000000;
    *test_ptr = 42;  /* 触发缺页 */
    if (*test_ptr == 42) {
        sbi_puts("Page fault handler OK\n");
    }

    /* 测试 3：未映射地址触发异常 */
    /* volatile int x = *(int *)0xDEAD0000; */  /* 应该触发异常 */
}
```

---

## 7. 与 Linux 早期页表的对比

| 特性 | 本实验 | Linux `setup_vm()` |
|------|--------|-------------------|
| 页表级数 | 3 级（Sv39） | 3 级（Sv39） |
| 映射方式 | 1GB 超级页 + 4KB 页 | 1GB 超级页为主 |
| 缺页处理 | 动态分配 | 早期无，后期由 `do_page_fault()` 处理 |
| 用户空间 | 独立页表 | 每个进程独立页表 |
| ASID | 未使用 | 进程切换时使用 |

---

## 小结

| 要点 | 说明 |
|------|------|
| Sv39 三级页表 | VPN[2:0] 每级 9 位，对应 512 个 PTE |
| 超级页 | L2/L1 直接叶子，减少 TLB miss |
| 启用 MMU | `csrw satp` + `sfence.vma` |
| 缺页处理 | 分配物理页 → 建立映射 → 刷新 TLB → 返回重试 |
| 用户隔离 | 独立页表 + U-bit，sret 进入 U-mode |

→ 下一实验：[实验四：H 扩展与两阶段地址翻译](./lab04-h-extension-two-stage-mmu.md)
