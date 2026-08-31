# RISC-V AIA 完全指南：从入门到实践

> 本文档面向初学者和开发者，从问题出发，逐步讲解 RISC-V 高级中断架构的设计动机、核心概念和实际应用。
>
> **参考资料：**
> - The RISC-V Advanced Interrupt Architecture Specification, v1.0（riscv-interrupts-aia，2025-03-12）。下文标注的「AIA §x.y」均指此规范。
> - RISC-V Privileged Architecture Specification
> - Linux Kernel MSI/IOMMU 文档
> - GitHub: riscv/riscv-aia

---

## 目录

- [1. 中断是什么？为什么需要中断？](#1-中断是什么为什么需要中断)
- [2. 中断控制器的演进历史](#2-中断控制器的演进历史)
- [3. RISC-V PLIC 的五大痛点](#3-risc-v-plic-的五大痛点)
- [4. AIA 如何解决这些问题](#4-aia-如何解决这些问题)
- [5. 有线中断 (Wired Interrupt)](#5-有线中断-wired-interrupt)
- [6. MSI (Message-Signaled Interrupt)](#6-msi-message-signaled-interrupt)
- [7. 为什么 MSI 比有线中断更好？](#7-为什么-msi-比有线中断更好)
- [8. 虚拟化与中断的困境](#8-虚拟化与中断的困境)
- [9. IOMMU：连接设备与虚拟化的桥梁](#9-iommu连接设备与虚拟化的桥梁)
- [10. AIA 整体架构](#10-aia-整体架构)
- [11. IMSIC 详解](#11-imsic-详解)
- [12. APLIC 详解](#12-aplic-详解)
- [13. 中断流转完整示例](#13-中断流转完整示例)
- [14. Guest 中断文件](#14-guest-中断文件)
- [15. IOMMU 中断重映射](#15-iommu-中断重映射)
- [16. MRIF：内存驻留中断文件](#16-mrif内存驻留中断文件)
- [17. CSR 寄存器完整参考](#17-csr-寄存器完整参考)
- [18. 代码示例](#18-代码示例)
- [19. QEMU 实践指南](#19-qemu-实践指南)
- [20. 常见问题与调试](#20-常见问题与调试)

---

## 1. 中断是什么？为什么需要中断？

先给本篇定位：它属于[中断与异常](./04-interrupts-and-exceptions.md)的进阶篇——那篇讲 trap 机制和 PLIC 这类"事实标准"控制器，本篇讲 ratified 的正式答案 AIA。读完你应该能回答：为什么 PLIC 不够用、MSI 凭什么更好、虚拟化中断怎么直投，以及拿到一块 AIA 硬件时每个寄存器该怎么配。

中断就是外部事件发生时，硬件主动通知 CPU，让 CPU 暂停当前工作去处理紧急事件。没有中断的世界里 CPU 只能轮询——每隔一段时间去问一遍每个设备"你好了吗"，绝大多数询问都是浪费。

常见的中断来源：

- **键盘/鼠标** — 用户输入
- **网卡** — 收到网络数据包
- **磁盘** — 数据读写完成
- **定时器** — 时间片到期，操作系统进行任务切换
- **错误** — 内存错误、设备异常等

现代计算机有几十甚至上百个设备可能产生中断，而 CPU 的中断引脚数量有限，也不可能让所有设备同时喊话。于是需要一个**中断控制器**居中协调：

```
设备1 ──┐
设备2 ──┤
设备3 ──┼──> 中断控制器 ──> CPU
 ...   ─┤                    (一个引脚)
设备N ──┘
```

它的职责有四条：

1. **汇聚** — 收集所有设备的中断请求
2. **仲裁** — 决定哪个中断优先处理
3. **路由** — 把中断发送给合适的 CPU 核心
4. **通知** — 告诉 CPU 是哪个设备产生了中断

---

## 2. 中断控制器的演进历史

主线是两条：中断源数量从个位数涨到上百，CPU 核心从一个涨到几十个——每一代控制器都在回答"更多设备、更多核心怎么协调"。

- **PIC（如 Intel 8259）**：单核时代产物，8 个中断输入可级联到 15 个，优先级固定。
- **APIC**：多核时代，每个 CPU 核心配一个 Local APIC，由 IO APIC 把中断路由到不同核心，支持约 256 个中断源。
- **PCI INTx → MSI**：PCI 设备最初用 INTA~INTD 四根引脚，多个设备共享一根线，CPU 收到中断后要逐个询问"是你吗？"，效率低下。MSI 用一次内存写入取代共享引脚，设备直接声明自己的身份（见第 6 节）。
- **RISC-V 的路线**：先有 PLIC（只处理有线中断），后演进为 AIA（原生支持 MSI 与虚拟化）。

---

## 3. RISC-V PLIC 的五大痛点

PLIC (Platform-Level Interrupt Controller) 是 RISC-V 最初的中断控制器标准。随着系统越来越复杂，它的问题逐渐暴露。

1. **不支持 MSI。** PLIC 只能接收有线中断。对 PCIe 网卡、NVMe 固态硬盘这类本身以 MSI 为首选的现代设备，必须外加硬件把 MSI 转换成有线中断，增加复杂性和延迟。
2. **M-mode 和 S-mode 共享寄存器。** PLIC 的全局寄存器（优先级、pending 等）同时被两个特权级访问，两个特权级可能同时修改配置，软件需要加锁保护。
3. **占用大量物理地址空间。** 优先级寄存器、使能位图等按中断源和 hart 线性铺开，总地址空间可达数 MB——在嵌入式系统里这是宝贵资源。
4. **不支持触发方式配置。** 有的设备需要边沿触发（电平跳变时触发一次），有的需要电平触发（高电平期间持续 pending），PLIC 无法按源灵活配置。
5. **虚拟化支持几乎为零。** 这是最大的问题：PLIC 不知道中断属于哪个 VM，所有中断必须先交给 Hypervisor，再由软件转发给目标 VM——每次中断都要 Hypervisor 介入，延迟成倍增加。

---

## 4. AIA 如何解决这些问题

AIA (Advanced Interrupt Architecture) 针对 PLIC 的每个痛点都给出了解决方案：

| PLIC 痛点 | AIA 解决方案 |
|-----------|-------------|
| 不支持 MSI | 引入 IMSIC，原生支持 MSI |
| M/S-mode 共享寄存器 | 每个特权级有独立的 CSR 和中断文件 |
| 占用大量地址空间 | 寄存器走 CSR 间接访问，MMIO 空间大幅缩小 |
| 不支持触发方式配置 | APLIC 的 sourcecfg 可按源配置边沿/电平触发（AIA §4.5.2） |
| 虚拟化支持差 | Guest 中断文件 + IOMMU 中断重映射 |

AIA 的核心设计理念可以概括为三点：

1. **分离关注点** — APLIC 处理有线中断（并可转换为 MSI），IMSIC 接收 MSI，各司其职。
2. **硬件级虚拟化** — 每个 VM 可以分到独立的中断文件，设备中断直接投递给 VM，无需 Hypervisor 逐次介入。
3. **灵活的优先级** — 软件可配置中断优先级，本地中断和外部中断可以混合排序（通过 iprio 数组，AIA §5.4.1）。

---

## 5. 有线中断 (Wired Interrupt)

"有线"指设备通过一根真实的物理信号线连接到中断控制器：

```
┌──────────┐         ┌──────────┐         ┌──────┐
│  UART    │──IRQ──> │          │         │      │
│  设备    │         │  APLIC   │──IRQ──> │ CPU  │
├──────────┤         │          │         │      │
│  GPIO    │──IRQ──> │          │         │      │
│  设备    │         │          │         │      │
└──────────┘         └──────────┘         └──────┘
```

两种触发方式（在 AIA 中由 APLIC 的 sourcecfg 配置）：

- **电平触发 (Level-triggered)**：信号为有效电平期间一直 pending，设备撤销信号后自动清除。可靠、不易丢中断；但若软件不处理根源，中断会反复触发。
- **边沿触发 (Edge-triggered)**：只在电平跳变瞬间记录一次，响应快，但软件没及时处理时后续变化不会累积记录，可能丢事件。

有线中断的局限也很明显：每增加一个设备就要一根线，芯片引脚在设计时就封了顶；中断线连到哪个控制器是布线决定的，软件改不了路由。MSI 就是为绕开这两个限制设计的。

---

## 6. MSI (Message-Signaled Interrupt)

MSI 把中断变成**一次内存写入操作**：设备不再拉高中断线，而是向一个约定的地址写入一个约定的数据，硬件识别出这次写入并把它当作中断。

**配置阶段（系统启动时）**：操作系统为设备分配 MSI 目标地址和数据值（即中断 ID），写进设备的配置空间（PCI Capability 或 MSI-X 表项）。以 MSI-X 为例，每个中断向量有独立的"地址 + 数据"对，所以同一设备的不同事件可以指向不同 hart 的不同中断文件——这是有线中断完全做不到的。

**运行阶段（设备需要中断时）**：设备对目标地址执行一次 32 位写入。这个写入被路由到目标中断文件，对应的 identity 位被置为 pending。

在 AIA 中，MSI 的目的地是 **IMSIC 的中断文件页**（AIA §3.5）：

- 每个中断文件独占一个自然对齐的 **4 KiB 物理页**；
- 页内偏移 `0x000` 是 `seteipnum_le`（小端），`0x004` 是 `seteipnum_be`（大端，可选实现）；
- 写入的 32 位数据就是 **interrupt identity number**：`0` 无效（伪中断），`1–2047` 为有效中断号。

所以"发一个 MSI"等价于：

```c
*(volatile uint32_t *)(imsic_file_base + 0x000) = interrupt_id;  // seteipnum_le
```

**算一遍**：给 hart2 的 supervisor 中断文件发 identity 21，该文件页基址为 `0x2800_2000`（假设按 §3.6 连续排布、每文件一页），则一次 4 字节小端写 `*(uint32_t *)0x2800_2000 = 21` 就完成了整个"中断发送"——没有状态位要轮询、没有命令门铃要敲。

4 KiB 页对齐一方面简化了地址解码（一页一个中断文件），另一方面与操作系统的页粒度管理天然对齐。至于"哪个地址对应哪个 hart 的哪个特权级"，由平台约定和 APLIC 的 MSI 地址参数（mmsiaddrcfg/smsiaddrcfg，AIA §4.5.3）共同决定。

---

## 7. 为什么 MSI 比有线中断更好？

结论：六个维度全面占优，代价是路由正确性从"布线保证"变成"软件配置保证"——配错地址就静默丢中断（见 20.3 节）。

| 特性 | 有线中断 | MSI |
|------|---------|-----|
| 物理资源 | 需要中断线 | 不需要，使用内存写入 |
| 共享 | 多个设备可能共享中断线 | 每个事件有独立的 identity |
| 扩展性 | 受引脚数量限制 | 受地址空间限制，近乎无限 |
| 路由灵活性 | 硬件布线决定 | 软件改写 MSI 地址即可改变路由 |
| 虚拟化 | 难以虚拟化 | 天然支持（配合中断重映射） |
| 性能 | 共享时需逐个查询设备 | 直接知道是哪个事件 |

一个典型场景：多队列 NVMe SSD 有几十个 I/O 队列。有线中断方案里所有队列挤在一根线上，CPU 收到中断后要遍历检查；MSI 方案里每个队列一个 identity，还可以通过改写 MSI 地址把不同队列分摊到不同核心并行处理。高性能网卡的多队列收包同理。

---

## 8. 虚拟化与中断的困境

虚拟化的几个基本角色（细节见 [06 篇 H 扩展](./06-virtualization-h-extension.md)）：vCPU 是 VM 看到的 "CPU"，实际是物理 hart 的时间片；Hypervisor（如 KVM）管理多个 VM；Guest OS 运行在 VM 内。

地址转换多了两层，三个术语贯穿全文：

```
GVA (Guest Virtual Address)    Guest 进程使用的虚拟地址
   │  Guest 页表
   ▼
GPA (Guest Physical Address)   VM 看到的"物理地址"，实际是虚拟的
   │  Hypervisor 维护的第二阶段页表（HGATP）
   ▼
HPA (Host Physical Address)    真实的物理内存/设备地址
```

CPU 侧的 GVA→GPA→HPA 由 MMU 两阶段翻译硬件搞定；但**设备的 DMA 和 MSI 不经过 MMU**——直通后它们直接发出 GPA，没人替它做第二段翻译。

为了性能，高性能设备（网卡、GPU）通常**直通**给 VM：VM 直接驱动物理设备，绕过 Hypervisor 的软件模拟。但直通立刻带来中断问题：

VM 中的驱动配置设备 MSI：MSI 地址 = GPA 页基址（**这是 GPA！**），MSI 数据 = 中断 ID。

设备发送中断时直接写入这个地址，问题在于：

1. 这个地址是 Guest 视角的"物理地址"，不是真实物理地址
2. 若不加处理直接写入，会写到错误的内存位置，可能破坏其他 VM 或 Host 的数据
3. 即使地址碰巧正确，系统也需要知道这次中断应该投递给哪个 VM 的哪个 vCPU

这就是 IOMMU 中断重映射要解决的问题。

---

## 9. IOMMU：连接设备与虚拟化的桥梁

MMU 负责 CPU 侧的虚拟地址翻译与进程间隔离；IOMMU (I/O Memory Management Unit) 把同样的保护搬到设备侧：

- **DMA 重映射**：设备的 DMA 地址经过 IOMMU 页表转换后才落到真实物理内存。设备只能访问授权区域，还能把不连续的物理内存拼成连续 DMA 空间。
- **中断重映射**：拦截设备的 MSI 写入，查表转换后再转发——解决上一节直通设备的中断投递问题。

在 RISC-V 体系里，IOMMU 对 MSI 的处理靠专门的 **MSI 页表**（AIA 第 8 章）：

1. 设备上下文（DC）中的 `msiptp` 指向一张 MSI 页表，地址掩码/模式字段（`msi_addr_mask`/`msi_addr_pattern`）定义了如何从 MSI 写入的 GPA 中提取"中断文件号"，以此索引页表项（AIA §8.4）。
2. 每个页表项（MSI PTE）16 字节。**基本翻译模式**（V=1, C=0, M=3，AIA §8.5.1）把访问地址 bit 12 以上的部分替换为 PTE 中的 PPN，保留低 12 位页内偏移——也就是把"GPA 上的虚拟中断文件页"重定向到真实的物理中断文件页。
3. **MRIF 模式**（V=1, C=0, M=1，见第 16 节）则把 MSI 记录到内存里的中断文件，而不是任何硬件 IMSIC。

注意这套机制与普通 DMA 翻译是**并行的两张表**：MSI 写不走常规 IOATC 页表，专表专用。这样一方面避免把设备中断误当数据处理，另一方面为 MRIF 这种非地址语义的 PTE 格式留了空间（AIA §8.5 前言）。

结果：VM 配置的是 GPA 没问题，IOMMU 自动转换成 HPA，中断正确落到目标 hart 的中断文件，不同 VM 的中断完全隔离。

---

## 10. AIA 整体架构

先看全景：APLIC 管有线中断、IMSIC 收 MSI、IOMMU 做重映射。下面按组件图 → 三条数据流 → 跨架构对照的顺序展开。

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
│  └──────┬───────┘          │  │  Machine Interrupt File    │  │    │
│         │                  │  │  - eidelivery              │  │    │
│         ▼                  │  │  - eithreshold             │  │    │
│  ┌──────────────┐          │  │  - eie/eip 数组            │  │    │
│  │    APLIC     │          │  │  - *topi / *topei          │  │    │
│  │              │          │  └────────────────────────────┘  │    │
│  │ - 汇聚中断   │          │  ┌────────────────────────────┐  │    │
│  │ - 优先级仲裁 │          │  │  Supervisor Interrupt File │  │    │
│  │ - 路由配置   │          │  │  (结构同上)                 │  │    │
│  │              │          │  └────────────────────────────┘  │    │
│  │ 投递模式：   │          │  ┌────────────────────────────┐  │    │
│  │ 1. Direct   │─────────>│  │  Guest Interrupt File n    │  │    │
│  │    (线中断) │          │  │  (供 VS-mode 的 VM 使用)    │  │    │
│  │ 2. MSI      │─────────>│  └────────────────────────────┘  │    │
│  │    (MSI)    │          │           ...                     │    │
│  └──────────────┘          └──────────────┬───────────────────┘    │
│         │                                 │ 中断信号                │
│         ▼                                 ▼                        │
│  ┌──────────────┐                  ┌──────────────┐                │
│  │    IOMMU     │                  │  MSI 设备     │                │
│  │ - DMA 重映射 │                  │ (PCIe 网卡)  │──> MSI 直写    │
│  │ - 中断重映射 │────────────────> └──────────────┘                │
│  └──────────────┘                                                  │
└─────────────────────────────────────────────────────────────────────┘
```

### 10.2 数据流详解

**场景 1：有线中断（Direct 模式）**

1. UART 设备收到数据，拉高中断线
2. APLIC 查配置：源的模式（边沿/电平）、是否使能、投递模式、目标 hart
3. Direct 模式下 APLIC 直接驱动 hart 的外部中断引脚——注意 hart 侧的 `eidelivery` 必须为 0x40000000、对应 IDC 的 `idelivery` 也为 1，这条链路才通
4. hart 进入中断处理程序，循环读 IDC 的 `claimi`：返回值非零即拿到最高优先级中断（identity 在 bits 25:16），读操作同时清 pending；返回零表示没有更多中断
5. 处理完成后返回；电平触发源要确认设备已撤销信号，否则会立刻再次触发

**场景 2：MSI 中断**

1. PCIe 网卡收到数据包，执行 MSI 写入：`*(uint32_t *)imsic_page = interrupt_id;`
2. 写入到达 IMSIC（可能经过 IOMMU 重映射）
3. IMSIC 的中断文件将对应 identity 位记为 pending
4. 若该中断使能、且通过 eithreshold 阈值筛选、eidelivery 开启，IMSIC 向 hart 发出外部中断信号（MEIP/SEIP 置位）
5. hart 特权级若在 mie/sie 中放行外部中断，进入中断处理程序
6. 用一条 `csrrw rd, stopei, x0` 同时读出并 claim（清除 pending）该中断，identity 从 rd 的 bits 26:16 取出

**场景 3：APLIC 把有线中断转换为 MSI**

1. GPIO 设备产生中断，APLIC 按 sourcecfg 的 SM 判定有效并记 pending
2. 该域配置为 MSI 投递模式（domaincfg.DM = 1）
3. APLIC 按该源 target 寄存器中的 Hart Index / Guest Index / EIID 计算目标地址，向目标 IMSIC 页的 `seteipnum_le` 发出 MSI 写
4. 后续流程同场景 2

一个容易忽略的细节（AIA §4.9.2）：电平源被转发成 MSI 的瞬间，APLIC 就清掉自己的 pending 位，并且**忽略该信号线直到它被撤销**。所以不会有中断风暴，反而有丢中断风险——ISR 处理了第一个诱因后线上若还有第二个有效电平，不补看一眼这个中断就永远不会再来了。对策是 ISR 退出前读一次 `in_clrip`：rectified input 仍为 1 就再处理一轮（完整分析见 20.5 节）。

### 10.3 与 x86 / ARM 中断架构的对照

| | RISC-V AIA | x86 | ARM GICv3+ |
|---|---|---|---|
| MSI 接收端 | IMSIC 中断文件（CSR + 每文件一页 MMIO） | Local APIC（MSI 地址指向 APIC 页） | GIC ITS（翻译表把 DeviceID+EventID 映射到 LPI） |
| 有线中断管理 | APLIC（可转 MSI） | IOAPIC | GIC Distributor / Redistributor |
| 虚拟化直投 | guest 中断文件，硬件直接注入 VS | posted interrupts（IR + VMCS） | 直接注入（LPI → list register） |
| 中断重映射 | IOMMU 的 MSI 页表 | IOMMU/IRTA 中断重映射表 | ITS 翻译表 |
| 优先级模型 | identity 越小越优先 + iprio 数组 | task priority class（粗粒度屏蔽） | LPI 无硬件优先级配置（固定序） |

共同趋势很明显：**消息化（一切皆内存写）、重映射化（路由表由软件掌管）、注入硬件化（guest 中断不过 hypervisor）**。AIA 的特色是把"每目标一个 4 KiB 中断文件页"做到了极致——MSI 地址本身就是页地址，翻译和虚拟化都围绕页粒度展开。

---

## 11. IMSIC 详解

### 11.1 中断文件 (Interrupt File)

IMSIC 是挂在每个 hart 本地的入站 MSI 控制器。MSI 不仅要送到特定的 hart，还要送到特定特权级——甚至（开了 H 扩展时）送到特定的虚拟 hart。**每这样一个投递目标对应一个中断文件**（AIA §3.1）。

数量上：假设实现了 S-mode，每个 hart 的 IMSIC 至少有 machine 和 supervisor 两个中断文件；实现了 H 扩展还可以有若干 guest 中断文件，数量恰好等于 GEILEN（H 扩展定义的 guest 外部中断数）。

每个中断文件的主体是两组位数组：**interrupt-pending**（记录已到达未处理）和 **interrupt-enable**（决定接受哪些）。每一位对应一个 interrupt identity number，取值 1 到 2047，identity 数值越小优先级越高。

identity 0 永远无效——它是硬件给"伪 MSI"（数据为 0 的写入）准备的垃圾桶位。这个设计让驱动可以把 `*topei` 读回零直接当作"没有中断"处理，不用额外的 valid 位。

软件视角下，一个 hart 的全部中断文件在物理地址空间里是连续排布的 4 KiB 页（AIA §3.6）：machine 文件在最前，然后 supervisor，再按编号排 guest 文件。APLIC 用 Base PPN + index 移位就能算出任意目标地址，前提正是这个约定。

### 11.2 中断文件的寄存器详解

#### eidelivery — 投递使能（AIA §3.8.1）

```
0          = 关闭投递（中断仍记入 pending，但不通知 CPU）
1          = 开启本中断文件的投递
0x40000000 = 可选值：改为由 PLIC/APLIC 直接向该 hart 投递外部中断，
             本中断文件退居幕后（guest 文件不支持此值）
```

典型用法：BSP 早期先把 eidelivery 保持为 0 挂账所有 MSI，等 trap handler、eie 位图都就绪后再置 1 放行；Direct 模式的系统则把它设成 0x40000000，让 APLIC 取代 IMSIC 成为外部中断源。注意 eidelivery 只控制"信号是否外发"，不影响文件内部的 pending 记录和 `*topei` 读值。

#### eithreshold — 优先级阈值（AIA §3.8.2）

identity 数值越小优先级越高。eithreshold 设的是**允许投递的最低优先级（即最大的 identity 号）**：

```
t = 0      ：无阈值，所有使能的中断都参与投递
t ≠ 0      ：identity ≥ t 的中断视为不存在，即使 eie 里使能也不投递
```

典型用法：进入关键代码段前把 t 调小，屏蔽低优先级（大 identity 号）中断。

#### eip[0..63] / eie[0..63] — pending 与 enable 数组（AIA §3.8.3 / §3.8.4）

最多 64 个 XLEN 宽的寄存器为一组，覆盖全部 identity：

- RV32：寄存器 n 管 identity `32n .. 32n+31`；
- RV64：**奇数号寄存器不存在**（访问会触发 illegal instruction，VS 态则是 virtual instruction exception），偶数号寄存器 `2m` 管 identity `64m .. 64m+63`。

把 identity 换算成 `(iselect, bit)` 是写驱动时的高频操作，两种宽度结果并不总相同：

```
identity 21   RV64 → eie0 (0xC0) bit 21     RV32 → eie0 (0xC0) bit 21
identity 70   RV64 → eie2 (0xC2) bit 6      RV32 → eie2 (0xC2) bit 6
identity 100  RV64 → eie2 (0xC2) bit 36     RV32 → eie3 (0xC3) bit 4
identity 2000 RV64 → eie62(0xFE) bit 16     RV32 → eie62(0xFE) bit 16
```

（RV32 下 eip/eie 的换算同理：`n = id / 32`，`bit = id % 32`。注意 iselect 始终是连续编号，"跳过"只发生在 RV64 的奇数号寄存器不存在这件事上。）

#### \*topei — 最高优先级外部中断（AIA §3.9）

`mtopei`（machine）、`stopei`（supervisor）、`vstopei`（VS）直接对应各自级别的中断文件。读返回当前最高优先级的 pending-and-enabled 且通过阈值筛选的中断，格式为：

```
bits 26:16  Interrupt identity
bits 10:0   Interrupt priority（等于 identity，冗余字段）
其余位为 0；无可投递中断时读回 0
```

**写入 = claim**：写 `*topei` 会按"当前寄存器值"清除对应 pending 位，写入的数值本身被忽略。因此规范强烈建议用一条 `csrrw rd, stopei, x0` 同时完成读和 claim——如果分成两条指令，中间可能插入更高优先级的新中断，导致误清新中断、丢失原中断。

#### \*topi — 最高优先级中断概览（AIA §5.2.2 / §5.4.2 / §6.3.3）

`mtopi`(0xFB0)、`stopi`(0xDB0)、`vstopi`(0xEB0) 是只读 CSR，汇报**包括本地中断在内的**全部中断中最高优先级的 pending-and-enabled 者。注意它的位域布局与 `*topei` **不同**：identity 在 bits 27:16、priority 在 bits 7:0。两者分工：`*topei` 只看外部中断且可写 claim；`*topi` 只读，用于快速判断"有没有可处理的中断"。

### 11.3 中断文件的间接访问机制

中断文件内部寄存器很多（eip/eie 各 64 个），不可能每个都占一个 CSR 编号。解决方案是**间接访问**：`*iselect` 选内部寄存器，`*ireg` 读写选中的寄存器（machine 用 miselect/mireg，supervisor 用 siselect/sireg，VS 用 vsiselect/vsireg）。

当 `*iselect` 落在 0x70–0xFF 时访问的就是 IMSIC 中断文件（AIA §3.7）：

```
0x70       eidelivery      投递使能
0x72       eithreshold     优先级阈值
0x80–0xBF  eip0 – eip63    pending 数组
0xC0–0xFF  eie0 – eie63    enable 数组
（0x71、0x73–0x7F 保留：读回零，写被忽略）
```

注意 `*iselect` 在 0x30–0x3F 区间的用途是**主要中断优先级数组 iprio**（本地中断的优先级排序，AIA §5.4），与 IMSIC 无关。

另外，Smcsrind/Sscsrind 扩展把间接窗口推广为 6 个别名寄存器（mireg/mireg2…mireg6，siselect 同理），用于访问更宽的寄存器组；当 `*iselect` 落在 0x30–0x3F 或 0x70–0xFF 时访问 mireg2..6 会触发 illegal instruction（AIA §2.1）。写通用库时别假设只有一对窗口 CSR。

### 11.4 本地中断与外部中断的统一排序

IMSIC 只管"identity 越小优先级越高"的外部中断排序，但 hart 同时还有 timer/software 等本地中断。AIA 用 **iprio 数组**（`*iselect` = 0x30–0x3F，每个字节是一个 major interrupt 的优先级号）给本地中断定优先级，数值越小越优先——与 IMSIC 的方向一致。`mtopi/stopi/vstopi` 汇报的就是本地中断与外部中断合并后的最高优先级者。

AIA 定义的全部 major interrupt 及默认优先级（AIA §5.1 Table 8）：

| 默认优先级（高→低） | major interrupt |
|---------------------|-----------------|
| 43 | 本地中断：高优先级 RAS 事件 |
| 11 / 3 / 7 | Machine：外部 / 软件 / 定时器 |
| 9 / 1 / 5 | Supervisor：外部 / 软件 / 定时器 |
| 12 | Guest 外部中断 (SGEI) |
| 10 / 2 / 6 | VS：外部 / 软件 / 定时器 |
| 13 | 本地中断：计数器溢出 |
| 35 | 本地中断：低优先级 RAS 事件 |

默认序只在多个中断同时 trap 到同一特权级时生效；trap 到更高级别永远更优先。若 iprio 全部实现为只读零，就按这张表排——这也是为什么调试时看 `stopi` 有值、`stopei` 却为零：pending 的可能是个本地中断。

---

## 12. APLIC 详解

### 12.1 APLIC 的角色

APLIC 是"有线中断的总管"：输入最多 1023 个有线中断源，负责检测（边沿/电平）、记 pending、仲裁、路由，最后以两种方式之一投递：

- **Direct 模式**：直接驱动 hart 的外部中断引脚，适合没有 IMSIC 的简单系统；
- **MSI 模式**：把中断转发成 MSI 写向目标 hart 的 IMSIC，适合需要 MSI 与虚拟化的系统。

### 12.2 中断域 (Domain) 概念

APLIC 的每个中断域有一套独立完整的控制寄存器，形成树状层级：根域管理全部中断源，可以把某些源**委托**给子域。委托关系记录在父域的 sourcecfg 里（D=1 + Child Index），子域再按同样规则继续下分；委托链的叶子都是 supervisor 级域。

```
machine 级根域（管理源 1..1023）
 ├── sourcecfg[5].D=1, Child Index=0 ──> supervisor 子域 0（接管源 5）
 ├── sourcecfg[6].D=1, Child Index=1 ──> supervisor 子域 1（接管源 6..9）
 └── 其余源留在 machine 域
```

被委托的源在父域中变为 inactive（pending/enable 强制为零）；子域中该源的 sourcecfg 初始为只读零，直到被写入非零值激活。

为什么需要域？权限隔离（S-mode 软件只能摸到自己域的寄存器）、灵活路由（不同域可投递到不同 hart 集合）、以及虚拟化场景下的分组管理。

### 12.3 中断源配置 sourcecfg（AIA §4.5.2）

每个源一个 32 位寄存器，bit 10 是 D (Delegate) 位，D 决定剩余字段的含义——**两种格式互斥**：

```
D = 1（委托给子域）：
bit 10        D, = 1
bits 9:0      Child Index —— 子域编号

D = 0（本域处理）：
bit 10        D, = 0
bits 2:0      SM (Source Mode) —— 源模式
```

SM 编码：

```
0 = Inactive   本域不活跃（屏蔽）
1 = Detached   活跃但脱离信号线，仅软件写 setip/setipnum 可置 pending
4 = Edge1      上升沿触发
5 = Edge0      下降沿触发
6 = Level1     高电平触发
7 = Level0     低电平触发
```

两个行为细节（AIA §4.5.2）：

- 改写 sourcecfg 时，若新源模式下 rectified input（输入电平经反相修正后的值）为高，硬件**可能**立即置起 pending——具体行为实现相关，软件不要依赖。
- 把源改成 Inactive 则一定清掉 pending 和 enable。

另外 Edge0/Level0 属于"反相"配置——先对输入取反再做判定，适合低有效的设备信号。

Detached 模式还有两个用途：一是软件模拟中断的正规入口（如虚拟设备、测试注入）；二是 MSI 转发的推荐搭配——MSI 源本来就没有"线"，用 Detached 语义最干净。

### 12.4 domaincfg 域配置（AIA §4.5.1）

```
bits 31:24  只读 0x80（字节序探针：按正确字节序读入时 bit 31 为 1）
bit  8      IE —— 全局中断使能，IE=0 时本域一切中断不外发
bit  2      DM —— 投递模式：0 = Direct，1 = MSI
bit  0      BE —— 本域控制区寄存器的字节序：0 = 小端，1 = 大端
```

### 12.5 寄存器布局（AIA §4.5，Table 6）

每个中断域的控制区至少 16 KiB、4 KiB 对齐，前 16 KiB 的布局如下（偏移均为相对域基址）：

```
0x0000  domaincfg
0x0004  sourcecfg[1] … 0x0FFC sourcecfg[1023]
0x1BC0  mmsiaddrcfg / mmsiaddrcfgh   （仅 machine 级域）
0x1BC8  smsiaddrcfg / smsiaddrcfgh   （仅 machine 级域）
0x1C00  setip[0..31]                 （批量置 pending，0x1C00–0x1C7C）
0x1CDC  setipnum                     （按编号置 pending）
0x1D00  in_clrip[0..31]              （批量翻转/清 pending，0x1D00–0x1D7C）
0x1DDC  clripnum                     （按编号清 pending）
0x1E00  setie[0..31]                 （批量使能，0x1E00–0x1E7C）
0x1EDC  setienum                     （按编号使能）
0x1F00  clrie[0..31]                 （批量禁用，0x1F00–0x1F7C）
0x1FDC  clrienum                     （按编号禁用）
0x2000  setipnum_le / 0x2004 setipnum_be（按端序置 pending）
0x3000  genmsi                       （手动生成一个 MSI：bits 31:18 Hart Index、
                                       bit 12 Busy 只读、bits 10:0 EIID）
0x3004  target[1] … 0x3FFC target[1023]（每源的目标配置）
```

MSI 模式下 `target[i]` 指定该源转发 MSI 的目的地；Direct 模式下则指定 hart index 和优先级。其余未列出的字节一律保留、只读零。

### 12.6 target 寄存器的两种格式（AIA §4.5.16）

```
Direct 模式（DM = 0）：              MSI 模式（DM = 1）：
bits 31:18  Hart Index               bits 31:18  Hart Index
bits 7:0    IPRIO                    bits 17:12  Guest Index
                                     bit  11     保留（读零）
                                     bits 10:0   EIID（转发 MSI 的数据值）
```

Guest Index 只有在 supervisor 级域且 hart 实现 H 扩展时才有效：非零值表示把 MSI 送往目标 hart 的对应 guest 中断文件，零值表示送往 supervisor 级文件。machine 级域中 Guest Index 恒读零。注意切换 DM 后，所有 active 源的 target 字段值变为 UNSPECIFIED，需要重新写入。

### 12.7 MSI 地址参数：mmsiaddrcfg/smsiaddrcfg（AIA §4.5.3–4.5.4 / §4.9.1）

APLIC 转发 MSI 时要回答"hart index + guest index → 哪个物理页地址"。这组参数由 machine 级根域提供：

- `mmsiaddrcfg`：Low Base PPN（低 32 位）；`mmsiaddrcfgh`：bit 31 L（锁存位）、HHXS/LHXS（高低段 hart index 的移位）、HHXW/LHXW（宽度）、High Base PPN——两者拼出 44 位 Base PPN；
- `smsiaddrcfg`/`smsiaddrcfgh` 结构相同，面向 S-mode/VS 中断文件。

地址计算规则（AIA §4.9.1），machine 级域：

```
g = (Hart Index >> LHXW) & (2^HHXW - 1)      // hart 组号
h = Hart Index & (2^LHXW - 1)                // 组内 hart 号
MSI address = (Base PPN | (g << (HHXS+12)) | (h << LHXS)) << 12
```

supervisor 级域在此基础上再拼上 Guest Index，且 hart index 要先换算成 machine 级编号；Base PPN 与 LHXS 取自 `smsiaddrcfg(h)`，HHXW/LHXW/HHXS 取自 `mmsiaddrcfgh`。MSI 数据取 target 的 EIID，**恒以小端发出**，与 domaincfg.BE 无关。

**算一遍**：设 Base PPN = `0x28000`（物理 `0x2800_0000`）、LHXW = 1、LHXS = 9、HHXW = 0。目标 target = {Hart Index = 3, Guest Index = 2, EIID = 50}，则 g = (3>>1)&0 = 0（HHXW=0）、h = 3&1 = 1：

```
address = (0x28000 | (0 << (HHXS+12)) | (1 << 9) | 2) << 12
        = (0x28000 + 0x200 + 2) << 12
        = 0x2820_2000 → 写入的数据为 50
```

即 hart1 的块从 `0x2820_0000` 起（每 hart 间隔 2 MiB），其 guest 文件 2 的页在块内偏移 2 页——正好对应 IMSIC 按 §3.6 排布的"supervisor 文件之后连续排 guest 文件"约定。

`genmsi`(0x3000) 则是给软件手动补发一个 MSI 的口子：写 Hart Index + EIID 后 Busy 置一，APLIC 发出后自动清零。规范提示它主要用于建立内存序同步点（AIA §4.9.3），常规转发别走这条路。

### 12.8 IDC 结构与 Direct 模式的寄存器（AIA §4.8）

控制区从 0x4000 起、每个候选 hart index 一项、每项 32 字节：

| IDC 内偏移 | 寄存器 | 说明 |
|-----------|--------|------|
| 0x00 | idelivery | =1 时 APLIC 直投该 hart 生效 |
| 0x04 | iforce | 写 1 强制产生一个中断信号 |
| 0x08 | ithreshold | 优先级阈值，语义同 IMSIC eithreshold |
| 0x18 | topi | 只读。**注意位域**：identity 在 bits 25:16、priority 在 bits 7:0 |
| 0x1C | claimi | 读值同 topi，但读操作同时 claim（清 pending） |

hart 侧处理 Direct 中断的套路与 IMSIC 一致：循环读 `claimi` 直到返回零。读 `claimi` 得到零还会顺手清掉 iforce。

### 12.9 两种投递模式的取舍

- **Direct**：不需要 IMSIC，路径短；但 hart 必须支持 eidelivery = 0x40000000 才能接住这种直投，否则等效于关中断（AIA §4.5.1）。不支持虚拟化直投 guest，且每个候选 hart 都要占一组 IDC 结构。
- **MSI**：要求系统里有 IMSIC；换来任意 hart/任意中断文件的路由能力，以及与 guest 中断文件、IOMMU 重映射的组合——虚拟化场景事实上只有这一条路。

选型上可以这么记：**单核/无 MMU 的深度嵌入式用 Direct 就够**（省掉 IMSIC 的 CSR 与页表开销）；**只要上了 SMP + 虚拟化，一律 MSI 模式**——Direct 模式下 target 只有 Hart Index 和 IPRIO，既不能路由到 guest 文件，也没有 IOMMU 重映射的参与位置。

两种模式可以在同一个 APLIC 的不同域里并存，machine 域走 Direct、supervisor 域走 MSI 是常见组合。

### 12.10 域控制区的 C 结构参考

以下结构体逐字段对照 Table 6 核验过（含各保留区间距），可直接用于 MMIO 访问：

```c
struct aplic_domain {
    volatile uint32_t domaincfg;        /* 0x0000 */
    volatile uint32_t sourcecfg[1023];  /* 0x0004 - 0x0FFC */
    uint8_t           _rsv0[0xBC0];     /* 0x1000 - 0x1BBF */
    volatile uint32_t mmsiaddrcfg;      /* 0x1BC0, 仅 machine 级域 */
    volatile uint32_t mmsiaddrcfgh;     /* 0x1BC4 */
    volatile uint32_t smsiaddrcfg;      /* 0x1BC8 */
    volatile uint32_t smsiaddrcfgh;     /* 0x1BCC */
    uint8_t           _rsv1[0x30];      /* 0x1BD0 - 0x1BFF */
    volatile uint32_t setip[32];        /* 0x1C00 - 0x1C7C */
    uint8_t           _rsv2[92];        /* 0x1C80 - 0x1CDB */
    volatile uint32_t setipnum;         /* 0x1CDC */
    uint8_t           _rsv3[32];        /* 0x1CE0 - 0x1CFF */
    volatile uint32_t in_clrip[32];     /* 0x1D00 - 0x1D7C */
    uint8_t           _rsv4[92];        /* 0x1D80 - 0x1DDB */
    volatile uint32_t clripnum;         /* 0x1DDC */
    uint8_t           _rsv5[32];        /* 0x1DE0 - 0x1DFF */
    volatile uint32_t setie[32];        /* 0x1E00 - 0x1E7C */
    uint8_t           _rsv6[92];        /* 0x1E80 - 0x1EDB */
    volatile uint32_t setienum;         /* 0x1EDC */
    uint8_t           _rsv7[32];        /* 0x1EE0 - 0x1EFF */
    volatile uint32_t clrie[32];        /* 0x1F00 - 0x1F7C */
    uint8_t           _rsv8[92];        /* 0x1F80 - 0x1FDB */
    volatile uint32_t clrienum;         /* 0x1FDC */
    uint8_t           _rsv9[32];        /* 0x1FE0 - 0x1FFF */
    volatile uint32_t setipnum_le;      /* 0x2000 */
    volatile uint32_t setipnum_be;      /* 0x2004 */
    uint8_t           _rsv10[0xFF8];    /* 0x2008 - 0x2FFF */
    volatile uint32_t genmsi;           /* 0x3000 */
    volatile uint32_t target[1023];     /* 0x3004 - 0x3FFC */
};
```

间距规律也好记：数组（setip/in_clrip/setie/clrie）到对应 `*num` 寄存器之间留 92 字节，`*num` 到下一个数组之间留 32 字节；只有 msiaddr 组和 genmsi/target 区跨度较大。

几个操作寄存器的语义容易想当然，单独拎出来：

- **setip[0..31] / setipnum**：写 1 置 pending。对 Detached 源这是软件模拟中断的唯一入口。
- **in_clrip[0..31]**（AIA §4.5.7）：名字有迷惑性——**读**它返回的是各源的 rectified input 值（即"线现在是什么电平"），**写** 1 才是清对应 pending 位。调试"中断反复触发"时先读它看信号是否还悬着。
- **clripnum / setienum / clrienum**：按源号单点操作，写的值就是源号；非法源号的写入被忽略。
- **setipnum_le/be**：与 setipnum 的区别仅在字节序解释，供不同端序的 MSI 路径复用。

---

## 13. 中断流转完整示例

### 13.1 场景：PCIe 网卡 MSI 中断

```
系统配置：
- 4 核 RISC-V 处理器，每个 hart 有 IMSIC（M + S 中断文件）
- APLIC 配置为 MSI 模式
- 网卡的某个 MSI-X 项指向 hart1 S-mode 中断文件，ID = 50

步骤 1：初始化 APLIC（S-mode 域）
  aplic->domaincfg = IE(1<<8) | DM_MSI(1<<2);
  aplic->smsiaddrcfg / smsiaddrcfgh = S-mode IMSIC 地址参数
      （Base PPN + hart index 移位/宽度，多为只读常量，读出照用）;
  aplic->sourcecfg[NET_IRQ] = SM_Edge1 (=4);
  // target 编码：hart index << 18 | guest index << 12 | EIID
  aplic->target[NET_IRQ] = (1 << 18) | (0 << 12) | 50;

步骤 2：初始化 IMSIC S-mode 中断文件（RV64，间接访问）
  // eidelivery = 1（iselect 0x70）
  csrw siselect, 0x70;
  csrw sireg, 1;

  // eithreshold = 0：无阈值（iselect 0x72）
  csrw siselect, 0x72;
  csrw sireg, 0;

  // 使能 ID 50：RV64 下落在 eie0（iselect 0xC0）bit 50
  //   （RV32 下则是 eie1 = 0xC1 的 bit 18）
  csrw siselect, 0xC0;
  csrs sireg, (1 << 50);

步骤 3：网卡收到数据包，执行 MSI 写入
  *(uint32_t *)hart1_s_imsic_page = 50;   // 写页内 seteipnum_le

步骤 4：IMSIC 处理（hart1 S-mode 文件内部）
  1. ID 50 的 pending 位置 1
  2. 检查：eie 使能 ✓，eidelivery = 1 ✓，
     eithreshold = 0 无阈值 ✓（若 eithreshold = t 且 t <= 50 则不投递）
  3. sip.SEIP 拉高，hart1 收到 supervisor 外部中断

步骤 5：CPU 处理中断
  1. 保存上下文，进入中断处理程序
  2. 一条指令完成"读 + claim"：
     csrrw t0, stopei, x0     // t0 = (50 << 16) | 50
  3. 按 t0 的 bits [26:16] 分发到网卡驱动
  注意：不要用"先 csrr 读、再 csrw 写"两条指令来 claim——
  两指令之间若有更高优先级中断到来，写操作会错清新中断、丢掉原中断。

对照：如果同一系统走 Direct 模式（无 IMSIC），差异只在第 4/5 步——
APLIC 不再发 MSI，而是直接拉高 hart1 的 SEIP；hart1 改从 APLIC 域控制区
0x4000 + hart_index*32 + 0x1C 处读 claimi 完成查询和确认，
且 eidelivery 必须配成 0x40000000 而不是 1。
```

### 13.2 场景：虚拟机中的设备直通

```
系统配置：KVM Hypervisor，VM1 直通 PCIe 网卡，IOMMU 启用 MSI 重映射
VM1 的 vCPU0 绑定在物理 hart0，分到 guest 中断文件 3

步骤 1：Hypervisor 配置（一次性）
  - 设置 hstatus.VGEIN = 3（该 vCPU 的 guest 文件号）
  - hgeie 使能要注入的 guest 中断位
  - IOMMU 设备上下文：msiptp 指向 MSI 页表，
    为 VM 视角的每个中断文件页配基本翻译模式 PTE：
      GPA 页 0x28001000 ──(PPN 替换)──> 物理页(hart0, guest file 3)

步骤 2：VM1 初始化网卡（完全不知道自己在虚拟机里）
  驱动像裸机一样配 IMSIC：
    vsiselect = 0x70 (eidelivery)，vsireg = 1
    vsiselect = 0xC0 (eie0)，置位 ID 对应 bit
  再把 MSI 地址(GPA 页基址)和数据(ID)写进网卡 MSI-X 表

步骤 3：网卡发送中断：向 GPA 页写入 ID

步骤 4：IOMMU 拦截，查 MSI PTE，地址高位替换后转发到
  物理 hart0 guest 文件 3 页的 seteipnum_le

步骤 5：Guest 文件记 pending → hgeip 对应位 active → SGEI 注入 vCPU

步骤 6：VM1 的中断处理程序读 vstopei、claim、处理、返回
  —— 整个过程 Hypervisor 没有介入！
```

13.2 与 13.1 有一个对称性：VM 内部的操作与裸机完全同构（只是 CSR 换成 vs* 前缀、地址换成 GPA）。硬件做的事就是把"GPA→HPA"和"guest 文件选择(VGEIN)"两层映射藏进了路径里。

理解了这一点，虚拟化中断调试就归结为逐层确认这两层映射是否配置到位。

---

## 14. Guest 中断文件

### 14.1 为什么需要 Guest 中断文件？

没有 guest 中断文件时，每个进 VM 的中断都要 Hypervisor 经手：收中断 → 保存/恢复上下文 → 注入虚拟中断，延迟和 CPU 开销都大。有了 guest 中断文件，设备 MSI 可以由硬件直接投递进 VM，Hypervisor 只在配置阶段介入一次，延迟接近裸机。

### 14.2 Guest 中断文件的结构

每个 hart 的 IMSIC 中断文件分层排布（AIA §3.1 / §3.6）：

```
┌─────────────────────────────────────────────┐
│              IMSIC (per hart)                │
│                                             │
│  Machine Interrupt File      ← M-mode 使用  │
│  Supervisor Interrupt File   ← HS-mode 使用  │
│  Guest Interrupt File 1..GEILEN             │
│    ↑ 每个 guest 文件分给一个虚拟 hart，      │
│      供其 VS-mode 使用                      │
└─────────────────────────────────────────────┘
```

关键点：guest 文件的数量由 **GEILEN**（H 扩展定义的 guest 外部中断数上限）决定，不是固定的 255；supervisor 文件与各 guest 文件的 4 KiB 页在地址上连续排列（AIA §3.6）。每个 guest 文件拥有独立的 eidelivery/eithreshold/eip/eie 全套状态，与普通中断文件行为一致（唯一差别：eidelivery 不支持 0x40000000）。

选择哪个 guest 文件靠 hstatus.VGEIN 字段（bits 17:12，6 位宽），所以 guest 文件号有效范围是 1..GEILEN，且 GEILEN 本身受字段宽度约束。hypervisor 给 vCPU 分配文件时要把这个号写进该 vCPU 将来运行时可见的 hstatus 里。

### 14.3 Guest 中断文件的访问

HS-mode 访问某个 guest 文件，靠的不是独立 CSR，而是 **hstatus.VGEIN 字段**选择文件号，然后用 VS 窗口 CSR（vsiselect/vsireg/vstopei）间接访问其内部寄存器（AIA §3.7）：

```
// 选择 Guest File 3 并读它的 eidelivery：
csrs hstatus, VGEIN(3);
csrw vsiselect, 0x70;
csrr t0, vsireg;
```

guest 文件有中断活动时，active 位出现在 HS 的 **hgeip**(0xE12) 中，HS 用 **hgeie**(0x607) 控制哪些位触发 SGEI 异常注入 vCPU——这两个是 H 扩展的 CSR，不是 AIA 新增。注入链路完整走一遍：

```
设备 MSI → guest 文件 pending 位 → hgeip 对应位变 active
        → （hgeie 该位为 1）→ hvip.VSEIP 位被置起
        → （vsie.VSEIE 使能、sstatus.VS 放行）
        → vCPU 下次执行时 trap 进 VS-mode，scause = 外部中断
```

任何一环的使能位没开，中断就停在那一层——验证篇的"分锅决策树"正是沿这条链逐级排查。

VM 迁移要把虚拟 hart 换到另一个物理 guest 文件时，hypervisor 只需改 hstatus.VGEIN 并迁移文件状态（AIA §6.1.2），上层的 vsiselect/vstopei 视图不变。

VM 内部（VS-mode）则完全不用操心选择问题：vsiselect/vsireg/vstopei 自动作用于自己被分配的那个 guest 文件，`csrr t0, vstopei` 读到的就是自己的最高优先级外部中断。

### 14.4 没有 guest 文件时：虚拟中断注入

guest 文件是可选特性（GEILEN 可为 0）。没有它时 VS 级外部中断走软件注入路径，AIA 为此新增了几个 HS CSR（AIA 第 6 章 / §2.3 Table 5）：

- `hvien`(0x608)：逐位使能"虚拟中断"，被使能的位不再要求真实硬件 pending，可由 hypervisor 直接在 `hvip`(0x645) 里置位；
- `hvictl`(0x609)：控制注入中断的 identity 号（IID 字段）等属性；
- `hviprio1`(0x646)/`hviprio2`(0x647)：为一小部分常用中断号提供优先级字段，其余走 iprio 数组。

`vstopi` 会把真实中断和这些注入的虚拟中断**合并排序**后汇报最高优先级者，所以 VM 内的软件看到的仍是一个统一的 top-interrupt 视图。这条路径每次注入都要 hypervisor 写 `hvip`，性能天然不如 guest 文件直投——它是兼容兜底，不是推荐路径。

两种路径怎么选：有 guest 文件时，设备中断走直投、hypervisor 只在配置/迁移时碰 CSR；没有 guest 文件（GEILEN=0）或需要注入"软件伪造"的中断（如虚拟定时器、virtio 事件）时，才用 hvip 注入。真实的 KVM/RISC-V 实现是两条并用：设备中断走 guest 文件，虚拟设备事件走 hvictl 注入。

---

## 15. IOMMU 中断重映射

### 15.1 为什么需要中断重映射？

直通设备的 MSI 写入的是 GPA，直接放行要么写坏内存、要么投错地方。IOMMU 用 MSI 页表解决：从 MSI 目标 GPA 中按掩码提取中断文件号索引 MSI PTE，PTE 决定这次写入最终去哪（AIA §8.4–8.5）。

### 15.2 两种 MSI PTE 模式

**基本翻译模式（V=1, C=0, M=3，AIA §8.5.1）**——最常用的直通路径：

```
第一双字（RV64）：
bit 63      C = 0
bits 53:10  PPN        真实中断文件页的物理页号
bits 2:1    M = 3
bit 0       V = 1
第二双字：IOMMU 忽略，软件可用

翻译规则：保留 MSI 地址低 12 位（页内偏移），
高位整体替换为 PPN → 写入落到真实 IMSIC 页的 seteipnum_le
```

这条规则正好利用了"一个中断文件一页"的设计：hypervisor 只要为每个虚拟中断文件页准备一个 PTE，就能把整页 MSI 重定向。

**算一遍**：VM 里设备被配成向 GPA `0x2800_1000` 写数据 50（页基址+0，即虚拟文件页的 `seteipnum_le`）。IOMMU 先按 DC 的掩码/模式从地址中提取中断文件号、索引到对应 MSI PTE；该 PTE 为基本翻译模式、PPN = `0x2400_5`，于是地址 bit 12 以上的 `0x2800_1` 被整体替换为 `0x2400_5`，低 12 位偏移 `0x000` 原样保留——写入最终落在物理地址 `0x2400_5000`，正是某个真实 guest 文件页内偏移 0x000 的 `seteipnum_le`，identity 50 被记为 pending。反过来这也解释了配置纪律：**设备 MSI 地址必须落在中断文件页基址（或 +4）上**，偏移落在保留字节上的写入会被目标硬件忽略。

**MRIF 模式（V=1, C=0, M=1，AIA §8.5.2）**——目标不是任何硬件文件而是内存中的 MRIF：

```
第一双字：
bit 63      C = 0
bits 53:7   MRIF Address[55:9]   目标 MRIF 的物理地址（512 字节对齐）
bits 2:1    M = 1
bit 0       V = 1
第二双字：
bit 60      NID[10]
bits 53:10  NPPN                 notice MSI 的目标页
bits 9:0    NID[9:0]             notice MSI 的数据值
```

每次经此 PTE 记录 MSI 后，IOMMU 向 NPPN/NID 指定的目的地发一条 notice MSI。

另外 V=1 且 C=1 的 PTE 由实现自定义解释；V=0 则无效，两个双字软件随便用。

---

## 16. MRIF：内存驻留中断文件

硬件 guest 中断文件数量受 GEILEN 限制，而虚拟 hart 可能远多于硬件文件（云场景一台宿主机跑成百上千个 vCPU 很常见）。MRIF (Memory-Resident Interrupt File) 让 IOMMU 把 MSI **直接记录到普通内存**里，不再消耗硬件中断文件（AIA §8.3）。

**格式（AIA §8.3.1）**：一个 MRIF 占 512 字节、512 字节对齐，组织为 32 对小端双字，pending 位与 enable 位**交替存放**：

```
0x000  identities 1–63    的 pending 位
0x008  identities 1–63    的 enable 位
0x010  identities 64–127  的 pending 位
0x018  identities 64–127  的 enable 位
…
0x1F0  identities 1984–2047 的 pending 位
0x1F8  identities 1984–2047 的 enable 位
```

**工作方式**：hypervisor 在 MSI PTE 里配好 MRIF 地址与 notice MSI 的目的地（NPPN + NID，AIA §8.5.2）。之后每个打到该 MRIF 的 MSI，IOMMU 用 AMOOR（原子更新时）或读改写序列置位对应 pending 位，然后**必发一条 notice MSI 通知 hypervisor** "MRIF 内容变了"。

hypervisor 轮询/调度到该虚拟 hart 时再消化这些 pending。

与硬件 guest 文件对比一下取舍：

| | 硬件 guest 文件 | MRIF |
|---|---|---|
| 数量上限 | GEILEN（每 hart） | 内存大小（近乎无限） |
| 投递延迟 | 硬件直投，接近裸机 | 多一跳 notice MSI + 软件补投 |
| enable/pending 语义 | eie/eip 硬件仲裁 | 只是内存里的位，软件解释 |
| 适用场景 | 少量高性能直通 VM | 海量轻量虚拟 hart / 迁移快照 |

代价也要看清：MRIF 路径每次中断都多一次 hypervisor 介入（notice MSI），只是把"每中断注入"换成了"批量补投"，并且依赖 IOMMU 支持原子或非原子的 MRIF 更新。细节（无原子更新时的安全流程等）见 AIA §8.3.3–8.3.4。

notice MSI 自己也要有去处（AIA §8.3.5）：规范建议把它定向到一个专门的 guest 中断文件——hypervisor 在该文件里只使能一个专用 identity，这样 notice 一到就能与普通设备中断区分开。hypervisor 收到后扫描对应 MRIF 的 pending 位，逐个补投。

若系统支持原子更新，MRIF 的 eidelivery/eithreshold 状态由软件另存在 MRIF 结构之外，换入时一并恢复。

---

## 17. CSR 寄存器完整参考

本节是查阅用的编号速查表，按特权级分组；寄存器语义见第 11 节，编号依据 AIA §2.2–2.3。

### 17.1 Machine 级别 CSR

| CSR 名称 | 编号 | 访问 | 描述 |
|----------|------|------|------|
| `mie`/`mip` | 0x304/0x344 | 读/写 | 加宽到 64 位（AIA 扩展了本地中断位） |
| `miselect` | 0x350 | 读/写 | Machine 间接选择寄存器 |
| `mireg` | 0x351 | 读/写 | Machine 间接数据寄存器 |
| `mireg2`/`mireg3` | 0x352/0x353 | 读/写 | Smcsrind/Sscsrind 扩展的别名窗口 |
| `mtopei` | 0x35C | 读/写 | Machine top 外部中断（读=查询，写=claim）（AIA §3.9） |
| `mtopi` | 0xFB0 | 只读 | Machine top 中断概览（含本地中断） |

### 17.2 Supervisor 级别 CSR

| CSR 名称 | 编号 | 访问 | 描述 |
|----------|------|------|------|
| `sie`/`sip` | 0x104/0x144 | 读/写 | 加宽到 64 位 |
| `siselect` | 0x150 | 读/写 | Supervisor 间接选择寄存器 |
| `sireg` | 0x151 | 读/写 | Supervisor 间接数据寄存器 |
| `sireg2`/`sireg3` | 0x152/0x153 | 读/写 | Smcsrind/Sscsrind 扩展的别名窗口 |
| `stopei` | 0x15C | 读/写 | Supervisor top 外部中断 |
| `stopi` | 0xDB0 | 只读 | Supervisor top 中断概览 |

### 17.3 Hypervisor 与 VS 级别 CSR

| CSR 名称 | 编号 | 访问 | 描述 |
|----------|------|------|------|
| `hideleg` | 0x603 | 读/写 | Hypervisor 中断委托 |
| `hie`/`hip` | 0x604/0x644 | 读/写 | 加宽到 64 位 |
| `hvien` | 0x608 | 读/写 | Hypervisor 虚拟中断使能 |
| `hvictl` | 0x609 | 读/写 | Hypervisor 虚拟中断控制 |
| `hvip` | 0x645 | 读/写 | Hypervisor 虚拟中断 pending |
| `hviprio1` | 0x646 | 读/写 | VS 级中断优先级 1 |
| `hviprio2` | 0x647 | 读/写 | VS 级中断优先级 2 |
| `vsiselect` | 0x250 | 读/写 | VS 间接选择寄存器 |
| `vsireg` | 0x251 | 读/写 | VS 间接数据寄存器 |
| `vstopei` | 0x25C | 读/写 | VS top 外部中断 |
| `vstopi` | 0xEB0 | 只读 | VS top 中断概览 |
| `hgeip` | 0xE12 | 只读 | Guest 外部中断 pending（H 扩展 CSR，非 AIA 新增） |
| `hgeie` | 0x607 | 读/写 | Guest 外部中断使能（H 扩展 CSR，非 AIA 新增） |

以上编号见 AIA §2.2–2.3（Table 4/Table 5）；`hgeip`/`hgeie` 见 Privileged Architecture 的 H 扩展章节。

RV32 下上述 64 位 CSR 各有对应的高半寄存器（AIA Table 3/4/5），常用几个：

| CSR 名称 | 编号 | 说明 |
|----------|------|------|
| `mieh` | 0x313 | mie 高 32 位 |
| `mvienh` | 0x318 | mvien 高 32 位 |
| `hidelegh` | 0x613 | hideleg 高 32 位 |
| `hvienh` | 0x618 | hvien 高 32 位 |
| `hviph` | 0x655 | hvip 高 32 位 |
| `hviprio1h`/`hviprio2h` | 0x656/0x657 | hviprio1/2 高 32 位 |
| `vsieh`/`vsiph` | 0x214/0x254 | vsie/vsip 高 32 位 |

注意：间接窗口 CSR（miselect/siselect 等）与 `*topei` 在 RV32/RV64 都是当前 XLEN 宽，没有高半版本。

### 17.4 间接寄存器地址映射（\*iselect ∈ 0x70–0xFF 时，AIA §3.7）

| iselect | 寄存器 | 描述 |
|----------|--------|------|
| 0x70 | eidelivery | 中断投递使能 |
| 0x72 | eithreshold | 优先级阈值（identity ≥ t 不投递） |
| 0x80–0xBF | eip0 – eip63 | pending 数组 |
| 0xC0–0xFF | eie0 – eie63 | enable 数组 |

`*iselect` 的 0x30–0x3F 区间留给主要中断优先级数组 iprio（本地中断排序用），不属于 IMSIC。

### 17.5 易混淆 CSR 速查

| 你可能听说的 | 实际情况 |
|--------------|----------|
| `hgein` | 不存在。guest 文件选择用 `hstatus.VGEIN` 字段 |
| `htopei` / `htopi` | 不存在。HS 用 `stopei`/`stopi`，top 概览是 `stopi`(0xDB0)/`vstopi`(0xEB0) |
| `hiselect` / `hireg` | 不存在。间接窗口是 `vsiselect`/`vsireg`（0x250/0x251） |
| `hgeip` = 0xE13 | 错。`hgeip` = **0xE12**，`hgeie` = 0x607 |
| `hvien`=0x658 等连号 | 错。实际为 hvien 0x608、hvictl 0x609、hviprio1 0x646、hviprio2 0x647 |
| iselect 0x30 = eidelivery | 错。0x30–0x3F 是 iprio 数组；eidelivery 在 **0x70** |
| eip 数组在 0xC0 起 | 反了。**eip 从 0x80 起，eie 从 0xC0 起** |

上表前三行是网上资料里流传最广的以讹传讹——AIA 规范第 2 章的 Table 4/Table 5 里根本没有这四个 CSR。

---

## 18. 代码示例

### 18.1 初始化 IMSIC S-mode 中断文件

以下代码假定 RV64（偶数号 eie/eip 寄存器各管 64 个 identity）。

```c
// 间接访问辅助函数
static inline void imsic_s_write(uint32_t iselect, uint64_t value) {
    asm volatile("csrw siselect, %0" :: "r"(iselect));
    asm volatile("csrw sireg, %0" :: "r"(value));
}

static inline uint64_t imsic_s_read(uint32_t iselect) {
    uint64_t value;
    asm volatile("csrw siselect, %0" :: "r"(iselect));
    asm volatile("csrr %0, sireg" : "=r"(value));
    return value;
}

#define ISELECT_EIDELIVERY  0x70
#define ISELECT_EITHRESHOLD 0x72
#define ISELECT_EIP(n)      (0x80 + ((n) / 64) * 2)  /* RV64: 偶数号 */
#define ISELECT_EIE(n)      (0xC0 + ((n) / 64) * 2)  /* RV64: 偶数号 */

// 使能中断投递
void imsic_s_enable_delivery(void) {
    imsic_s_write(ISELECT_EIDELIVERY, 1);
}

// 设置优先级阈值（identity >= threshold 的中断不投递；0 = 无阈值）
void imsic_s_set_threshold(uint8_t threshold) {
    imsic_s_write(ISELECT_EITHRESHOLD, threshold);
}

// 使能/禁用特定中断 identity
void imsic_s_set_enable(uint32_t id, bool enable) {
    uint32_t iselect = ISELECT_EIE(id);
    uint64_t mask = 1ULL << (id % 64);
    if (enable)
        imsic_s_write(iselect, imsic_s_read(iselect) | mask);
    else
        imsic_s_write(iselect, imsic_s_read(iselect) & ~mask);
}

// 读 + claim 最高优先级外部中断（一条指令完成，避免竞态）
uint32_t imsic_s_claim(void) {
    uint64_t v;
    asm volatile("csrrw %0, stopei, x0" : "=r"(v));
    return (uint32_t)((v >> 16) & 0x7FF);   // identity 在 bits 26:16
}

// 只查询不 claim：用 stopi 看全部中断（含本地），或直接读 stopei
uint32_t imsic_s_peek(void) {
    uint64_t v;
    asm volatile("csrr %0, stopei" : "=r"(v));
    return (v >> 16) & 0x7FF;               // 返回 0 表示当前无可投递外部中断
}

// 若必须"先查后claim"两步走，安全做法是经 eip 数组清位，
// 而不是回头写 stopei（写 stopei 会按当前值 claim，可能错删新中断）
void imsic_s_clear_pending(uint32_t id) {
    uint32_t iselect = ISELECT_EIP(id);     /* 0x80 + (id / 64) * 2 */
    imsic_s_write(iselect, imsic_s_read(iselect) & ~(1ULL << (id % 64)));
}

// 完整初始化
void imsic_s_init(void) {
    imsic_s_set_threshold(0);               // 无阈值
    imsic_s_enable_delivery();              // 打开投递
}
```

### 18.2 初始化 APLIC

```c
// APLIC 域控制区关键寄存器（S-mode 域，偏移见 AIA Table 6）
#define APLIC_DOMAINCFG        0x0000
#define APLIC_SOURCECFG(i)     (0x0004 + ((i) - 1) * 4)   // i: 1..1023
#define APLIC_SETIPNUM         0x1CDC
#define APLIC_SETIE_BASE       0x1E00                     // setie[0..31]
#define APLIC_SETIENUM         0x1EDC
#define APLIC_TARGET(i)        (0x3004 + ((i) - 1) * 4)

#define APLIC_S_BASE 0x0D000000   // 平台相关，示例值

static inline void aplic_write32(uint64_t off, uint32_t val) {
    *(volatile uint32_t *)(APLIC_S_BASE + off) = val;
}

// domaincfg：bit8 IE、bit2 DM(1=MSI)、bit0 BE；bits31:24 只读 0x80
void aplic_s_configure_domain(int msi_mode) {
    aplic_write32(APLIC_DOMAINCFG, (1u << 8) | (msi_mode ? (1u << 2) : 0));
}

// 配置中断源：上升沿触发 + MSI 目标（hart index / EIID 按 target 字段编码填入）
void aplic_s_configure_source(uint32_t src_id, uint32_t target_val) {
    aplic_write32(APLIC_SOURCECFG(src_id), 4);        // SM = Edge1
    aplic_write32(APLIC_TARGET(src_id), target_val);
}

// 按编号使能中断源（也可写 setie[] 位图）
void aplic_s_enable_source(uint32_t src_id) {
    aplic_write32(APLIC_SETIENUM, src_id);
}

// 等价的位图写法：setie[n] 的 bit m 对应源号 32n + m + 1
// （源号从 1 起，所以 word/bit 都要偏移 1）
void aplic_s_enable_source_bitmap(uint32_t src_id) {
    uint32_t n = (src_id - 1) / 32;
    uint32_t m = (src_id - 1) % 32;
    aplic_write32(APLIC_SETIE_BASE + n * 4, 1u << m);
}

// MSI 模式的 target 编码：hart index << 18 | guest index << 12 | EIID
uint32_t aplic_msi_target(uint32_t hart_idx, uint32_t guest_idx, uint32_t eiid) {
    return (hart_idx << 18) | ((guest_idx & 0x3F) << 12) | (eiid & 0x7FF);
}

// Direct 模式：配置 hart index n 的 IDC 结构（域基址 + 0x4000 + n*32）
#define APLIC_IDC(n)      (0x4000u + (n) * 32)
#define APLIC_IDC_IDELIVERY 0x00
#define APLIC_IDC_IFORCE    0x04
#define APLIC_IDC_ITHRESHOLD 0x08
#define APLIC_IDC_TOPI      0x18
#define APLIC_IDC_CLAIMI    0x1C

void aplic_s_direct_enable_hart(uint32_t hart_idx) {
    aplic_write32(APLIC_IDC(hart_idx) + APLIC_IDC_IDELIVERY, 1);
}

// Direct 模式的 claim：读 claimi，返回值 bits 25:16 是源号，零表示没有中断
uint32_t aplic_s_direct_claim(uint32_t hart_idx) {
    return read32(APLIC_S_BASE + APLIC_IDC(hart_idx) + APLIC_IDC_CLAIMI);
}

static inline uint32_t aplic_read32(uint64_t off) {
    return *(volatile uint32_t *)(APLIC_S_BASE + off);
}
```

> **在途 MSI 的同步**（AIA §4.9.3）：APLIC 发出的 MSI 到达目标 IMSIC 有不确定延迟。改配置（比如把中断迁到别的 hart）或关停 hart 前，需要知道"旧配置的 MSI 都走完了"。规范给的同步手段就是 `genmsi`：写一次并等 Busy 回零，即可断定此前的 MSI 都已离开 APLIC——这也是 genmsi 被定位为"内存序同步点"的原因。

> 注意偏移易混点：`setip` 在 0x1C00，而 `setie` 在 **0x1E00**——两者相差一组数组宽度加保留区，手写偏移时最容易踩。

---

## 19. QEMU 实践指南

本节给出在 QEMU 上跑通 AIA 并调试中断的最小路径：启动 → 核对设备树 → GDB/monitor 定位。

### 19.1 启动支持 AIA 的 RISC-V 虚拟机

```bash
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
dtc -I fs /sys/firmware/devicetree/base > aia.dts
grep -A 20 aplic aia.dts
grep -A 20 imsic aia.dts
```

**典型设备树片段：**

```dts
aplic@d000000 {
    compatible = "riscv,aplic";
    reg = <0x0 0xd000000 0x0 0x4000000>;   // 域控制区基址与大小
    interrupts-extended = <&cpu0_intc 0xffffffff>;  // Direct 模式连到各 hart
    riscv,ndev = <64>;                     // 中断源数量
};

imsic@28000000 {
    compatible = "riscv,imsics";
    reg = <0x0 0x28000000 0x0 0x4000000>;  // 全部中断文件页的连续区域
    interrupts-extended = <&cpu0_intc 0xffffffff>;
    riscv,guest-num = <7>;                 // 每个 hart 的 guest 文件数（GEILEN）
};
```

读设备树时重点核对三件事：IMSIC 区域大小是否够 `（文件数 × 4 KiB）`对齐排布、`guest-num` 与 hypervisor 的 GEILEN 预期是否一致、APLIC 的 reg 大小是否覆盖到 target 数组（≥16 KiB，Direct 模式还要加 IDC 区）。

### 19.3 使用 QEMU 调试中断

```bash
# 启动 QEMU 并开启 GDB 调试（-s 监听 :1234，-S 暂停等待连接）
qemu-system-riscv64 \
    -machine virt,aia=aplic-imsic \
    -s -S \
    -kernel your-kernel.bin

riscv64-unknown-elf-gdb your-kernel.bin
(gdb) target remote :1234
(gdb) break *0x80200000
(gdb) continue
```

几个实战技巧：

- **QEMU monitor 看中断**：monitor 里 `info irq` 可看中断控制器状态；`-d guest_errors,int` 能打出异常的 MMIO 访问与中断事件。
- **GDB 直接读 CSR**：`(gdb) p/x $siselect`、`p/x $sireg`——注意读 `sireg` 前先设好 `siselect`，间接窗口是有状态的。
- **软件旁路验证**：不依赖设备，直接在 guest/HS 态往 IMSIC 页写 identity（或用 `setipnum`），可以单独验证"IMSIC → CPU"后半段链路，把问题二分。这个手法在 [24 篇](./24-interrupt-validation.md)里被列为标准用例步骤。

---

## 20. 常见问题与调试

### 20.0 一棵排查决策树

"软件看不到中断"时，沿数据流方向逐级二分：

```
设备发 MSI 了吗？
├─ 没有 → 查设备侧：MSI Capability/MSI-X 表配置、mask 位、
│         设备自己的中断使能
└─ 写出去了 → 到达目标中断文件了吗？（读 eip 对应位 / hgeip）
   ├─ 没到 → IOMMU 拦截层：PTE 有效位、DC 配置、地址是否落在
   │         中断文件页基址；或 APLIC 转发层：domaincfg.IE/DM、
   │         sourcecfg.SM、setie、target
   └─ 到了 → CPU 为什么没 trap？
      ├─ eidelivery ≠ 1（或 0x40000000）？→ 打开
      ├─ eie 位没使能 / iselect 算错（RV64 奇数号不存在）？
      ├─ eithreshold 非零且 identity ≥ 阈值？→ 调阈值
      ├─ mie/sie 的 MEIE/SEIE 没开，或 sstatus.SIE 全局关着？
      └─ 都开了 → 读 stopei 应非零；为零则查上一级
```

### 20.1 中断没有投递到 CPU

按决策树走到底后，最常翻车的几个点：

1. **APLIC domaincfg**：IE 位（bit 8）是否置 1，DM 是否符合预期模式。
2. **中断源**：sourcecfg 的 SM 不是 Inactive；setienum/setie 已使能；MSI 模式下 target 已配置。
3. **IMSIC eidelivery**（iselect 0x70）为 1（或 0x40000000 走 APLIC 直投）。
4. **eithreshold**（iselect 0x72）：确认方向——identity ≥ eithreshold 的中断**不投递**。若设了非零阈值，务必保证目标中断 identity 小于它。

```c
uint32_t threshold = imsic_s_read(ISELECT_EITHRESHOLD);
assert(threshold == 0 || irq_id < threshold);   // identity 越小优先级越高
```

5. **eie 对应位**：注意 RV64 下奇数号 eie/eip 寄存器不存在，别算错 iselect。

### 20.2 虚拟化环境中 Guest 收不到中断

先确认选择器这一层，再往下查重映射：

```c
// HS 态自检：VGEIN 必须是已实现的 guest 文件号（1..GEILEN），0 无效
uint64_t hstatus = read_csr(hstatus);
uint64_t vgein = (hstatus >> 12) & 0x3F;
assert(vgein != 0);
```

1. **hstatus.VGEIN 未设置或无效**——VS 窗口 CSR 将无法访问任何 guest 文件。
2. **IOMMU MSI 页表未配置或 PTE 的 V=0**——MSI 写会被拒或落空。
3. **hgeip 对应位没亮**——说明中断根本没到 guest 文件，回头查上游；亮了但 vCPU 没 trap，查 hgeie 使能与 hideleg 委托链。
4. **MRIF 场景**：VM 换入时确认 notice MSI 指向的内存区域状态已恢复一致。

### 20.3 MSI 地址配置错误

```c
void dump_aplic_msi_config(void) {
    printf("M-mode MSI addr cfg: 0x%08x:0x%08x\n",
           read32(APLIC_S_BASE + 0x1BC0), read32(APLIC_S_BASE + 0x1BC4));
    printf("S-mode MSI addr cfg: 0x%08x:0x%08x\n",
           read32(APLIC_S_BASE + 0x1BC8), read32(APLIC_S_BASE + 0x1BCC));
}
```

把打印出的 base PPN/组参数与设备树里 IMSIC 节点的 reg 对一遍，是最快的定位手段。另一类隐蔽错误：设备 MSI 地址没落在中断文件页基址（+0 或 +4）上——写入会落到页内保留字节被静默忽略，不报错、无中断。

### 20.4 收到身份为 0 的伪中断

identity 0 无效，但硬件仍可能上报（比如 claim 到 0）。IMSIC 侧 `*topei` 读回零表示"没有可投递中断"，驱动必须把零当作"无事发生"处理而不是数组下标 0；APLIC Detached 模式下软件误写 setipnum(0) 也会造出这种假 pending。

规范对 IOMMU 的要求是照常把 identity-0 的 MSI 记进 MRIF 的 bit 0 并发 notice（AIA §8.3.1），所以 MRIF 路径同样要容忍这个"幽灵位"。

### 20.5 电平触发源的丢中断问题

与直觉相反，APLIC 转发电平源 MSI 后会清 pending 并忽略信号线，直到线被撤销（AIA §4.9.2）。如果 ISR 处理了第一个诱因后线上还有第二个有效电平（比如两个队列同时就绪），不补看一眼这个中断就再也不会触发。规范给的两个对策（AIA §4.9.2）：

1. ISR 退出前读一次 `in_clrip` 对应位：rectified input 仍为 1 就再处理一轮，直到观察到信号撤销才能安全退出；
2. 或者退出前往 `setipnum` 写一次该源号——若线仍有效，pending 位会被重新置起并触发再次转发；线已撤销则无事发生。

### 20.6 性能问题排查

1. **中断延迟过高**：检查是否有非必要的 eithreshold 屏蔽、Direct 模式下 IDC 的 ithreshold 设置。
2. **claim 方式不当**：用两条指令分别读写 `*topei` 不仅可能丢中断，还会引入额外重试开销，统一用 `csrrw`。
3. **量化**：

```c
uint64_t start = read_csr(mcycle);
handle_interrupt();
printf("took %lu cycles\n", read_csr(mcycle) - start);
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
| GEILEN | Guest External Interrupt LENgth | H 扩展定义的 guest 外部中断数上限 |
| VM | Virtual Machine | 虚拟机 |
| Hart | Hardware Thread | 硬件线程（RISC-V 的 CPU 核心概念） |

---

## 总结与下一步

本文从 PLIC 的痛点出发，讲解了 AIA 的三大组件：IMSIC 以中断文件为单位接收 MSI 并提供 per-hart/per-privilege/per-VM 的隔离；APLIC 管理有线中断并在两种投递模式间转换；IOMMU 的 MSI 页表与 MRIF 补齐了直通虚拟化的最后一环。寄存器细节请以 AIA 规范原文为准，本文标注的章节号可直接对照查阅。

中断链路的知识到这里闭环了，但"软件看不到中断"在验证视角下是另一个故事：怎么把这些寄存器语义变成可在 Palladium/FPGA 上跑的用例矩阵、失败时怎么分层定位——那是验证篇的任务。

→ 下一步：[中断子系统验证](./24-interrupt-validation.md)——把本篇的 IMSIC/APLIC/guest 文件机制换成验证视角，产出可直接执行的中断用例矩阵。

---

## 参考资料

- RISC-V Advanced Interrupt Architecture Specification v1.0（2025-03-12）：[github.com/riscv-non-isa/riscv-aia](https://github.com/riscv-non-isa/riscv-aia)——本仓库 `riscv/reference/` 下有本地副本
- 相关篇目：[中断与异常](./04-interrupts-and-exceptions.md)（PLIC 与 trap 基础）、[H 扩展与虚拟化](./06-virtualization-h-extension.md)（两阶段翻译与注入）、[中断子系统验证](./24-interrupt-validation.md)、[IOMMU 与虚拟化验证](./25-iommu-virtualization-validation.md)
- Linux 内核：`Documentation/arch/riscv/`，驱动实现在 `drivers/irqchip/irq-riscv-imsic-*.c`、`irq-riscv-aplic-*.c`
