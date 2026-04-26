# RISC-V AIA 完全指南：从入门到实践

> 本文档面向初学者和开发者，从问题出发，逐步讲解 RISC-V 高级中断架构的设计动机、核心概念和实际应用。
>
> **参考资料：**
> - The RISC-V Advanced Interrupt Architecture Specification
> - RISC-V Privileged Architecture Specification
> - Linux Kernel MSI/IOMMU 文档
> - GitHub: riscv/riscv-aia

---

## 目录

### 第一部分：背景与动机
- [1. 中断是什么？为什么需要中断？](#1-中断是什么为什么需要中断)
- [2. 中断控制器的演进历史](#2-中断控制器的演进历史)
- [3. RISC-V PLIC 的五大痛点](#3-risc-v-plic-的五大痛点)
- [4. AIA 如何解决这些问题](#4-aia-如何解决这些问题)

### 第二部分：核心概念详解
- [5. 有线中断 (Wired Interrupt)](#5-有线中断-wired-interrupt)
- [6. MSI (Message-Signaled Interrupt)](#6-msi-message-signaled-interrupt)
- [7. 为什么 MSI 比有线中断更好？](#7-为什么-msi-比有线中断更好)
- [8. 虚拟化与中断的困境](#8-虚拟化与中断的困境)
- [9. IOMMU：连接设备与虚拟化的桥梁](#9-iommu连接设备与虚拟化的桥梁)

### 第三部分：AIA 架构详解
- [10. AIA 整体架构](#10-aia-整体架构)
- [11. IMSIC 详解](#11-imsic-详解)
- [12. APLIC 详解](#12-aplic-详解)
- [13. 中断流转完整示例](#13-中断流转完整示例)

### 第四部分：虚拟化深入
- [14. Guest 中断文件](#14-guest-中断文件)
- [15. IOMMU 中断重映射](#15-iommu-中断重映射)
- [16. MRIF：内存驻留中断文件](#16-mrif内存驻留中断文件)

### 第五部分：开发者实践
- [17. CSR 寄存器完整参考](#17-csr-寄存器完整参考)
- [18. 代码示例](#18-代码示例)
- [19. QEMU 实践指南](#19-qemu-实践指南)
- [20. 常见问题与调试](#20-常见问题与调试)

---

# 第一部分：背景与动机

## 1. 中断是什么？为什么需要中断？

### 1.1 一个生活中的类比

想象你在办公室工作：

**没有中断的世界（轮询模式）：**
- 你需要每隔 5 秒去前台看看有没有你的快递
- 即使没有快递，你也要跑一趟
- 如果快递到了但你没去前台，你就不知道

**有中断的世界：**
- 前台收到你的快递后，主动打电话通知你
- 你可以专心工作，不用频繁跑前台
- 收到通知后你再去处理

**中断的本质：** 外部事件发生时，硬件主动通知 CPU，让 CPU 暂停当前工作去处理紧急事件。

### 1.2 计算机中的中断

```
时间线：
CPU: [执行任务 A] -----> [收到中断] -----> [处理中断] -----> [继续任务 A]
                         ↑
设备：[数据准备好了] ────┘
```

常见的中断来源：
- **键盘/鼠标** — 用户输入
- **网卡** — 收到网络数据包
- **磁盘** — 数据读写完成
- **定时器** — 时间片到期，操作系统进行任务切换
- **错误** — 内存错误、设备异常等

### 1.3 为什么需要中断控制器？

现代计算机有几十甚至上百个设备可能产生中断。如果每个设备都直接连到 CPU：

```
问题 1：CPU 的中断引脚数量有限
问题 2：多个设备同时中断怎么办？
问题 3：如何知道是哪个设备中断？
问题 4：哪些中断更重要，应该优先处理？
```

**中断控制器**就是为了解决这些问题而存在的。它就像一个"前台秘书"：

```
设备1 ──┐
设备2 ──┤
设备3 ──┼──> 中断控制器 ──> CPU
 ...   ─┤                    (一个引脚)
设备N ──┘
```

中断控制器的职责：
1. **汇聚** — 收集所有设备的中断请求
2. **仲裁** — 决定哪个中断优先处理
3. **路由** — 把中断发送给合适的 CPU 核心
4. **通知** — 告诉 CPU 是哪个设备产生了中断

---

## 2. 中断控制器的演进历史

### 2.1 早期：PIC (Programmable Interrupt Controller)

```
┌─────────────────────────────────────┐
│         Intel 8259 PIC              │
│                                     │
│  8 个中断输入 ──> 1 个中断输出       │
│  可级联：最多 15 个中断              │
│  固定优先级                         │
└─────────────────────────────────────┘
```

**特点：**
- 只能管理少量中断（最多 15 个）
- 优先级固定，不可配置
- 单核时代够用

### 2.2 多核时代：APIC (Advanced PIC)

```
┌─────────────────────────────────────┐
│              Local APIC             │
│         (每个 CPU 核心一个)          │
│                                     │
│  IO APIC ──> Local APIC ──> CPU0   │
│            ──> Local APIC ──> CPU1  │
│            ──> Local APIC ──> CPU2  │
└─────────────────────────────────────┘
```

**改进：**
- 支持多核 CPU
- 可以配置中断发送给哪个核心
- 支持更多中断源（最多 256 个）

### 2.3 PCI 时代：引脚中断 → MSI

PCI 设备最初使用**引脚中断**（INTx）：

```
PCI 设备 ──> INTA/INTB/INTC/INTD 引脚 ──> 中断控制器 ──> CPU
```

**问题：**
- 多个设备共享同一个中断引脚
- CPU 收到中断后，需要逐个询问设备："是你中断的吗？"
- 效率低下

**解决方案：MSI (Message-Signaled Interrupt)**

```
PCI 设备 ──> 内存写入 ──> 直接触发 CPU 中断
           (不需要共享引脚)
```

### 2.4 RISC-V 的路线：PLIC → AIA

```
时间线：

2015-2020          2022-2024          未来
  │                  │                 │
  ▼                  ▼                 ▼
PLIC ──────────> AIA ──────────> 更高级的中断架构
(基础)           (高级)
```

---

## 3. RISC-V PLIC 的五大痛点

PLIC (Platform-Level Interrupt Controller) 是 RISC-V 最初的中断控制器标准。随着系统越来越复杂，PLIC 的问题逐渐暴露。

### 痛点 1：不支持 MSI

```
PLIC 只能处理有线中断：

设备 ──> 物理中断线 ──> PLIC ──> CPU

对于 PCIe 等现代设备：
- 设备本身支持 MSI（通过内存写入触发中断）
- 但 PLIC 无法接收 MSI
- 需要额外的硬件将 MSI 转换为有线中断
- 增加了复杂性和延迟
```

**实际影响：** PCIe 网卡、NVMe 固态硬盘等现代设备都优先使用 MSI，PLIC 无法直接支持。

### 痛点 2：M-mode 和 S-mode 共享寄存器

```
PLIC 的全局寄存器同时被 M-mode 和 S-mode 访问：

┌─────────────────────────────────────┐
│         PLIC 全局寄存器              │
│                                     │
│  M-mode 读写 ◄────┐                 │
│                   ├──── 冲突！       │
│  S-mode 读写 ◄────┘                 │
└─────────────────────────────────────┘
```

**问题：**
- 两个特权级可能同时修改配置
- 需要软件加锁保护
- 增加了开销和复杂性

### 痛点 3：占用大量物理地址空间

PLIC 的寄存器布局：

```
PLIC 地址空间：
├── 优先级寄存器：1024 个 × 4 字节 = 4KB
├── Pending 位图：1024 位 = 128 字节
├── 使能寄存器：每个 hart 128 字节 × hart 数量
├── 阈值寄存器：每个 hart 4 字节
├── Claim/Complete：每个 hart 4 字节
└── 总地址空间：可能达到数 MB
```

**问题：** 在嵌入式系统中，地址空间是宝贵资源。

### 痛点 4：不支持中断线触发方式配置

```
PLIC 无法配置中断是边沿触发还是电平触发：

边沿触发：中断信号从低变高时触发一次
         ┌─
         │
      ───┘

电平触发：中断信号为高期间持续触发
      ┌──────────
      │
──────┘

不同设备需要不同的触发方式，PLIC 无法灵活配置。
```

### 痛点 5：虚拟化支持几乎为零

这是 PLIC 最大的问题。在虚拟化场景中：

```
┌─────────────────────────────────────┐
│           Hypervisor                │
│                                     │
│  ┌─────────┐  ┌─────────┐           │
│  │  VM1    │  │  VM2    │           │
│  │ (Guest) │  │ (Guest) │           │
│  └────┬────┘  └────┬────┘           │
│       │            │                 │
│       └────┬───────┘                 │
│            ▼                         │
│         PLIC (共享)                   │
└─────────────────────────────────────┘

问题：
1. PLIC 不知道中断属于哪个 VM
2. 所有中断必须先交给 Hypervisor
3. Hypervisor 再决定转发给哪个 VM
4. 每次中断都需要 Hypervisor 介入
5. 中断延迟大幅增加
```

**实际影响：** 在虚拟化环境中，设备中断延迟可能增加数倍，严重影响性能。

---

## 4. AIA 如何解决这些问题

AIA (Advanced Interrupt Architecture) 针对 PLIC 的每个痛点都提供了解决方案：

| PLIC 痛点 | AIA 解决方案 |
|-----------|-------------|
| 不支持 MSI | 引入 IMSIC，原生支持 MSI |
| M/S-mode 共享寄存器 | 每个特权级有独立的 CSR 和中断文件 |
| 占用大量地址空间 | 使用 CSR 间接访问，减少 MMIO 空间 |
| 不支持触发方式配置 | APLIC 的 sourcecfg 可配置边沿/电平触发 |
| 虚拟化支持差 | Guest 中断文件 + IOMMU 中断重映射 |

**AIA 的核心设计理念：**

```
1. 分离关注点
   - APLIC 处理有线中断
   - IMSIC 处理 MSI
   - 各司其职，互不干扰

2. 硬件级虚拟化
   - 每个 VM 有独立的中断文件
   - VM 可以直接处理设备中断
   - 减少 Hypervisor 介入

3. 灵活的优先级
   - 软件可配置所有中断的优先级
   - 支持中断抢占
   - 本地中断和外部中断可以混合排序
```

---

# 第二部分：核心概念详解

## 5. 有线中断 (Wired Interrupt)

### 5.1 什么是"有线"？

"有线"指的是**物理信号线**。设备通过一根真实的电线连接到中断控制器。

```
┌──────────┐         ┌──────────┐         ┌──────┐
│  UART    │──IRQ──> │          │         │      │
│  设备    │         │  APLIC   │──IRQ──> │ CPU  │
├──────────┤         │          │         │      │
│  GPIO    │──IRQ──> │          │         │      │
│  设备    │         │          │         │      │
└──────────┘         └──────────┘         └──────┘
     ↑                    ↑
   物理线               物理线
```

### 5.2 有线中断的两种触发方式

**电平触发 (Level-triggered)：**

```
中断线电平：
高电平 ────────────────────────> 持续产生中断请求
         ┌─────────────
         │
低电平 ──┘

特点：
- 只要电平为高，中断就一直 pending
- 设备处理完后，设备拉低电平，中断自动清除
- 如果软件忘记处理，中断会持续触发
- 更可靠，不容易丢失中断
```

**边沿触发 (Edge-triggered)：**

```
中断线电平：
         ┌─ 上升沿触发
         │
─────────┘

特点：
- 只在电平变化时触发一次
- 软件必须清除中断状态
- 如果软件没及时处理，后续变化不会被记录
- 响应更快，但可能丢失中断
```

### 5.3 有线中断的局限性

```
问题 1：每增加一个设备，就需要一根中断线
        100 个设备 = 100 根线 = 芯片引脚不够用

问题 2：中断线只能连接到一个中断控制器
        设备移动到其他核心？需要重新布线

问题 3：中断线数量受硬件限制
        芯片设计时就确定了，无法动态增加

问题 4：多核系统中，路由复杂
        哪个核心处理哪个设备的中断？
```

**这就是为什么需要 MSI...**

---

## 6. MSI (Message-Signaled Interrupt)

### 6.1 MSI 的核心思想

MSI 的本质：**把中断变成一次内存写入操作**。

```
传统有线中断：
设备 ──> 拉高中断线 ──> 中断控制器检测到电平变化 ──> 通知 CPU

MSI 中断：
设备 ──> 向特定地址写入特定数据 ──> 硬件识别为中断 ──> 通知 CPU
```

### 6.2 MSI 是如何工作的？

**配置阶段（系统启动时）：**

```
1. 操作系统为设备分配一个 MSI 地址和数据：
   
   MSI 地址：0x28000040  (指向某个 hart 的 IMSIC)
   MSI 数据：0x00000015  (中断 ID = 21)

2. 操作系统把地址和数据写入设备的配置寄存器：
   
   设备配置空间：
   ├── MSI Address Low  = 0x28000040
   ├── MSI Address High = 0x00000000
   └── MSI Data         = 0x00000015
```

**运行阶段（设备需要中断时）：**

```
3. 设备需要中断时，执行一次内存写入：
   
   *(uint32_t *)0x28000040 = 0x00000015;
   
4. 这个写入被硬件识别为中断请求
5. IMSIC 接收到中断 ID = 21
6. IMSIC 通知对应的 CPU hart
```

### 6.3 MSI 地址的结构

```
MSI 地址 (64-bit)：
┌─────────────────────────────────────────────────────────────┐
│                     基地址 (4KB 对齐)                        │
│                                                             │
│  高 32 位                  │  低 32 位                       │
│  ┌─────────────────────┐   ┌─────────────────────────────┐  │
│  │  目标 hart 标识      │   │  中断文件偏移                │  │
│  │  (哪个 CPU 核心)     │   │  (M-mode/S-mode/Guest)      │  │
│  └─────────────────────┘   └─────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘

MSI 数据：
┌─────────────────────────────────────────────────────────────┐
│                    中断 ID (Interrupt Identity)              │
│                                                             │
│  0 = 无效/伪中断                                             │
│  1-2047 = 有效中断                                           │
└─────────────────────────────────────────────────────────────┘
```

### 6.4 为什么 MSI 地址是 4KB 对齐？

```
4KB = 4096 字节 = 一页内存

原因：
1. 每个中断文件占用 4KB 空间
2. 写入该空间内的任何地址都会触发中断
3. 4KB 对齐简化了地址解码硬件
4. 与操作系统的页管理对齐
```

---

## 7. 为什么 MSI 比有线中断更好？

### 7.1 对比表格

| 特性 | 有线中断 | MSI |
|------|---------|-----|
| 物理资源 | 需要中断线 | 不需要，使用内存写入 |
| 共享 | 多个设备可能共享中断线 | 每个设备有独立的中断向量 |
| 扩展性 | 受引脚数量限制 | 理论上无限（受地址空间限制） |
| 路由灵活性 | 硬件布线决定 | 软件配置地址即可改变路由 |
| 虚拟化 | 难以虚拟化 | 天然支持虚拟化 |
| 性能 | 需要逐个查询设备 | 直接知道是哪个中断 |
| 延迟 | 较高（共享时需要轮询） | 较低（直接定位） |

### 7.2 具体场景分析

**场景 1：高性能网卡**

```
有线中断方式：
网卡收到数据包 ──> 共享中断线 ──> CPU 收到中断
CPU 需要查询：是网卡中断吗？是其他设备吗？
→ 每次中断都要轮询，浪费 CPU 时间

MSI 方式：
网卡收到数据包 ──> 写入 MSI 地址 ──> CPU 直接知道是网卡中断
→ 无需轮询，直接处理
```

**场景 2：多队列 NVMe SSD**

```
现代 NVMe SSD 有多个 I/O 队列（通常 8-64 个）

有线中断：
- 所有队列共享一个中断
- CPU 收到中断后，需要检查所有队列
- 无法并行处理

MSI：
- 每个队列有独立的中断向量
- 可以分配到不同的 CPU 核心
- 多个核心并行处理不同队列
- 性能提升数倍
```

**场景 3：多核系统中的中断负载均衡**

```
有线中断：
设备的中断线连到固定的中断控制器
→ 中断总是发送给固定的 CPU 核心
→ 某个核心可能过载，其他核心空闲

MSI：
操作系统可以动态修改 MSI 地址
→ 把中断路由到不同的 CPU 核心
→ 实现负载均衡
```

---

## 8. 虚拟化与中断的困境

### 8.1 虚拟化的基本概念

```
┌─────────────────────────────────────────────┐
│                  物理机器                     │
│                                             │
│  ┌─────────────────────────────────────┐    │
│  │          Hypervisor (VMM)           │    │
│  │                                     │    │
│  │  ┌──────────┐  ┌──────────┐        │    │
│  │  │   VM1    │  │   VM2    │        │    │
│  │  │ ┌──────┐ │  │ ┌──────┐ │        │    │
│  │  │ │vCPU1 │ │  │ │vCPU1 │ │        │    │
│  │  │ │vCPU2 │ │  │ │vCPU2 │ │        │    │
│  │  │ └──────┘ │  │ └──────┘ │        │    │
│  │  └──────────┘  └──────────┘        │    │
│  └─────────────────────────────────────┘    │
│                                             │
│  物理 CPU 核心 0    物理 CPU 核心 1          │
└─────────────────────────────────────────────┘

关键概念：
- vCPU (虚拟 CPU)：VM 看到的"CPU"，实际是物理 CPU 的时间片
- Hypervisor：管理多个 VM 的软件（如 KVM、Xen）
- Guest OS：VM 中运行的操作系统
- Host OS：物理机器上运行的操作系统
```

### 8.2 虚拟化中的地址转换问题

```
三种地址：

1. GVA (Guest Virtual Address)
   - Guest OS 进程使用的虚拟地址
   - Guest OS 的页表转换为 GPA

2. GPA (Guest Physical Address)
   - Guest OS 看到的"物理地址"
   - 实际是虚拟的，需要 Hypervisor 转换为 HPA

3. HPA (Host Physical Address)
   - 真实的物理内存地址
   - 只有 Hypervisor 知道

转换过程：
GVA ──(Guest 页表)──> GPA ──(Hypervisor 页表)──> HPA
```

### 8.3 设备直通 (Device Passthrough)

**为什么需要设备直通？**

```
传统虚拟化（设备模拟）：

VM ──> 虚拟设备 ──> Hypervisor ──> 物理设备 ──> 实际硬件
     (软件模拟)      (软件模拟)

问题：
- 每次 I/O 都需要 Hypervisor 介入
- 性能损失 30%-50%
- 对于高性能设备（网卡、GPU）不可接受

设备直通：

VM ──> 物理设备 (直接访问)
     (通过 IOMMU 保护)

优势：
- 性能接近原生（损失 < 5%）
- VM 直接使用物理设备驱动
- 适合高性能场景
```

### 8.4 直通设备的中断问题

```
问题场景：

物理设备支持 MSI，它要发送中断：

设备配置（在 VM 中设置）：
  MSI 地址 = 0x28000040  (这是 GPA！)
  MSI 数据 = 0x00000015

设备发送中断：
  设备写入地址 0x28000040

问题：
1. 这个地址是 GPA，不是真实的物理地址
2. 如果直接写入，会写到错误的内存位置
3. 可能破坏其他 VM 或 Host 的数据！
4. 即使地址正确，中断应该投递到哪个 VM？

这就是 IOMMU 中断重映射要解决的问题...
```

---

## 9. IOMMU：连接设备与虚拟化的桥梁

### 9.1 IOMMU 是什么？

```
MMU (Memory Management Unit)：
  CPU 虚拟地址 ──> 物理地址
  保护：进程 A 不能访问进程 B 的内存

IOMMU (I/O Memory Management Unit)：
  设备 DMA 地址 ──> 物理地址
  保护：设备 A 不能访问设备 B 的内存
       设备不能访问未授权的内存区域
```

### 9.2 IOMMU 的两大功能

**功能 1：DMA 重映射 (DMA Remapping)**

```
没有 IOMMU：
设备 DMA ──> 直接访问物理内存
问题：设备可以访问任何内存，不安全

有 IOMMU：
设备 DMA ──> IOMMU 页表转换 ──> 物理内存
优势：
- 设备只能访问授权的内存区域
- 可以把不连续的物理内存映射为连续的 DMA 空间
- 每个设备有独立的地址空间
```

**功能 2：中断重映射 (Interrupt Remapping)**

```
这就是解决 8.4 中问题的关键！

设备发送 MSI：
  设备写入 GPA 地址

IOMMU 拦截：
  1. 识别这是 MSI 写入
  2. 查中断重映射表
  3. 转换为正确的 HPA（目标 IMSIC 地址）
  4. 可能修改 MSI 数据（中断 ID）
  5. 转发到正确的目标

结果：
  - VM 配置的是 GPA，没问题
  - IOMMU 自动转换为 HPA
  - 中断正确投递到目标 hart 的 IMSIC
  - 不同 VM 的中断完全隔离
```

### 9.3 IOMMU 中断重映射详解

```
┌─────────────────────────────────────────────────────────────┐
│              IOMMU 中断重映射流程                            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  VM1 中的设备                                               │
│       │                                                     │
│       │ MSI 写入                                            │
│       │ 地址: GPA 0x28000040                                │
│       │ 数据: 0x15                                          │
│       ▼                                                     │
│  ┌─────────────────┐                                        │
│  │    IOMMU        │                                        │
│  │                 │                                        │
│  │  中断重映射表：  │                                        │
│  │  ┌───────────┐  │                                        │
│  │  │ 输入      │  │ 输出                                    │
│  │  │ GPA+数据  │  │ HPA+数据                                │
│  │  ├───────────┤  │                                        │
│  │  │0x28000040 │──┼─> 0x24001000 (Hart 2, M-mode)         │
│  │  │ + 0x15    │  │ + 0x15                                  │
│  │  └───────────┘  │                                        │
│  └────────┬────────┘                                        │
│           │                                                 │
│           │ 转换后的 MSI                                     │
│           │ 地址: HPA 0x24001000                            │
│           │ 数据: 0x15                                      │
│           ▼                                                 │
│  ┌─────────────────┐                                        │
│  │  Hart 2 IMSIC   │                                        │
│  │  (M-mode 文件)   │                                       │
│  └─────────────────┘                                        │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 9.4 IOMMU 与 AIA 的关系

```
IOMMU 和 AIA 是互补的关系：

AIA 提供：
- IMSIC：接收 MSI 的硬件
- APLIC：管理有线中断并转换为 MSI
- Guest 中断文件：支持虚拟化

IOMMU 提供：
- DMA 重映射：保护内存访问
- 中断重映射：转换 MSI 地址，支持设备直通

两者结合：
- VM 可以直接控制物理设备
- 设备中断直接投递到 VM 的 guest 中断文件
- 无需 Hypervisor 介入每次中断
- 性能接近原生
```

---

# 第三部分：AIA 架构详解

## 10. AIA 整体架构

### 10.1 组件关系图

```
┌─────────────────────────────────────────────────────────────────────┐
│                        完整 AIA 系统                                 │
│                                                                     │
│  ┌──────────────┐          ┌──────────────────────────────────┐    │
│  │  有线中断设备 │          │            IMSIC                 │    │
│  │  (UART,GPIO) │          │  (每个 hart 本地)                │    │
│  │              │          │                                  │    │
│  │  中断线      │          │  ┌────────────────────────────┐  │    │
│  └──────┬───────┘          │  │  M-mode Interrupt File     │  │    │
│         │                  │  │  - eidelivery              │  │    │
│         ▼                  │  │  - eithreshold             │  │    │
│  ┌──────────────┐          │  │  - eie[0..63]              │  │    │
│  │    APLIC     │          │  │  - topi / topei            │  │    │
│  │              │          │  └────────────────────────────┘  │    │
│  │ - 汇聚中断   │          │  ┌────────────────────────────┐  │    │
│  │ - 优先级仲裁 │          │  │  S-mode Interrupt File     │  │    │
│  │ - 路由配置   │          │  │  (同上)                     │  │    │
│  │              │          │  └────────────────────────────┘  │    │
│  │ 投递模式：   │          │  ┌────────────────────────────┐  │    │
│  │ 1. Direct   │─────────>│  │  Guest Interrupt File 0    │  │    │
│  │    (线中断) │          │  │  (VM1 使用)                 │  │    │
│  │ 2. MSI      │─────────>│  └────────────────────────────┘  │    │
│  │    (MSI)    │          │  ┌────────────────────────────┐  │    │
│  └──────────────┘          │  │  Guest Interrupt File 1    │  │    │
│         │                  │  │  (VM2 使用)                 │    │
│         │ MSI              │  └────────────────────────────┘  │    │
│         ▼                  │           ...                    │    │
│  ┌──────────────┐          │  ┌────────────────────────────┐  │    │
│  │    IOMMU     │          │  │  Guest Interrupt File 254  │  │    │
│  │              │          │  │  (VM255 使用)               │  │    │
│  │ - DMA 重映射 │          │  └────────────────────────────┘  │    │
│  │ - 中断重映射 │          └──────────────┬───────────────────┘    │
│  └──────┬───────┘                         │                        │
│         │                                 │ 中断信号               │
│         ▼                                 ▼                        │
│  ┌──────────────┐                  ┌──────────────┐                │
│  │  MSI 设备     │                  │   RISC-V     │                │
│  │  (PCIe 网卡)  │────────────────>│    Hart      │                │
│  │              │   MSI 直接写入   │  (CPU Core)  │                │
│  └──────────────┘                  └──────────────┘                │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 10.2 数据流详解

**场景 1：有线中断处理流程**

```
1. UART 设备收到数据，拉高中断线
2. APLIC 检测到中断
3. APLIC 查配置：
   - 这个中断源的模式是什么？（边沿/电平）
   - 是否使能？
   - 投递模式是什么？（Direct/MSI）
   - 目标 hart 是哪个？
4. 如果是 Direct 模式：
   APLIC 直接驱动 hart 的外部中断引脚
5. hart 收到外部中断，进入中断处理程序
6. 中断处理程序读取 APLIC 获取中断详情
7. 处理完成后，清除 APLIC 中的 pending 状态
```

**场景 2：MSI 中断处理流程**

```
1. PCIe 网卡收到数据包
2. 网卡执行 MSI 写入：
   *(uint32_t *)0x28000040 = 0x00000015;
3. 写入到达 IMSIC（可能经过 IOMMU 转换）
4. IMSIC 的中断文件记录中断 ID 15 为 pending
5. IMSIC 进行优先级仲裁
6. 如果该中断优先级最高且使能：
   IMSIC 通知 hart 有外部中断
7. hart 进入中断处理程序
8. 读取 topei 获取中断 ID
9. 处理完成后，写入 topei 清除中断
```

**场景 3：APLIC 转换有线中断为 MSI**

```
1. GPIO 设备产生中断
2. APLIC 接收中断
3. APLIC 配置为 MSI 投递模式
4. APLIC 向目标 hart 的 IMSIC 写入 MSI：
   *(uint32_t *)target_imsic_address = interrupt_id;
5. 后续流程同场景 2
```

---

## 11. IMSIC 详解

### 11.1 中断文件 (Interrupt File) 是什么？

中断文件是 IMSIC 的核心概念。它不是真正的文件，而是一组**寄存器集合**，用于管理一个特权级或一个虚拟机的中断状态。

```
想象中断文件是一个"中断邮箱"：

┌─────────────────────────────────────┐
│         Interrupt File              │
│                                     │
│  收件箱 (pending 寄存器)             │
│  ├── 中断 1: 有邮件 ☑               │
│  ├── 中断 2: 无邮件 ☐               │
│  ├── 中断 3: 有邮件 ☑               │
│  └── ...                            │
│                                     │
│  过滤器 (eie 使能寄存器)             │
│  ├── 中断 1: 接收 ☑                 │
│  ├── 中断 2: 拒收 ☐                 │
│  └── 中断 3: 接收 ☑                 │
│                                     │
│  优先级标签 (eithreshold)            │
│  ── 只处理优先级 >= 5 的邮件         │
│                                     │
│  最紧急邮件 (topei)                  │
│  ── 中断 3 (优先级 8)                │
│                                     │
│  投递开关 (eidelivery)               │
│  ── 开启：把邮件通知给 CPU           │
│  ── 关闭：暂存邮件，不通知           │
└─────────────────────────────────────┘
```

### 11.2 中断文件的寄存器详解

#### eidelivery — 投递使能

```
作用：控制中断是否可以通知到 CPU

值：
  0 = 关闭投递（中断仍然记录，但不通知 CPU）
  1 = 开启投递（中断通知 CPU）

使用场景：
- 初始化时设置为 1，使能中断
- 处理中断时临时设置为 0，防止嵌套中断
- 休眠时设置为 0，唤醒后恢复
```

#### eithreshold — 中断阈值

```
作用：设置最低优先级阈值，低于此优先级的中断不投递

值：
  0 = 所有优先级的中断都投递
  1-254 = 只投递优先级 >= 该值的中断
  255 = 不投递任何中断（等效于关闭）

使用场景：
- 关键代码段：提高阈值，屏蔽低优先级中断
- 正常执行：设置为 0，接收所有中断
- 类似 x86 的中断优先级掩码
```

#### eie[0..63] — 中断使能

```
作用：每个位控制一个中断是否使能

结构：
  eie[0]: 位 0-63，控制中断 ID 0-63
  eie[1]: 位 0-63，控制中断 ID 64-127
  ...
  eie[63]: 位 0-63，控制中断 ID 4032-4095

使用：
  使能中断 ID 21：
  eie[0] 的位 21 设置为 1

  禁用中断 ID 100：
  eie[1] 的位 36 (100-64) 设置为 0
```

#### topi / topei — 最高优先级中断信息

```
topi (间接访问)：
┌─────────────────────────────────────────────────────────────┐
│                        topi (32-bit)                         │
├──────────────────────┬──────────────────────────────────────┤
│  中断 ID (12-bit)     │  中断优先级 (8-bit)  │  保留 (12-bit) │
└──────────────────────┴──────────────────────────────────────┘

topei (直接 CSR 访问)：
┌─────────────────────────────────────────────────────────────┐
│                        topei (32-bit)                        │
├──────────────────────┬──────────────────────────────────────┤
│  中断 ID (12-bit)     │            保留 (20-bit)              │
└──────────────────────┴──────────────────────────────────────┘

读取：返回当前最高优先级的 pending 且使能的中断
写入：完成该中断（清除 pending 状态）
```

### 11.3 中断文件的间接访问机制

为什么需要间接访问？

```
问题：
中断文件内部有很多寄存器（eie 有 64 个，每个 64 位）
如果每个寄存器都分配一个 CSR 编号，CSR 空间不够用

解决方案：间接访问
使用两个 CSR 配合：
  xiselect：选择要访问的内部寄存器
  xireg：读写选中的寄存器

类似"先拨分机号，再通话"：
  1. csrw siselect, 0x30    ← 选择 eidelivery
  2. csrr t0, sireg          ← 读取 eidelivery 的值
```

常用间接寄存器地址：

```
0x30 — eidelivery     投递使能
0x31 — eithreshold    中断阈值
0x32 — eie0           中断使能 0 (ID 0-63)
0x33 — eie1           中断使能 1 (ID 64-127)
...
0x70 — topi           最高优先级中断信息
0x72 — topei          最高优先级中断 ID
```

---

## 12. APLIC 详解

### 12.1 APLIC 的角色

```
APLIC 是"有线中断的总管"：

┌─────────────────────────────────────────────────────────────┐
│                     APLIC                                   │
│                                                             │
│  输入：最多 1023 个有线中断源                                │
│                                                             │
│  处理：                                                       │
│  ├── 检测中断（边沿/电平）                                   │
│  ├── 记录 pending 状态                                      │
│  ├── 优先级仲裁                                             │
│  ├── 查路由表                                               │
│  └── 投递到目标 hart                                         │
│                                                             │
│  输出：                                                       │
│  ├── Direct 模式：驱动 hart 的外部中断引脚                   │
│  └── MSI 模式：向 IMSIC 写入 MSI                             │
└─────────────────────────────────────────────────────────────┘
```

### 12.2 中断域 (Domain) 概念

```
中断域是 APLIC 中的逻辑分组：

┌─────────────────────────────────────────────────────┐
│              Root Domain (根域)                      │
│              特权级：M-mode 或 S-mode                │
│                                                     │
│  管理中断源 1-512                                    │
│  投递到 Hart 0-3                                     │
│                                                     │
│  ┌───────────────────┐  ┌───────────────────┐       │
│  │  Child Domain 1   │  │  Child Domain 2   │       │
│  │  委托的中断源      │  │  委托的中断源      │       │
│  │  513-768          │  │  769-1023         │       │
│  │  投递到 Hart 4-7  │  │  投递到 Hart 8-11 │       │
│  └───────────────────┘  └───────────────────┘       │
│                                                     │
└─────────────────────────────────────────────────────┘

为什么需要域？
1. 权限隔离：不同域可以有不同的访问权限
2. 虚拟化：每个 VM 可以有自己的域
3. 灵活路由：不同域可以路由到不同的 hart
```

### 12.3 中断源配置 (sourcecfg)

每个中断源有一个 32 位配置寄存器：

```
sourcecfg[x] 结构：

┌────────┬───────────┬────────┬────────┬──────────────────┐
│   D    │    CI     │   SM   │  保留   │    其他配置       │
│ (委托) │ (子域索引) │(源模式) │         │                  │
│ 1-bit  │  10-bit   │ 3-bit  │         │                  │
└────────┴───────────┴────────┴────────┴──────────────────┘

D (Delegation) 位：
  0 = 不委托，SM 字段有效，中断由当前域处理
  1 = 委托给子域，CI 字段指定子域索引

SM (Source Mode) 字段：
  0 = Inactive    中断屏蔽，不活跃
  1 = Detached    仅软件触发（写入 setip）
  4 = Edge1       上升沿触发
  5 = Edge0       下降沿触发
  6 = Level1      高电平触发
  7 = Level0      低电平触发
```

### 12.4 投递模式详解

#### Direct Delivery 模式

```
适用场景：
- 简单系统，没有 IMSIC
- 不需要 MSI 支持
- 中断数量较少

流程：
APLIC ──> hart 外部中断引脚 ──> CPU 中断处理

特点：
- 类似传统 PLIC
- 实现简单
- 不支持虚拟化
```

#### MSI Delivery 模式

```
适用场景：
- 有 IMSIC 的系统
- 需要 MSI 支持
- 需要虚拟化

流程：
APLIC ──> MSI 写入 ──> IMSIC ──> 中断文件 ──> CPU 中断处理

特点：
- 性能更好
- 支持虚拟化
- 可以路由到任意 hart 的任意中断文件
```

### 12.5 APLIC 寄存器布局详解

```c
struct Aplic {
    // ===== 域配置 =====
    u32 domaincfg;              // 域配置：使能、投递模式、字节序
    u32 sourcecfg[1023];        // 1023 个中断源的配置

    u8 _reserved1[0xBC0];       // 保留区域

    // ===== MSI 地址配置 =====
    u32 mmsiaddrcfg;            // M-mode MSI 地址 (低32位)
    u32 mmsiaddrcfgh;           // M-mode MSI 地址 (高32位)
    u32 smsiaddrcfg;            // S-mode MSI 地址 (低32位)
    u32 smsiaddrcfgh;           // S-mode MSI 地址 (高32位)

    u8 _reserved2[0x30];

    // ===== Pending 操作 =====
    u32 setip[32];              // 批量设置 pending (位数组，32×32=1024 位)
    u8 _reserved3[92];
    u32 setipnum;               // 按编号设置 pending (写入中断号即可)

    u8 _reserved4[0x20];
    u32 in_clrip[32];           // 批量清除 pending
    u8 _reserved5[92];
    u32 clripnum;               // 按编号清除 pending

    // ===== 使能操作 =====
    u32 setie[32];              // 批量使能中断
    u8 _reserved7[92];
    u32 setienum;               // 按编号使能

    u32 clrie[32];              // 批量禁用中断
    u8 _reserved9[92];
    u32 clrienum;               // 按编号禁用

    // ===== 特殊操作 =====
    u32 setipnum_le;            // 设置 pending (小端序)
    u32 setipnum_be;            // 设置 pending (大端序)

    u8 _reserved11[4088];

    // ===== 目标配置 =====
    u32 genmsi;                 // 生成 MSI 的目标 hart 和中断文件
    u32 target[1023];           // 每个中断源的目标 hart 配置
};
```

---

## 13. 中断流转完整示例

### 13.1 场景：PCIe 网卡中断

```
系统配置：
- 4 核 RISC-V 处理器
- 每个 hart 有 IMSIC（M-mode + S-mode 中断文件）
- APLIC 配置为 MSI 模式
- PCIe 网卡支持 MSI-X

步骤 1：系统初始化

  // 配置 APLIC
  aplic->domaincfg = IE | DM_MSI;  // 使能 + MSI 模式

  // 配置 MSI 地址
  aplic->smsiaddrcfg = 0x28000000;  // S-mode IMSIC 基地址
  aplic->smsiaddrcfgh = 0x00000000;

  // 配置网卡中断源
  aplic->sourcecfg[NET_IRQ] = Edge1;  // 上升沿触发
  aplic->target[NET_IRQ] = Hart1;     // 路由到 Hart 1

步骤 2：网卡驱动初始化

  // 配置 IMSIC S-mode 中断文件
  csrw siselect, 0x30;
  csrw sireg, 1;              // eidelivery = 1

  csrw siselect, 0x31;
  csrw sireg, 0;              // eithreshold = 0 (接收所有优先级)

  // 使能网卡中断 (假设中断 ID = 50)
  csrw siselect, 0x32 + (50 / 64);  // 选择 eie[0]
  csrs sireg, (1ULL << (50 % 64));  // 使能位 50

步骤 3：网卡收到数据包

  网卡硬件：
    1. DMA 数据包到内存
    2. 执行 MSI 写入：
       *(uint32_t *)0x28001000 = 50;

步骤 4：IMSIC 处理

  IMSIC Hart1 S-mode 中断文件：
    1. 接收中断 ID 50
    2. 标记为 pending
    3. 检查 eie[0] 位 50 = 1 (已使能)
    4. 检查 eidelivery = 1 (已使能)
    5. 检查优先级 >= eithreshold (0 >= 0，满足)
    6. 更新 topei = 50
    7. 发送中断信号到 Hart1

步骤 5：CPU 处理中断

  Hart1 收到外部中断：
    1. 保存当前上下文
    2. 跳转到中断处理程序
    3. 读取 stopei 获取中断 ID
    4. 调用网卡驱动的中断处理函数
    5. 驱动处理数据包
    6. 写入 stopei = 50 清除中断
    7. 恢复上下文，继续执行
```

### 13.2 场景：虚拟机中的设备直通

```
系统配置：
- Hypervisor (KVM)
- VM1 直通 PCIe 网卡
- IOMMU 启用中断重映射

步骤 1：Hypervisor 配置

  // 为 VM1 分配 guest 中断文件
  // Hart0, Guest File 0

  // 配置 IOMMU 中断重映射表
  // 输入：GPA 0x28000040 + Data 0x15
  // 输出：HPA (Guest File 0 地址) + Data 0x15

步骤 2：VM1 初始化网卡

  // VM1 看到的 MSI 地址 (GPA)
  MSI Address = 0x28000040
  MSI Data = 0x15

  // VM1 配置自己的中断处理
  // (VM1 不知道自己在虚拟机中)

步骤 3：网卡发送中断

  网卡写入：
    *(uint32_t *)0x28000040 = 0x15;

步骤 4：IOMMU 转换

  IOMMU 拦截写入：
    1. 识别为 MSI
    2. 查中断重映射表
    3. 转换为 Hart0 Guest File 0 的地址
    4. 转发

步骤 5：Guest 中断文件处理

  Guest File 0：
    1. 接收中断 ID 0x15
    2. 更新 topei
    3. 通知 vCPU

步骤 6：VM1 处理中断

  VM1 的中断处理程序：
    1. 读取 stopei
    2. 处理网卡中断
    3. 清除中断

  整个过程 Hypervisor 没有介入！
```

---

# 第四部分：虚拟化深入

## 14. Guest 中断文件

### 14.1 为什么需要 Guest 中断文件？

```
没有 Guest 中断文件时：

VM 中断流程：
设备 ──> Hypervisor ──> 注入虚拟中断 ──> VM
       (每次都要介入)

问题：
- 每次中断都需要 Hypervisor
- Hypervisor 需要保存/恢复上下文
- 延迟高（可能增加 10-100 倍）
- CPU 开销大

有 Guest 中断文件时：

VM 中断流程：
设备 ──> Guest 中断文件 ──> VM
       (Hypervisor 不参与)

优势：
- 中断直接投递到 VM
- Hypervisor 只在配置时介入
- 延迟接近原生
- CPU 开销小
```

### 14.2 Guest 中断文件结构

```
每个 hart 的 IMSIC 可以有多个中断文件：

┌─────────────────────────────────────────────┐
│              IMSIC (per hart)                │
│                                             │
│  ┌─────────────────────────────────────┐    │
│  │  Machine Interrupt File             │    │
│  │  - 供 M-mode Hypervisor 使用        │    │
│  │  - 处理机器级中断                   │    │
│  └─────────────────────────────────────┘    │
│                                             │
│  ┌─────────────────────────────────────┐    │
│  │  Supervisor Interrupt File          │    │
│  │  - 供 HS-mode Hypervisor 使用       │    │
│  │  - 处理监管级中断                   │    │
│  └─────────────────────────────────────┘    │
│                                             │
│  ┌─────────────────────────────────────┐    │
│  │  Guest Interrupt File 0             │    │
│  │  - 供 VM1 (VS-mode) 使用            │    │
│  │  - VM1 直接管理自己的中断           │    │
│  └─────────────────────────────────────┘    │
│                                             │
│  ┌─────────────────────────────────────┐    │
│  │  Guest Interrupt File 1             │    │
│  │  - 供 VM2 (VS-mode) 使用            │    │
│  └─────────────────────────────────────┘    │
│                                             │
│  ...                                        │
│                                             │
│  ┌─────────────────────────────────────┐    │
│  │  Guest Interrupt File 254           │    │
│  │  - 最多支持 255 个 guest 文件       │    │
│  └─────────────────────────────────────┘    │
│                                             │
└─────────────────────────────────────────────┘

每个 guest 文件有独立的：
- eidelivery
- eithreshold
- eie[0..63]
- topi / topei
```

### 14.3 Guest 中断文件的访问

```
Hypervisor 选择 guest 文件：

  // 选择 Guest File 1
  csrw hgein, 1;

  // 现在访问的是 Guest File 1 的寄存器
  csrr t0, hgeip;  // 读取 Guest File 1 的 pending 状态

VM 访问自己的 guest 文件：

  // VM 在 VS-mode 下，自动访问自己的 guest 文件
  csrr t0, stopei;  // 读取自己的最高优先级中断
  // 不需要 hgein，硬件自动路由
```

---

## 15. IOMMU 中断重映射

### 15.1 为什么需要中断重映射？

```
场景：VM 使用直通设备

VM 配置设备 MSI：
  MSI 地址 = GPA (Guest Physical Address)
  MSI 数据 = 中断 ID

问题：
1. 设备写入的是 GPA，不是真实物理地址
2. 直接写入会破坏内存
3. 需要转换为正确的 IMSIC 地址

解决方案：IOMMU 中断重映射

IOMMU 维护中断重映射表：
  输入 (GPA + 数据) ──> 输出 (HPA + 数据)

设备写入 GPA 时：
  IOMMU 拦截 ──> 查表转换 ──> 转发到正确的 IMSIC
```

### 15.2 中断重映射表结构

```
中断重映射表条目 (IRTE)：

┌─────────────────────────────────────────────────────────────┐
│                     IRTE (128-bit)                           │
├─────────────────────────────────────────────────────────────┤
│  字段                  │  描述                               │
├─────────────────────────────────────────────────────────────┤
│  Present               │  条目是否有效                       │
│  Destination ID        │  目标 IMSIC (hart ID)               │
│  Interrupt Vector      │  目标中断 ID                        │
│  Trigger Mode          │  边沿/电平                          │
│  Delivery Mode         │  投递模式                           │
│  Guest Mode            │  是否投递到 guest 文件              │
│  Guest ID              │  guest 文件索引                     │
│  ...                   │  其他控制位                         │
└─────────────────────────────────────────────────────────────┘
```

---

## 16. MRIF：内存驻留中断文件

### 16.1 什么是 MRIF？

```
MRIF = Memory-Resident Interrupt File

问题：
- 每个 hart 最多支持 255 个 guest 中断文件
- 但系统可能有数千个 VM
- 硬件中断文件不够用

解决方案：
- 把不活跃的 VM 的中断文件放到内存中
- 调度到 CPU 时，加载到硬件中断文件
- 类似虚拟内存的换入换出

┌─────────────────────────────────────────────┐
│              MRIF 机制                       │
│                                             │
│  活跃的 VM：                                 │
│    中断文件在硬件 IMSIC 中                   │
│    ┌─────────────────────────────────────┐  │
│    │  Guest File 0 (VM1)  ── 活跃       │  │
│    │  Guest File 1 (VM2)  ── 活跃       │  │
│    └─────────────────────────────────────┘  │
│                                             │
│  不活跃的 VM：                               │
│    中断文件在内存中                          │
│    ┌─────────────────────────────────────┐  │
│    │  VM3 中断文件 ── 内存区域 A          │  │
│    │  VM4 中断文件 ── 内存区域 B          │  │
│    │  VM5 中断文件 ── 内存区域 C          │  │
│    │  ...                                │  │
│    │  VM1000 中断文件 ── 内存区域 Z       │  │
│    └─────────────────────────────────────┘  │
│                                             │
│  调度时：                                    │
│    VM3 被调度到 CPU ──> 加载中断文件到 IMSIC │
│    VM1 被换出 ──> 保存中断文件到内存         │
└─────────────────────────────────────────────┘
```

### 16.2 MRIF 的工作流程

```
VM 换出：

1. Hypervisor 决定换出 VM1
2. 读取 VM1 的 guest 中断文件状态：
   - eidelivery
   - eithreshold
   - eie[0..63]
   - pending 状态
3. 保存到内存中的 MRIF 区域
4. 释放硬件 guest 中断文件

VM 换入：

1. Hypervisor 决定调度 VM3
2. 分配一个空闲的硬件 guest 中断文件
3. 从内存中的 MRIF 区域加载状态
4. 配置 IOMMU 中断重映射到新的 guest 文件
5. VM3 恢复运行

设备中断投递到换出的 VM：

1. 设备发送 MSI
2. IOMMU 识别目标 VM 已换出
3. IOMMU 写入内存中的 MRIF 区域
4. 标记中断为 pending
5. VM 换入时，pending 中断一起加载
```

---

# 第五部分：开发者实践

## 17. CSR 寄存器完整参考

### 17.1 Machine 级别 CSR

| CSR 名称 | 编号 | 访问 | 描述 |
|----------|------|------|------|
| `mtopei` | 0x35C | 读/写 | Machine 最高优先级外部中断 ID |
| `mtopi` | 0xFB0 | 只读 | Machine 中断拓扑信息（间接） |
| `miselect` | 0x350 | 读/写 | Machine 间接选择寄存器 |
| `mireg` | 0x351 | 读/写 | Machine 间接数据寄存器 |
| `mireg2` | 0x352 | 读/写 | Machine 间接数据寄存器 2 |
| `mireg3` | 0x353 | 读/写 | Machine 间接数据寄存器 3 |

### 17.2 Supervisor 级别 CSR

| CSR 名称 | 编号 | 访问 | 描述 |
|----------|------|------|------|
| `stopei` | 0x15C | 读/写 | Supervisor 最高优先级外部中断 ID |
| `stopi` | 0xDB0 | 只读 | Supervisor 中断拓扑信息（间接） |
| `siselect` | 0x150 | 读/写 | Supervisor 间接选择寄存器 |
| `sireg` | 0x151 | 读/写 | Supervisor 间接数据寄存器 |
| `sireg2` | 0x152 | 读/写 | Supervisor 间接数据寄存器 2 |
| `sireg3` | 0x153 | 读/写 | Supervisor 间接数据寄存器 3 |

### 17.3 Hypervisor 级别 CSR

| CSR 名称 | 编号 | 访问 | 描述 |
|----------|------|------|------|
| `htopei` | 0x65C | 读/写 | Hypervisor 最高优先级外部中断 ID |
| `htopi` | 0xEB0 | 只读 | Hypervisor 中断拓扑信息（间接） |
| `hiselect` | 0x650 | 读/写 | Hypervisor 间接选择寄存器 |
| `hireg` | 0x651 | 读/写 | Hypervisor 间接数据寄存器 |
| `hireg2` | 0x652 | 读/写 | Hypervisor 间接数据寄存器 2 |
| `hireg3` | 0x653 | 读/写 | Hypervisor 间接数据寄存器 3 |
| `hvien` | 0x658 | 读/写 | Hypervisor 虚拟中断使能 |
| `hvictl` | 0x659 | 读/写 | Hypervisor 虚拟中断控制 |
| `hviprio1` | 0x65A | 读/写 | Hypervisor 虚拟中断优先级 1 |
| `hviprio2` | 0x65B | 读/写 | Hypervisor 虚拟中断优先级 2 |
| `hgein` | 0xE12 | 读/写 | Hypervisor Guest 中断文件选择 |
| `hgeip` | 0xE13 | 只读 | Hypervisor Guest 中断 pending 状态 |

### 17.4 间接寄存器地址映射

| 间接地址 | 寄存器名 | 描述 | 适用级别 |
|----------|----------|------|----------|
| 0x30 | eidelivery | 中断投递使能 | M/S/VS |
| 0x31 | eithreshold | 中断阈值 | M/S/VS |
| 0x32 | eie0 | 中断使能 0 (ID 0-63) | M/S/VS |
| 0x33 | eie1 | 中断使能 1 (ID 64-127) | M/S/VS |
| 0x34-0x71 | eie2-eie63 | 中断使能 2-63 | M/S/VS |
| 0xC0 | eip0 | 中断 pending 0 (ID 0-63) | M/S/VS |
| 0xC1 | eip1 | 中断 pending 1 (ID 64-127) | M/S/VS |
| 0xC2-0xFF | eip2-eip63 | 中断 pending 2-63 | M/S/VS |

> **注意**：`topi` 和 `topei` 不是间接寄存器，而是直接 CSR。`mtopi`(0xFB0)、`stopi`(0xDB0)、`htopi`(0xEB0) 为只读；`mtopei`(0x35C)、`stopei`(0x15C)、`htopei`(0x65C) 可读写（写入完成中断确认）。

---

## 18. 代码示例

### 18.1 初始化 IMSIC S-mode 中断文件

```c
// IMSIC S-mode 中断文件初始化

// 间接访问辅助函数
static inline void imsic_s_write(uint32_t ireg, uint64_t value) {
    asm volatile("csrw siselect, %0" :: "r"(ireg));
    asm volatile("csrw sireg, %0" :: "r"(value));
}

static inline uint64_t imsic_s_read(uint32_t ireg) {
    uint64_t value;
    asm volatile("csrw siselect, %0" :: "r"(ireg));
    asm volatile("csrr %0, sireg" : "=r"(value));
    return value;
}

// 使能中断投递
void imsic_s_enable_delivery(void) {
    imsic_s_write(0x30, 1);  // eidelivery = 1
}

// 设置中断阈值
void imsic_s_set_threshold(uint8_t threshold) {
    imsic_s_write(0x31, threshold);  // eithreshold
}

// 使能特定中断
void imsic_s_enable_interrupt(uint32_t interrupt_id) {
    uint32_t eie_index = interrupt_id / 64;
    uint32_t bit = interrupt_id % 64;
    uint32_t ireg = 0x32 + eie_index;
    
    uint64_t current = imsic_s_read(ireg);
    current |= (1ULL << bit);
    imsic_s_write(ireg, current);
}

// 禁用特定中断
void imsic_s_disable_interrupt(uint32_t interrupt_id) {
    uint32_t eie_index = interrupt_id / 64;
    uint32_t bit = interrupt_id % 64;
    uint32_t ireg = 0x32 + eie_index;
    
    uint64_t current = imsic_s_read(ireg);
    current &= ~(1ULL << bit);
    imsic_s_write(ireg, current);
}

// 获取最高优先级中断
uint32_t imsic_s_get_top_interrupt(void) {
    uint32_t interrupt_id;
    asm volatile("csrr %0, stopei" : "=r"(interrupt_id));
    return interrupt_id & 0xFFF;  // 低 12 位是中断 ID
}

// 完成中断处理
void imsic_s_complete_interrupt(uint32_t interrupt_id) {
    asm volatile("csrw stopei, %0" :: "r"(interrupt_id));
}

// 完整初始化
void imsic_s_init(void) {
    // 1. 设置阈值为 0（接收所有优先级）
    imsic_s_set_threshold(0);
    
    // 2. 禁用所有中断
    for (int i = 0; i < 64; i++) {
        imsic_s_write(0x32 + i, 0);
    }
    
    // 3. 使能中断投递
    imsic_s_enable_delivery();
}
```

### 18.2 初始化 APLIC

```c
// APLIC 寄存器结构
struct aplic_regs {
    volatile uint32_t domaincfg;
    volatile uint32_t sourcecfg[1023];
    volatile uint8_t _reserved1[0xBC0];
    volatile uint32_t mmsiaddrcfg;
    volatile uint32_t mmsiaddrcfgh;
    volatile uint32_t smsiaddrcfg;
    volatile uint32_t smsiaddrcfgh;
    volatile uint8_t _reserved2[0x30];
    volatile uint32_t setip[32];
    volatile uint8_t _reserved3[92];
    volatile uint32_t setipnum;
    volatile uint8_t _reserved4[0x20];
    volatile uint32_t in_clrip[32];
    volatile uint8_t _reserved5[92];
    volatile uint32_t clripnum;
    volatile uint8_t _reserved6[32];
    volatile uint32_t setie[32];
    volatile uint8_t _reserved7[92];
    volatile uint32_t setienum;
    volatile uint8_t _reserved8[32];
    volatile uint32_t clrie[32];
    volatile uint8_t _reserved9[92];
    volatile uint32_t clrienum;
    volatile uint8_t _reserved10[32];
    volatile uint32_t setipnum_le;
    volatile uint32_t setipnum_be;
    volatile uint8_t _reserved11[4088];
    volatile uint32_t genmsi;
    volatile uint32_t target[1023];
};

// APLIC 基地址 (S-mode)
#define APLIC_S_BASE 0x0D000000

// 获取 APLIC 寄存器指针
static inline struct aplic_regs *aplic_s(void) {
    return (struct aplic_regs *)APLIC_S_BASE;
}

// 配置 APLIC 域
void aplic_s_configure_domain(int msi_mode) {
    struct aplic_regs *aplic = aplic_s();
    
    // IE=1 (使能), DM=msi_mode, BE=0 (小端)
    aplic->domaincfg = (1 << 8) | (msi_mode << 2);
}

// 配置中断源
void aplic_s_configure_source(uint32_t source_id, 
                               uint32_t mode, 
                               uint32_t target_hart) {
    struct aplic_regs *aplic = aplic_s();
    
    // 配置源模式
    aplic->sourcecfg[source_id - 1] = mode;
    
    // 配置目标 hart
    aplic->target[source_id - 1] = target_hart;
}

// 使能中断源
void aplic_s_enable_source(uint32_t source_id) {
    struct aplic_regs *aplic = aplic_s();
    
    // 计算字节偏移和位偏移
    uint32_t word_index = (source_id - 1) / 32;
    uint32_t bit_index = (source_id - 1) % 32;
    
    // 设置中断使能位
    // setie 寄存器位于 offset 0x1C00 + word_index * 4
    volatile uint32_t *setie = (volatile uint32_t *)((uint8_t *)aplic + 0x1C00);
    setie[word_index] = (1 << bit_index);
}
```

---

## 19. QEMU 实践指南

### 19.1 启动支持 AIA 的 RISC-V 虚拟机

```bash
# 使用 virt 机器类型，启用 AIA
qemu-system-riscv64 \
    -machine virt,aia=aplic-imsic \
    -cpu rv64 \
    -m 2G \
    -kernel your-kernel.bin \
    -nographic
```

**AIA 模式选项：**
- `aia=off`：禁用 AIA，使用传统 PLIC
- `aia=aplic`：仅启用 APLIC（有线中断模式）
- `aia=aplic-imsic`：启用 APLIC + IMSIC（完整 AIA 模式）

### 19.2 查看设备树中的 AIA 信息

```bash
# 在 Linux 中查看设备树
dtc -I fs /sys/firmware/devicetree/base > aia.dts

# 查看 APLIC 节点
cat aia.dts | grep -A 20 aplic

# 查看 IMSIC 节点
cat aia.dts | grep -A 20 imsic
```

**典型设备树片段：**
```dts
aplic@d000000 {
    compatible = "riscv,aplic";
    reg = <0x0 0xd000000 0x0 0x4000000>;
    interrupts-extended = <&cpu0_intc 0xffffffff>;
    riscv,ndev = <64>;
};

imsic@28000000 {
    compatible = "riscv,imsic";
    reg = <0x0 0x28000000 0x0 0x4000000>;
    interrupts-extended = <&cpu0_intc 0xffffffff>;
    riscv,guest-num = <7>;
};
```

### 19.3 使用 QEMU 调试中断

```bash
# 启动 QEMU 并开启 GDB 调试
qemu-system-riscv64 \
    -machine virt,aia=aplic-imsic \
    -s -S \
    -kernel your-kernel.bin

# 在另一个终端连接 GDB
riscv64-unknown-elf-gdb your-kernel.bin
(gdb) target remote :1234
(gdb) break *0x80200000
(gdb) continue
```

---

## 20. 常见问题与调试

### 20.1 中断没有投递到 CPU

**排查步骤：**

1. **检查 APLIC domaincfg 寄存器**
   ```c
   // IE 位必须为 1
   assert(aplic->domaincfg & (1 << 8));
   ```

2. **检查中断源是否使能**
   ```c
   // 检查 setie 寄存器
   uint32_t word_index = (source_id - 1) / 32;
   uint32_t bit_index = (source_id - 1) % 32;
   assert(aplic->setie[word_index] & (1 << bit_index));
   ```

3. **检查 IMSIC eidelivery**
   ```c
   // eidelivery 必须为 1
   assert(imsic_s_read(0x00) & 1);
   ```

4. **检查中断优先级阈值**
   ```c
   // eithreshold 应该小于中断优先级
   uint32_t threshold = imsic_s_read(0x04);
   uint32_t priority = get_interrupt_priority(source_id);
   assert(priority >= threshold);
   ```

### 20.2 虚拟化环境中 Guest 收不到中断

**常见原因：**

1. **IOMMU 中断重映射未配置**
   - 检查 IOMMU 是否启用了中断重映射
   - 验证 guest 中断文件地址映射是否正确

2. **Guest 中断文件未分配**
   - 确认 Hypervisor 已为 VM 分配了 guest 中断文件
   - 检查 `hgeip` 寄存器中的 pending 状态

3. **MRIF 状态未正确恢复**
   - VM 换入时，确保从 MRIF 区域加载了完整的中断状态

### 20.3 MSI 地址配置错误

**调试技巧：**

```c
// 打印 APLIC 的 MSI 地址配置
void dump_aplic_msi_config(void) {
    struct aplic_regs *aplic = aplic_s();
    
    printf("M-mode MSI addr: 0x%lx\n", 
           ((uint64_t)aplic->mmsiaddrcfgh << 32) | aplic->mmsiaddrcfg);
    printf("S-mode MSI addr: 0x%lx\n", 
           ((uint64_t)aplic->smsiaddrcfgh << 32) | aplic->smsiaddrcfg);
}

// 验证 IMSIC 地址是否匹配
void verify_imsic_address(void) {
    uint64_t expected_addr = get_imsic_base_address();
    uint64_t actual_addr = read_aplic_msi_address();
    
    if (expected_addr != actual_addr) {
        printf("MSI address mismatch!\n");
        printf("Expected: 0x%lx\n", expected_addr);
        printf("Actual:   0x%lx\n", actual_addr);
    }
}
```

### 20.4 性能问题排查

**中断延迟过高：**

1. **检查中断优先级设置**
   - 高优先级中断被低优先级阈值阻塞
   - 调整 `eithreshold` 值

2. **检查中断嵌套配置**
   - 确认 `eidelivery` 允许中断嵌套
   - 验证中断处理程序是否正确保存/恢复上下文

3. **使用性能计数器**
   ```c
   // 读取机器级性能计数器
   uint64_t mcycle_start = read_csr("mcycle");
   handle_interrupt();
   uint64_t mcycle_end = read_csr("mcycle");
   
   printf("Interrupt handling took %lu cycles\n", 
          mcycle_end - mcycle_start);
   ```

---

## 附录：术语表

| 缩写 | 全称 | 中文说明 |
|------|------|----------|
| AIA | Advanced Interrupt Architecture | 高级中断架构 |
| PLIC | Platform-Level Interrupt Controller | 平台级中断控制器 |
| IMSIC | Incoming MSI Controller | 入站 MSI 控制器 |
| APLIC | Advanced Platform-Level Interrupt Controller | 高级平台级中断控制器 |
| MSI | Message-Signaled Interrupt | 消息信号中断 |
| IOMMU | Input/Output Memory Management Unit | 输入输出内存管理单元 |
| MRIF | Memory-Resident Interrupt File | 内存驻留中断文件 |
| CSR | Control and Status Register | 控制与状态寄存器 |
| VM | Virtual Machine | 虚拟机 |
| VMM | Virtual Machine Monitor | 虚拟机监视器（Hypervisor） |
| Hart | Hardware Thread | 硬件线程（RISC-V 的 CPU 核心概念） |

---

## 总结

本文档从问题出发，系统讲解了 RISC-V AIA 的设计动机、核心概念和实践方法：

1. **为什么需要 AIA？** — PLIC 存在性能瓶颈、缺乏虚拟化支持、不支持 MSI 等问题
2. **AIA 的核心组件** — IMSIC 处理 MSI，APLIC 管理有线中断和 MSI 转换
3. **虚拟化支持** — Guest 中断文件、IOMMU 中断重映射、MRIF 共同实现高效虚拟化
4. **开发者实践** — 提供了完整的代码示例和调试指南

通过理解这些概念，你可以更好地设计和使用基于 RISC-V AIA 的系统。