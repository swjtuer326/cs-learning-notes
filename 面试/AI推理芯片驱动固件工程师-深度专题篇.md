# AI 推理芯片驱动/固件开发 — 深度专题篇

> **定位**：本文是面试题库的第三配套文件，聚焦于五个在 AI 推理芯片驱动/固件开发中至关重要但常规面试题库较少深入的专题：**IOMMU 与 DMA 隔离、GSP 固件与 RPC 协议、按需分页与异构内存、AI 芯片电源与热管理、多租户安全与隔离**。每个专题以"为什么重要 → 核心机制 → 工程实践 → 面试角度"组织。

---

## 专题一：IOMMU 与 DMA 隔离 —— AI 推理芯片的安全底座

### 为什么重要

在 AI 推理 SoC 中，AI 加速核通过 DMA 直接读写系统内存（模型权重、KV cache、中间激活）。如果没有 IOMMU，一个被攻破的 Guest VM 或一个有 bug 的 AI 核固件可以：

1. **用 DMA 写覆盖 Hypervisor 的内核数据结构**——Guest 驱动配置 AI 核的 DMA 源/目标地址时，直接填宿主机物理地址（裸机时代的习惯）
2. **跨租户数据窃取**——AI 核 A 的 DMA 读越界，读到 AI 核 B 的显存分区
3. **伪装成合法 MSI 中断**——AI 核直接写 IMSIC 地址，伪造不属于它的中断向量

IOMMU 的解法是把 CPU MMU 的翻译/权限模型照搬到 IO 互连上：设备发出的每个总线地址（IOVA 或 GPA）先经过 IOMMU 的页表翻译和权限检查，才被放到物理总线上。这个机制在 x86（VT-d）、ARM（SMMUv3）上已经成熟 10+ 年，RISC-V IOMMU v1.0.1 是同一思想的第三个实现。

### 核心机制：RISC-V IOMMU 的翻译层次

```
设备发出 DMA 地址 (IOVA / GPA)
       ↓
[IOMMU 的第一阶段翻译 (可选)]
   输入: IOVA (设备视角的虚拟地址)
   输出: GPA (Guest 物理地址) 或 SPA (系统物理地址,若无第二阶段)
   页表格式: Sv39/Sv48/Sv57 (与 CPU 完全相同)
       ↓
[IOMMU 的第二阶段翻译 (可选,用于虚拟化)]
   输入: GPA
   输出: SPA (系统物理地址)
   页表格式: Sv39x4/Sv48x4 (与 hgatp 格式相同)
       ↓
[权限检查: R/W + 内存属性检查]
       ↓
[总线事务到物理地址]
```

**RISC-V IOMMU 与 Intel VT-d / ARM SMMUv3 的同构对比**：

| 维度 | Intel VT-d | ARM SMMUv3 | RISC-V IOMMU |
|------|-----------|-----------|-------------|
| **设备→翻译上下文** | Root/Context 表,按 PCIe BDF 索引 | Stream Table (STE),按 StreamID 索引 | 设备目录表 DDT,按 `device_id` 索引 |
| **进程级地址空间** | PASID → PASID 表 | SubstreamID → CD (Context Descriptor) | `process_id` → PDT (进程目录表) |
| **命令接口** | Invalidate Queue 等一组队列 | 命令队列 CMDQ | 命令队列 CQ |
| **故障上报** | Fault Recording 寄存器 | Event Queue | 故障队列 FQ / 页请求队列 PQ |
| **MSI 翻译** | IRTE (Interrupt Remapping Table) | L1/L2 中断配置表 | MSI 地址翻译表 (与 AIA/IMSIC 关联) |
| **页表格式** | 自有的 4/5 级页表 | 自有的 3/4 级页表 | **复用 CPU 的 Sv39/Sv48/Sv57** |

> **RISC-V 最关键的设计选择**：IOMMU 页表格式**完全复用 CPU 页表**（Sv39/Sv48）。这带来两个好处：① 验证工作量减半——页表遍历逻辑只需验证一份，CPU 和 IOMMU 共用参考模型；② 同一张 G-stage 页表可同时用于 CPU 侧两阶段翻译和 IOMMU 第二阶段——Hypervisor 只维护一份页表。

### 工程实践：AI 推理芯片的 IOMMU 配置实例

**场景**：SoC 有两个 AI 推理核，各自通过 DMA 访问独立的显存分区，互不干扰。

```
IOMMU 设备目录表 (DDT):
  device_id=0 (AI 核 A):
    → 进程目录表 PDT[process_id=0] (进程 A):
        → 第一阶段页表 (Sv39): IOVA → GPA
        → 第二阶段页表 (Sv39x4): GPA → SPA
          GPA 0x0_0000_0000 → SPA 0x10_0000_0000  (AI 核 A 显存分区, 16GB)
          GPA 0x1_0000_0000 → SPA 0x20_0000_0000  (共享 DDR, 只读)
  
  device_id=1 (AI 核 B):
    → 进程目录表 PDT[process_id=1] (进程 B):
        → 第一阶段页表 (Sv39): IOVA → GPA
        → 第二阶段页表 (Sv39x4): GPA → SPA
          GPA 0x0_0000_0000 → SPA 0x11_0000_0000  (AI 核 B 显存分区, 16GB)
          GPA 0x1_0000_0000 → SPA 0x20_0000_0000  (共享 DDR, 只读)
```

关键点：AI 核 A 和 B 访问相同的 GPA `0x0_0000_0000`，通过不同的第二阶段页表映射到不同的物理地址——这正是 IOMMU 提供的隔离。

**mmap 与 IOMMU 的配合**：

当管理核的 Zephyr/Linux 驱动为 AI 核做 mmap（映射 AI 核的本地 SRAM 到管理核的地址空间），IOMMU 在另一侧做反向映射：
```
管理核 VA → 管理核 MMU → SPA ← IOMMU 第二阶段 ← GPA ← IOMMU 第一阶段 ← AI 核 DMA 地址
```

这确保了"管理核看 AI 核的地址"与"AI 核看自己的地址"的一致性。

### 面试角度

**Q: IOMMU 缺页（没有建立映射）时会发生什么？与 CPU MMU 缺页有何不同？**

> CPU MMU 缺页：触发 page fault 异常 → OS handler → 分配物理页 → 建立 PTE → 返回重试。可以由软件完全处理。
> 
> IOMMU 缺页：
> - **有 PRI (Page Request Interface)**：IOMMU 向 CPU 发送页请求（放入 FQ/PQ），CPU 处理完页请求后，IOMMU 重试事务。适用于可重试的 DMA（如 GPU 的 UVM）。
> - **无 PRI**：IOMMU 产生故障记录（放入 FQ），**终止该 DMA 事务**（返回错误 completion 给设备）。这是大多数嵌入式 DMA 引擎的行为。
> 
---

## 专题二：GSP 固件与 RPC 协议 —— "为何把逻辑放入 GPU 上的微控制器"

### 为什么重要

Turing (RTX 20xx) 之后，NVIDIA GPU 上多了一个 RISC-V 核心（GSP, GPU System Processor），运行闭源的 GSP-RM 固件。GSP 通过 Falcon2 框架与 CPU 侧 RM 通信——把原本在 CPU 侧、通过 PCIe MMIO 操作 GPU 寄存器的高频逻辑，迁移到 GPU 内部的微控制器上。理解这个迁移的动机和实现，对 AI 推理芯片的"管理核↔加速核固件分离"设计有直接指导意义。

### 核心机制：RM CPU ↔ GSP RPC 通信协议

**为什么把逻辑迁入 GSP**：

1. **延迟降低**：CPU 侧 RM 访问 GPU 内部寄存器需要通过 PCIe MMIO 读/写，每次往返 ~500ns。GSP 在 GPU 内部，寄存器访问 ~10ns（内部总线）。高频操作（功耗状态切换、温度轮询、引擎调度）累积的延迟差异显著。
2. **PCIe 带宽节省**：GSP 本地处理了本来需要走 PCIe 的控制面流量。推理高峰时，PCIe 带宽应全部用于数据搬运（权重更新、KV cache 迁移），不应被控制面消耗。
3. **安全隔离**：GPU 内部寄存器不应被 CPU 侧软件直接操作（安全风险）。GSP 作为"可信代理"承载敏感操作。

**RPC 协议栈**：

```
CPU 侧 RM                              GSP 侧 (GPU 上)
kernel_gsp.c (RPC 客户端)              GSP-RM 固件
     ↕                                       ↕
GspMsgQueueSendCommand()             [消息队列 consumer]
     ↕                                       ↕
[消息队列: 共享内存中的环形缓冲]  ← 物理位于 GPU BAR1 映射的显存中
     ↕                                       ↕
[MCTP 传输层头]  ← 标准管理协议     [MCTP 解包]
     ↕
[NVDM NVIDIA Data Message 头] ← NVIDIA 私有
     ↕
[RM RPC payload: function_id + args]
```

**一次 RPC 调用的完整时序**：
```
1. CPU-RM: 填充 RPC payload → 加 NVDM 头 → 加 MCTP 头
2. CPU-RM: 写消息到共享队列的 tail slot
3. CPU-RM: 写 doorbell 寄存器 (通知 GSP: 有新消息)
4. GSP: doorbell 中断 → 读队列 head → 取消息
5. GSP: 解析 RPC → 执行操作 (如: 调整 GPU 时钟)
6. GSP: 填充 response → 写共享队列 tail → 发 MSI 中断给 CPU
7. CPU-RM: MSI 中断 → bottom-half → 读 response → 返回给调用者
```

总延迟：~5–50μs（取决于 GSP 负载和 PCIe 延迟）。这比 CPU 直接 MMIO 访问慢（MMIO ~0.5μs），但比在突发批量操作中每步都 MMIO 快得多（批量 RPC 摊销了协议头开销）。

### GSP 模式的一般性设计原则

从 NVIDIA GSP 的设计中可以抽象出一些在异构 SoC 中决定"功能放 Host 还是放片上控制器"的通用原则：

- 频率高 + 延迟敏感（温度监控、时钟切换）→ 倾向于放片上控制器固件
- 复杂决策 + 需要 OS 信息（显存碎片整理、多进程调度策略）→ 倾向于放 Host 侧驱动
- 安全敏感（寄存器保护、固件签名验证）→ 倾向于放片上控制器固件

但要注意：这些原则的具体落地高度依赖于 SoC 的实际硬件架构、片上控制器的计算能力和实时性要求。NVIDIA 的 GSP 是 RISC-V + Falcon2 框架的组合，有足够的计算能力承载复杂固件；一个资源更受限的片上控制器可能只能处理最基础的电源/时钟管理。

### 面试角度

**Q: 如果不用 GSP/RPC 模式，把所有 GPU 控制逻辑都放在 CPU 侧 RM 有什么问题？**

> 1. **延迟累积**：电源状态切换需要写 20+ 个寄存器 × 0.5μs MMIO 延迟 = 10μs。如果切换频繁（如推理请求间歇的 idle→active 切换），延迟累积到几十微秒级别。
> 2. **PCIe 带宽污染**：推理高峰时控制面的 MMIO 事务与数据面的 DMA 事务竞争 PCIe 带宽。GSP 本地处理消除了控制面的 PCIe 流量。
> 3. **安全攻击面**：如果 CPU 侧 RM 被攻破，攻击者可以直接 MMIO 读写 GPU 内部寄存器——改变电源状态（过压）、篡改页表（越权读显存）、写固件区域（持久化恶意代码）。GSP 固件提供了隔离边界。
> 4. **OS 兼容性**：CPU 侧 RM 需要为每个 OS 编写寄存器操作代码。GSP 固件对 OS 透明——适应新 OS 只需适配 RPC 协议（而非数以千计的寄存器偏移）。

---

## 专题三：按需分页与异构内存管理 —— "200GB 模型跑在 80GB GPU 上"

### 为什么重要

LLM 推理面临直接的内存压力：70B 参数的 FP16 模型 = 140GB 权重（不含 KV cache 和激活）。但 H100 最多只有 80GB HBM。UVM 的按需分页让"模型大于显存"成为可能——把不常用的层换出到 CPU 内存，用到时再换入。理解这套机制的内核实现，是优化"GPU 内存超分配"推理性能的关键。

### 核心机制：UVM 缺页处理全链路

**两种缺页路径**：

1. **GPU 侧缺页**（GPU SM 访问 UVM 管理的内存 → TLB miss → MMU walk → PTE invalid）：
   ```
   SM → GPU MMU fault → 中断 (MSI-X 特定向量)
   → RM 的 top-half (intr.c) → 判断此 VA 属于 UVM
   → 转交 UVM: uvm_gpu_fault()
     → 查 uvm_va_space → uvm_va_block (2MB 管理单元)
     → resident_mask[GPU] == 0? → 没有本地页 → 需要迁移/分配
     → 选源处理器: resident_mask 中标记有数据的处理器
     → uvm_page_populate():
       1. 从 PMA 分配 GPU 物理页 (64KB/2MB)
       2. 从源处理器 DMA 搬运数据 (CE copy engine)
       3. 更新 GPU PTE: valid, aperture=VIDEO_LOCAL
     → 重放 faulting 指令 (SM 重新执行)
   ```

2. **CPU 侧缺页**（CPU 访问 UVM 管理的内存 → 普通 Linux 缺页）：
   ```
   CPU → MMU 缺页 → Linux do_page_fault()
   → VMA 的 vm_ops->fault = uvm_vm_fault
   → uvm_vm_fault():
     → uvm_va_block_fault() 同上, 但从 CPU 侧分配系统内存页
     → migrate_vma (如果页面目前在 GPU,通过 HMM 回迁)
   ```

**迁移决策（Access Counter 硬件）**：
GPU 硬件在每个 64KB 页上维护访问计数器。定期中断（~100ms 周期）通知 UVM，UVM 据此决定：
- 计数器高且本地无页 → 预取 (prefetch)
- 计数器低且本地有页 → 驱逐候选 (eviction candidate)

**驱逐与 thrashing 防止**：
- 驱逐策略：LRU + access counter 综合——计数低 + 最近未被访问 = 驱逐
- 防止 thrashing（页面在两个 GPU 间反复迁移）：如果连续 3 次驱逐同一页 → 标记为 "shared"（两个 GPU 都保留副本，写时通过 break-COW 同步）

### 工程实践：LLM KV cache 的 UVM 管理

```
LLM decode 的 KV cache 内存模式：

每个 token 生成后, KV cache 追加 key/value 向量。
如果用 UVM Managed Memory:

[GPU 0 的 HBM] ← KV heads 1-16, 按需缺页 (写时 fault)
[GPU 1 的 HBM] ← KV heads 17-32, 按需缺页
[CPU DDR]       ← 驱逐的旧 KV cache 层 (访问频率低)

问题: 每次 decode step 都写 KV cache → write-fault → populate
     但如果多个 GPU 共享 KV cache (TP), 写操作导致所有 GPU PTE invalidate
     → 其他 GPU 下次读需重新 fault → thrashing

解决: KV cache 不应该用 UVM Managed (自动迁移)
     而应用 cuMemCreate + cuMemMap: 显式分配在指定 GPU,
     或用 cudaMemAdviseSetAccessedBy + cudaMemPrefetchAsync 手动控制。
```

### 面试角度

**Q: UVM 的按需分页与 Linux swap 的按需分页有何本质不同？**

> | 维度 | UVM (GPU) | Linux swap |
> |------|----------|-----------|
> | **触发方** | GPU SM / CPU MMU 缺页 | CPU MMU 缺页 |
> | **数据搬运方** | CE (Copy Engine, GPU 内部的 DMA 引擎) | CPU (memcpy) |
> | **页面大小** | 64KB (PMA 粒度) / 2MB (大页) | 4KB / 2MB (THP) |
> | **迁移方向** | GPU↔CPU, GPU↔GPU (NVLink/PCIe) | DRAM↔Disk |
> | **延迟** | ~10μs (NVLink) ~50μs (PCIe) | ~5000μs (SSD) ~10000μs (HDD) |
> | **决策依据** | GPU access counter 硬件 + LRU | 软件 LRU/Clock 算法 |
> 
> **本质差别**：UVM 的"backing store"是另一个处理器的内存（GPU 显存、CPU DDR），而 Linux swap 的 backing store 是磁盘。这意味着 UVM 的缺页延迟是 μs 级的（内存↔内存），swap 是 ms 级的（内存↔磁盘）。这是为什么 UVM 可以用于延迟敏感的推理场景，而 swap 绝不行。

---

## 专题四：AI 芯片的电源与热管理 —— "推理卡的真正成本在电费"

### 为什么重要

一张 H100 SXM5 的 TDP 是 700W，一个 8 卡节点是 5.6kW——等效于一个家庭的空调。数据中心 GPU 的电费在 3 年 TCO 中占比 30-40%。AI 推理芯片的电源管理不仅是"环保"，更是商业竞争力——能效比（TOPS/W）直接影响定价。

### 核心机制：GPU 功耗的分层管理

**NVIDIA GPU 的功耗层次**：

```
App 层: 应用请求 GPU 算力
  ↓
GPU Boost: 根据负载动态调整核心时钟 (类似 CPU Turbo Boost)
  ↓
P-States (Performance States): P0(max) → P5 → P8(idle/idle with memory clock down)
  ↓
Thermal Management: 温度传感器 → 降频 (thermal throttling) → 紧急断电
  ↓
Power Cap: 可编程的功耗上限 (如 nvidia-smi -pl 600)
  ↓
VRM (Voltage Regulator Module): 供电电压/电流的硬件控制
```

**各层在谁的管辖下**：

| 层次 | 管理者 | 延迟要求 | 机制 |
|------|--------|---------|------|
| **P-States 切换** | GSP 固件 | < 10μs | 固件写 GPU 时钟 PLL 控制寄存器 |
| **温度监控** | GSP 固件 | 10-100ms 周期 | 读片内温度传感器 → 查表决定 target P-state |
| **Power Cap** | RM (CPU 侧) | 100ms-1s | RM 根据配置限制总功耗不超过 Cap |
| **应用层策略** | 用户态 (DCGM) | 1-10s | 监控 GPU 利用率 → 建议降频 |

**Zephyr RTOS 的 PM 框架**（用于管理核功耗管理）：

```c
// 管理核的 AI 核 PM 设备定义
DEVICE_DT_DEFINE(DT_NODELABEL(ai_core), ai_pm_init, NULL,
    &ai_pm_data, &ai_pm_config,
    POST_KERNEL, CONFIG_AI_ENGINE_INIT_PRIORITY,
    &ai_pm_api);

// PM action 回调
static int ai_pm_action(const struct device *dev, enum pm_device_action action) {
    switch (action) {
    case PM_DEVICE_ACTION_SUSPEND:
        // 通过 SBI/mailbox 通知 AI 核进入 idle
        // 门控 AI 核时钟 → 功耗从 50W 降到 5W
        sbi_ai_core_stop(core_id);
        break;
    case PM_DEVICE_ACTION_RESUME:
        // 恢复 AI 核时钟 → PLL 锁定 → 通知 AI 核就绪
        sbi_ai_core_start(core_id, entry_addr);
        break;
    case PM_DEVICE_ACTION_LOW_POWER:
        // 降低 AI 核频率但不停止
        sbi_ai_core_set_pstate(core_id, P8);
        break;
    }
    return 0;
}
```

### 工程实践：AI 推理负载的功耗优化策略

**LLM decode 的功耗特征**：
- Prefill phase（第一次 prompt 处理）：高 SM 利用率 + 高 HBM 带宽 → 接近 TDP（~700W）
- Decode phase（后续 token 生成）：低 SM 利用率（memory-bound）+ 中等 HBM 带宽 → ~400W
- Idle between requests：几乎 0 SM 利用率 → 但仍 ~150W（HBM 自刷新 + 芯片漏电）

**优化策略**：
1. **decode 阶段降频**：decode 是 memory-bound（瓶颈在 HBM 带宽），降低核心频率几乎不影响延迟，但减少 ~100W 功耗
2. **idle 阶段快速挂起**：请求间隔 > 100ms 时，将 GPU 推入 deeper idle state（关闭部分 HBM bank 的刷新）→ 功耗 < 50W
3. **批量推理（batching）**：多个请求合并，SM 利用率提高，分摊 idle 功耗 → 每 token 功耗降低

### 面试角度

**Q: 如何设计一个 AI 推理 SoC 管理核的"功耗调度器"来平衡能效和延迟 SLA？**

> 设计要点：
> 1. **两级控制**：粗粒度（管理核 Zephyr PM policy）+ 细粒度（AI 核片上控制器固件）
> 2. **预测式降频**：根据请求队列长度和 p99 latency 历史，预测未来 100ms 的负载——如果队列 < 阈值 → 降频；队列 > 阈值 → 升频
> 3. **快速恢复**：AI 核从 idle 到 active 的 PLL 锁定延迟必须 < 10μs（从请求到达管理核到 AI 核开始计算的总延迟的 SLA 是 < 100μs）
> 4. **DVFS (Dynamic Voltage Frequency Scaling)**：降至更低频率时同步降低电压——功耗 ∝ f × V²
> 5. **热感知**：温度传感器读数输入到管理核的 PID 控制器，防止 thermal throttling（突然降频导致延迟 spike）

---

## 专题五：多租户安全与隔离 —— "一张 GPU 同时跑 10 个用户的推理"

### 为什么重要

AI 推理服务的商业模型通常是多租户的——一张 GPU 同时服务多个客户。如果租户 A 的代码能读到租户 B 的数据（模型权重、推理输出），这是安全灾难。GPU 的隔离机制比 CPU 更复杂，因为 GPU 的"地址空间"由以下组成：**显存分区、GPU MMU 页表、channel/context、fence/event、中断向量**——隔离必须覆盖所有这些。

### 核心机制：多层隔离体系

```
隔离层 1: MIG (GPU 硬件分片)
  ├ SM 分区: 物理切分 (CI 1: 28 SM, CI 2: 28 SM, ...
  ├ HBM 分区: 独立 PMA + Heap (硬隔离)
  ├ VASpace: 独立 GMMU 页表
  └ 故障隔离: CI 1 的 Xid 48 不影响 CI 2

隔离层 2: CUDA MPS (Multi-Process Service)
  ├ 共用 SM (时分复用)
  ├ 共用显存池 (UVM 弱隔离)
  └ MPS server 崩溃 → 所有 client 崩溃

隔离层 3: 纯软件 (CUDA Stream priority)
  ├ 共用 SM + 共用显存
  └ 无故障隔离
```

**IOMMU 的租户隔离角色**（对于非 MIG 系统）：

在无 MIG 的 GPU 上，IOMMU 是租户间 DMA 隔离的唯一手段：
- **VFIO/PCIe SR-IOV**：把 GPU 切为多个 Virtual Function (VF)，每个 VF 有自己的 PCIe BDF → IOMMU 的 DDT 按 BDF 索引
- 每个 VF 对应独立的 IOMMU 进程目录表 (PDT) → 访问独立的显存物理地址范围
- GPU 侧的 GMMU 再做一层翻译（GPU VA → GPU PA），形成"IOMMU + GMMU"两层隔离

### 工程实践：推理服务的租户隔离部署

```
物理 GPU 0 (H100)
  ├ MIG 实例 1 (10GB): 租户 A (GPT-3 推理, SLA 要求 p99 < 50ms)
  │   KMD 状态: /dev/nvidia0-1 (独立 PMA, Heap, VASpace, channel pool)
  │   进程: gpt3_worker pid=1234
  │   CUDA context: 0x7f...00
  │
  ├ MIG 实例 2 (20GB): 租户 B (Llama-2 推理, 吞吐优先)
  │   KMD 状态: /dev/nvidia0-2
  │   进程: llama_worker pid=5678
  │   CUDA context: 0x7f...80
  │
  └ MIG 实例 3 (10GB): 租户 C (内部测试)
      KMD 状态: /dev/nvidia0-3
      进程: test_worker pid=9012
```

**KMD 侧的隔离检查清单**：
- [ ] 每个 MIG 实例独立的 RM client handle (不可跨实例引用)
- [ ] `NV_ESC_RM_ALLOC_MEMORY` 分配只能访问自己的 PMA (硬件保证)
- [ ] P2P 跨 MIG 实例默认禁用（需显式配置并审计）
- [ ] Doorbell WDT token 绑定到特定 channel→特定 MIG 实例
- [ ] `uvm_enable_peer_access` 只在同一 MIG 实例的 GPU 间允许
- [ ] `dmesg` 的 Xid 错误能快速区分哪个 MIG 实例出错

### 面试角度

**Q: 在设计 AI 推理芯片时，如何决定用 MIG 式的硬件分片还是纯软件多租户？**

> 决策矩阵：
> 
> | 需求 | 硬件分片 (MIG-like) | 纯软件 (MPS+Stream) |
> |------|-------------------|-------------------|
> | **必须要求** |  |  |
> | 故障隔离 (一个租户的 Xid 不影响其他) | ✅ 必需硬件分片 | ❌ 做不到 |
> | 显存硬隔离 (租户 B 不能读租户 A 的数据) | ✅ | ⚠️ 依赖 UVM 隔离,弱保证 |
> | 延迟 SLA 的硬保证 (专属 SM slices) | ✅ | ⚠️ 依赖优先级,可能被抢占 |
> | **灵活性** |  |  |
> | 动态调整租户资源 | ❌ 需重启 GPU | ✅ 运行时调整 |
> | GPU 利用率最大化 | 折中 (空闲 SM 无法被其他租户用) | 最高 |
> | **成本** |  |  |
> | 硅面积 | +5-10% (额外的 PMA/MMU 实例) | 0% |
> | 驱动复杂度 | 高 (需要 per-instance RM 状态) | 中 |
> 
> 结论：对于商业化的多租户推理服务（不同客户的 SLA 和法律隔离要求）→ **必须 MIG 式硬件分片**。对于内部的多模型并行推理（同一用户、无隔离需求）→ MPS 足够。

---

## 附录：专题速查

| 专题 | 核心面试线索 | 关联题库题号 |
|------|------------|------------|
| IOMMU/DMA 隔离 | "设备 DMA 如何不破坏 Guest 隔离？" "缺页时怎么办？" | Q9 (两阶段翻译), Q4 (PMP vs IOMMU) |
| GSP/RPC 协议 | "为什么 GPU 逻辑要进微控制器？" "RPC 协议栈长什么样？" | Q30 (RM/GSP 三层), Q44 (KMD 设计) |
| 按需分页 | "200GB 模型怎么跑在 80GB GPU 上？" "thrashing 怎么防止？" | Q33 (UVM 三层), Q41 (延迟翻倍) |
| 电源/热管理 | "推理卡的电费怎么优化？" "Zephyr PM 怎么控制 AI 核？" | Q45 (异构 SoC), Q27 (实时性) |
| 多租户安全 | "一张 GPU 同时跑 10 个客户怎么隔离？" "MIG vs MPS？" | Q38 (MIG), Q44 (KMD 设计) |

---

**文档版本**：v1.0  
**最后更新**：2026-07-29  
**定位**：`AI推理芯片驱动固件工程师面试题库.md` 与 `实战场景与案例.md` 的专题深度补强