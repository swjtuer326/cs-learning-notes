# GPU 集群互联：NVLink 到 InfiniBand

> **核心命题**：分布式训练和推理的性能瓶颈不在计算，而在通信。理解 GPU 互联技术——从节点内的 NVLink/NVSwitch 到跨节点的 InfiniBand/RoCE——是理解大规模 LLM 系统的基础。

### 前置知识

| 需要了解 | 参考文档 |
|----------|----------|
| 分布式训练并行策略 | [05-LLM分布式训练](../05-LLM分布式训练：并行策略与ZeRO.md) |
| NVIDIA GPU 架构演进 | [11-NVIDIA GPU架构演进](./11-NVIDIA-GPU架构演进与LLM.md) |

## 目录

1. [互联技术全景](#互联技术全景)
2. [节点内互联：NVLink 与 NVSwitch](#节点内互联nvlink-与-nvswitch)
3. [跨节点互联：InfiniBand 与 RoCE](#跨节点互联infiniband-与-roce)
4. [网内计算：SHARP](#网内计算sharp)
5. [拓扑感知调度](#拓扑感知调度)
6. [集群网络设计](#集群网络设计)
7. [故障与可靠性](#故障与可靠性)
8. [最新互联技术](#最新互联技术)

---

## 互联技术全景

```
┌─────────────────────────────────────────────────────────────────────────┐
│                       GPU 集群互联层次                                    │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  层次 1: 节点内 (Intra-Node)                                            │
│  ┌──────────────────────────────────────────┐                          │
│  │  NVLink / NVSwitch                       │                          │
│  │  带宽: 900 GB/s (H100) → 1.8 TB/s (B200) │                          │
│  │  拓扑: 全互联 (NVSwitch) 或 Mesh          │                          │
│  │  用途: TP (张量并行)                      │                          │
│  └──────────────────────────────────────────┘                          │
│                                                                         │
│  层次 2: 机架内 (Intra-Rack)                                            │
│  ┌──────────────────────────────────────────┐                          │
│  │  NVLink Switch / InfiniBand / RoCE       │                          │
│  │  带宽: 400 GB/s (NDR) → 800 GB/s (XDR)   │                          │
│  │  拓扑: Fat-Tree / DragonFly              │                          │
│  │  用途: PP (流水线并行), DP (数据并行)     │                          │
│  └──────────────────────────────────────────┘                          │
│                                                                         │
│  层次 3: 跨机架 (Inter-Rack)                                            │
│  ┌──────────────────────────────────────────┐                          │
│  │  InfiniBand / RoCE / Spectrum-X          │                          │
│  │  带宽: 400 GB/s (NDR)                    │                          │
│  │  拓扑: Fat-Tree / DragonFly+             │                          │
│  │  用途: DP (数据并行), ZeRO 通信           │                          │
│  └──────────────────────────────────────────┘                          │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 节点内互联：NVLink 与 NVSwitch

### 2.1 NVLink 演进

| 代际 | 架构 | 单链路带宽 | GPU 间链路数 | 总带宽 | 代表 GPU |
|------|------|-----------|-------------|--------|---------|
| **NVLink 1.0** | Pascal | 40 GB/s | 4 | 160 GB/s | P100 |
| **NVLink 2.0** | Volta | 50 GB/s | 6 | 300 GB/s | V100 |
| **NVLink 3.0** | Ampere | 50 GB/s | 12 | 600 GB/s | A100 |
| **NVLink 4.0** | Hopper | 50 GB/s | 18 | 900 GB/s | H100 |
| **NVLink 5.0** | Blackwell | 100 GB/s | 18 | 1.8 TB/s | B100/B200 |

### 2.2 NVSwitch

```
NVSwitch: 实现节点内 GPU 全互联

无 NVSwitch (A100 之前):
  ┌───┐   ┌───┐
  │GPU│───│GPU│
  │ 0 │   │ 1 │
  └─┬─┘   └─┬─┘
    │       │
  ┌─┴─┐   ┌─┴─┐
  │GPU│───│GPU│
  │ 2 │   │ 3 │
  └───┘   └───┘
  
  问题: GPU 0 和 GPU 3 通信需要经过 GPU 1 或 GPU 2
  → 非直连 GPU 间带宽减半

有 NVSwitch (DGX H100):
  ┌───┐ ┌───┐ ┌───┐ ┌───┐ ┌───┐ ┌───┐ ┌───┐ ┌───┐
  │GPU│ │GPU│ │GPU│ │GPU│ │GPU│ │GPU│ │GPU│ │GPU│
  │ 0 │ │ 1 │ │ 2 │ │ 3 │ │ 4 │ │ 5 │ │ 6 │ │ 7 │
  └─┬─┘ └─┬─┘ └─┬─┘ └─┬─┘ └─┬─┘ └─┬─┘ └─┬─┘ └─┬─┘
    │     │     │     │     │     │     │     │
    └─────┴─────┴──┬──┴─────┴─────┴─────┴─────┘
                   │
            ┌──────┴──────┐
            │  NVSwitch   │  (4 个 NVSwitch 芯片)
            │  全互联      │
            └─────────────┘
  
  → 任意两个 GPU 间带宽相同 (900 GB/s)
  → 所有 GPU 可以同时全速通信
```

### 2.3 NVSwitch 代际

| 代际 | 总交换带宽 | GPU 数 | 代表系统 |
|------|-----------|--------|---------|
| **NVSwitch 1.0** | 2.4 TB/s | 8 (A100) | DGX A100 |
| **NVSwitch 2.0** | 3.6 TB/s | 8 (H100) | DGX H100 |
| **NVSwitch 3.0** | 7.2 TB/s | 8 (B200) | DGX B200 |
| **NVSwitch 4.0** | 14.4 TB/s | 72 (GB200) | GB200 NVL72 |

### 2.4 GB200 NVL72

```
GB200 NVL72: 72 GPU 全互联

架构:
  ┌─────────────────────────────────────────────┐
  │              GB200 NVL72 Rack                │
  │                                             │
  │  ┌─────────────────────────────────────┐    │
  │  │  18 × Compute Tray                  │    │
  │  │  每个 Tray: 2 × GB200 (4 GPU)       │    │
  │  │  总计: 72 GPU                       │    │
  │  └─────────────────────────────────────┘    │
  │                    │                        │
  │  ┌─────────────────────────────────────┐    │
  │  │  9 × NVSwitch Tray                  │    │
  │  │  每个 Tray: 2 × NVSwitch 4.0        │    │
  │  │  总计: 18 NVSwitch                  │    │
  │  │  全双工带宽: 14.4 TB/s              │    │
  │  └─────────────────────────────────────┘    │
  │                                             │
  └─────────────────────────────────────────────┘

意义:
  - 72 GPU 可以作为一个巨大的"单 GPU"使用
  - TP=72 成为可能
  - 极大简化了并行策略设计
```

---

## 跨节点互联：InfiniBand 与 RoCE

### 3.1 InfiniBand

```
InfiniBand (IB): 高性能计算互联标准

代际演进:
  SDR (2005):   10 Gb/s
  DDR (2007):   20 Gb/s
  QDR (2009):   40 Gb/s
  FDR (2011):   56 Gb/s
  EDR (2014):  100 Gb/s
  HDR (2018):  200 Gb/s
  NDR (2021):  400 Gb/s  ← 当前主流
  XDR (2024):  800 Gb/s  ← 最新

关键特性:
  1. RDMA (Remote Direct Memory Access):
     - 直接访问远程内存，绕过 CPU
     - 极低延迟 (~1μs)
     - 零拷贝

  2. 可靠传输:
     - 链路层流控 (Credit-based)
     - 无丢包 (与以太网不同)
     - 端到端重传

  3. 自适应路由:
     - 动态选择路径
     - 负载均衡
     - 拥塞控制
```

### 3.2 RoCE (RDMA over Converged Ethernet)

```
RoCE v2: 在以太网上实现 RDMA

与 InfiniBand 对比:

| 维度 | InfiniBand | RoCE v2 |
|------|-----------|---------|
| **物理层** | IB 专用 | 以太网 |
| **带宽** | 400 Gb/s (NDR) | 400 Gb/s |
| **延迟** | ~1μs | ~2-3μs |
| **丢包** | 无 (链路层流控) | 有 (需要 PFC/ECN) |
| **成本** | 高 (专用交换机) | 中 (通用交换机) |
| **生态** | NVIDIA/Mellanox | 多厂商 |
| **规模** | 数千节点 | 数万节点 |
| **适用** | 高性能训练 | 云原生推理 |

RoCE 的挑战:
  - PFC (Priority Flow Control): 防止丢包但可能引起拥塞扩散
  - ECN (Explicit Congestion Notification): 拥塞通知
  - DCQCN: 数据中心拥塞控制算法
```

### 3.3 NVIDIA Spectrum-X

```
Spectrum-X: NVIDIA 的以太网 AI 网络方案

组成:
  - Spectrum-4 交换机 (400G, 51.2T)
  - BlueField-3 DPU
  - 自适应路由 + 拥塞控制

特点:
  - 以太网生态 + InfiniBand 级性能
  - 自适应路由: 动态选择最优路径
  - 端到端遥测: 实时监控网络状态
  - 与 InfiniBand 互补 (非替代)
```

---

## 网内计算：SHARP

### 4.1 SHARP 原理

```
SHARP (Scalable Hierarchical Aggregation and Reduction Protocol):

传统 All-Reduce:
  GPU 0 ──┐
  GPU 1 ──┼──▶ Switch ──▶ GPU 0 (接收所有数据)
  GPU 2 ──┤              ──▶ GPU 1 (接收所有数据)
  GPU 3 ──┘              ──▶ GPU 2 (接收所有数据)
                          ──▶ GPU 3 (接收所有数据)
  
  问题: 交换机只是转发数据，不参与计算
       → 数据需要多次经过交换机

SHARP All-Reduce:
  GPU 0 ──┐
  GPU 1 ──┼──▶ Switch (在交换机内完成归约!) ──▶ GPU 0
  GPU 2 ──┤                                  ──▶ GPU 1
  GPU 3 ──┘                                  ──▶ GPU 2
                                             ──▶ GPU 3
  
  → 交换机完成归约计算
  → 数据只需经过交换机一次
  → 带宽需求减半!
```

### 4.2 SHARP 效果

```
SHARP 性能提升:

  All-Reduce 128MB, 8 GPU:
    无 SHARP: ~200μs
    有 SHARP: ~120μs (1.7× 加速)
  
  大规模 (1024 GPU):
    无 SHARP: ~2ms
    有 SHARP: ~0.8ms (2.5× 加速)

SHARP 的限制:
  - 只支持特定归约操作 (SUM, MIN, MAX, ...)
  - 需要 InfiniBand 交换机支持
  - 数据量不能超过交换机内存
```

---

## 拓扑感知调度

### 5.1 为什么需要拓扑感知

```
问题: GPU 间通信带宽差异巨大

  NVLink 域内: 900 GB/s
  同机架 IB:   400 GB/s
  跨机架 IB:   400 GB/s (但延迟更高)
  
  → 带宽差异: 2-10×

如果调度器不知道拓扑:
  TP 组可能跨机架 → 通信瓶颈
  PP 组可能跨机架 → bubble 增大
```

### 5.2 拓扑感知策略

```
拓扑感知调度原则:

1. TP 组: 必须在 NVLink 域内 (同一节点)
   → 通信量最大，需要最高带宽

2. PP 组: 优先同机架
   → 通信量中等，延迟敏感

3. DP 组: 可以跨机架
   → 通信量相对小，对延迟不敏感

4. EP 组: 优先同机架
   → All-to-All 通信量大

实现:
  - Kubernetes + GPU Topology Manager
  - Slurm + Topology Plugin
  - 自定义调度器 (如 NVIDIA Run:ai)
```

### 5.3 拓扑类型

```
常见 GPU 集群拓扑:

Fat-Tree:
  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐
  │ Spine│ │ Spine│ │ Spine│ │ Spine│  (核心层)
  └──┬───┘ └──┬───┘ └──┬───┘ └──┬───┘
     │        │        │        │
  ┌──┴────────┴──┐ ┌──┴────────┴──┐
  │    Leaf      │ │    Leaf      │  (接入层)
  └──┬───┬───┬───┘ └──┬───┬───┬───┘
     │   │   │        │   │   │
  ┌──┴─┐┌──┴─┐┌──┴─┐ ┌──┴─┐┌──┴─┐┌──┴─┐
  │Node││Node││Node│ │Node││Node││Node│
  └────┘└────┘└────┘ └────┘└────┘└────┘

DragonFly+:
  分组全互联 + 组间部分互联
  → 更好的可扩展性
  → 更低的直径
```

---

## 集群网络设计

### 6.1 带宽需求计算

```
训练带宽需求:

  模型: 175B 参数 (GPT-3 规模)
  DP: 1024 GPU
  
  ZeRO-3 通信量 per step:
    参数 All-Gather: 175B × 2 bytes = 350GB
    梯度 Reduce-Scatter: 350GB
    总计: ~700GB per step
  
  目标 step time: 1s
  → 需要 700GB/s 有效带宽
  
  考虑效率 (80%):
  → 需要 ~875GB/s 网络带宽

推理带宽需求 (Disaggregated):
  KV Cache 传输: 
    每层: b × s × d × 2 bytes
    Llama-3-70B: 1 × 4096 × 8192 × 2 = 64MB per layer
    80 层: ~5GB per request
    
  100 QPS → 500GB/s KV Cache 传输带宽
```

### 6.2 网络设计原则

```
AI 集群网络设计:

1. 无阻塞 (Non-blocking):
   - 任意两个 GPU 间可达线速
   - Fat-Tree 或 DragonFly+ 拓扑

2. 低延迟:
   - 跳数最小化
   - 直连优先

3. 高带宽利用率:
   - 负载均衡 (自适应路由)
   - 拥塞控制

4. 可扩展:
   - 支持数千到数万 GPU
   - 增量扩展

5. 容错:
   - 多路径
   - 快速故障恢复
```

---

## 故障与可靠性

### 7.1 故障模式

```
GPU 集群常见故障:

  硬件故障:
  - GPU 故障 (ECC error, 过热)
  - NVLink 故障 (链路降级)
  - 网卡故障 (IB HCA 故障)
  - 交换机故障 (端口故障)
  
  软件故障:
  - NCCL timeout
  - CUDA error
  - OOM (Out of Memory)
  
  网络故障:
  - 链路抖动 (link flap)
  - 拥塞扩散
  - 路由环路
```

### 7.2 故障恢复

```
训练故障恢复:

1. Checkpoint:
   - 每 N 步保存一次 (如 N=1000)
   - 保存: 模型参数 + 优化器状态 + 数据迭代器位置
   - 175B 模型 checkpoint: ~350GB (FP16)

2. 故障检测:
   - 心跳检测 (NCCL heartbeat)
   - GPU 健康检查 (DCGM)
   - 网络健康检查

3. 恢复流程:
   a. 检测故障
   b. 停止所有 GPU
   c. 加载最近的 checkpoint
   d. 跳过故障 GPU (如果有备用)
   e. 恢复训练

4. 弹性训练:
   - 支持动态增减 GPU
   - TorchElastic / DeepSpeed Elasticity
```

---

## 最新互联技术

### 8.1 Ultra Ethernet Consortium (UEC)

```
UEC: 开放标准的 AI 网络

目标:
  - 替代 InfiniBand 的开放方案
  - 800G/1.6T 以太网
  - 优化 AI/HPC 工作负载

成员: AMD, Intel, Meta, Microsoft, Broadcom, Cisco, HPE, ...

关键特性:
  - 多路径 (packet spraying)
  - 灵活排序 (不保证包顺序)
  - 网内计算
  - 拥塞控制优化
```

### 8.2 PCIe 6.0 与 CXL 3.0

```
PCIe 6.0 (2025):
  - 带宽: 128 GB/s (×16) (PCIe 5.0 的 2×)
  - PAM4 调制
  - FEC (Forward Error Correction)
  - 对 GPU-GPU 通信影响有限 (NVLink 仍是主力)

CXL 3.0 (Compute Express Link):
  - 基于 PCIe 6.0
  - 内存池化: 多 GPU 共享内存池
  - 内存共享: GPU 直接访问其他 GPU 的显存
  - 对 LLM 推理的潜在影响:
    → 扩展 GPU 显存 (通过 CXL 内存池)
    → 减少模型分片需求
```

### 8.3 光互联

```
光互联 (Optical Interconnect):

趋势: 铜缆 → 光互联

铜缆限制:
  - 距离: < 3m (高速信号衰减)
  - 密度: 物理体积大
  - 功耗: 随距离增加

光互联优势:
  - 距离: 数百米
  - 带宽: Tb/s 级
  - 功耗: 与距离无关

应用:
  - 跨机架互联
  - 机架内互联 (co-packaged optics)
  - 芯片间互联 (硅光子)

代表:
  - NVIDIA: 与 Ayar Labs 合作
  - Intel: 硅光子技术
  - Ayar Labs: TeraPHY 光 I/O
```

---

> **关键原则**：
> 1. **NVLink 是 TP 的基础**：没有 NVLink 就没有高效的张量并行
> 2. **InfiniBand 是训练的标准**：低延迟、无丢包、RDMA
> 3. **RoCE 是推理的选择**：成本低、生态好、云原生
> 4. **SHARP 是免费的性能提升**：网内计算减少一半通信量
> 5. **拓扑感知是必须的**：不知道拓扑的调度 = 浪费 50% 带宽

---

## 参考资料

- [NVLink & NVSwitch](https://www.nvidia.com/en-us/data-center/nvlink/) — NVIDIA 互联技术白皮书
- [InfiniBand](https://www.infinibandta.org/) — IBTA 规格文档
- [SHARP](https://docs.nvidia.com/networking/display/SHARPv2) — 网内归约协议
