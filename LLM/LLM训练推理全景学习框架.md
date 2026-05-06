# LLM 训练与推理全景学习框架：从软件架构到芯片架构

> **核心命题**：训练和推理的本质都是**数据在存储层级间流动，被计算单元变换**的过程。LLM的每一次优化——无论是算法、系统还是芯片层面——本质上都是在重新划分"计算-访存-通信"的边界。
> **学习策略**：自上而下（从模型语义理解到底层实现），再自下而上（从硬件约束反推算法设计动机），形成闭环认知。

## 目录

1. [总体架构全景图](#总体架构全景图)
2. [第一层：模型与算法层（What to Compute）](#第一层模型与算法层what-to-compute)
3. [第二层：分布式系统层（How to Scale）](#第二层分布式系统层how-to-scale)
4. [第三层：算子与编译器层（How to Execute）](#第三层算子与编译器层how-to-execute)
5. [第四层：芯片微架构层（Where to Execute）](#第四层芯片微架构层where-to-execute)
6. [第五层：互联与基础设施层（How to Connect）](#第五层互联与基础设施层how-to-connect)
7. [端到端实践路线图](#端到端实践路线图)
8. [分阶段学习计划](#分阶段学习计划)
9. [关键资源索引](#关键资源索引)

---

## 总体架构全景图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         LLM 训练/推理 全栈视图                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  第一层: 模型与算法           Transformer / MoE / Diffusion / Mamba          │
│  ───────────────────         Attention / MLP / LayerNorm / Position Enc     │
│         ↕                    训练算法: SGD→AdamW, LR Schedule, 混合精度      │
│                                                                             │
│  第二层: 分布式系统           数据并行(DP) / 张量并行(TP) / 流水线并行(PP)    │
│  ───────────────────         序列并行(SP) / 专家并行(EP) / ZeRO 系列         │
│         ↕                    3D并行: DP×TP×PP 的组合空间搜索                  │
│                                                                             │
│  第三层: 算子与编译器         FlashAttention-1/2/3, CUDA Core/Tensor Core   │
│  ───────────────────         Kernel Fusion, KV Cache 管理, 量化反量化       │
│         ↕                    Triton / XLA / TensorRT / MLIR 编译栈          │
│                                                                             │
│  第四层: 芯片微架构           SM / Tensor Core / HBM / L2 Cache / Register   │
│  ───────────────────         SIMT→ITS 演进, TMA, Warp Scheduler, Barrier    │
│         ↕                    FP32→FP16→BF16→FP8→FP4 精度演进                │
│                                                                             │
│  第五层: 互联与基础设施        NVLink / NVSwitch / InfiniBand / RoCE         │
│  ───────────────────         SHARP (网内归约), 拓扑感知调度, 光互联          │
│                              GPU集群: 供电/散热/机架/故障恢复                 │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**关键洞察**：五层之间不是孤立的，优化决策需要**跨层协同**：
- 模型层的 GQA/MQA 设计 → 直接降低推理时 KV Cache 的 HBM 访存量（芯片层）
- FlashAttention 的分块策略 → 既利用了 SM 的 Shared Memory（芯片层），又改变了训练的访存模式（系统层）
- FP8 训练 → 需要模型算法（缩放因子设计）+ 编译器（量化图插入）+ 芯片支持（HMMA/Transformer Engine）三层配合

**已有文档的覆盖关系**：
- [LLM注意力机制发展与演进.md](./LLM注意力机制发展与演进.md) → 覆盖第一层（注意力算法深入）
- [NVIDIA-GPU架构演进与LLM.md](./NVIDIA-GPU架构演进与LLM.md) → 覆盖第四层（GPU微架构演进）
- **本文档** → 填补第二、三、五层及端到端串联，形成完整拼图

---

## 第一层：模型与算法层（What to Compute）

> 这一层定义"算什么问题"，是后续所有优化的源头。理解模型结构才能理解计算负载。

### 1.1 模型结构基础

| 主题 | 核心问题 | 学习要点 |
|------|---------|---------|
| **Transformer 标准结构** | 为什么是 Decoder-only？ | Self-Attention / Cross-Attention 的差异；Encoder-Decoder vs Decoder-only vs Prefix-LM 三种范式的计算特性对比 |
| **MLP 与激活函数** | SwiGLU 为什么比 ReLU 好？ | FFN→GLU 变体→Gated FFN 的演进；MLP 的计算量占比（训练 ~60%） |
| **LayerNorm 与 RMSNorm** | Pre-Norm vs Post-Norm 对训练稳定性的影响 | 归一化位置与梯度传播的关系；RMSNorm 消除均值平移的计算节省 |
| **位置编码** | RoPE 为什么成为事实标准？ | Sinusoidal→Learned→Relative→RoPE→ALiBi→YaRN→NoPE；外推性的物理意义 |
| **Tokenizer** | BPE vs SentencePiece vs Tiktoken | 词表大小对 Embedding 层计算量和内存的影响 |

### 1.2 注意力机制（详见已有文档）

> 📎 **已有深度覆盖**：[LLM注意力机制发展与演进.md](./LLM注意力机制发展与演进.md) 从时间/结构/机制/归一化/位置/效率/应用七个维度系统梳理

在训练推理全景中需要额外关注的视角：

| 注意力变体 | 对训练的影响 | 对推理的影响 |
|-----------|-------------|-------------|
| **MHA** | 标准基准；QKV 全量计算，显存峰值高 | KV Cache 占用 = 2×n_heads×d_head×L，长序列瓶颈 |
| **GQA** | KV 头数减少，TP 切分策略不同 | KV Cache 显著减小（n_kv_heads/n_heads 倍），Llama-2/3 标配 |
| **MQA** | 极致压缩 KV，可能影响收敛 | KV Cache 最小，但输出质量有损 |
| **MLA (DeepSeek)** | 低秩压缩 KV，训练时需解压矩阵 | KV Cache 仅存压缩后的 latent，推理极省显存 |
| **Ring Attention / Striped Attention** | 序列维度的并行化（SP），跨 GPU 分布 | 无限长上下文的理论基础 |

### 1.3 模型架构变体

| 架构 | 计算特征 | 对系统层的影响 |
|------|---------|--------------|
| **Dense Transformer** | 所有参数参与每次前向 | TP 切分均匀；负载均衡简单 |
| **MoE (Mixture of Experts)** | 稀疏激活，每次只激活部分 Expert | 引入 Expert Parallelism(EP)；负载不均（token-to-expert 分布偏斜）；All-to-All 通信成为瓶颈 |
| **Mamba / SSM** | 线性复杂度，RNN 风格状态更新 | 无 KV Cache，但缺少成熟的 TP/PP 策略；硬件适配仍在早期 |
| **Linear Attention** | 将 softmax 替换为 kernel 分解，O(N) 复杂度 | 训练吞吐高但可能有收敛质量损失；KV 存储形式不同 |

### 1.4 训练算法与超参策略

```
训练全流程的计算与访存特征变化：
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Warmup      │     │  Stable      │     │  Decay       │
│  (LR 爬坡)   │────▶│  (恒定/余弦) │────▶│  (LR 衰减)   │
│  Batch size  │     │  混合精度    │     │  模型收敛    │
│  预热期      │     │  FP16/BF16+  │     │  最终质量    │
└──────────────┘     └──────────────┘     └──────────────┘
```

| 技术 | 原理 | 系统层代价 |
|------|------|-----------|
| **混合精度训练 (AMP)** | FP32 Master Weights + FP16/FP8 前向/反向 | 维护两份权重；需要 loss scaling 防下溢 |
| **Gradient Accumulation** | 多步 micro-batch 累积再更新 | 增加有效 batch size，但不增加显存峰值 |
| **Gradient Clipping** | 限制梯度范数，防止训练崩溃 | 需要全局 all-reduce 梯度范数 |
| **Activation Checkpointing (Gradient Ckpt)** | 不存储中间激活，反向时重算 | 用 33% 额外计算换 ~4x 激活显存节省 |
| **FP8 训练 (Transformer Engine)** | 前向 FP8，反向 FP8，Master FP32 | 需要芯片支持（Hopper+）；量化和反量化 kernel 开销 |
| **Curriculum Learning** | 短序列→长序列渐进训练 | 序列长度变化导致显存和计算负载波动 |

---

## 第二层：分布式系统层（How to Scale）

> 单卡永远不够。这一层解决"怎么把计算拆到多张卡/多台机器上"，是训练最大的系统工程挑战。

### 2.1 并行策略全景

```
                张量并行 TP                       流水线并行 PP
              (层内参数切分)                    (按层间切分)
         ┌─────────────────┐            ┌─────────────────────────┐
         │   GPU0  ...  GPU7  │            │  GPU0→GPU1→...→GPU7    │
         │  ┌─────────────┐ │            │  Layer0→Layer1→...→L7  │
         │  │ 同一层的W被 │  │            │                         │
         │  │ 切分到多张卡  │ │            │  Bubble 问题：         │
         │  └─────────────┘ │            │  前向后向交替时的空闲     │
         └─────────────────┘            └─────────────────────────┘
                ↕    ↕                           ↕    ↕
         ┌─────────────────┐            ┌─────────────────────────┐
         │   GPU0    GPU1     │            │  GPU0   GPU1  ... GPU7 │
         │    ↓       ↓       │            │   ↓      ↓        ↓    │
         │   每个Rank          │            │  不同输入,              │
         │   相同输入,         │            │  相同模型参数           │
         │   不同参数分片       │            │                        │
         └─────────────────┘            └─────────────────────────┘
              数据并行 DP                      序列并行 SP
        (每个Rank有完整模型副本)           (序列长度维切分)
```

| 并行策略 | 切分维度 | 通信模式 | 通信量 | 适用场景 |
|---------|---------|---------|-------|---------|
| **DP (Data Parallel)** | Batch 维 | All-Reduce (梯度) | ~2Φ (参数量的 2 倍) | 基础策略，必须组合使用 |
| **TP (Tensor Parallel)** | 参数矩阵的行/列 | All-Reduce + All-Gather | 每层多次小通信，对带宽敏感 | 层内切分，需 NVLink 高带宽 |
| **PP (Pipeline Parallel)** | 模型深度（层） | P2P Send/Recv (激活) | 低（仅传递激活） | 跨节点友好，但 bubble 问题 |
| **SP (Sequence Parallel)** | 序列长度维 | All-Reduce / All-Gather | 与 TP 结合时复用通信 | 长序列训练必备 |
| **EP (Expert Parallel)** | Expert 维度 | All-to-All (token 路由) | 取决于 gating 分布 | MoE 模型专用策略 |
| **ZeRO-1/2/3** | 优化器状态/梯度/参数分片 | Reduce-Scatter + All-Gather | ZeRO-3 通信量 ~1.5Φ | 显存优化显著，但通信增加 |

### 2.2 3D 并行的组合策略

实际大模型训练都是 **DP × TP × PP × (EP)** 的复合策略：

```
以 GPT-175B 为例 (A100 × 1024 训练)：

  数据并行 (DP) = 64        → 每个 DP 组有同样的数据，不同参数分片
  张量并行 (TP) = 8         → 每层的 Attention/MLP 在 8 张卡内切分
  流水线并行 (PP) = 16       → 模型切 16 段，每段 8 张卡（TP）
  ─────────────────────
  总显卡 = 64 × 8 × 16 / (TP/PP 去重后) 的设计空间搜索

  关键约束：
  - TP 组内必须用 NVLink 互联（带宽 ~900 GB/s）
  - PP 可跨节点（带宽 ~100 GB/s 即可）
  - DP 通信频率最低，可通过梯度累积降低
```

### 2.3 通信原语与拓扑映射

| 通信原语 | 语义 | LLM 训练中的位置 |
|---------|------|-----------------|
| **All-Reduce** | 所有 Rank 求和后广播 | DP 梯度同步；TP 的前向输出聚合 |
| **Reduce-Scatter** | 求和后分片分发 | ZeRO 系列的分片梯度更新 |
| **All-Gather** | 收集所有分片并广播 | ZeRO 的参数重组；TP 的列切分结果收集 |
| **All-to-All** | 每对 Rank 间双向交换 | MoE 的 token dispatch/combine |
| **P2P Send/Recv** | 点对点传输 | PP 微批次间传递激活 |
| **Barrier** | 同步点 | 训练步边界 |

**拓扑感知**：通信原语的物理实现取决于物理拓扑（Ring / Tree / NVSwitch），好的并行策略会让重通信（TP）命中高带宽域（NVSwitch 域内），轻通信（PP/DP）走低带宽跨节点链路。

### 2.4 训练系统的全局问题

| 问题 | 描述 | 解决思路 |
|------|------|---------|
| **负载不均衡** | MoE 的 token 分布不均；流水线 bubble | Auxiliary Loss 鼓励均衡路由；vPP(交错流水线)、Breadth-First 调度 |
| **故障恢复** | 千卡集群 MTBF ~ 小时级 | Elastic Training（动态增删节点）；Checkpoint 策略（频次 × 写带宽的权衡）|
| **弹性扩缩容** | Spot 实例、抢占式资源 | 无缝重新分区；参数重新分布的最小通信量 |
| **收敛性** | 大 batch 影响泛化 | Layer-wise Adaptive LR (LAMB/LARS)；Seq-level 的 batch 定义 |
| **检查点效率** | 千亿参数 checkpoint 几十 GB | 异步写；分布式 checkpoint 分片写入；Fast Persist |

---

## 第三层：算子与编译器层（How to Execute）

> 这是软件与硬件的"翻译层"——将 PyTorch 的高级算子变换为可在特定芯片上高效执行的指令序列。

### 3.1 核心算子的计算-访存特征

```
┌──────────────────────────────────────────────────────────────┐
│                   LLM 核心算子的瓶颈分类                        │
├──────────────┬───────────────┬───────────────┬───────────────┤
│              │  GEMM-Based   │ Memory-Bound  │ Latency-Bound │
├──────────────┼───────────────┼───────────────┼───────────────┤
│ 训练前向     │ Linear (QKV/o)│ LayerNorm     │ Gather/Scatter│
│              │ MLP 投影层    │ Dropout       │ Embedding查表 │
│              │ Attention(QK^T)│              │               │
│ 训练反向     │ dW (梯度 GEMM)│ dInput (小块  │ 链式法则调度  │
│              │ dA (大矩阵)   │  GEMM 变体)   │               │
│ 推理前向     │ Prefill 的    │ Decode 的     │ KV Cache      │
│              │ GEMM          │ Attention     │ 索引更新      │
│              │               │  (GEMV 本质)  │               │
└──────────────┴───────────────┴───────────────┴───────────────┘

关键区分：Decoder 的 Prefill（一次处理所有 prompt token）vs Decode（逐 token 自回归）
  - Prefill 是 compute-bound（大矩阵乘法）
  - Decode 是 memory-bound（每次只算 1 token，但需要加载整个 KV Cache）
```

### 3.2 关键 Kernel 优化技术

| 技术 | 解决的问题 | 核心思想 | 典型实现 |
|------|-----------|---------|---------|
| **FlashAttention-1/2/3** | Attention 的 HBM 读写带宽瓶颈 | 分块计算（tiling）+ Online Softmax；Forward 只用 O(N²d) 次 HBM 读写而非 O(N²dM) | Dao-AILab; `flash_attn` |
| **Flash-Decoding** | Decode 阶段长 KV Cache | 将 KV Cache 沿 sequence 维度分块并行，最后的 softmax reduction | FlashInfer / vLLM |
| **PagedAttention** | KV Cache 显存碎片 | 将 KV Cache 按 page (block) 管理，类似 OS 虚拟内存 | vLLM 核心创新 |
| **FlashInfer** | 统一 Attention Kernel 库 | 为 Prefill / Decode / Append 等不同阶段提供对应的优化 kernel | FlashInfer |
| **Activation Ckpt (Cuda-side)** | 反向时的激活重算 | Selectively recompute；与 PyTorch autograd hooks 结合 | Megatron-LM |
| **LayerNorm/RMSNorm Fusion** | 多次小 kernel launch overhead | 将 LayerNorm 与其后的 GEMM 融合为一个 kernel | Apex / TorchDynamo |

### 3.3 编译器与代码生成栈

```
┌────────────────────────────────────────────────────────────┐
│                    编译器与代码生成层级                        │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  高级表示 (IR)                                             │
│  ┌──────────────────────────────────────────────────────┐ │
│  │ PyTorch FX Graph / TorchScript / JAX Jaxpr / ONNX    │ │
│  │ → 图级别的算子融合、死代码消除、形状传播               │ │
│  └──────────────────────────────────────────────────────┘ │
│                          ↓                                 │
│  DSL / 代码生成层                                          │
│  ┌──────────────────────────────────────────────────────┐ │
│  │ Triton (OpenAI)    → Python DSL → PTX (AMD 也支持)   │ │
│  │ TVM (Apache)       → Tensor Expression → 多后端      │ │
│  │ OpenAI Triton      → Decoupled from CUDA, Block 抽象  │ │
│  └──────────────────────────────────────────────────────┘ │
│                          ↓                                 │
│  低级 IR / 后端                                            │
│  ┌──────────────────────────────────────────────────────┐ │
│  │ MLIR / LLVM         → 通用编译器基础设施               │ │
│  │ NVCC → PTX → SASS   → CUDA 原生编译路径               │ │
│  │ TensorRT            → 推理专用优化编译器               │ │
│  │ XLA (Google)        → TPU 优化, HLO → LLO             │ │
│  └──────────────────────────────────────────────────────┘ │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

**关键洞察**：Triton 的崛起代表了行业趋势——用 Python DSL 编写高性能 GPU kernel，而非直接写 CUDA C++。Triton 将"Block 级 tiling"作为一等抽象，自动处理 thread 映射和 memory coalescing，大幅降低 kernel 开发成本。

### 3.4 推理引擎对比

| 推理引擎 | 核心优化技术 | 适用场景 | 关键创新 |
|---------|-------------|---------|---------|
| **vLLM** | PagedAttention, Continuous Batching, Prefix Caching | 高吞吐在线服务 | PagedAttention 解决了 KV Cache 碎片 |
| **TensorRT-LLM** | 图优化 + FP8/INT4 量化 + 多节点推理 | NVIDIA 生态最优性能 | 深度编译器优化，与 GPU 特性紧耦合 |
| **SGLang** | RadixAttention (Prefix 自动复用) + Structured Output | 长 System Prompt, 复杂编程 Agent | Prefix Caching 粒度比 vLLM 更细 |
| **llama.cpp** | GGUF 量化格式 + CPU/GPU 混合推理 | 消费级设备部署 | 极致的量化方案 + 跨平台 |
| **Ollama** | 封装 llama.cpp + 一键部署 | 本地 LLM 体验 | 易用性，但性能不如专业引擎 |

### 3.5 关键维度：KV Cache 管理

```
KV Cache 的生命周期与挑战：

  ┌─────────────────────────────────────────────────────────┐
  │  单次请求视角:                                           │
  │                                                         │
  │  Prompt Tokens → Prefill (计算密集) → 生成 KV Cache      │
  │       ↓                                                 │
  │  Decode (逐 token 自回归, 访存密集)                      │
  │    → 每步追加新的 KV → 读取全部历史 KV                   │
  │       ↓                                                 │
  │  EOS → 释放 KV Cache                                    │
  │                                                         │
  │  系统视角 (多请求并发):                                   │
  │  - 显存主要被 KV Cache 占据 (非模型权重)                  │
  │  - PagedAttention: 不连续分配, 按需 page in/out          │
  │  - Prefix Caching: 相同 prefix (system prompt) 复用     │
  │  - KV Cache 量化: KV 用 FP8/INT8 存储而非 BF16          │
  │  - Layer-wise Prefill: 边 prefill 边 decode, 减少峰值    │
  └─────────────────────────────────────────────────────────┘
```

---

## 第四层：芯片微架构层（Where to Execute）

> 软件优化的极限受限于硬件能力边界。这一层回答"芯片上到底发生了什么"。

> 📎 **已有深度覆盖**：[NVIDIA-GPU架构演进与LLM.md](./NVIDIA-GPU架构演进与LLM.md) 从 Tesla 到 Blackwell 的完整架构演进，包含 SM、Tensor Core、TMA、Cluster 等关键概念

在训练推理全景中需要的补充视角：

### 4.1 计算核的类型与分工

| 计算单元 | 指令类型 | 精度 | 吞吐特征 (A100) | 在 LLM 中的角色 |
|---------|---------|------|----------------|----------------|
| **CUDA Core (FP32/INT32)** | FADD, FMUL, FMA | FP32/INT32 | 19.5 TFLOPS | 非 GEMM 操作：LayerNorm, Softmax, GELU, Gather |
| **Tensor Core (1st-5th Gen)** | HMMA, WGMMA | FP16/BF16/FP8/FP4 | 312 TFLOPS (FP16) | **全部 GEMM**: Attention QK^T, MLP 投影, Linear 层 |
| **Tensor Core (MMA)** | MMA.sync | FP16/BF16 | 同上 | Warp 级矩阵乘加 (≤Ampere) |
| **Tensor Core (WGMMA)** | WGMMA | FP8/FP16 | Hopper 2× | Warp-Group 级, 配合 TMA 使用 |
| **TMA (Tensor Mem Accelerator)** | - | - | 硬件异步数据搬运 | Hopper+ 取代 LDG/STG；支持 5D tensor 寻址 |

### 4.2 内存层级与数据搬运策略

```
                  容量             带宽 (A100)        延迟
  ┌──────────┐
  │ Register │  ~256 KB/SM       ~8 TB/s (内部)     ~0 cycles
  │  Register│  每线程最多 255 regs, 溢出→L1 (性能灾难)
  └────┬─────┘
       │
  ┌────┴─────┐
  │   L1 /    │  ~192 KB/SM       ~4 TB/s            ~30 cycles
  │   Shared  │  可配置分配: L1或Shared Memory
  │   Memory  │  Shared Mem: CTA 内线程间通信/数据复用
  └────┬─────┘         ← FlashAttention 的分块在此完成
       │
  ┌────┴─────┐
  │   L2     │  40 MB (全局)      ~4 TB/s            ~200 cycles
  │  Cache   │  SM 间共享; NVLink 与 L2 直接交互
  └────┬─────┘         ← Persistent Kernel 可在此缓存
       │
  ┌────┴─────┐
  │   HBM2e/ │  40/80 GB          2 TB/s             ~300-800 cycles
  │   HBM3   │  (H100: 3.35 TB/s)  **主要瓶颈**
  └────┬─────┘         ← 模型权重, KV Cache, 激活存储在此
       │
  ┌────┴─────┐
  │  NVMe/   │  TB 级              ~7-50 GB/s         ~μs-ms
  │  Network │                                    Offload 可选
  └──────────┘
```

**LLM 训练的核心矛盾**：
- Tensor Core 计算速度 >> HBM 带宽增速（**roofline model 的瓶颈在访存**）
- 解决方案：利用 SM 的 Shared Memory 和 Register File 做数据复用（tiling），减少 HBM 读写次数
- FlashAttention 就是 roofline 思想在 Attention 算子上最成功的应用

### 4.3 关键 GPU 架构特性与 LLM 优化的对应关系

| GPU 特性 | 引入代际 | 对 LLM 训练的直接影响 |
|---------|---------|---------------------|
| **Tensor Core** | Volta | 矩阵乘法吞吐量提升 8-12×（vs CUDA Core GEMM） |
| **ITS (独立线程调度)** | Volta | 细粒度同步使 Attention Kernel 中 warp 级协作更高效 |
| **TF32 TC** | Ampere | 训练精度 vs 速度的新平衡点（19.5 → 156 TFLOPS） |
| **FP8 TC + Transformer Engine** | Hopper (H100) | FP8 训练的硬件基础；训练吞吐翻倍 |
| **TMA** | Hopper (H100) | 硬件异步数据搬运 → FlashAttention-3 的基础 |
| **DSM (Distributed Shared Memory)** | Hopper | Cluster 内 SM 可跨芯片访问 Shared Memory |
| **SM-to-SM direct NVLink** | Hopper | All-Reduce 的带宽翻倍 |
| **FP4 TC + TMEM** | Blackwell (B200) | 推理时权重进一步压缩；近存计算减少数据搬运 |
| **MIG** | Ampere | 单 GPU 切分为多个推理实例，提高推理 GPU 利用率 |

### 4.4 非 NVIDIA 路线

| 芯片 | 架构特点 | LLM 适配现状 |
|------|---------|-------------|
| **Google TPU v5p** | 脉动阵列 + 专用 ICI 互联 | JAX/XLA 生态；训练 LLM 效率极高但生态封闭 |
| **AMD MI300X** | CDNA3, 类似 CUDA 的 ROCm 栈 | 192GB HBM3 显存优势；软件栈追赶中 |
| **Intel Gaudi 2/3** | 异构计算 + 片上 HBM + RoCE 集成 | 原生支持 PyTorch；推理部署有一席之地 |
| **AWS Trainium 2** | 专用训练芯片 + Neuron SDK | 低成本训练选项；软件生态约束 |
| **Ascend 910B** | 达芬奇架构 | 华为生态；受限出口但国内替代主力 |
| **Cerebras WSE-3** | 晶圆级芯片，片上内存巨大 | 适合 MoE 等通信密集型模型 |

---

## 第五层：互联与基础设施层（How to Connect）

> 百/千/万卡集群中，互联网络往往比单个芯片的计算能力更早成为瓶颈。

### 5.1 GPU 互联技术栈

```
                    GPU 间互联层级

  ┌──────────────────────────────────────────────────────────┐
  │                     机架视图                              │
  │                                                          │
  │   ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐   │
  │   │  GPU0   │  │  GPU1   │  │  GPU2   │  │  GPU3   │   │
  │   └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘   │
  │        │ NVLink 900 GB/s │            │            │    │
  │        └─────────────────┘            │            │    │
  │   ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐   │
  │   │  GPU4   │  │  GPU5   │  │  GPU6   │  │  GPU7   │   │
  │   └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘   │
  │        │            │            │            │        │
  │   ═════╧════════════╧════════════╧════════════╧════    │
  │              NVSwitch (全互联, 3.2 TB/s)                 │
  │   ══════════════════════════════════════════════════    │
  │                       ↕                                  │
  │   ┌─────────────────────────────────────────────────┐   │
  │   │  InfiniBand NDR400 / RoCE (400 GB/s per port)   │   │
  │   │  跨节点互联 → 连接不同 HGX/DGX 节点               │   │
  │   └─────────────────────────────────────────────────┘   │
  │                                                          │
  └──────────────────────────────────────────────────────────┘
```

| 互联技术 | 带宽 (单向/每链路) | 拓扑 | 特征 |
|---------|-------------------|------|------|
| **NVLink 4.0** | 100 GB/s | 全互联 (NVSwitch) | GPU-GPU 直接通信；TP 依赖 |
| **NVSwitch 3.0** | 3.2 TB/s (全双工) | 多层 fat-tree / Clos | 域内任意 GPU 间等带宽 |
| **NVLink-C2C** | 450 GB/s (Grace-Hopper) | 点对点 | CPU-GPU 一致性互联 |
| **InfiniBand NDR400** | 400 Gbps | 自适应路由 | 超大规模集群标配；SHARP |
| **RoCE v2** | 400 Gbps | ECMP | 以太网替代方案，成本低 |
| **PCIe 5.0** | 64 GB/s (×16) | 树形 | 通用但带宽低，仅适合轻通信 |

### 5.2 通信对 LLM 训练的约束

```
问题: 为什么大模型训练不能无限扩展 DP？

  DP 做 All-Reduce 梯度同步，通信量为每步 ~2×Φ (Φ=参数量)

  以 175B 模型, FP16 为例:
    参数量 Φ = 350 GB
    All-Reduce 通信量 = 700 GB / step

  假设 InfiniBand 400 Gbps × 8 链路 (有效 200 GB/s):
    通信时间 ≥ 3.5s / step
    如果单步计算时间为 10s → 通信占比 35% (可接受)
    如果优化后单步 2s → 通信占比 175% (完全不可接受!)

  结论:
  - 通信和计算需要 overlap (分布式优化器, async All-Reduce)
  - DP 不能无限增加，需要 TP/PP 补充
  - 网络拓扑 (rail-optimized, dragonfly) 比绝对带宽更重要
```

### 5.3 网内计算（SHARP / In-Network Computing）

| 技术 | 原理 | LLM 训练价值 |
|------|------|-------------|
| **SHARP (Mellanox)** | All-Reduce 在交换机芯片内完成（非端点）| 减少 50% 网络数据量；延迟降低 2-4× |
| **NVSwitch SHARP** | 域内 All-Reduce 由 NVSwitch 芯片完成 | TP/DP 的 Reduction 可以完全 offload |
| **In-Network Aggregation** | 智能网卡/交换机做梯度聚合 | 减少节点 CPU/GPU 的聚合开销 |

### 5.4 基础设施关键问题

| 问题 | 规模门槛 | 影响 |
|------|---------|------|
| **功耗与散热** | >1000 GPU | DGX H100: 10.2kW/节点 → 液冷必需 |
| **MTBF (平均故障间隔)** | >512 GPU | 训练几天必有一次故障；checkpoint + 恢复体系 |
| **存储带宽** | >100B 模型 | Checkpoint 写: 175B×2bytes≈350GB → 需高速并行文件系统 |
| **资源调度碎片** | 多租户集群 | Gang Scheduling vs Elastic; topology-aware placement |
| **光互联 vs 铜缆** | >1000 GPU | 跨机架光模块成本可能超过 GPU 本身 |

---

## 端到端实践路线图

### 实操项目建议（由浅入深）

```
难度 Level 1: 单卡实践              Estimated: 1-2 weeks
├── 用 transformers 跑通 GPT-2 训练 (HuggingFace Trainer)
├── 理解 DataLoader + Gradient Accumulation 的 batch 逻辑
├── 用 torch.profiler 分析 Attention/MLP 的耗时分布
└── 实现简单的 KV Cache 并手动推理解码

难度 Level 2: 单机多卡              Estimated: 2-3 weeks
├── 用 PyTorch DDP / FSDP 训练 Llama-2-7B
├── 理解 FSDP (ZeRO-3) 的参数分片与重组通信
├── 手写一个简单的 TP (列切 Linear + All-Reduce)
├── 用 NCCL 调试工具分析 collective 性能
└── 体验 activation checkpointing 对显存和速度的影响

难度 Level 3: 多机训练              Estimated: 3-4 weeks
├── 部署 Megatron-LM 或 DeepSpeed, 跑通 7B→70B 的多机训练
├── 深入理解 3D 并行的配置策略
├── 调试一次 TP/PP 不匹配导致的性能退化
├── 用 NSight Systems 分析端到端 timeline
└── 解读一次 OOM / NCCL timeout → 学会看训练日志

难度 Level 4: Kernel 与编译器        Estimated: 4-6 weeks
├── 用 Triton 写一个 Fused MLP kernel（替代 PyTorch 多个 kernel）
├── 手动实现一个简化版 FlashAttention (online softmax + tiling)
├── 用 torch.compile + inductor backend 观察生成的 Triton 代码
├── 对比手写 CUDA kernel vs Triton kernel 的性能
└── 阅读 FlashAttention-2 源码 + PagedAttention 源码

难度 Level 5: 集群部署与性能调优      Estimated: 4-6 weeks
├── 用 vLLM 部署 Llama-3-70B 并压测 (不同 QPS / input len / output len)
├── 对比不同并行策略 (vLLM TP vs Ray Serve DP) 的吞吐
├── 做一次 Prefix Caching 效果的量化分析
├── 用 TensorRT-LLM 部署并对比 vLLM 性能
└── 理解 Continuous Batching 的调度策略实现
```

---

## 分阶段学习计划

### Phase 1: 单卡视角，建立端到端直觉（优先：第一层 + 第三层浅层）

| 序号 | 学习内容 | 已有资源 | 产出 |
|------|---------|---------|------|
| 1.1 | 回顾 Transformer 完整结构（不只是 Attention）| [LLM注意力机制发展](./LLM注意力机制发展与演进.md) | 笔记补全 MLP / LayerNorm / RoPE 部分 |
| 1.2 | 用 PyTorch 手写 GPT-2 的完整 forward pass | - | 代码 |
| 1.3 | torch.profiler 分析一次训练的 GPU 时间线 | - | 分析笔记 |
| 1.4 | 实现 KV Cache 解码循环，观察每个 step 的耗时 | - | 代码 + 笔记 |
| 1.5 | 阅读 PyTorch FSDP 论文 (ZeRO-3) | - | 阅读笔记 |

### Phase 2: 多卡训练，理解分布式（优先：第二层全面学习）

| 序号 | 学习内容 | 潜在文档产出 | 关联 |
|------|---------|-------------|------|
| 2.1 | NCCL 通信原语: All-Reduce 的 Ring/Tree 实现 | 分布式系统笔记 | 第二层 |
| 2.2 | Megatron-LM 论文: TP → SP → PP 的组合逻辑 | 分布式系统笔记 | 第二层 |
| 2.3 | DeepSpeed ZeRO 论文三篇: ZeRO-1→2→3→Offload | - | 第二层 |
| 2.4 | 实际搭建 7B 模型的 DDP/FSDP 训练 | - | 实操 |
| 2.5 | 3D 并行的通信量分析与配置公式推导 | - | 第二层 |

### Phase 3: 深入算子，理解硬件约束（优先：第三层 + 第四层）

| 序号 | 学习内容 | 已有资源 | 产出 |
|------|---------|---------|------|
| 3.1 | FlashAttention 论文精读 (1/2/3) | - | 论文笔记 |
| 3.2 | [GPU 架构演进](./NVIDIA-GPU架构演进与LLM.md) 与算子优化的映射 | - | 交叉分析笔记 |
| 3.3 | 用 Triton 手写 FusedAttention kernel | - | 代码 |
| 3.4 | NSight Compute 分析单个 kernel 的 SM 利用率 / 访存效率 | - | 分析笔记 |
| 3.5 | PagedAttention / FlashInfer 源码阅读 | - | 源码笔记 |

### Phase 4: 推理部署，产业链落地（优先：第三层推理专项）

| 序号 | 学习内容 | 产出建议 |
|------|---------|---------|
| 4.1 | vLLM Continuous Batching 调度原理 | 笔记 |
| 4.2 | TensorRT-LLM 图编译流程 | 笔记 |
| 4.3 | Speculative Decoding / Medusa 等投机解码技术 | 笔记 |
| 4.4 | 量化技术全景: GPTQ / AWQ / SmoothQuant / FP8 推理 | 独立文档 |

### Phase 5: 芯片前沿 + 全栈串联（优先：第四层 + 第五层）

| 序号 | 学习内容 | 产出建议 |
|------|---------|---------|
| 5.1 | Hopper → Blackwell 的推理/训练新特性 & 论文对应关系 | 补充 GPU 文档 |
| 5.2 | 集群网络拓扑对训练效率的影响量化 | 笔记 |
| 5.3 | 非 NVIDIA 芯片的软件栈适配难度评估 | 笔记 |
| 5.4 | 自己设计一个"最小化全栈 demo"（模型 + TP/PP + Kernel 优化）| 代码 |

---

## 文档体系建议（建议产出的笔记列表）

结合已有文档和建议补充的新文档，形成一个完整的笔记体系：

```
LLM/
├── LLM注意力机制发展与演进.md          ✅ 已有 (第一层-深入)
├── NVIDIA-GPU架构演进与LLM.md           ✅ 已有 (第四层-深入)
├── LLM训练推理全景学习框架.md           ✅ 本文档 (总索引+框架)
├── [待补充] Transformer完整结构与训练算法.md   (第一层-补充)
├── [待补充] LLM分布式训练: 并行策略与ZeRO.md   (第二层)
├── [待补充] LLM推理引擎: vLLM到TensorRT-LLM.md (第三层)
├── [待补充] FlashAttention论文精读与Triton实践.md (第三层)
├── [待补充] LLM量化技术: PTQ/QAT到FP8推理.md   (第三层)
├── [待补充] GPU集群互联: NVLink到InfiniBand.md  (第五层)
└── [待补充] LLM推理性能调优实战.md              (端到端)
```

---

> **学习原则**：
> 1. **问题驱动**：每学一个技术，问"它解决了之前哪个瓶颈？引入了什么新矛盾？"
> 2. **数据流视角**：时刻追踪"这个操作中，数据从哪来、到哪去、搬运成本是多少？"
> 3. **跨层关联**：软件优化（GQA）→ 硬件（HBM 读少了）→ 系统（TP 切分变了），形成三维关联
> 4. **动手优先**：概念看懂 30% 就开始动手跑代码，benchmark 数据比理论讨论更有说服力
</parameter>
</invoke>
</tool_calls>