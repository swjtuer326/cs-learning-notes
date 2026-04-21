# MSI / MSI-X 中断机制

> 核心问题：PCIe设备如何高效地异步通知CPU？MSI/MSI-X在内核中如何实现？
> 关联索引：[PCIe核心知识索引](./pcie-learning-resources.md) Phase 0, 5

---

## 0. 前置背景

### 0.1 什么是中断

中断是设备通知CPU有事件需要处理的机制。CPU不需要轮询设备状态，而是等待设备主动发出信号：

```
无中断: CPU不断检查设备状态 → 浪费CPU时间
有中断: CPU做其他工作 → 设备完成时通知CPU → CPU响应处理
```

### 0.2 中断控制器

CPU不是直接接收中断信号，而是通过中断控制器中转：

| 架构 | 中断控制器 | MSI目标地址 |
|------|----------|------------|
| x86 | Local APIC (每CPU一个) | 0xFEEx_xxxx (APIC MMIO区域) |
| ARM | GIC ITS (Interrupt Translation Service) | GIC ITS寄存器地址 |
| RISC-V | AIA IMSIC | IMSIC MMIO地址 |

MSI的Message Address就是中断控制器的MMIO地址，写入该地址即触发中断。

### 0.3 为什么PCIe不用引脚中断

传统PCI使用4根物理引脚(INTA-INTD)传递中断信号，存在严重问题：

```
INTx的问题:
1. 共享: 多个设备共享一根引脚，CPU收到中断后必须逐个查询是哪个设备
2. 数量少: 只有4根线，大型系统严重不足
3. 边带信号: 不在PCIe TLP路径上，Switch需要额外逻辑转发
4. 不支持虚拟化: VF无法拥有独立引脚
```

MSI将中断投递变为一次Memory Write TLP，完全融入PCIe数据路径，解决了上述所有问题。

### 0.4 x86 MSI地址格式

x86架构下，MSI的Message Address指向Local APIC的MMIO区域，格式有严格定义：

```
Message Address (32-bit模式):
  [31:20] = 0xFEE  (APIC MMIO基址)
  [19:12] = Destination ID (目标CPU的APIC ID)
  [11]    = RH (Redirection Hint)
  [10]    = DM (Delivery Mode: 0=Fixed, 1=Lowest Priority)
  [9:0]   = 0 (保留)

Message Data:
  [15:8]  = Vector (中断向量号, 0x10-0xFE)
  [7:0]   = Delivery Mode | Trigger Mode | Level等
```

```
Message Address (64-bit模式, 支持x2APIC):
  [63:40] = 0
  [39:32] = Destination ID [31:8] (x2APIC扩展)
  [31:20] = 0xFEE
  [19:12] = Destination ID [7:0]
  ...同上
```

> 📌 ARM架构使用GIC ITS，MSI Address指向ITS的GITS_TRANSLATER寄存器，DeviceID由硬件自动附加（从BDF推导）。

---

## 1. 规范机制

### 1.1 中断演进

```mermaid
graph LR
    INTx["INTx<br/>引脚中断<br/>4线共享"] --> MSI["MSI<br/>1-32向量<br/>无Per-Vector Mask"] --> MSIX["MSI-X<br/>1-2048向量<br/>Per-Vector Mask"]
```

| 特性 | INTx | MSI | MSI-X |
|------|------|-----|-------|
| 投递方式 | Message模拟边带信号 | MemWr TLP | MemWr TLP |
| 向量数 | 4 (共享) | 1-32 (2的幂) | 1-2048 |
| Per-Vector Mask | ✗ | ✗ | ✓ |
| 地址/数据 | 固定路由 | Capability寄存器 | MMIO表 |
| 配置位置 | Interrupt Pin/Line | MSI Capability | MSI-X Capability + BIR |

### 1.2 MSI本质

MSI的中断投递就是一次**Memory Write TLP**：

```
设备发起 MemWr TLP:
  Address = Message Address (Local APIC地址)
  Data    = Message Data (向量号)
  → APIC接收 → 触发CPU中断
```

> 不需要中断引脚，不需要共享，不需要CPU轮询。

### 1.3 MSI Capability结构

```
Config Space (Capability链表):
┌─────────────────────────────┐
│ Cap ID = 0x05 (MSI)         │  +0
│ Next Ptr                    │  +1
│ Message Control             │  +2  [16bit]
│   [6:4] MME: 启用的向量数   │
│   [7]   64-bit Address Cap  │
│   [8]   Per-Vector Mask Cap │
│   [0]   MSI Enable          │
├─────────────────────────────┤
│ Message Address (Low)       │  +4  [32bit]
│ Message Address (High)      │  +8  [32bit, 可选]
│ Message Data                │  +8/+C [16bit]
│ Mask Bits                   │  +C/+10 [32bit, 可选]
│ Pending Bits                │  +10/+14 [32bit, 可选]
└─────────────────────────────┘
```

### 1.4 MSI-X Capability与表结构

```
Config Space:
┌─────────────────────────────┐
│ Cap ID = 0x11 (MSI-X)       │  +0
│ Next Ptr                    │  +1
│ Message Control             │  +2  [16bit]
│   [10:0] Table Size (N-1)   │
│   [14]   Function Mask       │
│   [15]   MSI-X Enable        │
├─────────────────────────────┤
│ Table Offset/BIR            │  +4  [偏移 | BAR索引]
│ PBA Offset/BIR              │  +8  [偏移 | BAR索引]
└─────────────────────────────┘

MSI-X Table (MMIO, 每项16B):
┌──────────────────────────────┐
│ Entry 0:                     │
│   Message Address (Low)      │  +0
│   Message Address (High)     │  +4
│   Message Data               │  +8
│   Vector Control (Mask bit)  │  +C  [bit0=1: masked]
├──────────────────────────────┤
│ Entry 1: ...                 │
└──────────────────────────────┘

Pending Bit Array (PBA):
  Bit[i] = 1: Entry[i]有中断被Mask时挂起
```

**MSI vs MSI-X关键区别**：
- MSI的地址/数据在Config Space中，数量有限(最多32)
- MSI-X的地址/数据在MMIO表中(BAR空间)，数量可达2048
- MSI-X支持Per-Vector Mask，MSI不支持(除非有Extended Capability)

---

## 2. Linux内核实现

### 2.1 代码结构

```
drivers/pci/msi/
├── msi.c          # MSI核心逻辑: enable/disable/mask/unmask/write_msg
├── api.c          # 驱动API: pci_enable_msi/msix/alloc_irq_vectors
├── irqdomain.c    # irqdomain集成: 分配/释放IRQ
├── legacy.c       # 非irqdomain路径 (x86传统)
└── pcidev_msi.c   # pci_dev MSI相关操作
```

### 2.2 全局开关

```c
// drivers/pci/msi/msi.c
bool pci_msi_enable = true;  // 可通过 pci=nomsi 关闭
```

### 2.3 MSI支持检查

```c
// drivers/pci/msi/msi.c
static int pci_msi_supported(struct pci_dev *dev, int nvec)
{
    if (!pci_msi_enable)
        return 0;
    if (!dev || dev->no_msi)
        return 0;
    if (nvec < 1)
        return 0;

    // 检查整条路径上的桥是否支持MSI
    for (bus = dev->bus; bus; bus = bus->parent)
        if (bus->bus_flags & PCI_BUS_FLAGS_NO_MSI)
            return 0;

    return 1;
}
```

### 2.4 驱动API演进

```mermaid
graph TD
    OLD_MSI["旧API<br/>pci_enable_msi()<br/>pci_enable_msix_range()"] --> NEW["新API<br/>pci_alloc_irq_vectors()"]
    OLD_MSI -->|"单向量MSI"| DEV_IRQ["dev->irq"]
    NEW -->|"统一MSI/MSI-X"| RANGE["minvec..maxvec<br/>自动选择MSI或MSI-X"]

    style NEW fill:#e8f5e9
```

**推荐使用新API**：

```c
// 驱动中启用MSI-X
int nvec = pci_alloc_irq_vectors(dev, minvec, maxvec,
                                  PCI_IRQ_MSIX | PCI_IRQ_MSI);
if (nvec < 0)
    return nvec;  // 失败

// 请求中断
for (i = 0; i < nvec; i++)
    request_irq(pci_irq_vector(dev, i), handler, 0, name, data);

// 清理
pci_free_irq_vectors(dev);
```

### 2.5 MSI消息写入

```c
// drivers/pci/msi/msi.c
void __pci_write_msi_msg(struct msi_desc *entry, struct msi_msg *msg)
{
    struct pci_dev *dev = msi_desc_to_pci_dev(entry);

    if (entry->pci.msi_attrib.is_msix) {
        // MSI-X: 写入MMIO表
        void __iomem *base = pci_msix_desc_addr(entry);
        writel(msg->address_lo, base + PCI_MSIX_ENTRY_LOWER_ADDR);
        writel(msg->address_hi, base + PCI_MSIX_ENTRY_UPPER_ADDR);
        writel(msg->data, base + PCI_MSIX_ENTRY_DATA);
    } else {
        // MSI: 写入Config Space
        int pos = dev->msi_cap;
        pci_write_config_dword(dev, pos + PCI_MSI_ADDRESS_LO,
                               msg->address_lo);
        if (entry->pci.msi_attrib.is_64) {
            pci_write_config_dword(dev, pos + PCI_MSI_ADDRESS_HI,
                                   msg->address_hi);
            pci_write_config_word(dev, pos + PCI_MSI_DATA_64,
                                  msg->data);
        } else {
            pci_write_config_word(dev, pos + PCI_MSI_DATA_32,
                                  msg->data);
        }
    }
}
```

### 2.6 MSI-X Mask/Unmask

```c
// drivers/pci/msi/msi.c
void pci_msi_update_mask(struct msi_desc *desc, u32 clear, u32 set)
{
    struct pci_dev *dev = msi_desc_to_pci_dev(desc);
    raw_spinlock_t *lock = &dev->msi_lock;

    if (!desc->pci.msi_attrib.can_mask)
        return;

    raw_spin_lock_irqsave(lock, flags);
    desc->pci.msi_mask &= ~clear;
    desc->pci.msi_mask |= set;
    pci_write_config_dword(dev, desc->pci.mask_pos, desc->pci.msi_mask);
    raw_spin_unlock_irqrestore(lock, flags);
}

// MSI-X的Per-Vector Mask
void __pci_msix_mask_desc(struct msi_desc *desc, u32 mask)
{
    void __iomem *addr = pci_msix_desc_addr(desc);
    u32 ctrl = readl(addr + PCI_MSIX_ENTRY_VECTOR_CTRL);
    ctrl |= PCI_MSIX_ENTRY_CTRL_MASKBIT;
    writel(ctrl, addr + PCI_MSIX_ENTRY_VECTOR_CTRL);
}
```

### 2.7 irqdomain集成

```c
// drivers/pci/msi/irqdomain.c
int pci_msi_setup_msi_irqs(struct pci_dev *dev, int nvec, int type)
{
    struct irq_domain *domain = dev_get_msi_domain(&dev->dev);

    if (domain && irq_domain_is_hierarchy(domain))
        // 现代路径: 通过irqdomain分配
        return msi_domain_alloc_irqs_all_locked(&dev->dev,
                                                 MSI_DEFAULT_DOMAIN, nvec);

    // 传统路径: 架构特定实现
    return pci_msi_legacy_setup_msi_irqs(dev, nvec, type);
}
```

```mermaid
graph TD
    DRV["驱动调用<br/>pci_alloc_irq_vectors()"] --> CORE["MSI Core<br/>msi.c"]
    CORE --> CHECK["pci_msi_supported()"]
    CHECK --> DOMAIN{"有irqdomain?"}
    DOMAIN -->|是| HIER["msi_domain_alloc_irqs()<br/>irqdomain.c"]
    DOMAIN -->|否| LEGACY["pci_msi_legacy_setup()<br/>legacy.c"]
    HIER --> ALLOC["irq_domain_alloc_irqs()"]
    ALLOC --> WRITE["__pci_write_msi_msg()"]
    LEGACY --> WRITE

    style DRV fill:#e8f5e9
    style WRITE fill:#fff3e0
```

---

## 3. 中断亲和性与多向量

### 3.1 多向量MSI

MSI支持1-32个向量（2的幂），通过Multiple Message Enable (MME) 控制：

```c
// MSI Message Control
// MME [6:4]: 启用的向量数 = 2^MME
// MMC [3:1]: 设备支持的最大向量数 = 2^MMC

// 驱动请求4个MSI向量:
int nvec = pci_alloc_irq_vectors(dev, 4, 4, PCI_IRQ_MSI);
// MME = 2 (2^2 = 4 vectors)
// Data字段的低2位作为向量偏移
```

> 多向量MSI要求连续的向量号，分配可能失败。MSI-X无此限制。

### 3.2 中断亲和性

```c
// 设置中断亲和性 (绑定到特定CPU)
struct cpumask mask;
cpumask_clear(&mask);
cpumask_set_cpu(3, &mask);
irq_set_affinity(pci_irq_vector(dev, 0), &mask);

// 内核自动调整Message Address
// x86: 修改APIC目标地址
// ARM: 修改GIC ITS的Interrupt Translation Table
```

### 3.3 中断重映射 (Interrupt Remapping)

在虚拟化环境中，VF的MSI地址可能被恶意篡改（GPA指向其他VM的内存）。Intel VT-d提供**中断重映射**保护：

```
无中断重映射:
  VF → MemWr TLP (Address=任意) → APIC → 中断到任意CPU
  风险: VF可伪造中断攻击其他VM

有中断重映射:
  VF → MemWr TLP → IOMMU IRTE检查 → 合法则重映射 → APIC
  IRTE (Interrupt Remapping Table Entry):
    - 验证Source ID (BDF) 是否匹配
    - 验证中断向量是否在授权范围
    - 可修改目标CPU和向量号
```

```c
// Intel VT-d中断重映射
struct irte {
    u64 present:1;        // 条目有效
    u64 fpd:1;            // Fault Processing Disable
    u64 dm:1;             // Delivery Mode
    u64 rh:1;             // Redirection Hint
    u64 tm:1;             // Trigger Mode
    u64 pad:3;
    u64 av:1;             // Auto Verify (SVT)
    u64 sid:16;           // Source ID (BDF)
    u64 dest_id:32;       // 目标APIC ID (可被重写)
    // ...
};
```

> 📌 中断重映射是VF直通安全的必要条件。Linux内核在启用VFIO时自动检查IOMMU中断重映射支持。

### 3.4 动态MSI-X分配

Linux 5.17+ 支持运行时动态分配MSI-X向量：

```c
// 检查是否支持动态分配
bool can_dyn = pci_msix_can_alloc_dyn(dev);

// 动态分配一个向量
struct msi_map map = pci_msix_alloc_irq_at(dev, MSI_ANY_INDEX, NULL);
if (map.virq > 0)
    request_irq(map.virq, handler, 0, name, data);

// 释放
pci_msix_free_irq(dev, map);
```

---

## 4. MSI-X与SR-IOV

VF需要独立的MSI-X向量，内核提供了PF驱动的回调接口：

```c
// PF驱动实现
struct pci_driver my_pf_driver = {
    .sriov_get_vf_total_msix = my_get_vf_total_msix,
    .sriov_set_msix_vec_count = my_set_vf_msix_count,
};

// 查看VF总MSI-X向量数
// cat /sys/bus/pci/devices/.../sriov_vf_total_msix

// 设置单个VF的MSI-X向量数
// echo 32 > /sys/bus/pci/devices/.../sriov_vf_msix_count
```

> 📌 详见 [SR-IOV虚拟化](./sriov-virtualization.md)

---

## 5. 实战调试

### 5.1 查看中断信息

```bash
# 设备中断统计
cat /proc/interrupts | grep -i "nvme\|mlx\|eth"

# MSI-X表 (需要debugfs)
cat /sys/kernel/debug/pci/0000:01:00.0/msix

# 查看设备MSI/MSI-X状态
lspci -vvv -s 01:00.0 | grep -E "MSI|MSI-X"

# 查看已分配的IRQ向量
ls /sys/kernel/irq/ | head -20
cat /sys/kernel/irq/32/type  # 查看IRQ类型
```

### 5.2 常见问题

| 现象 | 原因 | 排查 |
|------|------|------|
| `nvec=-ENOSPC` | 可用向量不足 | 减少maxvec或检查`/proc/interrupts` |
| 中断丢失 | MSI-X Entry被Mask | 检查Vector Control bit0 |
| 性能差 | 所有中断在同一CPU | 设置`irqaffinity=`内核参数 |
| VF无MSI-X | PF未配置VF向量 | 检查`sriov_vf_total_msix` |
| `pci=nomsi` | 全局禁用MSI | 移除该内核参数 |

### 5.3 性能优化

```bash
# irqbalance守护进程 (自动分配中断亲和性)
systemctl status irqbalance

# 手动设置NVMe多队列中断亲和性
for i in /sys/kernel/irq/*/; do
    if grep -q nvme "$i/name" 2>/dev/null; then
        echo $(cat "$i/smp_affinity_list")
    fi
done
```

---

## 6. 代码阅读路线

| 顺序 | 文件 | 关注函数 |
|------|------|----------|
| 1 | `drivers/pci/msi/api.c` | `pci_enable_msi()`, `pci_enable_msix_range()`, `pci_alloc_irq_vectors()` |
| 2 | `drivers/pci/msi/msi.c` | `__pci_write_msi_msg()`, `__pci_read_msi_msg()`, `pci_msi_update_mask()` |
| 3 | `drivers/pci/msi/irqdomain.c` | `pci_msi_setup_msi_irqs()`, `pci_msi_domain_write_msg()` |
| 4 | `drivers/pci/msi/legacy.c` | 传统x86 MSI路径 |
| 5 | `include/uapi/linux/pci_regs.h` | `PCI_MSI_*`, `PCI_MSIX_*` 寄存器定义 |

```mermaid
graph TD
    A["api.c<br/>驱动入口"] --> B["msi.c<br/>核心逻辑"]
    B --> C["irqdomain.c<br/>IRQ分配"]
    B --> D["legacy.c<br/>传统路径"]
    B --> E["__pci_write_msi_msg()<br/>写入Config/MMIO"]

    C -->|"irq_domain_alloc_irqs()"| F["内核IRQ子系统"]
    E -->|"MemWr TLP"| G["Local APIC / GIC"]

    style A fill:#e8f5e9
    style B fill:#fff3e0
    style E fill:#e3f2fd
```

---

*源码版本：Linux 6.x | 更新：2026-04-21*
