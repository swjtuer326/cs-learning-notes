# 实验四：H 扩展与两阶段地址翻译

> 本实验在 QEMU 上启用 RISC-V H 扩展，配置两阶段地址翻译，展示 GVA → GPA → HPA 的完整流程。这是理解 RISC-V 服务器虚拟化的核心实验。

---

## 实验目标

1. 在 QEMU 上启动带 H 扩展的 RISC-V Linux
2. 编写 KVM 用户态程序，创建 VM 并运行 Guest
3. 配置两阶段页表（vsatp + hgatp）
4. 观察 VM Exit/Entry 流程

---

## 前置知识

- [虚拟化：H 扩展与 KVM](../03-privileged/virtualization.md)
- [内存管理：Sv39x4](../03-privileged/memory-management.md)

---

## 1. 环境准备

### 1.1 检查 KVM 支持

```bash
# 宿主机需要是 RISC-V 且内核编译了 KVM
ls /dev/kvm
# 输出: /dev/kvm

# 检查 cpuinfo
cat /proc/cpuinfo | grep isa
# 期望包含: rv64imafdc_h

# 加载 KVM 模块
sudo modprobe kvm
```

### 1.2 使用 QEMU 启动嵌套虚拟化

```bash
# 启动 Host Linux（第一层 QEMU）
qemu-system-riscv64 \
    -machine virt,aia=aplic-imsic \
    -cpu rv64,h=true \
    -smp 4 -m 8G -nographic \
    -bios opensbi/fw_dynamic.bin \
    -kernel host-linux/Image \
    -append "root=/dev/vda2 console=ttyS0" \
    -drive file=host-rootfs.ext4,format=raw

# 在 Host Linux 内部加载 KVM，再启动 Guest
```

> 如果没有物理 RISC-V 机器，可以在 x86 上用 QEMU 模拟 RISC-V，但无法测试 KVM（因为 QEMU 不支持 RISC-V 嵌套虚拟化）。本实验需要在真实 RISC-V 硬件或支持嵌套虚拟化的环境中运行。

---

## 2. KVM API 用户态程序

```c
/* kvm_vm.c — 最小 KVM 用户态程序 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <linux/kvm.h>

#define RAM_SIZE (256 * 1024 * 1024)  /* 256MB */

int main(void) {
    int kvm_fd, vm_fd, vcpu_fd;
    struct kvm_userspace_memory_region mem;
    struct kvm_run *run;
    size_t mmap_size;

    /* 1. 打开 KVM */
    kvm_fd = open("/dev/kvm", O_RDWR);
    if (kvm_fd < 0) {
        perror("open /dev/kvm");
        return 1;
    }

    /* 2. 创建 VM */
    vm_fd = ioctl(kvm_fd, KVM_CREATE_VM, 0);
    if (vm_fd < 0) {
        perror("KVM_CREATE_VM");
        return 1;
    }

    /* 3. 分配 Guest 内存 */
    void *ram = mmap(NULL, RAM_SIZE, PROT_READ | PROT_WRITE,
                     MAP_SHARED | MAP_ANONYMOUS, -1, 0);
    memset(ram, 0, RAM_SIZE);

    /* 加载 Guest 镜像到内存（例如一个裸机程序） */
    FILE *f = fopen("guest.bin", "rb");
    if (f) {
        fread(ram, 1, RAM_SIZE, f);
        fclose(f);
    }

    /* 4. 设置 Guest 内存 */
    memset(&mem, 0, sizeof(mem));
    mem.slot = 0;
    mem.guest_phys_addr = 0x80000000;  /* GPA */
    mem.memory_size = RAM_SIZE;
    mem.userspace_addr = (unsigned long)ram;
    ioctl(vm_fd, KVM_SET_USER_MEMORY_REGION, &mem);

    /* 5. 创建 vCPU */
    vcpu_fd = ioctl(vm_fd, KVM_CREATE_VCPU, 0);
    if (vcpu_fd < 0) {
        perror("KVM_CREATE_VCPU");
        return 1;
    }

    /* 6. 映射 kvm_run 结构 */
    mmap_size = ioctl(kvm_fd, KVM_GET_VCPU_MMAP_SIZE, 0);
    run = mmap(NULL, mmap_size, PROT_READ | PROT_WRITE,
               MAP_SHARED, vcpu_fd, 0);

    /* 7. 设置 vCPU 寄存器 */
    /* 注意：以下为概念代码。实际 RISC-V KVM API 中应使用 KVM_SET_ONE_REG
     * 通过 ioctl(vcpu_fd, KVM_SET_ONE_REG, &one_reg) 设置单个寄存器，
     * one_reg.id = KVM_REG_RISCV | KVM_REG_RISCV_CORE | ...，
     * 而非 KVM_SET_REGS（该接口在 RISC-V KVM 中可能不可用）。 */
    struct kvm_regs regs;
    memset(&regs, 0, sizeof(regs));
    regs.pc = 0x80000000;       /* Guest 入口 */
    regs.sp = 0x80000000 + RAM_SIZE;
    ioctl(vcpu_fd, KVM_SET_REGS, &regs);

    /* 8. 设置 S-mode CSR */
    struct kvm_sregs sregs;
    ioctl(vcpu_fd, KVM_GET_SREGS, &sregs);
    sregs.satp = 0;             /* Guest 早期不使用 MMU */
    ioctl(vcpu_fd, KVM_SET_SREGS, &sregs);

    /* 9. 运行 VM */
    printf("Running VM...\n");
    while (1) {
        int ret = ioctl(vcpu_fd, KVM_RUN, 0);
        if (ret < 0) {
            perror("KVM_RUN");
            break;
        }

        switch (run->exit_reason) {
        case KVM_EXIT_HLT:
            printf("Guest halted\n");
            goto done;

        case KVM_EXIT_MMIO:
            /* Guest 访问 MMIO */
            printf("MMIO: addr=0x%llx, is_write=%d\n",
                   (unsigned long long)run->mmio.phys_addr,
                   run->mmio.is_write);
            /* 模拟设备... */
            break;

        case KVM_EXIT_IO:
            /* Guest 执行 I/O */
            printf("IO exit\n");
            break;

        case KVM_EXIT_INTR:
            /* 被信号中断 */
            break;

        case KVM_EXIT_FAIL_ENTRY:
            printf("Fail entry: hw_entry_failure_reason=0x%llx\n",
                   (unsigned long long)run->fail_entry.hardware_entry_failure_reason);
            goto done;

        case KVM_EXIT_INTERNAL_ERROR:
            printf("Internal error\n");
            goto done;

        default:
            printf("Unknown exit reason: %d\n", run->exit_reason);
            goto done;
        }
    }

done:
    munmap(run, mmap_size);
    close(vcpu_fd);
    close(vm_fd);
    close(kvm_fd);
    munmap(ram, RAM_SIZE);
    return 0;
}
```

---

## 3. 手动配置两阶段页表（内核模块视角）

以下代码展示 KVM 内核模块如何配置 hgatp 和 vsatp：

```c
/* kvm_riscv_stage2.c — 概念代码，非完整实现 */

/* 第二阶段页表（Host 管理） */
#define STAGE2_PAGE_SIZE    4096
#define HGATP_MODE_SV39X4   (8ULL << 60)

struct kvm_mmu_page {
    uint64_t spt[512];  /* Stage-2 page table */
};

/* 建立 GPA → HPA 映射（第二阶段） */
void kvm_riscv_stage2_map_page(struct kvm *kvm,
                                uint64_t gpa, uint64_t hpa,
                                uint64_t flags) {
    struct kvm_mmu_page *root = kvm->arch.pgd;
    uint64_t *table = root->spt;

    /* Sv39x4: 3 级页表（根页表 1024 项） */
    /* Level 3 (root): GPA[39:30], 10-bit index, 1024 entries */
    uint64_t idx3 = (gpa >> 30) & 0x3FF;
    uint64_t pte3 = table[idx3];
    if (!(pte3 & PTE_V)) {
        struct kvm_mmu_page *new = alloc_stage2_page();
        table[idx3] = make_pte(virt_to_phys(new), PTE_V);
        pte3 = table[idx3];
    }
    table = phys_to_virt((pte3 >> PTE_PPN_SHIFT) << 12);

    /* Level 2: GPA[29:21], 9-bit index */
    uint64_t idx2 = (gpa >> 21) & 0x1FF;
    uint64_t pte2 = table[idx2];
    if (!(pte2 & PTE_V)) {
        struct kvm_mmu_page *new = alloc_stage2_page();
        table[idx2] = make_pte(virt_to_phys(new), PTE_V);
        pte2 = table[idx2];
    }
    table = phys_to_virt((pte2 >> PTE_PPN_SHIFT) << 12);

    /* Level 1 (leaf): GPA[20:12], 9-bit index */
    uint64_t idx1 = (gpa >> 12) & 0x1FF;
    table[idx1] = make_pte(hpa, flags | PTE_V | PTE_A | PTE_D);
}

/* VM 切换时写 hgatp */
void kvm_riscv_vcpu_load(struct kvm_vcpu *vcpu, int cpu) {
    struct kvm_mmu_page *pgd = vcpu->kvm->arch.pgd;
    uint64_t hgatp = HGATP_MODE_SV39X4 |
                     (vcpu->kvm->arch.vmid << 44) |
                     (virt_to_phys(pgd) >> 12);

    csr_write(CSR_HGATP, hgatp);
    /* 刷新第二阶段 TLB */
    asm volatile("hfence.gvma zero, zero" ::: "memory");
}

/* Guest 切换时写 vsatp（通过 KVM_SET_SREGS 设置） */
void kvm_riscv_vcpu_put(struct kvm_vcpu *vcpu) {
    /* 保存 Guest CSR */
    vcpu->arch.vsatp = csr_read(CSR_VSATP);
    vcpu->arch.vsstatus = csr_read(CSR_VSSTATUS);
    /* ... */

    /* 禁用第二阶段翻译 */
    csr_write(CSR_HGATP, 0);
    asm volatile("hfence.gvma zero, zero" ::: "memory");
}
```

---

## 4. 观察 VM Exit/Entry

### 4.1 使用 tracepoints（需要 root）

```bash
# 启用 KVM tracepoints
cd /sys/kernel/debug/tracing
echo 1 > events/kvm/kvm_entry/enable
echo 1 > events/kvm/kvm_exit/enable
echo 1 > events/kvm/kvm_page_fault/enable

# 运行 KVM 程序，然后查看 trace
cat trace
```

### 4.2 常见 VM Exit 原因分析

| Exit 原因 | 触发条件 | 优化方向 |
|-----------|----------|----------|
| ECALL | Guest SBI 调用 | 批量处理、NACL |
| MMIO | 访问虚拟设备 | VirtIO、设备直通 |
| WFI | Guest idle | HALT polling |
| Timer | 定时器到期 | 虚拟定时器优化 |
| Page Fault | GPA 未映射 | 预映射大页 |

---

## 5. 思考题

1. 为什么第二阶段页表需要 `Sv39x4` 而不是普通的 `Sv39`？
2. `VMID` 的作用是什么？如果没有 VMID，VM 切换时需要做什么？
3. 在嵌套虚拟化场景中（L0 → L1 → L2），需要几阶段页表？
4. 为什么 `hfence.gvma` 需要指定 VMID？什么情况下可以用 `zero, zero`？

---

## 小结

| 要点 | 说明 |
|------|------|
| 两阶段翻译 | vsatp（GVA→GPA）+ hgatp（GPA→HPA） |
| Sv39x4 | 第二阶段专用，3 级页表（根页表 1024 项），1TB GPA |
| VMID | TLB 标记，避免 VM 切换全刷 |
| VM Exit/Entry | Guest trap → Host KVM 处理 → sret 返回 |
| KVM API | /dev/kvm → KVM_CREATE_VM → KVM_CREATE_VCPU → KVM_RUN |

---

## 参考

- [KVM RISC-V 内核代码](https://github.com/torvalds/linux/tree/master/arch/riscv/kvm)
- [QEMU RISC-V H 扩展支持](https://github.com/qemu/qemu/tree/master/target/riscv)
- [RISC-V H 扩展规范](https://github.com/riscv/riscv-isa-manual)
