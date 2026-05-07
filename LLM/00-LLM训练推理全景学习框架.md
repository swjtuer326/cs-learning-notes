# LLM 训练与推理全景学习框架：从软件架构到芯片架构

> **核心命题**：训练和推理的本质都是**数据在存储层级间流动，被计算单元变换**的过程。LLM的每一次优化——无论是算法、系统还是芯片层面——本质上都是在重新划分"计算-访存-通信"的边界。
> **学习策略**：自上而下（从模型语义理解到底层实现），再自下而上（从硬件约束反推算法设计动机），形成闭环认知。

## 目录

1. [总体架构全景图](#总体架构全景图)
2. [第〇层：数据工程（What to Feed）](#第〇层数据工程what-to-feed)
3. [第一层：模型与算法层（What to Compute）](#第一层模型与算法层what-to-compute)
4. [第 1.5 层：Post-Training 与对齐（How to Align）](#第-15-层post-training-与对齐how-to-align)
5. [第二层：分布式系统层（How to Scale）](#第二层分布式系统层how-to-scale)
6. [第三层：算子与编译器层（How to Execute）](#第三层算子与编译器层how-to-execute)
7. [第 3.5 层：推理服务架构（How to Serve）](#第-35-层推理服务架构how-to-serve)
8. [第四层：芯片微架构层（Where to Execute）](#第四层芯片微架构层where-to-execute)
9. [第五层：互联与基础设施层（How to Connect）](#第五层互联与基础设施层how-to-connect)
10. [评估与度量体系](#评估与度量体系)
11. [端到端实践路线图](#端到端实践路线图)
12. [分阶段学习计划](#分阶段学习计划)
13. [关键资源索引](#关键资源索引)

---

## 总体架构全景图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         LLM 训练/推理 全栈视图                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  第〇层: 数据工程            数据采集→清洗→去重→配比→Tokenization             │
│  ───────────────────        合成数据生成, 数据课程学习, 去污染                 │
│         ↕                                                                   │
│  第一层: 模型与算法           Transformer / MoE / Diffusion / Mamba          │
│  ───────────────────         Attention / MLP / LayerNorm / Position Enc     │
│         ↕                    训练算法: SGD→AdamW, LR Schedule, 混合精度      │
│                                                                             │
│  第1.5层: Post-Training       SFT (指令微调) → RLHF (Reward+PPO) → DPO      │
│  ───────────────────         Constitutional AI, Rejection Sampling          │
│         ↕                    GRPO, Multi-Token Prediction                   │
│                                                                             │
│  第二层: 分布式系统           数据并行(DP) / 张量并行(TP) / 流水线并行(PP)    │
│  ───────────────────         序列并行(SP) / 专家并行(EP) / ZeRO 系列         │
│         ↕                    3D并行: DP×TP×PP 的组合空间搜索                  │
│                                                                             │
│  第三层: 算子与编译器         FlashAttention-1/2/3, CUDA Core/Tensor Core   │
│  ───────────────────         Kernel Fusion, KV Cache 管理, 量化反量化       │
│         ↕                    Triton / XLA / TensorRT / MLIR 编译栈          │
│                                                                             │
│  第3.5层: 推理服务架构        Continuous Batching, Disaggregated Serving     │
│  ───────────────────         Prefix Caching, Speculative Decoding           │
│         ↕                    Request Scheduling, SLA/SLO 管理               │
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

**关键洞察**：各层之间不是孤立的，优化决策需要**跨层协同**：
- 数据层的质量过滤与配比 → 直接影响模型层的收敛速度和最终效果
- 模型层的 GQA/MQA 设计 → 直接降低推理时 KV Cache 的 HBM 访存量（芯片层）
- Post-Training 的 DPO/RLHF → 需要分布式系统层的支持（多模型协同训练）
- FlashAttention 的分块策略 → 既利用了 SM 的 Shared Memory（芯片层），又改变了训练的访存模式（系统层）
- FP8 训练 → 需要模型算法（缩放因子设计）+ 编译器（量化图插入）+ 芯片支持（HMMA/Transformer Engine）三层配合
- 推理服务架构的 Disaggregated Serving → 改变了 Prefill/Decode 的硬件资源配比（芯片层+互联层）

**已有文档的覆盖关系**：
- [03-LLM注意力机制发展与演进.md](./03-LLM注意力机制发展与演进.md) → 覆盖第一层（注意力算法深入）
- [11-NVIDIA-GPU架构演进与LLM.md](./11-NVIDIA-GPU架构演进与LLM.md) → 覆盖第四层（GPU微架构演进）
- **本文档** → 填补第〇层、第1.5层、第二、三、3.5、五层及端到端串联，形成完整拼图

---

## 第〇层：数据工程（What to Feed）

> 数据是 LLM 的"燃料"。数据质量对最终效果的影响往往超过模型架构的微调。这一层解决"喂什么数据、怎么喂"的问题。

### 0.1 数据采集与来源

| 来源 | 典型规模 | 特征 | 代表数据集 |
|------|---------|------|-----------|
| **Common Crawl** | PB 级 | 覆盖面广但噪声极大，需重度过滤 | C4, RefinedWeb, Dolma |
| **代码仓库** | TB 级 | 结构化强，对推理能力重要 | The Stack, StarCoderData |
| **学术论文** | TB 级 | 高质量但领域窄 | S2ORC, ArXiv |
| **书籍** | TB 级 | 长文本、高质量 | Books3, Gutenberg |
| **百科/知识库** | GB 级 | 事实性强 | Wikipedia, Wikidata |
| **对话/指令数据** | GB 级 | 用于 SFT 和对齐 | ShareGPT, OpenHermes, UltraChat |
| **合成数据** | 可无限扩展 | 用强模型生成训练弱模型 | Self-Instruct, Evol-Instruct, Phi-4 合成策略 |

### 0.2 数据清洗 Pipeline

```
原始数据 → 语言检测 → 质量过滤 → 去重 → 毒性/PII 过滤 → 配比混合 → 训练数据
  │           │          │        │         │           │
  │           │          │        │         │           └── DoReMi/DoGE 自动配比
  │           │          │        │         └── 毒性分类器, PII 正则/模型检测
  │           │          │        └── MinHash (文档级) + 精确去重 (段落级)
  │           │          └── Perplexity scoring, Classifier-based quality
  │           └── FastText / CLD3 语言分类
  └── Common Crawl WARC/WET 文件解析
```

| 清洗步骤 | 方法 | 关键参数 |
|---------|------|---------|
| **语言检测** | FastText, CLD3 | 保留目标语言，去除低置信度 |
| **质量过滤** | Perplexity scoring (KenLM), 规则过滤 (长度/重复度/特殊字符比例) | 阈值选择影响保留率 |
| **文档级去重** | MinHash + LSH (如 128 个 hash 函数, 0.8 相似度阈值) | 去重粒度 (文档/段落) |
| **精确去重** | Suffix Array, Bloom Filter | 精确匹配 vs 近似匹配 |
| **毒性过滤** | Perspective API, 自训练分类器 | 误杀率 vs 漏过率 |
| **PII 去除** | 正则匹配 + NER 模型 | 邮箱/电话/身份证/地址 |
| **去污染** | 训练集与 benchmark 的 n-gram 重叠检测 | 防止 benchmark 泄露 |

### 0.3 数据混合与配比策略

| 策略 | 原理 | 代表工作 |
|------|------|---------|
| **启发式配比** | 人工设定各领域比例（如 50% web + 20% code + 10% wiki + ...） | Llama 系列, Falcon |
| **DoReMi** | 用小模型自动搜索最优领域配比，再用于大模型训练 | DoReMi (Stanford, 2023) |
| **DoGE** | 基于梯度相似度的动态数据选择 | DoGE (2024) |
| **Data Curriculum** | 从简单到复杂、从短到长的渐进式训练 | Phi 系列, Llama-3 |
| **退火 (Annealing)** | 训练末期用高质量数据做退火，提升 benchmark 表现 | Llama-3, Phi-4 |

### 0.4 Tokenizer 训练与选型

| Tokenizer | 算法 | 特征 | 代表模型 |
|-----------|------|------|---------|
| **BPE** | Byte-Pair Encoding | 从字符级开始逐步合并高频对 | GPT 系列, Llama (tiktoken) |
| **SentencePiece** | BPE / Unigram | 支持原始文本（无需预分词），多语言友好 | Llama-1/2, Mistral |
| **WordPiece** | 基于似然的合并 | BERT 风格 | BERT, T5 |
| **Unigram** | 基于概率的剪枝 | 从大词表逐步剪枝 | XLNet, ALBERT |

**关键设计决策**：
- **词表大小**：越大 → 序列越短（吞吐高），但 Embedding 层计算/内存增加。典型值：32K (Llama-2) → 128K (Llama-3, Qwen2.5)
- **多语言支持**：大词表 + 多语言训练数据 → 避免 token 膨胀（非英语文本被拆成过多 token）
- **特殊 Token 设计**：Chat Template 的 `<|system|>`, `<|user|>`, `<|assistant|>` 等控制 token

### 0.5 合成数据生成

> 2024-2025 年最重要的数据趋势：用强模型生成训练数据，突破互联网数据瓶颈。

| 方法 | 原理 | 应用场景 |
|------|------|---------|
| **Self-Instruct** | 用少量种子指令让模型生成更多指令 | SFT 数据扩充 |
| **Evol-Instruct** | 逐步增加指令复杂度（深度/广度进化） | WizardLM, 复杂指令数据 |
| **Persona-driven** | 用不同角色设定生成多样化数据 | 对话多样性 |
| **Math/Code 合成** | 用规则/模板生成带答案的数学题和代码题 | 推理能力训练 |
| **Phi-4 策略** | 多智能体合成 + 验证 + 过滤的完整 pipeline | 小模型高质量训练 |

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

> 📎 **已有深度覆盖**：[03-LLM注意力机制发展与演进.md](./03-LLM注意力机制发展与演进.md) 从时间/结构/机制/归一化/位置/效率/应用七个维度系统梳理

在训练推理全景中需要额外关注的视角：

| 注意力变体 | 对训练的影响 | 对推理的影响 |
|-----------|-------------|-------------|
| **MHA** | 标准基准；QKV 全量计算，显存峰值高 | KV Cache 占用 = 2×n_heads×d_head×L，长序列瓶颈 |
| **GQA** | KV 头数减少，TP 切分策略不同 | KV Cache 显著减小（n_kv_heads/n_heads 倍），Llama-2/3 标配 |
| **MQA** | 极致压缩 KV，可能影响收敛 | KV Cache 最小，但输出质量有损 |
| **MLA (DeepSeek)** | 低秩压缩 KV，训练时需解压矩阵 | KV Cache 仅存压缩后的 latent，推理极省显存 |
| **Ring Attention / Striped Attention** | 序列维度的并行化（SP），跨 GPU 分布 | 无限长上下文的理论基础 |

### 1.3 模型架构变体

| 架构 | 计算特征 | 对系统层的影响 | 代表模型 |
|------|---------|--------------|---------|
| **Dense Transformer** | 所有参数参与每次前向 | TP 切分均匀；负载均衡简单 | Llama-2/3, Qwen2.5, Gemma-2 |
| **MoE (Mixture of Experts)** | 稀疏激活，每次只激活部分 Expert | 引入 Expert Parallelism(EP)；负载不均（token-to-expert 分布偏斜）；All-to-All 通信成为瓶颈 | Mixtral 8×7B, DeepSeek-V2/V3, Qwen2.5-MoE, DBRX |
| **DeepSeekMoE** | Shared Expert + Fine-grained Routed Experts | 共享专家常驻激活，减少 All-to-All 压力；细粒度专家降低负载不均 | DeepSeek-V2/V3 |
| **Mamba / SSM** | 线性复杂度，RNN 风格状态更新 | 无 KV Cache，但缺少成熟的 TP/PP 策略；硬件适配仍在早期 | Mamba, Mamba-2 |
| **Jamba (Mamba-Transformer Hybrid)** | 交替使用 Mamba 层和 Attention 层 | 兼顾长序列效率和注意力质量；每层计算模式不同，调度复杂 | Jamba (AI21 Labs) |
| **Linear Attention** | 将 softmax 替换为 kernel 分解，O(N) 复杂度 | 训练吞吐高但可能有收敛质量损失；KV 存储形式不同 | RWKV, RetNet |
| **Multi-Token Prediction (MTP)** | 每个位置预测未来 N 个 token | 训练时增加 N 个独立输出头；推理时可做 speculative decoding 的 draft model | Meta MTP, DeepSeek-V3 MTP |
| **Diffusion LLM** | 用扩散模型替代自回归生成 | 非自回归生成，可并行解码；训练和推理的计算模式完全不同 | LLaDA, dLLM |

#### MoE 深入：路由与负载均衡

```
MoE 层的计算流程：
  Input (batch × seq × d_model)
      │
      ▼
  Router / Gate (Linear: d_model → n_experts)
      │
      ▼
  Softmax → Top-k 选择 (通常 k=2)
      │
      ├──▶ Expert 0 ──┐
      ├──▶ Expert 1 ──┤
      ├──▶ Expert 2 ──┤──▶ Weighted Sum → Output
      ├──▶ ...        ──┤
      └──▶ Expert N-1 ──┘
```

| 路由策略 | 原理 | 优缺点 |
|---------|------|--------|
| **Token Choice (Top-k)** | 每个 token 选择 top-k 个 expert | 简单，但可能导致 expert 负载不均（热门 expert 过载） |
| **Expert Choice** | 每个 expert 选择 top-C 个 token | 保证负载均衡，但可能有些 token 不被任何 expert 处理 |
| **Auxiliary Loss** | 额外损失项鼓励均匀路由 | 常用方案，但需要调权重系数 |
| **z-Loss** | 限制 router logit 的大小，防止数值不稳定 | 配合 auxiliary loss 使用 |
| **Shared Expert (DeepSeekMoE)** | 一部分 expert 始终激活，处理所有 token | 减少路由压力，保证基础能力 |

### 1.4 训练算法与效率优化技术

```
训练全流程的计算与访存特征变化：
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Warmup      │     │  Stable      │     │  Decay       │
│  (LR 爬坡)   │────▶│  (恒定/余弦) │────▶│  (LR 衰减)   │
│  Batch size  │     │  混合精度    │     │  模型收敛    │
│  预热期      │     │  FP16/BF16+  │     │  最终质量    │
└──────────────┘     └──────────────┘     └──────────────┘
```

#### 精度管理

| 技术 | 原理 | 系统层代价 |
|------|------|-----------|
| **混合精度训练 (AMP)** | FP32 Master Weights + FP16/FP8 前向/反向 | 维护两份权重；需要 loss scaling 防下溢 |
| **FP8 训练 (Transformer Engine)** | 前向 FP8，反向 FP8，Master FP32 | 需要芯片支持（Hopper+）；量化和反量化 kernel 开销 |
| **BF16 vs FP16** | BF16 动态范围大（同 FP32 指数位），无 loss scaling | BF16 吞吐与 FP16 相同，但精度更低（7bit 尾数 vs 10bit） |

#### 内存优化

| 技术 | 原理 | 效果 |
|------|------|------|
| **Activation Checkpointing** | 不存储中间激活，反向时重算 | 用 ~33% 额外计算换 ~4x 激活显存节省 |
| **Selective Activation Ckpt** | 只重算 attention 部分，保留 MLP 激活 | 比全量重算更快，显存节省略少 |
| **Gradient Accumulation** | 多步 micro-batch 累积再更新 | 增加有效 batch size，不增加显存峰值 |
| **ZeRO-Offload** | 将优化器状态/梯度 offload 到 CPU/NVMe | 用 PCIe 带宽换 GPU 显存 |
| **GaLore** | 用梯度低秩投影减少优化器状态内存 | 全参训练内存减少 ~80%，但有收敛风险 |
| **LoRA / QLoRA** | 只训练低秩适配矩阵，冻结原权重 | 可训练参数 <1%，显存需求极低 |
| **DoRA** | LoRA 的改进：将权重分解为幅度+方向 | 学习模式更接近全参微调 |

#### 计算优化

| 技术 | 原理 | 效果 |
|------|------|------|
| **Sequence Packing** | 将多个短序列拼接为一个长序列，用 attention mask 隔离 | 减少 padding 浪费，吞吐提升 20-50% |
| **Liger Kernel** | 将训练中的多个算子（cross-entropy, rms norm, rope）融合为高效 kernel | 训练吞吐提升 20%+，显存减少 |
| **Unsloth** | 手写 Triton kernel + 精度技巧优化微调 | 微调加速 2-5×，显存减少 50-80% |
| **FlashAttention (训练侧)** | 分块计算 attention，减少 HBM 读写 | 训练吞吐提升 2-4×（长序列更明显） |
| **Async Checkpointing** | 异步写 checkpoint，不阻塞训练步 | 消除 checkpoint 导致的 GPU 空闲 |

#### 训练稳定性

| 技术 | 解决的问题 | 方法 |
|------|-----------|------|
| **Gradient Clipping** | 梯度爆炸 | 按范数截断梯度（阈值通常 1.0） |
| **Loss Spike Recovery** | 训练中突然 loss 飙升 | 回滚到之前 checkpoint，跳过当前 batch |
| **Numerical Stability** | FP16/BF16 下溢出/下溢 | Loss scaling (FP16), BF16 天然优势 |
| **Embedding Normalization** | Embedding 层梯度异常大 | 对 Embedding 输出做 LayerNorm |

---

## 第 1.5 层：Post-Training 与对齐（How to Align）

> Pre-training 产出的是"知识丰富但不会聊天"的 Base Model。Post-Training 将其转化为可安全使用的 Chat/Instruct Model。这是从"能用"到"好用"的关键阶段。

### 1.5.1 Post-Training 全流程

```
Pre-trained Base Model
        │
        ▼
┌──────────────────┐
│  SFT              │  指令微调：让模型学会"对话格式"和"遵循指令"
│  (Supervised      │  数据：高质量的 (instruction, response) 对
│   Fine-Tuning)    │  方法：标准的 next-token prediction loss
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Alignment        │  对齐：让模型的输出符合人类偏好（有用、无害、诚实）
│  ┌──────────────┐ │
│  │ RLHF         │ │  Reward Model + PPO 强化学习
│  │ DPO / ORPO   │ │  直接偏好优化，无需显式 Reward Model
│  │ Constitutional│ │  用 AI 反馈替代人类反馈
│  └──────────────┘ │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Reasoning        │  推理增强：提升模型的复杂推理能力
│  Enhancement      │  Rejection Sampling, Self-Play, GRPO
│  (可选)           │  DeepSeek-R1, OpenAI o1 的技术路线
└──────────────────┘
```

### 1.5.2 SFT：指令微调

| 主题 | 核心内容 |
|------|---------|
| **数据格式** | Chat Template: `<\|system\|>...<\|user\|>...<\|assistant\|>...`；loss 只在 assistant 部分计算 |
| **数据质量 > 数据量** | LIMA 论文证明：1000 条高质量指令数据即可激发模型的指令遵循能力 |
| **数据多样性** | 覆盖对话、写作、代码、数学、推理、安全拒绝等多种场景 |
| **训练策略** | 通常 1-3 epochs，学习率比 pre-training 低 1-2 个数量级 |

### 1.5.3 RLHF：基于人类反馈的强化学习

```
RLHF 三阶段：

阶段 1: Reward Model (RM) 训练
  ┌──────────────────────────────────────────────┐
  │  输入: (prompt, response_A, response_B)       │
  │  人类标注: response_A > response_B            │
  │  训练: 用 Bradley-Terry 模型学习偏好排序      │
  │  输出: RM(prompt, response) → scalar reward   │
  └──────────────────────────────────────────────┘

阶段 2: PPO 强化学习
  ┌──────────────────────────────────────────────┐
  │  Policy: SFT 后的模型（Actor）                │
  │  Reward: RM 打分 + KL penalty（防偏离太远）   │
  │  目标: max E[reward - β × KL(π||π_ref)]      │
  │  需要同时维护: Actor, Reference, RM, Critic   │
  │  → 4 个模型在 GPU 上，显存压力极大            │
  └──────────────────────────────────────────────┘
```

### 1.5.4 DPO 及其变体：无需 Reward Model 的对齐

| 方法 | 原理 | 优缺点 |
|------|------|--------|
| **DPO (Direct Preference Optimization)** | 将 RLHF 的 reward 最大化转化为一个分类 loss，直接在偏好数据上训练 | 简单稳定，但需要成对偏好数据 |
| **ORPO** | 在 SFT loss 中直接加入偏好对齐项，一个阶段完成 SFT+对齐 | 更简单，不需要单独的偏好数据集 |
| **SimPO** | 用序列平均 log-probability 作为隐式 reward，不需要 reference model | 比 DPO 更简单，效果相当或更好 |
| **KTO** | 只需要单条反馈（好/坏），不需要成对比较 | 数据收集成本更低 |

### 1.5.5 推理增强：从 DeepSeek-R1 到 OpenAI o1

> 2024-2025 年最重要的 Post-Training 趋势：通过 RL 和搜索激发模型的"慢思考"能力。

| 技术 | 原理 | 代表工作 |
|------|------|---------|
| **Rejection Sampling** | 对每个 prompt 生成多个 response，用 RM 选最好的做 SFT | DeepSeek-R1 冷启动 |
| **GRPO (Group Relative Policy Optimization)** | PPO 的改进：去掉 Critic 模型，用组内相对 reward 做 baseline | DeepSeek-R1 核心 RL 算法 |
| **Self-Play / Self-Improvement** | 模型生成数据 → 自我验证 → 自我训练 | AlphaGo 风格迁移到 LLM |
| **Test-Time Compute Scaling** | 推理时增加计算量（多步推理、搜索）换取更好的答案 | OpenAI o1/o3 |
| **Constitutional AI** | 用 AI 反馈（而非人类反馈）做对齐，规模化 | Claude 系列 |

### 1.5.6 Post-Training 的系统层挑战

| 挑战 | 描述 | 解决方案 |
|------|------|---------|
| **多模型显存压力** | RLHF 需要同时加载 Actor/Ref/RM/Critic 四个模型 | 模型 offload, 分阶段训练, DPO 替代 |
| **偏好数据收集** | 高质量人类偏好数据昂贵 | AI 偏好标注, Constitutional AI |
| **Reward Hacking** | 模型学会"刷分"而非真正变好 | KL penalty, 多维度 RM, 迭代更新 RM |
| **对齐税 (Alignment Tax)** | 对齐后 benchmark 能力可能下降 | 数据混合, 迭代 SFT+RLHF |

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

  数据并行 (DP) = 8         → 每个 DP 组有同样的数据，不同参数分片
  张量并行 (TP) = 8         → 每层的 Attention/MLP 在 8 张卡内切分
  流水线并行 (PP) = 16       → 模型切 16 段，每段 8 张卡（TP）
  ─────────────────────
  总显卡 = 8 × 8 × 16 = 1024

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

### 2.5 序列并行深入：Ulysses vs Ring Attention

> 当序列长度超过单卡显存容量时，需要将序列切分到多张卡上。

| 方案 | 切分方式 | 通信模式 | 优缺点 |
|------|---------|---------|--------|
| **DeepSpeed Ulysses** | 按 head 维度切分 QKV，每卡持有全部序列的部分 head | All-to-All 在 attention 前后交换 | 通信量小，但需要 head 数 ≥ SP 度数 |
| **Ring Attention** | 按序列维度切分 QKV，每卡持有部分序列的全部 head | 环形 P2P 传递 KV block | 不限制 head 数，但通信延迟随 GPU 数线性增长 |
| **Striped Attention** | 交错切分，每卡持有不连续的序列片段 | 类似 Ring Attention | 负载更均衡 |
| **USP (Unified SP)** | Ulysses + Ring Attention 混合 | 根据序列长度和 head 数动态选择 | Megatron-LM 最新方案 |

### 2.6 ZeRO 系列深入

```
ZeRO 的三个优化级别：

ZeRO-1: 优化器状态分片 (Optimizer State Partitioning)
  ┌──────────────────────────────────────────────────────┐
  │  每个 GPU 只存 1/N 的优化器状态 (Adam 的 m, v)        │
  │  显存节省: ~4× (Adam 状态占 Φ×12 bytes → Φ×12/N)     │
  │  通信: Reduce-Scatter + All-Gather (梯度+参数)        │
  └──────────────────────────────────────────────────────┘

ZeRO-2: + 梯度分片
  ┌──────────────────────────────────────────────────────┐
  │  每个 GPU 只存 1/N 的梯度                             │
  │  显存节省: ~8× (相比无 ZeRO)                          │
  │  通信: 同上，但梯度在 Reduce-Scatter 后即释放         │
  └──────────────────────────────────────────────────────┘

ZeRO-3: + 参数分片
  ┌──────────────────────────────────────────────────────┐
  │  每个 GPU 只存 1/N 的模型参数                         │
  │  前向/反向时需要 All-Gather 收集完整参数              │
  │  显存节省: ~N× (线性随 GPU 数扩展)                    │
  │  通信量: ~1.5Φ per step (比 DP 的 2Φ 略少)           │
  │  代价: 通信频率极高，对带宽敏感                       │
  └──────────────────────────────────────────────────────┘
```

| 配置 | 单卡显存占用 (Φ=参数量, N=GPU数, Ψ=优化器状态因子) | 通信量/step |
|------|---------------------------------------------------|------------|
| **无 ZeRO (纯 DP)** | Φ × (2 + 2 + 12) = 16Φ bytes (FP16) | 2Φ |
| **ZeRO-1** | Φ × (2 + 2) + 12Φ/N | ~2Φ |
| **ZeRO-2** | Φ × 2 + (2Φ + 12Φ)/N | ~2Φ |
| **ZeRO-3** | (2Φ + 2Φ + 12Φ)/N = 16Φ/N | ~1.5Φ |

### 2.7 通信-计算 Overlap 策略

| 策略 | 原理 | 实现 |
|------|------|------|
| **Gradient Bucketing** | 将梯度按层分组，一组计算完立即开始通信 | PyTorch DDP 默认行为 |
| **Async All-Reduce** | 反向传播和梯度 All-Reduce 并行 | NCCL 异步 API |
| **Overlap All-Gather with Forward** | 前向计算当前层时，预取下一层的参数 | ZeRO-3 / FSDP 的 `prefetch` 机制 |
| **Communication Scheduling** | 将通信和计算按依赖关系交错调度 | Megatron-LM, DeepSpeed 的 schedule 优化 |

### 2.8 自动并行策略搜索

| 工具 | 原理 | 特点 |
|------|------|------|
| **Alpa** | 用整数规划自动搜索最优 (DP, TP, PP) 组合 | 学术项目，覆盖全面 |
| **FlexFlow** | 基于模拟的自动并行搜索 | 支持非标准并行模式 |
| **Galvatron** | 考虑通信-计算 overlap 的自动搜索 | 更贴近实际性能 |
| **手动经验法则** | TP 优先填满 NVSwitch 域 → PP 跨节点 → DP 填满剩余 | 工业界最常用 |

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
| **FlashAttention-3** | Hopper 架构利用率不足 | 利用 WGMMA 指令 + TMA 异步拷贝，warp-group 级调度 | Dao-AILab (2024) |
| **FlexAttention** | 不同 Attention 变体需要不同 kernel | PyTorch 2.5+ 的可组合 Attention API，在 Python 层定义 score_mod | PyTorch 官方 |
| **Flash-Decoding** | Decode 阶段长 KV Cache | 将 KV Cache 沿 sequence 维度分块并行，最后的 softmax reduction | FlashInfer / vLLM |
| **PagedAttention** | KV Cache 显存碎片 | 将 KV Cache 按 page (block) 管理，类似 OS 虚拟内存 | vLLM 核心创新 |
| **FlashInfer** | 统一 Attention Kernel 库 | 为 Prefill / Decode / Append 等不同阶段提供对应的优化 kernel | FlashInfer |
| **Activation Ckpt (Cuda-side)** | 反向时的激活重算 | Selectively recompute；与 PyTorch autograd hooks 结合 | Megatron-LM |
| **LayerNorm/RMSNorm Fusion** | 多次小 kernel launch overhead | 将 LayerNorm 与其后的 GEMM 融合为一个 kernel | Apex / TorchDynamo |

### 3.3 量化技术全景

> 量化是推理优化的核心技术，通过降低精度来减少显存占用和提升吞吐。

#### 权重量化 (Weight Quantization)

| 方法 | 精度 | 原理 | 特点 |
|------|------|------|------|
| **GPTQ** | INT4/INT8 | 基于 Hessian 矩阵的逐层最优量化 | 需要校准数据，量化速度慢但效果好 |
| **AWQ** | INT4 | 识别"显著权重"通道并保留其精度 | 比 GPTQ 更快，效果相当 |
| **SmoothQuant** | INT8 (W8A8) | 将 activation 的量化难度"平滑"到 weight 上 | 同时量化权重和激活，适合 GEMM |
| **SpQR** | INT4 + 稀疏 FP16 | 对异常值单独保留高精度 | 精度损失极小，但压缩率略低 |
| **BitsAndBytes (NF4)** | NF4 (4-bit NormalFloat) | 信息论最优的 4-bit 数据类型 | QLoRA 的基础，HuggingFace 集成 |
| **GGUF / GGML** | INT4/INT5/INT8 | 混合精度 + CPU 推理优化 | llama.cpp 生态，消费级硬件推理 |

#### KV Cache 量化

| 方法 | 原理 | 压缩率 |
|------|------|--------|
| **KIVI** | 对 Key 按 channel 量化，Value 按 token 量化 | ~4× KV Cache 压缩 |
| **KVQuant** | 非均匀量化 + 异常值隔离 | ~4-8× 压缩 |
| **ZipCache** | 基于 token 重要性的自适应精度分配 | ~4× 压缩 |

#### 激活量化 (Activation Quantization)

| 方法 | 原理 | 挑战 |
|------|------|------|
| **SmoothQuant** | per-channel weight scaling + per-token activation scaling | 激活的异常值比权重更难处理 |
| **FP8 推理** | 利用 Hopper+ 的 FP8 Tensor Core | 动态范围比 INT8 大，无需复杂的 calibration |
| **LLM.int8()** | 混合精度：大部分 INT8 + 异常值 FP16 | 推理精度几乎无损 |

### 3.4 编译器与代码生成栈

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

### 3.5 torch.compile 与 TorchDynamo

```
torch.compile 的工作流程：

  PyTorch 代码 (eager mode)
        │
        ▼
  TorchDynamo (JIT 捕获)
  ┌──────────────────────────────────────────────┐
  │  在 Python bytecode 层面拦截执行             │
  │  捕获 PyTorch 操作 → 构建 FX Graph           │
  │  遇到无法捕获的操作 → graph break            │
  └──────────────────────────────────────────────┘
        │
        ▼
  TorchInductor (后端编译)
  ┌──────────────────────────────────────────────┐
  │  将 FX Graph 编译为 Triton / C++ / CUDA      │
  │  默认后端: Triton (生成 .py → JIT → PTX)     │
  │  优化: 算子融合, 内存规划, tiling 决策       │
  └──────────────────────────────────────────────┘
        │
        ▼
  编译后的 kernel (Triton → PTX → SASS)
```

| 特性 | 说明 |
|------|------|
| **`torch.compile(model, mode="reduce-overhead")`** | 适合 LLM 推理，用 CUDA Graph 消除 kernel launch overhead |
| **`torch.compile(model, mode="max-autotune")`** | 自动搜索最优 tiling 配置，编译慢但运行快 |
| **Graph Break** | 遇到 data-dependent control flow 时编译中断，回退到 eager |
| **Dynamic Shapes** | 支持可变 batch/seq 长度，但可能影响优化效果 |

### 3.6 推理引擎对比

| 推理引擎 | 核心优化技术 | 适用场景 | 关键创新 |
|---------|-------------|---------|---------|
| **vLLM** | PagedAttention, Continuous Batching, Prefix Caching | 高吞吐在线服务 | PagedAttention 解决了 KV Cache 碎片 |
| **TensorRT-LLM** | 图优化 + FP8/INT4 量化 + 多节点推理 | NVIDIA 生态最优性能 | 深度编译器优化，与 GPU 特性紧耦合 |
| **SGLang** | RadixAttention (Prefix 自动复用) + Structured Output | 长 System Prompt, 复杂编程 Agent | Prefix Caching 粒度比 vLLM 更细 |
| **llama.cpp** | GGUF 量化格式 + CPU/GPU 混合推理 | 消费级设备部署 | 极致的量化方案 + 跨平台 |
| **Ollama** | 封装 llama.cpp + 一键部署 | 本地 LLM 体验 | 易用性，但性能不如专业引擎 |

### 3.7 KV Cache 管理

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

## 第 3.5 层：推理服务架构（How to Serve）

> 单个推理 Kernel 的优化只是起点。将多个优化技术组合成一个高吞吐、低延迟的生产级服务系统，是推理工程的真正挑战。

### 3.5.1 Continuous Batching

```
传统 Static Batching vs Continuous Batching:

Static Batching:
  ┌─────────────────────────────────────────────────────┐
  │  Request 1: [████████████████]  (12 tokens)         │
  │  Request 2: [████████]          (8 tokens)          │
  │  Request 3: [██████████████████](16 tokens)         │
  │                                                     │
  │  → 必须等所有 request 完成才能组新 batch            │
  │  → 短 request 完成后 GPU 空闲等待长 request         │
  └─────────────────────────────────────────────────────┘

Continuous Batching (vLLM / TGI):
  ┌─────────────────────────────────────────────────────┐
  │  Step 1: [R1, R2, R3] → 各生成 1 token              │
  │  Step 2: R2 完成 → 立即加入 R4                      │
  │  Step 3: [R1, R3, R4] → 各生成 1 token              │
  │  Step 4: R1 完成 → 加入 R5                          │
  │  ...                                                 │
  │  → 每个 step 动态调整 batch，GPU 持续满载           │
  └─────────────────────────────────────────────────────┘
```

| 调度策略 | 原理 | 适用场景 |
|---------|------|---------|
| **FCFS (First-Come-First-Served)** | 按到达顺序处理 | 简单但可能长请求阻塞短请求 |
| **Priority-based** | 高优先级请求插队 | 交互式 + 批处理混合场景 |
| **Preemption** | 长请求被抢占，KV Cache 暂存/重算 | 保证延迟 SLO |
| **Chunked Prefill** | 将长 Prefill 拆成多个 chunk，与 decode 交替 | 减少 Prefill 对 decode 的阻塞 |

### 3.5.2 Disaggregated Prefill/Decode

> 2024 年最重要的推理架构创新：将 Prefill 和 Decode 分离到不同的 GPU 上。

```
传统 Colocated 架构:
  ┌──────────────────────────────────────┐
  │  GPU 0: Prefill + Decode             │
  │  GPU 1: Prefill + Decode             │
  │  → Prefill 的 compute burst 影响     │
  │    Decode 的延迟 (TTFT 和 TPOT 耦合) │
  └──────────────────────────────────────┘

Disaggregated 架构 (Splitwise / DistServe):
  ┌─────────────────┐   ┌─────────────────┐
  │  Prefill Pool    │   │  Decode Pool     │
  │  GPU 0,1 (高算力)│──▶│  GPU 2,3,4,5     │
  │  compute-bound   │   │  memory-bound    │
  └─────────────────┘   └─────────────────┘
  → Prefill 和 Decode 独立扩缩容
  → Prefill 用 HBM 小的 GPU, Decode 用 HBM 大的 GPU
  → 代价: KV Cache 需要跨 GPU 传输
```

| 方案 | 核心思想 | 代表工作 |
|------|---------|---------|
| **Splitwise** | Prefill/Decode 分离 + 不同 GPU 配比 | Meta (2024) |
| **DistServe** | 分离 + 独立的 scaling 策略 | PKU (2024) |
| **Mooncake** | 分离 + KV Cache 传输优化 | Moonshot AI / DeepSeek |
| **Tetriserve** | Prefill 用旧 GPU, Decode 用新 GPU | 异构硬件利用 |

### 3.5.3 Prefix Caching 与 KV Cache 复用

| 策略 | 原理 | 代表实现 |
|------|------|---------|
| **Automatic Prefix Caching (APC)** | 自动检测相同 prefix 并复用 KV Cache | vLLM |
| **RadixAttention** | 用 Radix Tree 管理 KV Cache，支持前缀匹配 | SGLang |
| **Prompt Cache** | 预先计算常见 System Prompt 的 KV Cache | 各引擎通用 |
| **Multi-turn Cache** | 多轮对话中复用历史 KV Cache | vLLM, SGLang |

### 3.5.4 Speculative Decoding（投机解码）

> 用小模型"猜"多个 token，大模型并行验证，将串行 decode 变为并行。

```
标准自回归解码 (每步 1 token):
  Model → token_1 → Model → token_2 → Model → token_3 → ...
  延迟 = N × (单次 forward 延迟)

Speculative Decoding:
  Draft Model (小模型) → 快速生成 K 个候选 token
  Target Model (大模型) → 一次 forward 并行验证 K 个 token
  接受 n 个正确 token (n ≤ K)，拒绝的重新生成
  延迟 ≈ N/n × (单次 forward 延迟)  → 加速 n×
```

| 方法 | Draft 模型来源 | 特点 |
|------|---------------|------|
| **Leviathan (Google)** | 独立的小模型 | 需要额外训练/部署一个小模型 |
| **Medusa** | 在原模型上加多个预测头 | 不需要独立 draft model |
| **Eagle** | 用 feature-level 信息预测 | 比 Medusa 更准确 |
| **Self-Speculative** | 跳过部分层作为 draft | 不需要额外模型，但加速比有限 |
| **Lookahead Decoding** | 用 Jacobi 迭代并行生成 | 不需要 draft model，纯算法技巧 |

### 3.5.5 推理服务的全局架构

```
┌─────────────────────────────────────────────────────────────────┐
│                      推理服务全局架构                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐                    │
│  │  API      │   │  Load    │   │  Router  │   ← 前端层          │
│  │  Gateway  │──▶│  Balancer│──▶│  (SGLang │                    │
│  │           │   │          │   │   /vLLM) │                    │
│  └──────────┘   └──────────┘   └────┬─────┘                    │
│                                     │                           │
│         ┌───────────────────────────┼───────────────────┐      │
│         │                           │                    │      │
│         ▼                           ▼                    ▼      │
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────┐          │
│  │  GPU Node 0 │   │  GPU Node 1 │   │  GPU Node N │  ← 推理层 │
│  │  ┌────────┐ │   │  ┌────────┐ │   │  ┌────────┐ │          │
│  │  │vLLM    │ │   │  │vLLM    │ │   │  │vLLM    │ │          │
│  │  │TP=8    │ │   │  │TP=8    │ │   │  │TP=8    │ │          │
│  │  └────────┘ │   │  └────────┘ │   │  └────────┘ │          │
│  └─────────────┘   └─────────────┘   └─────────────┘          │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  监控与可观测性: Prometheus + Grafana                     │  │
│  │  指标: TTFT, TPOT, QPS, GPU 利用率, KV Cache 命中率      │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 第四层：芯片微架构层（Where to Execute）

> 软件优化的极限受限于硬件能力边界。这一层回答"芯片上到底发生了什么"。

> 📎 **已有深度覆盖**：[11-NVIDIA-GPU架构演进与LLM.md](./11-NVIDIA-GPU架构演进与LLM.md) 从 Tesla 到 Blackwell 的完整架构演进，包含 SM、Tensor Core、TMA、Cluster 等关键概念

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
  │              NVSwitch (全互联, 3.6 TB/s)                 │
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
| **NVSwitch 2.0** | 3.6 TB/s (全双工) | 多层 fat-tree / Clos | 域内任意 GPU 间等带宽 |
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

### 5.5 非 NVIDIA 硬件生态对比

> 以 NVIDIA 为主线学习，但需要了解替代方案的存在和差异。

| 硬件平台 | 关键特征 | 软件栈 | 对 LLM 的影响 |
|---------|---------|--------|--------------|
| **AMD MI300X** | 192GB HBM3, 5.3TB/s, CDNA3 | ROCm + HIP | 显存容量优势明显，推理场景有竞争力；软件生态仍在追赶 |
| **AMD MI325X** | 256GB HBM3e, 6TB/s | ROCm | 2025 年推理最强单卡显存 |
| **Google TPU v5p/v6** | 专用 systolic array, ICI 互联 | JAX/XLA 原生 | 编译器依赖极重，灵活性换效率；Gemini 的训练硬件 |
| **Intel Gaudi 3** | 集成 RoCE (无需外部 NIC), 128GB HBM2e | PyTorch 原生 (Habana) | 网络架构不同，并行策略需重新设计 |
| **Apple Silicon (M 系列)** | 统一内存架构 (UMA), 最高 192GB | MLX, CoreML, llama.cpp | 推理场景有独特优势，大显存 + 低功耗 |
| **Cerebras CS-3** | Wafer-Scale 芯片, 900,000 cores, 44GB SRAM | 编译器驱动 | 编程模型完全不同，适合特定负载 |
| **Groq LPU** | 确定性数据流架构, SRAM only | 编译器决定一切 | 极低延迟推理，但显存容量受限 |
| **AWS Trainium2** | 96GB HBM, 专为训练优化 | AWS Neuron SDK | 云原生，与 SageMaker 深度集成 |
| **华为昇腾 910B** | 64GB HBM2e, 达芬奇架构 | CANN / MindSpore | 国产替代主力，软件生态差距大 |

### 5.6 互联技术更新 (2024-2025)

| 技术 | 带宽 | 关键变化 |
|------|------|---------|
| **NVLink 5.0 (Blackwell)** | 1.8 TB/s (双向) | 比 NVLink 4.0 翻倍 |
| **NVSwitch 3.0** | 7.2 TB/s (域内全双工) | 支持 8 GPU 域内全互联 (DGX B200) |
| **NVSwitch 4.0** | 14.4 TB/s (域内全双工) | 支持 72 GPU 域内全互联 (GB200 NVL72) |
| **Ultra Ethernet Consortium (UEC)** | 目标 800G/1.6T | 开放标准替代 InfiniBand |
| **Spectrum-X (NVIDIA)** | 400G | NVIDIA 的以太网方案，与 InfiniBand 互补 |
| **PCIe 6.0** | 128 GB/s (×16) | 2025 年落地，带宽翻倍 |
| **CXL 3.0** | 64 GB/s | 内存池化与共享，对 GPU 显存扩展有潜在影响 |

---

## 评估与度量体系

> 没有度量就没有优化。理解评估体系才能判断"做得好不好"。

### 评估指标

#### 训练指标

| 指标 | 定义 | 目标 |
|------|------|------|
| **Loss (Training/Validation)** | 交叉熵损失 | 持续下降，无 spike |
| **Perplexity (PPL)** | exp(loss)，可解释性更强 | 越低越好 |
| **Throughput** | tokens/s/GPU 或 samples/s | 越高越好 |
| **MFU (Model FLOPs Utilization)** | 实际 FLOPs / 理论峰值 FLOPs | 训练 >50% 为优秀 |
| **Gradient Norm** | 梯度的 L2 范数 | 稳定在合理范围，无爆炸 |
| **Time to First Token (训练)** | 从开始训练到第一个有意义的输出 | 快速验证模型设计 |

#### 推理指标

| 指标 | 定义 | 目标 |
|------|------|------|
| **TTFT (Time to First Token)** | 从请求到第一个 token 的时间 | <100ms (交互式) |
| **TPOT (Time per Output Token)** | 每个输出 token 的平均时间 | <50ms (交互式) |
| **QPS (Queries per Second)** | 每秒处理的请求数 | 越高越好 |
| **Throughput (tokens/s)** | 每秒生成的 token 总数 | 越高越好 |
| **P50/P95/P99 Latency** | 延迟分位数 | P99 < 2× P50 |
| **KV Cache Hit Rate** | Prefix Caching 命中率 | 越高越好 |

### Benchmark 体系

| 类别 | Benchmark | 评估维度 |
|------|----------|---------|
| **综合知识** | MMLU, MMLU-Pro | 57 个学科的多选题 |
| **推理** | GSM8K, MATH, ARC | 数学推理、科学推理 |
| **代码** | HumanEval, MBPP, LiveCodeBench | 代码生成与理解 |
| **语言理解** | HellaSwag, WinoGrande, PIQA | 常识推理 |
| **事实性** | TruthfulQA, SimpleQA | 幻觉检测 |
| **对话质量** | AlpacaEval, MT-Bench, Chatbot Arena | 人类偏好对齐 |
| **长上下文** | Needle-in-a-Haystack, RULER, LongBench | 长文本理解 |
| **安全** | HarmBench, AdvBench | 安全性与鲁棒性 |

### Scaling Laws

| 定律 | 核心结论 | 实践意义 |
|------|---------|---------|
| **Kaplan et al. (2020)** | Loss ∝ N^(-0.076) × D^(-0.095) × C^(-0.057) | 模型大小比数据量更重要 |
| **Chinchilla (2022)** | 最优: tokens ≈ 20× parameters | 数据量比之前认为的更重要；70B 模型需要 1.4T tokens |
| **Emergent Abilities** | 某些能力在模型达到一定规模后突然涌现 | 不能仅从小模型实验推断大模型行为 |
| **Scaling Laws for MoE** | MoE 的 scaling 规律与 Dense 不同 | 需要独立的 scaling 实验 |

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

难度 Level 4: Post-Training 与对齐   Estimated: 2-3 weeks
├── 用 LoRA/QLoRA 对 Llama-3-8B 做 SFT
├── 用 DPO 做偏好对齐（对比 SFT 前后的输出质量）
├── 体验 Unsloth 加速微调
└── 用 vLLM 部署微调后的模型并对比 Base Model

难度 Level 5: Kernel 与编译器        Estimated: 4-6 weeks
├── 用 Triton 写一个 Fused MLP kernel（替代 PyTorch 多个 kernel）
├── 手动实现一个简化版 FlashAttention (online softmax + tiling)
├── 用 torch.compile + inductor backend 观察生成的 Triton 代码
├── 对比手写 CUDA kernel vs Triton kernel 的性能
└── 阅读 FlashAttention-2 源码 + PagedAttention 源码

难度 Level 6: 推理服务与性能调优      Estimated: 4-6 weeks
├── 用 vLLM 部署 Llama-3-70B 并压测 (不同 QPS / input len / output len)
├── 对比不同并行策略 (vLLM TP vs Ray Serve DP) 的吞吐
├── 做一次 Prefix Caching 效果的量化分析
├── 用 TensorRT-LLM 部署并对比 vLLM 性能
├── 理解 Continuous Batching 的调度策略实现
└── 尝试 Speculative Decoding (Medusa / Eagle)

难度 Level 7: 数据工程实践            Estimated: 2-3 weeks
├── 用 DataTrove / text-dedup 做一次完整的网页数据清洗
├── 训练一个 BPE Tokenizer 并对比不同词表大小的效果
├── 用 Self-Instruct 生成 SFT 数据
└── 分析数据配比对小模型训练效果的影响
```

---

## 分阶段学习计划

### Phase 1: 单卡视角，建立端到端直觉（优先：第〇层 + 第一层 + 第三层浅层）

| 序号 | 学习内容 | 已有资源 | 产出 |
|------|---------|---------|------|
| 1.1 | 回顾 Transformer 完整结构（不只是 Attention）| [03-LLM注意力机制发展](./03-LLM注意力机制发展与演进.md) | 笔记补全 MLP / LayerNorm / RoPE 部分 |
| 1.2 | 用 PyTorch 手写 GPT-2 的完整 forward pass | - | 代码 |
| 1.3 | torch.profiler 分析一次训练的 GPU 时间线 | - | 分析笔记 |
| 1.4 | 实现 KV Cache 解码循环，观察每个 step 的耗时 | - | 代码 + 笔记 |
| 1.5 | 了解数据清洗 pipeline 的基本流程 | - | 笔记 |

### Phase 2: 多卡训练，理解分布式（优先：第二层全面学习）

| 序号 | 学习内容 | 潜在文档产出 | 关联 |
|------|---------|-------------|------|
| 2.1 | NCCL 通信原语: All-Reduce 的 Ring/Tree 实现 | 分布式系统笔记 | 第二层 |
| 2.2 | Megatron-LM 论文: TP → SP → PP 的组合逻辑 | 分布式系统笔记 | 第二层 |
| 2.3 | DeepSpeed ZeRO 论文三篇: ZeRO-1→2→3→Offload | - | 第二层 |
| 2.4 | 实际搭建 7B 模型的 DDP/FSDP 训练 | - | 实操 |
| 2.5 | 3D 并行的通信量分析与配置公式推导 | - | 第二层 |

### Phase 3: Post-Training 与对齐（优先：第 1.5 层）

| 序号 | 学习内容 | 产出 |
|------|---------|------|
| 3.1 | SFT 实践：用 LoRA/QLoRA 微调 Llama-3-8B | 代码 + 笔记 |
| 3.2 | DPO 论文精读与实践 | 论文笔记 |
| 3.3 | RLHF 完整流程理解（Reward Model + PPO） | 笔记 |
| 3.4 | DeepSeek-R1 技术报告精读（GRPO, Rejection Sampling） | 论文笔记 |
| 3.5 | 对比 SFT vs DPO vs RLHF 的效果差异 | 实验报告 |

### Phase 4: 深入算子，理解硬件约束（优先：第三层 + 第四层）

| 序号 | 学习内容 | 已有资源 | 产出 |
|------|---------|---------|------|
| 4.1 | FlashAttention 论文精读 (1/2/3) | - | 论文笔记 |
| 4.2 | [GPU 架构演进](./11-NVIDIA-GPU架构演进与LLM.md) 与算子优化的映射 | - | 交叉分析笔记 |
| 4.3 | 用 Triton 手写 FusedAttention kernel | - | 代码 |
| 4.4 | NSight Compute 分析单个 kernel 的 SM 利用率 / 访存效率 | - | 分析笔记 |
| 4.5 | PagedAttention / FlashInfer 源码阅读 | - | 源码笔记 |

### Phase 5: 推理部署，产业链落地（优先：第 3.5 层）

| 序号 | 学习内容 | 产出建议 |
|------|---------|---------|
| 5.1 | vLLM Continuous Batching 调度原理 | 笔记 |
| 5.2 | TensorRT-LLM 图编译流程 | 笔记 |
| 5.3 | Speculative Decoding / Medusa / Eagle 等投机解码技术 | 笔记 |
| 5.4 | 量化技术全景: GPTQ / AWQ / SmoothQuant / FP8 推理 | 独立文档 |
| 5.5 | Disaggregated Serving 架构理解与实践 | 笔记 |

### Phase 6: 芯片前沿 + 全栈串联（优先：第四层 + 第五层）

| 序号 | 学习内容 | 产出建议 |
|------|---------|---------|
| 6.1 | Hopper → Blackwell 的推理/训练新特性 & 论文对应关系 | 补充 GPU 文档 |
| 6.2 | 集群网络拓扑对训练效率的影响量化 | 笔记 |
| 6.3 | 非 NVIDIA 芯片的软件栈适配难度评估 | 笔记 |
| 6.4 | 自己设计一个"最小化全栈 demo"（模型 + TP/PP + Kernel 优化）| 代码 |
| 6.5 | 完整 Benchmark 评估实践（MMLU, HumanEval, Chatbot Arena 等） | 实验报告 |

---

## 文档体系建议（建议产出的笔记列表）

结合已有文档和建议补充的新文档，形成一个完整的笔记体系：

```
LLM/
├── 00-LLM训练推理全景学习框架.md                ✅ 总索引+框架
├── 01-LLM数据工程：从采集到Tokenization.md       ✅ 第〇层
├── 02-Transformer完整结构与训练算法.md           ✅ 第一层-补充
├── 03-LLM注意力机制发展与演进.md                 ✅ 第一层-深入
├── 04-LLM Post-Training：SFT、RLHF与DPO.md       ✅ 第1.5层
├── 05-LLM分布式训练：并行策略与ZeRO.md           ✅ 第二层
├── 06-FlashAttention论文精读与Triton实践.md      ✅ 第三层
├── 07-LLM量化技术：PTQ、QAT到FP8推理.md          ✅ 第三层
├── 08-LLM推理引擎：vLLM到TensorRT-LLM.md         ✅ 推理引擎
├── 09-LLM推理服务架构：调度、缓存与投机解码.md    ✅ 第3.5层
├── 10-GPU集群互联：NVLink到InfiniBand.md         ✅ 第五层
├── 11-NVIDIA-GPU架构演进与LLM.md                 ✅ 第四层-深入
├── 12-非NVIDIA AI芯片与软件栈对比.md             ✅ 第五层-补充
├── 13-LLM评估体系与Scaling Laws.md               ✅ 评估
└── 14-LLM推理性能调优实战.md                     ✅ 端到端
```

---

> **学习原则**：
> 1. **问题驱动**：每学一个技术，问"它解决了之前哪个瓶颈？引入了什么新矛盾？"
> 2. **数据流视角**：时刻追踪"这个操作中，数据从哪来、到哪去、搬运成本是多少？"
> 3. **跨层关联**：软件优化（GQA）→ 硬件（HBM 读少了）→ 系统（TP 切分变了），形成三维关联
> 4. **动手优先**：概念看懂 30% 就开始动手跑代码，benchmark 数据比理论讨论更有说服力
> 5. **全链路思维**：从数据采集 → Pre-training → Post-Training → 推理部署，理解每个环节的输入输出和瓶颈
</parameter>
</invoke>
</tool_calls>