# LLM 分布式训练：并行策略与 ZeRO

> **核心命题**：单个 GPU 的显存和算力永远不够。分布式训练的本质是在多个 GPU 之间分配模型、数据和计算，同时最小化通信开销。理解并行策略 = 理解 LLM 训练的物理约束。

### 关键术语

| 缩写 | 全称 | 含义 |
|------|------|------|
| DP | Data Parallelism | 数据并行，每个 GPU 持有完整模型副本，处理不同数据 |
| TP | Tensor Parallelism | 张量并行，将单层权重切分到多个 GPU |
| PP | Pipeline Parallelism | 流水线并行，将模型层切分到不同 GPU 流水线执行 |
| SP | Sequence Parallelism | 序列并行，将序列长度维度切分到多个 GPU |
| EP | Expert Parallelism | 专家并行，将 MoE 专家分布到不同 GPU |
| ZeRO | Zero Redundancy Optimizer | 零冗余优化器，分片优化器状态/梯度/参数以节省显存 |
| NCCL | NVIDIA Collective Communications Library | NVIDIA 集合通信库，GPU 间 All-Reduce 等通信的实现 |
| FSDP | Fully Sharded Data Parallel | PyTorch 原生的分片数据并行，ShardingStrategy 支持 ZeRO-2 (SHARD_GRAD_OP) 到 ZeRO-3 (FULL_SHARD) 多级配置 |

### 前置知识

| 需要了解 | 参考文档 |
|----------|----------|
| Transformer 结构与训练算法 | [02-Transformer完整结构](./02-Transformer完整结构与训练算法.md) |
| MoE 架构与专家并行 | [04-LLM MoE架构](./04-LLM%20MoE架构：路由、负载均衡与专家并行.md) |
| GPU 集群互联技术 | [deep-dive: GPU集群互联](./deep-dive/10-GPU集群互联：NVLink到InfiniBand.md) |

## 目录

1. [分布式训练全景](#分布式训练全景)
2. [数据并行 (DP/DDP)](#数据并行-dpddp)
3. [ZeRO 系列深入](#zero-系列深入)
4. [张量并行 (TP)](#张量并行-tp)
5. [流水线并行 (PP)](#流水线并行-pp)
6. [序列并行 (SP)](#序列并行-sp)
7. [专家并行 (EP)](#专家并行-ep)
8. [3D/4D 并行：组合策略](#3d4d-并行组合策略)
9. [通信原语与 NCCL](#通信原语与-nccl)
10. [通信-计算 Overlap](#通信-计算-overlap)
11. [自动并行搜索](#自动并行搜索)
12. [实践框架对比](#实践框架对比)

---

## 分布式训练全景

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        分布式并行策略全景                                 │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  数据并行 (DP)                                                          │
│  ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐                                      │
│  │GPU 0│ │GPU 1│ │GPU 2│ │GPU 3│  每个 GPU 有完整模型副本               │
│  │D0   │ │D1   │ │D2   │ │D3   │  处理不同数据 → All-Reduce 梯度        │
│  └─────┘ └─────┘ └─────┘ └─────┘                                      │
│                                                                         │
│  张量并行 (TP)                                                          │
│  ┌───────────────┐        将单层权重切分到多个 GPU                      │
│  │ GPU 0 │ GPU 1 │        每个 GPU 计算部分结果                         │
│  │  W/2  │  W/2  │        → All-Reduce / All-Gather 通信               │
│  └───────────────┘                                                      │
│                                                                         │
│  流水线并行 (PP)                                                        │
│  GPU 0    GPU 1    GPU 2    GPU 3                                      │
│  ┌───┐   ┌───┐   ┌───┐   ┌───┐                                        │
│  │L0 │──▶│L1 │──▶│L2 │──▶│L3 │  将层切分到不同 GPU                     │
│  │L4 │◀──│L5 │◀──│L6 │◀──│L7 │  按层顺序流水线执行                      │
│  └───┘   └───┘   └───┘   └───┘                                        │
│                                                                         │
│  序列并行 (SP)                                                          │
│  ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐                                      │
│  │S/4  │ │S/4  │ │S/4  │ │S/4  │  将序列维度切分到多个 GPU             │
│  └─────┘ └─────┘ └─────┘ └─────┘  Attention 需要 All-to-All            │
│                                                                         │
│  专家并行 (EP)                                                          │
│  ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐                                      │
│  │E0,E1│ │E2,E3│ │E4,E5│ │E6,E7│  将 MoE 专家分布到不同 GPU            │
│  └─────┘ └─────┘ └─────┘ └─────┘  Token 路由 → All-to-All              │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 数据并行 (DP/DDP)

### 2.1 原理

数据并行:
  每个 GPU 持有完整的模型副本
  每个 GPU 处理不同的 mini-batch 数据
  梯度通过 All-Reduce 同步

前向: $y_i = \text{Model}(x_i)$           # 第 $i$ 个 GPU 的输出
反向: $g_i = \frac{\partial L_i}{\partial \theta}$              # 局部梯度
同步: $g = \frac{1}{N} \sum_{i=1}^{N} g_i$          # All-Reduce 平均梯度
更新: $\theta = \theta - \eta \cdot g$              # 各 GPU 独立更新

### 2.2 DP vs DDP

| 特性 | DP (DataParallel) | DDP (DistributedDataParallel) |
|------|-------------------|-------------------------------|
| **通信方式** | PS 架构 (Parameter Server) | Ring All-Reduce |
| **通信时机** | 每次 forward/backward 后 | 只在 backward 后 |
| **梯度同步** | 主卡收集 → 广播 | 所有卡同时参与 All-Reduce |
| **效率** | 低 (主卡瓶颈) | 高 (带宽利用率高) |
| **适用规模** | 单机多卡 | 多机多卡 |

### 2.3 DP 的显存问题

DP 的显存占用 (每个 GPU):
  - 模型参数: $\Phi$ bytes (模型参数量，以字节计)
  - 梯度: $\Phi$ bytes
  - 优化器状态 (AdamW): $2\Phi$ bytes (m + v)
  - 激活值: $A$ bytes (取决于 batch_size × seq_len)
  
  总计: $4\Phi + A$

问题: 每个 GPU 都需要完整的 $4\Phi + A$
  → 对于 70B 模型 ($\Phi$=140GB in FP16):
    $4\Phi$ = 560GB → 远超单卡显存 (H100 80GB)
  → DP 无法单独训练大模型!

---

## ZeRO 系列深入

### 3.1 ZeRO 的三个阶段

ZeRO (Zero Redundancy Optimizer) 核心思想:
  去除 DP 中的数据并行冗余，将优化器状态、梯度、参数分布到各 GPU

ZeRO-1: 分片优化器状态
  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐
  │GPU 0 │ │GPU 1 │ │GPU 2 │ │GPU 3 │
  │m0,v0 │ │m1,v1 │ │m2,v2 │ │m3,v3 │  每个 GPU 只存 1/N 的优化器状态
  │full θ│ │full θ│ │full θ│ │full θ│
  └──────┘ └──────┘ └──────┘ └──────┘
  
  显存节省: $2\Phi \to 2\Phi/N$ (优化器状态)
  通信: All-Reduce 梯度 (与 DP 相同)

ZeRO-2: 分片优化器状态 + 梯度
  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐
  │GPU 0 │ │GPU 1 │ │GPU 2 │ │GPU 3 │
  │m0,v0 │ │m1,v1 │ │m2,v2 │ │m3,v3 │
  │g0    │ │g1    │ │g2    │ │g3    │  每个 GPU 只存 1/N 的梯度
  │full θ│ │full θ│ │full θ│ │full θ│
  └──────┘ └──────┘ └──────┘ └──────┘
  
  显存节省: $3\Phi \to 3\Phi/N$ (优化器状态 + 梯度)
  通信: Reduce-Scatter 梯度 (代替 All-Reduce)

ZeRO-3: 分片优化器状态 + 梯度 + 参数
  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐
  │GPU 0 │ │GPU 1 │ │GPU 2 │ │GPU 3 │
  │m0,v0 │ │m1,v1 │ │m2,v2 │ │m3,v3 │
  │g0    │ │g1    │ │g2    │ │g3    │
  │θ0    │ │θ1    │ │θ2    │ │θ3    │  每个 GPU 只存 1/N 的参数!
  └──────┘ └──────┘ └──────┘ └──────┘
  
  显存节省: $4\Phi \to 4\Phi/N$ (全部)
  通信: 前向需要 All-Gather 参数, 反向需要 Reduce-Scatter 梯度

### 3.2 ZeRO 显存分析

70B 模型 ($\Phi$=140GB FP16), $N$=64 GPU:

            DP      ZeRO-1   ZeRO-2   ZeRO-3
参数:       140GB   140GB    140GB    2.2GB
梯度:       140GB   140GB    2.2GB    2.2GB
优化器:     280GB   4.4GB    4.4GB    4.4GB
─────────────────────────────────────────────
总计:       560GB   284GB    146.6GB  8.8GB

→ ZeRO-3 将 560GB 压缩到 8.8GB!
→ 但通信量增加了 (前向 All-Gather 参数)

### 3.3 ZeRO-3 通信分析

ZeRO-3 每次 forward 的通信:

1. All-Gather 参数: 
   通信量 = $\Phi \cdot \frac{N-1}{N} \approx \Phi$ (大 $N$ 时)
   
2. 计算 forward

3. All-Gather 参数 (反向):
   通信量 $\approx \Phi$

4. Reduce-Scatter 梯度:
   通信量 $\approx \Phi$

总通信量: $\approx 3\Phi$ per step

对于 70B 模型: $3 \times 140\text{GB} = 420\text{GB}$ per step
  → 需要高带宽互联 (InfiniBand 400GB/s+)

### 3.4 ZeRO-Offload 和 ZeRO-Infinity

ZeRO-Offload: 将优化器状态和梯度 offload 到 CPU 内存
  GPU: 参数 + 激活
  CPU: 优化器状态 + 梯度
  → 单卡可训练 13B 模型

ZeRO-Infinity: Offload 到 NVMe SSD
  GPU: 参数 + 激活
  CPU: 优化器状态
  NVMe: 部分参数
  → 单卡可训练 1T 参数模型 (极慢)

---

## 张量并行 (TP)

### 4.1 原理

张量并行: 将单个层的权重矩阵切分到多个 GPU

列并行 (Column Parallel):
  将 $W \in \mathbb{R}^{d \times 4d}$ 按列切分:
  GPU 0: $W[:, {:}2d]$, GPU 1: $W[:, 2d{:}]$
  
  前向: 
    $y_0 = X \times W_0$  (GPU 0)
    $y_1 = X \times W_1$  (GPU 1)
    $y = [y_0, y_1]$  (All-Gather)
  
  通信: All-Gather (前向), Reduce-Scatter (反向)

行并行 (Row Parallel):
  将 $W \in \mathbb{R}^{4d \times d}$ 按行切分:
  GPU 0: $W[{:}2d, {:}]$, GPU 1: $W[2d{:}, {:}]$
  
  前向:
    $X$ 先按列切分 (来自上一个列并行)
    $y_0 = X_0 \times W_0$  (GPU 0)
    $y_1 = X_1 \times W_1$  (GPU 1)
    $y = y_0 + y_1$    (All-Reduce)
  
  通信: All-Reduce (前向), Identity (反向)

### 4.2 Megatron-LM 的 TP 方案

Megatron-LM 的 Transformer 层 TP 切分:

Attention:
  QKV 投影: 列并行 (按 head 切)
    GPU 0: Q_0, K_0, V_0 (heads 0..h/2-1)
    GPU 1: Q_1, K_1, V_1 (heads h/2..h-1)
  
  Attention 计算: 各 GPU 独立 (各自的 head)
  
  O 投影: 行并行
    GPU 0: O_0 = Attn_0 × W_O[:d_h×h/2, :]
    GPU 1: O_1 = Attn_1 × W_O[d_h×h/2:, :]
    All-Reduce: O = O_0 + O_1

FFN:
  h→4h: 列并行
    GPU 0: W_1[:, :2h], GPU 1: W_1[:, 2h:]
  
  4h→h: 行并行
    GPU 0: W_2[:2h, :], GPU 1: W_2[2h:, :]
    All-Reduce

每层通信: 2 × All-Reduce (前向) + 2 × All-Reduce (反向)

### 4.3 TP 的通信瓶颈

TP 通信量分析 (每层, 每个 token):

设 $b$ 为 batch size，$s$ 为序列长度，$d$ 为隐藏维度（d_model），$N$ 为 TP 并行度：

每层共 4 次 All-Reduce（Attention O 投影 + FFN 输出，前向/反向各一次）
每次 All-Reduce 操作的张量大小为 $bsd$ 元素 = $2bsd$ bytes (FP16)
All-Reduce 通信量精确公式 = $\frac{2(N-1)}{N} \times (\text{张量大小})$（见 §9.1 推导）

Attention:
  f (All-Reduce O): $\frac{4(N-1)}{N} bsd$ bytes
  b (All-Reduce grad): $\frac{4(N-1)}{N} bsd$ bytes

FFN:
  f (All-Reduce): $\frac{4(N-1)}{N} bsd$ bytes
  b (All-Reduce grad): $\frac{4(N-1)}{N} bsd$ bytes

总计: $\frac{16(N-1)}{N} bsd$ bytes per layer per step

常见 TP 度的通信量（以 $bsd$ 为单位）：
  TP=2: $\frac{N-1}{N} = 0.5 \to 8bsd$ bytes
  TP=4: $\frac{N-1}{N} = 0.75 \to 12bsd$ bytes
  TP=8: $\frac{N-1}{N} \approx 0.875 \to 14bsd$ bytes
  大 N 近似: $\frac{16(N-1)}{N} \approx 16 \to 16bsd$ bytes

对于 Llama-3-8B ($d$=4096, $b$=1, $s$=4096, TP=2):
  $$8 \times 1 \times 4096 \times 4096 = 128\text{MB} \text{ per layer}$$
  × 32 layers ≈ 4.1GB per step
  TP=4 时: $12bsd$ = 192MB per layer，≈ 6.1GB per step

→ TP 通信量极大，必须在 NVLink 域内 (同一节点)
→ 跨节点 TP 不可行 (带宽不够)

---

## 流水线并行 (PP)

### 5.1 原理

流水线并行: 将模型层切分到不同 GPU，按顺序流水线执行

GPU 0: Layers 0-7
GPU 1: Layers 8-15
GPU 2: Layers 16-23
GPU 3: Layers 24-31

前向: GPU 0 → GPU 1 → GPU 2 → GPU 3
反向: GPU 3 → GPU 2 → GPU 1 → GPU 0

通信: 只在切分边界传输激活值 (前向) 和梯度 (反向)
  通信量 = $b \times s \times d \times 2\text{ bytes (FP16)}$
  → 远小于 TP!

### 5.2 GPipe vs 1F1B

GPipe (朴素流水线):
  ┌────────────────────────────────────┐
  │ F0 │     │     │     │             │  GPU 0 空闲等待
  │     │ F1  │     │     │             │
  │     │     │ F2  │     │             │
  │     │     │     │ F3  │             │
  │     │     │     │     │ B3  │       │
  │     │     │     │ B2  │     │       │
  │     │     │ B1  │     │     │       │
  │     │ B0  │     │     │     │       │
  └────────────────────────────────────┘
  
  Bubble 比例: $\frac{P-1}{P+M-1}$  ($P$ 是 pipeline stage 数, $M$ 是 micro-batch 数)
  → M 越大，bubble 越小

1F1B (One-Forward-One-Backward, PipeDream):
  ┌────────────────────────────────────┐
  │ F0 │ F1  │ F2  │ F3  │             │
  │     │ F0  │ F1  │ F2  │ F3  │       │
  │     │     │ F0  │ F1  │ F2  │ F3  │
  │     │     │     │ F0  │ F1  │ F2  │
  │     │     │     │ B0  │ B1  │ B2  │
  │     │     │ B0  │ B1  │ B2  │ B3  │
  │     │ B0  │ B1  │ B2  │ B3  │     │
  │ B0  │ B1  │ B2  │ B3  │     │     │
  └────────────────────────────────────┘
  
  → 交替执行前向和反向，减少空闲
  → Bubble 比例: $\frac{P-1}{P+M-1}$ (与 GPipe 相同)
  → 但显存占用更均衡

### 5.3 PP 的显存分析

PP 显存占用 (每个 GPU):

1. 模型参数: $\Phi/P$ (只存 $P$ 分之一的层)
2. 优化器状态: $2\Phi/P$
3. 梯度: $\Phi/P$
4. 激活值: 取决于 micro-batch 数和 1F1B 调度

激活值显存 (1F1B):
  峰值激活 = $(P + M) \times A_{\text{per\_microbatch}}$
  
  其中 $A_{\text{per\_microbatch}}$ 是一个 micro-batch 的激活值

→ PP 的显存优势: 参数/优化器/梯度都除以 $P$
→ 但激活值可能较大 (需要存多个 micro-batch 的激活)

---

## 序列并行 (SP)

### 6.1 为什么需要 SP

问题: 长序列训练时，即使 TP+PP+ZeRO，激活值显存仍然不够

例如: Llama-3-8B, seq_len=128K, TP=4
  Attention 激活: $b \times s \times h \times d_h = 1 \times 128\text{K} \times 32 \times 128 = 524\text{M floats}$，其中 $d_h$ 是 head_dim
  → 2GB (FP32) → 可接受
  
  但 Dropout mask, LayerNorm 中间结果等累积:
  → 总激活值可能 > 40GB
  → 超出单卡显存!

### 6.2 SP-Ulysses vs SP-Ring

SP-Ulysses (DeepSpeed Ulysses):
  将序列按 head 维度切分:
  
  GPU 0: heads 0..h/4-1, 完整序列
  GPU 1: heads h/4..h/2-1, 完整序列
  ...
  
  Attention 前: All-to-All → 每个 GPU 有完整 head 的部分序列
  Attention 后: All-to-All → 恢复
  
  通信: 2 × All-to-All per layer
  通信量: $2 \times b \times s \times d \times 2\text{ bytes}$

SP-Ring (Ring Attention):
  将序列按位置切分:
  
  GPU 0: tokens 0..s/4-1
  GPU 1: tokens s/4..s/2-1
  ...
  
  Attention: 每个 GPU 计算自己的 Q 与所有 GPU 的 K, V
  → 通过 Ring 通信传递 K, V chunks
  
  通信: 2 × P2P send/recv per layer
  通信量: $2 \times b \times s \times d \times 2\text{ bytes}$ (与 Ulysses 相同)

All-Gather CP (Llama 3 采用):
  将序列按位置切分（同 SP-Ring），但使用 All-Gather 聚合 K, V：
  
  GPU 0: tokens 0..s/4-1
  GPU 1: tokens s/4..s/2-1
  ...
  
  Attention 前: All-Gather K, V → 每个 GPU 获得完整 K, V
  Attention 计算: 各 GPU 用本地 Q 与完整 K, V 计算
  Attention 后: 无需额外通信（输出保持切分）
  
  通信: 2 × All-Gather (K, V) per layer
  通信量: $2 \times \frac{N-1}{N} \times 2 \times b \times s \times d \times 2\text{ bytes}$（K+V 各一份）
  
  → 实现比 Ring/Ulysses 更简单，通信量随 $N$ 增大趋近 All-to-All
  → Llama 3 在 4D 并行 (TP × CP × PP × DP) 中以此作为 CP 维度

### 6.3 SP 对比

| 维度 | SP-Ulysses | SP-Ring (Ring Attention) | All-Gather CP (Llama 3) |
|------|-----------|--------------------------|--------------------------|
| **切分维度** | Head 维度 | 序列位置 | 序列位置 |
| **通信模式** | All-to-All | P2P Ring | All-Gather (K, V) |
| **通信量** | $2bsd$ bytes | $2bsd$ bytes | $\frac{4(N-1)}{N} bsd$ bytes |
| **负载均衡** | 好 (head 均匀) | 好 (序列均匀) | 好 (序列均匀) |
| **GQA 兼容** | 需要 $h_{kv}$ 整除 SP | 天然兼容 | 天然兼容 |
| **实现复杂度** | 中 | 高 (需要异步通信) | 低 |
| **代表实现** | DeepSpeed Ulysses | Ring Attention (Liu et al.) | Meta Llama 3 (4D 并行) |

---

## 专家并行 (EP)

### 7.1 MoE 的并行需求

MoE 层结构:
  Router: 选择 top-k 专家
  Experts: 每个 token 只激活 k 个专家 (k << E)
  
  例如 Mixtral 8×7B: E=8, k=2
  DeepSeek-V2: E=160, k=6 (共享专家 + 路由专家)

专家并行:
  将专家分布到不同 GPU
  Token 通过 All-to-All 路由到对应 GPU

### 7.2 EP 通信分析

EP 通信 (All-to-All):

1. Token Dispatch (前向):
   每个 GPU 将 token 发送到持有对应专家的 GPU
   通信量: b × s × d × 2 bytes (所有 token 的 hidden states)

2. Expert Computation:
   各 GPU 独立计算

3. Token Combine (前向):
   将计算结果发送回原 GPU
   通信量: b × s × d × 2 bytes

总通信: 2 × b × s × d × 2 bytes per MoE layer

优化:
  - 容量因子 (Capacity Factor): 限制每个专家处理的 token 数
  - 辅助损失 (Load Balancing Loss): 鼓励 token 均匀分布
  - DeepSeek-V2 的 Shared Expert: 减少路由 token 数

---

## 3D/4D 并行：组合策略

### 8.1 为什么需要组合

单一并行策略的局限:

DP: 显存放不下大模型 (需要完整模型副本)
TP: 通信量大，不能跨节点 (限制在 NVLink 域内)
PP: 有 bubble，GPU 利用率不高
ZeRO-3: 通信量大，大规模时成为瓶颈

→ 组合多种策略，取长补短

### 8.2 典型 3D/4D 并行配置

3D 并行 = DP × TP × PP（基础组合）
4D 并行 = DP × TP × PP × CP（Llama 3 实际采用，增加 Context Parallelism 维度）

GPU 拓扑:
  ┌─────────────────────────────────────────┐
  │  节点 0 (8 GPU, NVLink)                  │
  │  ┌───┐ ┌───┐ ┌───┐ ┌───┐               │
  │  │TP0│ │TP1│ │TP2│ │TP3│  TP=4          │
  │  └───┘ └───┘ └───┘ └───┘               │
  │  ┌───┐ ┌───┐ ┌───┐ ┌───┐               │
  │  │TP0│ │TP1│ │TP2│ │TP3│  PP=2          │
  │  └───┘ └───┘ └───┘ └───┘               │
  └─────────────────────────────────────────┘
  
  节点 1 (同样配置) → DP=2
  
  总 GPU: 2 × 2 × 4 = 16

配置公式:
  $N_{\text{gpu}} = DP \times TP \times PP$（3D）
  $N_{\text{gpu}} = DP \times TP \times PP \times CP$（4D，Llama 3）

  TP: 限制在 NVLink 域内 (通常 ≤ 8)
  CP: 可与 TP 协同，限制在同节点或同 NVSwitch 域内
  PP: 可以跨节点 (通信量小)
  DP: 可以跨节点 (ZeRO 优化)

### 8.3 配置搜索

给定模型大小和 GPU 数量，搜索最优 (DP, TP, PP):

约束:
  1. TP ≤ 8 (NVLink 域限制)
  2. PP ≤ num_layers (每层至少一个 GPU)
  3. DP × TP × PP = N_gpu
  4. 显存: 每个 GPU 的显存占用 < GPU 显存

目标: 最大化吞吐 (tokens/s)

搜索空间:

```python
for tp in [1, 2, 4, 8]:
    for pp in [1, 2, ..., num_layers]:
        dp = N_gpu / (tp × pp)
        if dp is integer and memory_ok(tp, pp, dp):
            throughput = estimate_throughput(tp, pp, dp)
            best = max(best, throughput)
```

---

## 通信原语与 NCCL

### 9.1 核心通信原语

| 原语 | 操作 | 通信量 | 用途 |
|------|------|--------|------|
| **All-Reduce** | $\sum x_i \to$ 所有 GPU | $\frac{2(N-1)}{N} \cdot \text{data}$ | DP 梯度同步, TP 输出合并 |
| **All-Gather** | 收集所有 GPU 的数据 | $\frac{N-1}{N} \cdot \text{data}$ | ZeRO-3 参数收集 |
| **Reduce-Scatter** | $\sum x_i \to$ 每个 GPU 一部分 | $\frac{N-1}{N} \cdot \text{data}$ | ZeRO-2/3 梯度同步 |
| **All-to-All** | 每个 GPU 向每个 GPU 发送 | data | SP, EP |
| **Broadcast** | GPU 0 → 所有 GPU | data | 参数初始化 |
| **P2P Send/Recv** | GPU i → GPU j | data | PP 边界传输 |

### 9.2 Ring All-Reduce

Ring All-Reduce (NCCL 默认):

N 个 GPU 排成环, 数据分成 N 份

阶段 1: Reduce-Scatter (N-1 步)
  Step 1: GPU i 发送 chunk (i-1)%N 给 GPU (i+1)%N
          接收后累加
  Step 2: 发送下一个 chunk
  ...
  Step N-1: 完成 → 每个 GPU 有 1/N 的完整归约结果

阶段 2: All-Gather (N-1 步)
  Step 1: GPU i 发送自己的归约结果给 GPU (i+1)%N
  Step 2: 转发收到的数据
  ...
  Step N-1: 完成 → 所有 GPU 有完整结果

$$T_{\text{allreduce}} = \frac{2(N-1) \cdot (\text{data}/N)}{\text{bandwidth}} = \frac{2(N-1)}{N} \cdot \frac{\text{data}}{\text{bandwidth}} \approx \frac{2 \cdot \text{data}}{\text{bandwidth}} \quad (\text{当 } N \text{ 较大时})$$

→ 带宽利用率接近 100%!

### 9.3 NCCL 调优

```
NCCL 环境变量:

NCCL_ALGO=Ring|Tree|CollnetDirect  # 算法选择
NCCL_PROTO=Simple|LL|LL128         # 协议选择
NCCL_MIN_NCHANNELS=4               # 最小通道数
NCCL_NSOCKS_PERTHREAD=4            # socket 数
NCCL_SOCKET_NTHREADS=4             # socket 线程数
NCCL_IB_DISABLE=0                  # 启用 InfiniBand
NCCL_IB_GID_INDEX=3                # RoCE v2 GID index
NCCL_DEBUG=INFO                    # 调试信息级别

常见问题:
  - NCCL timeout: 增加 NCCL_TIMEOUT
  - 性能差: 检查 PCIe 拓扑, 确保 GPU 间直连
  - 跨节点慢: 检查 InfiniBand/RoCE 配置
```

---

## 通信-计算 Overlap

### 10.1 为什么需要 Overlap

不 Overlap:
  ┌──────┐     ┌──────┐     ┌──────┐
  │Compute│────▶│Comm  │────▶│Compute│
  └──────┘     └──────┘     └──────┘
  
  通信期间 GPU 空闲!

Overlap:
  ┌──────┐
  │Compute│
  └──────┘
  ┌──────────────────┐
  │      Comm        │  ← 与下一个 Compute 重叠
  └──────────────────┘
  ┌──────┐
  │Compute│
  └──────┘

### 10.2 Overlap 技术

1. Gradient All-Reduce Overlap (DDP/FSDP):
   反向传播时，每计算完一层的梯度就启动异步 All-Reduce
   → 通信与下一层的反向计算重叠

2. Parameter All-Gather Overlap (ZeRO-3):
   预取下一层的参数 (异步 All-Gather)
   → 通信与当前层的计算重叠

3. PP Communication Overlap:
   发送激活值后立即开始下一 micro-batch 的计算
   → 通信与计算重叠

实现: CUDA Stream
  compute_stream: 计算
  comm_stream: 通信
  → 两个 stream 可以并行执行

---

## 自动并行搜索

### 11.1 问题定义

给定:
  - 模型配置 (层数, d_model, heads, ...)
  - 硬件配置 (GPU 数, 显存, 带宽, 拓扑)
  - 训练配置 (batch_size, seq_len, ...)

搜索:
  - 并行策略 (DP, TP, PP, SP, EP, ZeRO stage)
  - 最优配置 (最大化吞吐或最小化时间)

挑战:
  - 搜索空间巨大 (组合爆炸)
  - 需要准确的性能模型
  - 不同硬件的约束不同

### 11.2 代表工作

| 工具 | 方法 | 特点 |
|------|------|------|
| **Alpa** | 整数规划 + 动态规划 | 自动搜索 TP+PP 配置 |
| **Galvatron** | 动态规划 | 搜索 DP×TP×PP 空间 |
| **FlexFlow** | MCMC 采样 | 支持任意维度切分 |
| **Unity** | 整数规划 | 联合优化并行和 placement |
| **nnScaler** | 约束求解 | 微软的自动并行工具 |

---

## 实践框架对比

### 12.1 主流框架

| 框架 | 并行策略 | 特点 | 适用场景 |
|------|---------|------|---------|
| **Megatron-LM** | TP + PP + DP + SP | 性能极致，但配置复杂 | 大规模训练 (>100B) |
| **DeepSpeed** | ZeRO-1/2/3 + TP + PP | 易用，ZeRO 系列强大 | 中小规模训练 |
| **FSDP (PyTorch)** | ZeRO-2/3 (可配) | PyTorch 原生，生态好；Llama 3 训练使用 ~ZeRO-2 级别分片，组合 TP、PP | 中小规模训练 |
| **ColossalAI** | 多种并行 | 灵活，支持异构 | 研究和实验 |
| **torchtitan** | TP + PP + DP | Meta 官方，简洁 | 学习和小规模 |

### 12.2 选择指南

模型大小 → 推荐方案:

< 7B:
  → FSDP (单机多卡即可)
  → 或 DeepSpeed ZeRO-2

7B - 70B:
  → DeepSpeed ZeRO-3 + TP (如果需要)
  → 或 FSDP + TP

70B - 200B:
  → Megatron-LM: TP=4/8 + PP=4/8 + DP
  → 或 DeepSpeed ZeRO-3 + TP + PP

> 200B (如 MoE):
  → Megatron-LM: TP + PP + EP + SP
  → 需要精细的并行配置

---

> **关键原则**：
> 1. **通信是瓶颈**：TP 通信量大但延迟低 (NVLink)，PP 通信量小但 bubble 大
> 2. **ZeRO-3 是万能钥匙**：几乎任何模型都能训练，但通信开销大
> 3. **3D/4D 并行是工业标准**：基础 3D = TP(节点内) × PP(跨节点) × DP(数据并行)；Llama 3 扩展为 4D (增加 CP 维度)
> 4. **显存和通信是硬币两面**：省显存 = 增加通信，需要权衡
> 5. **先跑通再优化**：默认配置能跑通 > 手动调优到极致

---

## 十三、进阶：训练系统工程专题

前面 12 章覆盖了分布式训练的基础并行策略——DP/ZeRO、TP、PP、SP、EP 的基本原理和通信模式。在这些基础上，2025-2026 年的前沿实践引入了更复杂的系统工程优化。以下是关键主题的概要，详细分析见 [06-LLM 训练系统与稳定性](./06-LLM训练系统与稳定性.md)。

### 13.1 流水线调度进阶：DualPipe

DeepSeek-V3/V4 的 DualPipe 在 1F1B 和 ZeroBubble 基础上引入**双向调度**和**细粒度计算拆分**——从 pipeline 两端同时喂入 micro-batch，将 Attention 和 MoE FFN 的计算+通信拆为独立调度单元。Kimi-K2 评估后选择不采用（1T 参数下的双份参数存储问题），改用 warmup micro-batch overlap 替代。详见 [06 §2](./06-LLM训练系统与稳定性.md)。

### 13.2 MoE 通信重叠

EP All-to-All 是 MoE 训练的首要瓶颈。DeepSeek-V4 的 MegaMoE 将 Dispatch→Linear1→Act→Linear2→Combine 五阶段融合为单 kernel（理论加速 1.92×）。Kimi-K2 通过延迟权重梯度计算隐藏 EP 通信。MiniMax-01 提出 EP-ETP 解耦。详见 [06 §3](./06-LLM训练系统与稳定性.md)。

### 13.3 Fabric-Aware 通信

实际集群中 NVLink（~900 GB/s）与跨节点 IB（~50 GB/s）带宽差异巨大。Step3.5-Flash 的 Fabric-Aware 调度将 DP All-Reduce 拆为节点内+跨节点两阶段，并通过通信感知 rank 放置减少跨交换机流量 30-40%。详见 [06 §3.2](./06-LLM训练系统与稳定性.md)。

### 13.4 多模态训练：DEP

Kimi K2.5 的 DEP (Decoupled Encoder Process) 将视觉编码器前向、Backbone 训练、视觉反向拆为三阶段流水线，多模态训练效率达到纯文本训练的 ~90%。详见 [06 §3.3](./06-LLM训练系统与稳定性.md)。

---

> **关键原则**：
> 1. **通信是瓶颈**：TP 通信量大但延迟低 (NVLink)，PP 通信量小但 bubble 大
> 2. **ZeRO-3 是万能钥匙**：几乎任何模型都能训练，但通信开销大
> 3. **3D/4D 并行是工业标准**：基础 3D = TP(节点内) × PP(跨节点) × DP(数据并行)；Llama 3 扩展为 4D (增加 CP 维度)
> 4. **显存和通信是硬币两面**：省显存 = 增加通信，需要权衡
> 5. **先跑通再优化**：默认配置能跑通 > 手动调优到极致
> 6. **进阶训练系统工程**：[DualPipe / MegMoE / Fabric-Aware → 详见 06-训练系统与稳定性](./06-LLM训练系统与稳定性.md)

---

## 参考资料

- [Megatron-LM](https://arxiv.org/abs/1909.08053) — TP 并行策略
- [ZeRO](https://arxiv.org/abs/1910.02054) — 零冗余优化器
- [DeepSpeed](https://arxiv.org/abs/2201.05140) — 分布式训练框架
- [PyTorch FSDP](https://pytorch.org/docs/stable/fsdp.html) — 全分片数据并行

> **下一篇**：[LLM 训练系统与稳定性](./06-LLM训练系统与稳定性.md) — 从并行策略走向训练系统工程
