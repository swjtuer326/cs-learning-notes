# Transformer 完整结构与训练算法

> **核心命题**：Transformer 不只是 Attention。理解完整的 Transformer 结构——从 Embedding 到 LM Head，从 Pre-Norm 到 Post-Norm，从 SGD 到 AdamW——是深入 LLM 训练和推理的基础。

## 目录

1. [Transformer 完整架构回顾](#transformer-完整架构回顾)
2. [Embedding 层](#embedding-层)
3. [位置编码深入](#位置编码深入)
4. [Attention 机制变体](#attention-机制变体)
5. [MLP / FFN 层](#mlp--ffn-层)
6. [归一化层](#归一化层)
7. [LM Head 与损失函数](#lm-head-与损失函数)
8. [Pre-Norm vs Post-Norm](#pre-norm-vs-post-norm)
9. [训练算法全景](#训练算法全景)
10. [优化器深入](#优化器深入)
11. [学习率调度](#学习率调度)
12. [混合精度训练](#混合精度训练)
13. [训练稳定性技术](#训练稳定性技术)

---

## Transformer 完整架构回顾

### 1.1 标准 Decoder-only 架构 (GPT/Llama 风格)

```
输入 Token IDs: [t1, t2, t3, ..., tn]
         │
         ▼
    ┌─────────────┐
    │  Embedding   │  Token Embedding (vocab_size × d_model)
    │  Layer       │  参数: V × d_model
    └──────┬──────┘
           │
           ▼
    ┌─────────────┐
    │  RMS Norm    │  Pre-Norm (Llama 风格)
    └──────┬──────┘
           │
           ▼
    ┌─────────────┐
    │  Attention   │  Multi-Head / GQA / MQA
    │  Block       │  Q, K, V 投影 + Attention + O 投影
    └──────┬──────┘
           │
           ├──────────────────┐  Residual Connection
           ▼                  │
    ┌─────────────┐           │
    │  RMS Norm    │           │
    └──────┬──────┘           │
           │                  │
           ▼                  │
    ┌─────────────┐           │
    │  FFN / MLP   │  Gate + Up + Down (SwiGLU)
    │  Block       │  或 fc1 + fc2 (传统)
    └──────┬──────┘           │
           │                  │
           ├──────────────────┘  Residual Connection
           ▼
    ┌─────────────┐
    │  RMS Norm    │  Final Norm
    └──────┬──────┘
           │
           ▼
    ┌─────────────┐
    │  LM Head     │  Linear: d_model → vocab_size
    │  (Output)    │  通常与 Embedding 共享权重 (Weight Tying)
    └─────────────┘
           │
           ▼
    Logits: [n, vocab_size]
```

### 1.2 参数量计算

```
Llama 风格模型的参数量公式:

设: V = vocab_size, d = d_model, L = num_layers
    h = num_heads, d_h = head_dim (d = h × d_h)
    d_ff = FFN intermediate size

1. Embedding: V × d
2. Per Transformer Layer:
   a. Attention:
      - Q 投影: d × d
      - K 投影: d × (h_kv × d_h)  ← GQA 时 h_kv < h
      - V 投影: d × (h_kv × d_h)
      - O 投影: d × d
      总计: 2d² + 2d × h_kv × d_h
   
   b. FFN (SwiGLU):
      - Gate: d × d_ff
      - Up:   d × d_ff
      - Down: d_ff × d
      总计: 3d × d_ff
   
   c. RMS Norm: 2 × d (Attention Norm + FFN Norm)
   
   每层总计: 2d² + 2d × h_kv × d_h + 3d × d_ff + 2d

3. Final RMS Norm: d
4. LM Head: d × V (通常与 Embedding 共享)

总参数量 ≈ V×d + L×(2d² + 2d×h_kv×d_h + 3d×d_ff) + d + d×V
         ≈ 2Vd + L×(2d² + 2d×h_kv×d_h + 3d×d_ff)

示例: Llama-3-8B
  V=128256, d=4096, L=32, h=32, h_kv=8, d_h=128, d_ff=14336
  Embedding: 128256×4096 ≈ 525M
  Per Layer: 2×4096² + 2×4096×8×128 + 3×4096×14336 ≈ 218M
  Total: 525M + 32×218M ≈ 7.5B (加上 LM Head 共享 ≈ 8.0B)
```

---

## Embedding 层

### 2.1 Token Embedding

```
Token Embedding 矩阵: E ∈ R^{V × d}

每个 token ID i 对应一个 d 维向量 E[i]

前向: x = E[token_ids]  # [batch, seq_len, d]

梯度: ∂L/∂E[i] 只在 token i 出现的位置非零
     → Embedding 行的更新频率与 token 频率成正比
     → 高频 token 的 embedding 更新充分，低频 token 欠拟合
```

### 2.2 Weight Tying (权重共享)

```
Weight Tying: Embedding 和 LM Head 共享权重矩阵

LM Head: W_lm ∈ R^{V × d}
Embedding: W_emb ∈ R^{V × d}

共享: W_lm = W_emb^T (或直接使用同一个矩阵)

优点:
- 减少参数量: V×d (对于 Llama-3-8B 约 525M 参数)
- 正则化效果: 防止过拟合
- 语义一致性: 输入和输出使用相同的 token 表示

缺点:
- 约束了 LM Head 和 Embedding 的表达能力
- 大词表时共享可能不是最优
```

---

## 位置编码深入

### 3.1 绝对位置编码

#### Sinusoidal (原始 Transformer)

```
PE(pos, 2i)   = sin(pos / 10000^(2i/d))
PE(pos, 2i+1) = cos(pos / 10000^(2i/d))

特点:
- 固定编码，不参与训练
- 通过三角函数性质，PE(pos+k) 可表示为 PE(pos) 的线性函数
- 外推能力有限 (训练长度外的位置效果差)
```

#### Learned Position Embedding (GPT-2)

```
直接学习一个位置嵌入矩阵 P ∈ R^{max_seq_len × d}

x = E[token_ids] + P[positions]

缺点:
- 无法处理超过 max_seq_len 的序列
- 需要为每个位置学习独立的嵌入
```

### 3.2 RoPE (Rotary Position Embedding)

> 当前 LLM 的事实标准，Llama、Mistral、Qwen、DeepSeek 均使用。

```
RoPE 核心思想: 通过旋转矩阵将位置信息编码到 Q 和 K 中

对于位置 m 的向量 x ∈ R^d，将其分成 d/2 对 (x_0, x_1), (x_2, x_3), ...

对第 i 对应用旋转:
  [cos(m·θ_i)  -sin(m·θ_i)] [x_{2i}  ]
  [sin(m·θ_i)   cos(m·θ_i)] [x_{2i+1}]

其中 θ_i = 10000^(-2i/d)

关键性质:
  RoPE(q_m, m)^T · RoPE(k_n, n) = q_m^T · R_{n-m} · k_n
  
  → Attention score 只依赖于相对位置 (n-m)
  → 兼具绝对位置编码的便利和相对位置编码的泛化能力
```

**RoPE 的频率与长度外推**：

```
RoPE 的 θ_i 决定了不同维度对位置的敏感度:
- 低维度 (大 θ): 高频 → 对短距离位置变化敏感
- 高维度 (小 θ): 低频 → 对长距离位置变化敏感

外推问题: 训练长度 L_train 外的位置，高频维度没有见过
→ 直接外推效果差

解决方案:
1. Linear Scaling (Position Interpolation):
   θ_i' = θ_i / scale_factor
   → 压缩频率，使训练长度内的旋转角度覆盖更长的范围

2. NTK-aware Scaling:
   高频维度不缩放，低频维度按 NTK 理论缩放
   → 保留短距离分辨能力，扩展长距离

3. YaRN (NTK + temperature):
   在 NTK 基础上引入温度系数调整 attention score
```

### 3.3 ALiBi (Attention with Linear Biases)

```
ALiBi: 在 Attention Score 上加上线性偏置

Score(Q, K) = QK^T/√d - m × |i - j|

其中 m 是 head-specific 的斜率:
  m = 2^(-8 × h/H)  (h 是 head 索引, H 是总 head 数)

特点:
- 不需要位置编码
- 天然支持外推 (Bloom 使用)
- 但效果不如 RoPE (LLM 社区共识)
```

---

## Attention 机制变体

### 4.1 MHA → MQA → GQA

```
MHA (Multi-Head Attention):
  每个 head 有独立的 Q, K, V
  Q: [batch, seq, h, d_h]
  K: [batch, seq, h, d_h]
  V: [batch, seq, h, d_h]
  
  KV Cache 大小: 2 × batch × seq × h × d_h × 2bytes

MQA (Multi-Query Attention):
  所有 head 共享一组 K, V
  Q: [batch, seq, h, d_h]
  K: [batch, seq, 1, d_h]  ← 只有 1 个 KV head
  V: [batch, seq, 1, d_h]
  
  KV Cache 大小: 2 × batch × seq × 1 × d_h × 2bytes
  → 减少 h 倍! 但质量有损失

GQA (Grouped-Query Attention):
  Q heads 分成 G 组，每组共享一组 K, V
  Q: [batch, seq, h, d_h]
  K: [batch, seq, h_kv, d_h]  ← h_kv 个 KV head
  V: [batch, seq, h_kv, d_h]
  
  KV Cache 大小: 2 × batch × seq × h_kv × d_h × 2bytes
  → 减少 h/h_kv 倍
  
  典型配置:
  - Llama-3-8B:  h=32, h_kv=8  (G=4)
  - Llama-3-70B: h=64, h_kv=8  (G=8)
  - Mistral-7B:  h=32, h_kv=8  (G=4)
```

### 4.2 Multi-head Latent Attention (MLA)

> DeepSeek-V2/V3 的核心创新，极致压缩 KV Cache。

```
MLA 核心思想: 将 K 和 V 压缩到一个低维 latent space

传统 Attention:
  K = X × W_K  ∈ R^{d × d_h}
  V = X × W_V  ∈ R^{d × d_h}
  KV Cache: 2 × d_h per token

MLA:
  C_KV = X × W_DKV  ∈ R^{d × d_c}  (d_c << d_h, 如 d_c=512)
  K = C_KV × W_UK  ∈ R^{d_c × d_h}
  V = C_KV × W_UV  ∈ R^{d_c × d_h}
  
  KV Cache: d_c per token (只需缓存 C_KV!)
  
  压缩比: d_c / (2 × d_h)
  例如: 512 / (2 × 128) = 2× 压缩

MLA + Decoupled RoPE:
  Q 和 K 额外加一个 RoPE 分量 (不压缩)
  → 保留位置信息的同时压缩 KV Cache
```

### 4.3 Sliding Window Attention

```
Sliding Window Attention (Mistral):

每个 token 只 attend 到前 W 个 token (W = 4096 或 131072)

优点:
- 计算复杂度 O(W) 而非 O(seq_len)
- 适合长文本处理
- 与 FlashAttention 天然兼容

缺点:
- 无法直接利用超过 W 距离的信息
- 需要多层堆叠来传递长距离信息
```

---

## MLP / FFN 层

### 5.1 传统 FFN

```
传统 FFN (GPT-2):
  x → Linear(d → 4d) → GELU → Linear(4d → d) → x

参数量: 2 × d × 4d = 8d²
```

### 5.2 SwiGLU (当前主流)

```
SwiGLU (Llama, Mistral, Qwen):

  x → Gate(d → d_ff) ─┐
                        ├→ SiLU(Gate(x)) ⊙ Up(x) → Down(d_ff → d) → x
  x → Up(d → d_ff)   ─┘

参数量: 3 × d × d_ff

SwiGLU(x) = SiLU(xW_gate) ⊙ (xW_up)

其中 SiLU(x) = x × σ(x)  (Sigmoid Linear Unit)

为什么用 SwiGLU?
- 比 ReLU/GELU 效果更好 (PaLM 论文验证)
- 门控机制提供更强的非线性表达能力
- d_ff 通常设为 8/3 × d (而非传统 4d)，保持总参数量相近
```

### 5.3 FFN 变体对比

| 激活函数 | 公式 | 参数量 | 代表模型 |
|---------|------|--------|---------|
| **ReLU** | max(0, x) | 2d × d_ff | 早期 Transformer |
| **GELU** | x × Φ(x) | 2d × d_ff | GPT-2, BERT |
| **SwiGLU** | SiLU(xW_g) ⊙ (xW_u) | 3d × d_ff | Llama, Mistral, Qwen |
| **SwiGLU (MoE)** | Σ g_i × SwiGLU_i(x) | 3d × d_ff × E | Mixtral, DeepSeek-V2 |

---

## 归一化层

### 6.1 LayerNorm vs RMSNorm

```
LayerNorm:
  y = (x - μ) / σ × γ + β
  
  其中 μ = mean(x), σ = std(x)
  参数: γ, β ∈ R^d (2d 参数)
  计算: 需要均值和方差

RMSNorm (Llama, Mistral):
  y = x / RMS(x) × γ
  
  其中 RMS(x) = sqrt(mean(x²))
  参数: γ ∈ R^d (d 参数)
  计算: 只需要均方根，不需要减均值

为什么用 RMSNorm?
- 计算更快 (不需要减均值)
- 参数更少 (不需要 β)
- 效果与 LayerNorm 相当 (实验验证)
```

### 6.2 DeepNorm

```
DeepNorm (用于极深网络, 如 1000 层):

在 Post-Norm 基础上调整残差连接的权重:

  x_{l+1} = x_l + α × f_l(Norm(x_l))

其中 α < 1 (如 α = 0.5)

作用: 抑制深层网络的梯度爆炸，使极深 Transformer 可训练
```

---

## LM Head 与损失函数

### 7.1 交叉熵损失

```
对于自回归语言模型:

给定序列 x = [x_1, x_2, ..., x_n]
模型预测: P(x_t | x_{<t})

损失:
  L = -1/N × Σ_{t=1}^{N} log P(x_t | x_{<t})
    = -1/N × Σ_{t=1}^{N} log softmax(logits_t)[x_t]

其中 logits_t ∈ R^V, V = vocab_size

实现细节:
  loss = F.cross_entropy(
      logits.view(-1, vocab_size),
      targets.view(-1),
      ignore_index=pad_token_id  # 忽略 padding
  )
```

### 7.2 Perplexity

```
Perplexity = exp(Loss)

解释: 模型在每个位置上的"平均分支因子"
  PPL = 10 → 模型平均在 10 个 token 中犹豫
  PPL = 1  → 模型完全确定下一个 token

PPL 比 Loss 更直观，但优化时仍用 Loss (数值稳定性更好)
```

### 7.3 Chat 训练中的 Loss Masking

```
Chat 格式:
  <|user|> 你好 <|assistant|> 你好！有什么可以帮助你的？

Loss Masking:
  只在 assistant 部分计算 loss
  user 和 system 部分的 loss 设为 0

实现:
  loss_mask = (labels != IGNORE_INDEX)
  loss = (log_probs * loss_mask).sum() / loss_mask.sum()
```

---

## Pre-Norm vs Post-Norm

### 8.1 两种范式对比

```
Post-Norm (原始 Transformer):
  x → Attention(x) → LayerNorm(x + Attention(x))
  → FFN(x) → LayerNorm(x + FFN(x))
  
  问题: 深层网络梯度消失，训练不稳定

Pre-Norm (当前主流):
  x → x + Attention(LayerNorm(x))
  → x + FFN(LayerNorm(x))
  
  优点: 训练稳定，梯度流动好
  缺点: 浅层表示可能不够强 (Norm 在残差之前)

Sandwich-Norm (CogView):
  x → LayerNorm(x + Attention(LayerNorm(x)))
  → 结合两者优点
```

### 8.2 为什么 Pre-Norm 更稳定

```
Pre-Norm 的梯度分析:

对于 Pre-Norm:
  x_{l+1} = x_l + f_l(Norm(x_l))
  
  梯度: ∂L/∂x_l = ∂L/∂x_{l+1} × (I + ∂f_l/∂x_l)
  
  → 恒等映射 I 保证了梯度至少有一条直通路径
  → 即使 ∂f_l/∂x_l 很小，梯度也不会消失

对于 Post-Norm:
  x_{l+1} = Norm(x_l + f_l(x_l))
  
  → Norm 操作会缩放梯度
  → 深层时梯度可能消失
```

---

## 训练算法全景

### 9.1 训练算法分类

```
LLM 训练算法体系:

┌──────────────────────────────────────────────────────────────┐
│                      训练算法全景                              │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  精度管理:                                                    │
│  ├── FP32 (全精度, 已淘汰)                                    │
│  ├── FP16 Mixed Precision (Apex AMP)                         │
│  ├── BF16 Mixed Precision (当前主流)                          │
│  └── FP8 Training (Blackwell, 2025)                          │
│                                                              │
│  内存优化:                                                    │
│  ├── Gradient Accumulation (小 batch 模拟大 batch)            │
│  ├── Activation Checkpointing (时间换空间)                    │
│  ├── Gradient Checkpointing                                  │
│  ├── ZeRO-1/2/3 (DeepSpeed)                                  │
│  └── CPU Offload (ZeRO-Offload)                              │
│                                                              │
│  计算优化:                                                    │
│  ├── FlashAttention (高效 Attention)                         │
│  ├── Fused Kernels (减少 kernel launch)                      │
│  ├── torch.compile (图编译优化)                              │
│  └── Sequence Packing (填充短序列)                            │
│                                                              │
│  训练稳定性:                                                  │
│  ├── Gradient Clipping (防止梯度爆炸)                         │
│  ├── Weight Decay (正则化)                                   │
│  ├── Warmup + Cosine Decay (学习率调度)                       │
│  └── Loss Spike Detection (异常检测)                          │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

---

## 优化器深入

### 10.1 从 SGD 到 AdamW

```
SGD (随机梯度下降):
  θ_{t+1} = θ_t - η × g_t
  
  问题: 
  - 学习率对所有参数相同
  - 稀疏梯度更新慢
  - 容易陷入局部最优

SGD + Momentum:
  v_t = β × v_{t-1} + g_t
  θ_{t+1} = θ_t - η × v_t
  
  → 累积历史梯度方向，加速收敛

Adam:
  m_t = β₁ × m_{t-1} + (1-β₁) × g_t        (一阶矩)
  v_t = β₂ × v_{t-1} + (1-β₂) × g_t²       (二阶矩)
  m̂_t = m_t / (1-β₁^t)                      (偏差修正)
  v̂_t = v_t / (1-β₂^t)
  θ_{t+1} = θ_t - η × m̂_t / (√v̂_t + ε)
  
  → 自适应学习率，每个参数有不同的学习率

AdamW (当前 LLM 训练标准):
  与 Adam 的区别: Weight Decay 与梯度更新解耦
  
  Adam:  θ_{t+1} = θ_t - η × (m̂_t / (√v̂_t + ε) + λ × θ_t)
         → Weight Decay 与自适应学习率耦合
  
  AdamW: θ_{t+1} = θ_t - η × m̂_t / (√v̂_t + ε) - η × λ × θ_t
         → Weight Decay 独立于自适应学习率
         → 更好的泛化性能
```

### 10.2 AdamW 超参数

| 参数 | 典型值 | 含义 |
|------|--------|------|
| **lr (learning rate)** | 3e-4 (小模型) ~ 1.5e-4 (大模型) | 基础学习率 |
| **β₁** | 0.9 | 一阶矩衰减系数 |
| **β₂** | 0.95 (LLM) / 0.999 (CV) | 二阶矩衰减系数 |
| **ε** | 1e-8 | 数值稳定性 |
| **weight_decay** | 0.1 | L2 正则化强度 |
| **grad_clip** | 1.0 | 梯度裁剪阈值 |

### 10.3 其他优化器

| 优化器 | 特点 | 适用场景 |
|--------|------|---------|
| **Lion** | 只用符号，内存减半 | 大 batch 训练 (Google) |
| **Sophia** | 二阶 Hessian 估计 | 收敛更快 (Stanford) |
| **Muon** | 基于 Newton-Schulz 迭代 | 大模型训练 (Keller) |
| **Adafactor** | 分解二阶矩，省内存 | 极大规模训练 (T5) |
| **8-bit Adam** | 量化优化器状态 | 内存受限场景 |

---

## 学习率调度

### 11.1 标准调度策略

```
Warmup + Cosine Decay (LLM 训练标准):

  Step 0 → W (Warmup):
    lr = lr_max × step / W
    
  Step W → T (Total):
    lr = lr_min + 0.5 × (lr_max - lr_min) × (1 + cos(π × (step-W)/(T-W)))

  lr
  ▲
  │     ╱‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾╲
  │    ╱                            ╲
  │   ╱                              ╲___
  │  ╱                                    ╲___
  │ ╱                                          ╲___
  └──────────────────────────────────────────────▶ step
       Warmup          Cosine Decay

典型参数:
  - Warmup steps: 2000 (总步数的 1-2%)
  - lr_max: 3e-4 (小模型) ~ 1.5e-4 (大模型)
  - lr_min: lr_max × 0.1 (10%)
```

### 11.2 其他调度策略

| 策略 | 公式 | 特点 |
|------|------|------|
| **Constant** | lr = const | 简单，但效果差 |
| **Linear Decay** | lr = lr_max × (1 - t/T) | 简单有效 |
| **Cosine Decay** | lr = lr_min + 0.5(lr_max-lr_min)(1+cos(πt/T)) | 当前主流 |
| **WSD (Warmup-Stable-Decay)** | 先稳定再衰减 | DeepSeek 使用 |
| **Cyclic** | 周期性升降 | 较少用于 LLM |

### 11.3 Batch Size 与 Learning Rate 的关系

```
Scaling Rule (经验):
  lr ∝ √(batch_size)  (当 batch_size 变化不大时)

实践:
  - 小 batch (128): lr = 3e-4
  - 大 batch (4M):  lr = 1.5e-4
  
  → 大 batch 需要稍小的学习率
  → 但并非严格线性关系
```

---

## 混合精度训练

### 12.1 BF16 混合精度

```
BF16 (Brain Floating Point):
  1 sign + 8 exponent + 7 mantissa = 16 bits
  
  vs FP16: 1 sign + 5 exponent + 10 mantissa
  
  BF16 优势:
  - 动态范围与 FP32 相同 (8-bit exponent)
  - 不会溢出 (FP16 最大 65504, BF16 最大 3.4e38)
  - 不需要 loss scaling

混合精度训练流程:
  1. 前向: BF16 (权重 + 激活)
  2. 反向: BF16 (梯度计算)
  3. 更新: FP32 (优化器状态 + 权重 master copy)
  
  显存占用:
  - FP32: 4 bytes/param (权重) + 8 bytes/param (AdamW m+v) = 12 bytes
  - BF16: 2 bytes/param (权重) + 2 bytes/param (梯度) + 8 bytes/param (AdamW) = 12 bytes
  → 显存相近，但计算快 2×
```

### 12.2 FP8 训练 (2024-2025)

```
FP8 (E4M3 / E5M2):
  E4M3: 1 sign + 4 exponent + 3 mantissa (用于前向)
  E5M2: 1 sign + 5 exponent + 2 mantissa (用于反向)

FP8 训练挑战:
  1. 精度损失: 需要 per-tensor scaling
  2. 累积误差: 需要延迟 scaling 更新
  3. 硬件支持: H100/H200/B200 Transformer Engine

FP8 训练流程 (NVIDIA Transformer Engine):
  1. 前向: FP8 GEMM (自动选择 scaling factor)
  2. 反向: FP8 GEMM (梯度计算)
  3. 更新: FP16/FP32 (优化器状态)
  
  效果:
  - 吞吐提升: 1.5-2× vs BF16
  - 显存节省: ~30%
  - 精度损失: < 0.1% (benchmark)
```

---

## 训练稳定性技术

### 13.1 Gradient Clipping

```
梯度裁剪: 防止梯度爆炸

  g ← g × min(1, max_norm / ||g||)

典型 max_norm: 1.0

为什么需要:
  - LLM 训练中 loss spike 常见
  - 一次大的梯度更新可能破坏模型
  - 裁剪后训练更稳定
```

### 13.2 Loss Spike 处理

```
Loss Spike 检测与恢复:

1. 检测: loss > running_avg × threshold (如 3×)
2. 恢复策略:
   a. 回滚到最近的 checkpoint
   b. 跳过导致 spike 的数据 batch
   c. 减小学习率继续训练

3. 常见原因:
   - 数据中的异常样本 (极长文本、特殊字符)
   - 学习率过高
   - 数值不稳定 (NaN/Inf)
```

### 13.3 Embedding Normalization

```
Embedding LayerNorm (缓解 Embedding 退化):

  x = LayerNorm(Embedding(token_ids))

作用:
  - 防止 Embedding 范数过大
  - 改善训练稳定性
  - 提升最终效果 (Gemma 使用)
```

### 13.4 QK Normalization

```
QK LayerNorm (稳定 Attention 训练):

  Q = LayerNorm(X × W_Q)
  K = LayerNorm(X × W_K)

作用:
  - 防止 Attention logits 过大导致 softmax 饱和
  - 改善长序列训练的稳定性
  - 提升模型效果 (某些模型使用)
```

---

> **关键原则**：
> 1. **Pre-Norm + RMSNorm + SwiGLU + RoPE + GQA** 是 2024-2025 年 LLM 的事实标准配置
> 2. **AdamW + Warmup + Cosine Decay** 是训练的标准配方
> 3. **BF16 混合精度** 是当前性价比最优的选择，FP8 是未来方向
> 4. **训练稳定性** 比追求极致性能更重要——一次 loss spike 可能毁掉数天的训练
> 5. **参数量计算** 要精确到每个组件，理解参数分布才能理解显存分布
