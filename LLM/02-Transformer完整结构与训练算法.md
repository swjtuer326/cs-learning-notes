# Transformer 完整结构与训练算法

> **核心命题**：Transformer 不只是 Attention。理解完整的 Transformer 结构——从 Embedding 到 LM Head，从 Pre-Norm 到 Post-Norm，从 SGD 到 AdamW——是深入 LLM 训练和推理的基础。

### 关键术语

| 缩写 | 全称 | 含义 |
|------|------|------|
| RoPE | Rotary Position Embedding | 旋转位置编码，通过旋转矩阵将位置信息编码到 Q/K 中 |
| GQA | Grouped-Query Attention | 分组查询注意力，Q 头分组共享 KV 头 |
| MLA | Multi-head Latent Attention | 多头潜在注意力，DeepSeek-V2/V3 的 KV 低秩压缩方案 |
| SwiGLU | Swish-Gated Linear Unit | 门控线性激活函数，当前 LLM FFN 的主流选择 |
| RMSNorm | Root Mean Square Normalization | 均方根归一化，不需要减均值的轻量归一化层 |
| MTP | Multi-Token Prediction | 多 Token 预测，每个位置同时预测多个未来 token |
| mHC | Manifold-constrained Hyper-Connections | 流形约束超连接，DeepSeek-V4 替代标准残差连接的方案 |
| Muon | MomentUm Orthogonalized by Newton-schulz | 基于 Newton-Schulz 迭代的梯度正交化优化器 |
| SVD | Singular Value Decomposition | 奇异值分解，用于分析 Muon 导致 Attention Logit 爆炸的根因 |

### 前置知识

| 需要了解 | 参考文档 |
|----------|----------|
| LLM 训练推理全景框架 | [00-LLM训练推理全景学习框架](./00-LLM训练推理全景学习框架.md) |
| 注意力机制发展与演进 | [03-LLM注意力机制发展](./03-LLM注意力机制发展与演进.md) |

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
```

$$\text{参数量} \approx V \cdot d_{\text{model}} + L \cdot \left(2 d_{\text{model}}^{2} + 2 d_{\text{model}} \cdot h_{kv} \cdot d_h + 3 d_{\text{model}} \cdot d_{ff}\right)$$

```
示例: Llama-3-8B
  V=128256, d=4096, L=32, h=32, h_kv=8, d_h=128, d_ff=14336
  Embedding: 128256×4096 ≈ 525M
  Per Layer: 2×4096² + 2×4096×8×128 + 3×4096×14336 ≈ 218M
  Total: 525M + 32×218M ≈ 7.5B (加上 LM Head 共享 ≈ 8.0B)
```

---

## Embedding 层

### 2.1 Token Embedding

Token Embedding 矩阵: $E \in \mathbb{R}^{V \times d}$

每个 token ID $i$ 对应一个 $d$ 维向量 $E[i]$

前向: $x = E[\text{token\_ids}]$  `[batch, seq_len, d]`

梯度: $\frac{\partial \mathcal{L}}{\partial E[i]}$ 只在 token $i$ 出现的位置非零
→ Embedding 行的更新频率与 token 频率成正比
→ 高频 token 的 embedding 更新充分，低频 token 欠拟合

### 2.2 Weight Tying (权重共享)

**Weight Tying**: Embedding 和 LM Head 共享权重矩阵

LM Head: $W_{lm} \in \mathbb{R}^{V \times d}$
Embedding: $W_{emb} \in \mathbb{R}^{V \times d}$

共享: $W_{lm} = W_{emb}^T$ (或直接使用同一个矩阵)

优点:
- 减少参数量: $V \times d$ (对于 Llama-3-8B 约 525M 参数)
- 正则化效果: 防止过拟合
- 语义一致性: 输入和输出使用相同的 token 表示

缺点:
- 约束了 LM Head 和 Embedding 的表达能力
- 大词表时共享可能不是最优

---

## 位置编码深入

### 3.1 绝对位置编码

#### Sinusoidal (原始 Transformer)

$$
\begin{aligned}
PE_{(pos, 2i)} &= \sin\left(\frac{pos}{10000^{2i/d}}\right) \\
PE_{(pos, 2i+1)} &= \cos\left(\frac{pos}{10000^{2i/d}}\right)
\end{aligned}
$$

特点:
- 固定编码，不参与训练
- 通过三角函数性质，$PE(pos+k)$ 可表示为 $PE(pos)$ 的线性函数
- 外推能力有限 (训练长度外的位置效果差)

#### Learned Position Embedding (GPT-2)

直接学习一个位置嵌入矩阵 $P \in \mathbb{R}^{\text{max\_seq\_len} \times d}$

$$
x = E[\text{token\_ids}] + P[\text{positions}]
$$

缺点:
- 无法处理超过 $\text{max\_seq\_len}$ 的序列
- 需要为每个位置学习独立的嵌入

### 3.2 RoPE (Rotary Position Embedding)

> 当前 LLM 的事实标准，Llama、Mistral、Qwen、DeepSeek 均使用。

**RoPE 核心思想**: 通过旋转矩阵将位置信息编码到 Q 和 K 中

对于位置 $m$ 的向量 $x \in \mathbb{R}^d$，将其分成 $d/2$ 对 $(x_0, x_1), (x_2, x_3), ...$

对第 $i$ 对应用旋转:

$$
\begin{bmatrix}
\cos(m \cdot \theta_i) & -\sin(m \cdot \theta_i) \\
\sin(m \cdot \theta_i) & \cos(m \cdot \theta_i)
\end{bmatrix}
\begin{bmatrix}
x_{2i} \\
x_{2i+1}
\end{bmatrix}
$$

其中 $\theta_i = 10000^{-2i/d}$

**关键性质**:

$$
\text{RoPE}(q_m, m)^T \cdot \text{RoPE}(k_n, n) = q_m^T \cdot R_{n-m} \cdot k_n
$$

- Attention score 只依赖于相对位置 $(n-m)$
- 兼具绝对位置编码的便利和相对位置编码的泛化能力

**RoPE 的频率与长度外推**：

RoPE 的 $\theta_i$ 决定了不同维度对位置的敏感度:
- 低维度 (大 $\theta$): 高频 → 对短距离位置变化敏感
- 高维度 (小 $\theta$): 低频 → 对长距离位置变化敏感

外推问题: 训练长度 $L_{\text{train}}$ 外的位置，高频维度没有见过
→ 直接外推效果差

解决方案:
1. Linear Scaling (Position Interpolation):
   $\theta_i' = \theta_i / \text{scale\_factor}$
   → 压缩频率，使训练长度内的旋转角度覆盖更长的范围

2. NTK-aware Scaling:
   高频维度不缩放，低频维度按 NTK 理论缩放
   → 保留短距离分辨能力，扩展长距离

3. YaRN (NTK + temperature):
   在 NTK 基础上引入温度系数调整 attention score

### 3.3 ALiBi (Attention with Linear Biases)

```
ALiBi: 在 Attention Score 上加上线性偏置
```

$$
\text{Score}(Q, K) = \frac{QK^T}{\sqrt{d}} - m \times |i - j|
$$

其中 $m$ 是 head-specific 的斜率:

$$
m = 2^{-8 \times h/H} \quad (h \text{ 是 head 索引}, H \text{ 是总 head 数})
$$

特点:
- 不需要位置编码
- 天然支持外推 (Bloom 使用)
- 但效果不如 RoPE (LLM 社区共识)

---

## 长上下文扩展：从 RoPE Scaling 到训练策略

> RoPE 的频率结构决定了模型的外推能力边界。本节梳理从位置插值到动态 NTK 再到 YaRN 的演进脉络，以及长上下文训练的工程策略。

### 3.4 外推问题的根源

RoPE 的 $\theta_i = 10000^{-2i/d}$ 将 head 维度分为不同频率带：

| 频率带 | 维度范围 | 对位置的敏感度 | 外推行为 |
|--------|---------|--------------|---------|
| 高频 | $i$ 小（大 $\theta$） | 短距离位置变化 | 训练长度外直接外推 → 高频振荡，注意力崩溃 |
| 低频 | $i$ 大（小 $\theta$） | 长距离位置变化 | 内插后仍可工作 |

核心矛盾：直接外推时高频维度超出训练范围，而简单缩放会损失高频分辨能力。

### 3.5 位置插值 (Position Interpolation, PI)

$$
\theta_i' = \theta_i / s, \quad s = \frac{L_{\text{target}}}{L_{\text{train}}}
$$

将位置坐标从 $[0, L_{\text{target}}]$ 线性压缩到 $[0, L_{\text{train}}]$，所有频率带均匀缩放。

优点：不引入新的位置值，训练稳定性好
缺点：高频维度也被压缩 → 短距离分辨能力下降 → 局部排序能力受损

### 3.6 NTK-aware Scaling

核心思想：高频维度少缩放或不缩放，低频维度多缩放。

$$
\theta_i' = b' \cdot 10000^{-2i/d}, \quad b' = b \cdot s^{d/(d-2)}
$$

其中 $b = 10000$ 为原始 base，$s$ 为缩放因子。

| 维度 | 缩放行为 | 效果 |
|------|---------|------|
| 高频（$i$ 小） | 几乎不缩放 | 保留短距离分辨能力 |
| 低频（$i$ 大） | 大幅缩放 | 扩展长距离覆盖范围 |

CodeLlama 使用 NTK-aware Scaling 将上下文从 4K 扩展到 16K/100K。

### 3.7 YaRN (Yet another RoPE extensioN)

YaRN 在 NTK 基础上引入温度系数 $t$，调整注意力 logits 的尺度：

$$
\text{Attention}(Q, K) = \frac{QK^T}{\sqrt{d} \cdot t}
$$

其中温度 $t$ 按频率带分段设置：

| 频率带 | 条件 | 温度 $t$ | 说明 |
|--------|------|---------|------|
| 低频 | $\lambda_i < L_{\text{train}} / (2\pi)$ | 1.0 | 不需要调整 |
| 高频 | $\lambda_i > L_{\text{train}} / (2\pi)$ | $\sqrt{s} + 0.1 \cdot \log(s)$ | 补偿缩放后的注意力锐化 |
| 中频 | 其余 | 线性插值 | 平滑过渡 |

$\lambda_i = 2\pi / \theta_i$ 为波长。YaRN 是 Llama-3 等模型长上下文扩展的基础方案。

### 3.8 长上下文训练策略

| 策略 | 方法 | 代表工作 | 关键参数 |
|------|------|---------|---------|
| **长度课程** | 从短序列逐步增长到长序列 | 几乎所有 LLM | 初始 4K → 最终 128K，每阶段翻倍 |
| **RoPE 微调** | 在目标长度上用少量步数微调 RoPE 缩放因子 | Llama-3, Qwen2.5 | ~1000 步 BF16 微调 |
| **渐进式 NF4 微调** | 用 QLoRA 在长序列上微调 | 社区实践 | rank=64, 目标长度 128K |
| **混合长度训练** | 短序列和长序列混合训练 | DeepSeek-V3 | 短:长 = 9:1，避免长序列训练效率过低 |
| **序列并行** | 长序列切分到多 GPU | Ring Attention, Ulysses | [详见 05-分布式训练](./05-LLM分布式训练：并行策略与ZeRO.md) |

**工程经验**：

```
长上下文扩展的典型流程:

1. 在 4K/8K 长度上完成预训练
2. 应用 NTK/YaRN 缩放 RoPE 到目标长度
3. 在目标长度上做少量微调 (500-2000 步)
4. 验证: Needle-in-a-Haystack 测试
5. 如有质量退化: 缩短微调步数或降低学习率

常见陷阱:
- 直接外推不做微调 → 高频注意力崩溃
- 微调步数过多 → 短上下文能力退化
- 序列并行与 RoPE 不兼容 → 需要特殊处理
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

MLA 核心思想: 将 K 和 V 压缩到一个低维 latent space

**传统 Attention**:
$$
K = X \times W_K \in \mathbb{R}^{d \times d_h} \\
V = X \times W_V \in \mathbb{R}^{d \times d_h}
$$
KV Cache: $2 \times d_h$ per token

**MLA**:
$$
C_{KV} = X \times W_{DKV} \in \mathbb{R}^{d \times d_c} \quad (d_c \ll d_h, \text{如 } d_c=512) \\
K = C_{KV} \times W_{UK} \in \mathbb{R}^{d_c \times d_h} \\
V = C_{KV} \times W_{UV} \in \mathbb{R}^{d_c \times d_h}
$$

KV Cache: $d_c$ per token (只需缓存 $C_{KV}$!)

压缩比: $d_c / (2 \times d_h)$
例如: $512 / (2 \times 128) = 2\times$ 压缩

**MLA + Decoupled RoPE**:
$Q$ 和 $K$ 额外加一个 RoPE 分量 (不压缩)
→ 保留位置信息的同时压缩 KV Cache

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

**传统 FFN** (GPT-2):
$$
x \rightarrow \text{Linear}(d \rightarrow 4d) \rightarrow \text{GELU} \rightarrow \text{Linear}(4d \rightarrow d) \rightarrow x
$$

参数量: $2 \times d \times 4d = 8d^2$

### 5.2 SwiGLU (当前主流)

**SwiGLU** (Llama, Mistral, Qwen):

```
  x → Gate(d → d_ff) ─┐
                        ├→ SiLU(Gate(x)) ⊙ Up(x) → Down(d_ff → d) → x
  x → Up(d → d_ff)   ─┘
```

参数量: $3 \times d \times d_{ff}$

$$
\text{SwiGLU}(x) = \text{SiLU}(xW_{gate}) \odot (xW_{up})
$$

其中 $\text{SiLU}(x) = x \times \sigma(x)$ (Sigmoid Linear Unit)

为什么用 SwiGLU?
- 比 ReLU/GELU 效果更好 (PaLM 论文验证)
- 门控机制提供更强的非线性表达能力
- $d_{ff}$ 通常设为 $\frac{8}{3} \times d$ (而非传统 $4d$)，保持总参数量相近

### 5.3 FFN 变体对比

| 激活函数 | 公式 | 参数量 | 代表模型 |
|---------|------|--------|---------|
| **ReLU** | $\max(0, x)$ | $2d \times d_{ff}$ | 早期 Transformer |
| **GELU** | $x \times \Phi(x)$，其中 $\Phi(x)$ 是标准正态分布的累积分布函数（CDF） | $2d \times d_{ff}$ | GPT-2, BERT |
| **SwiGLU** | $\text{SiLU}(xW_g) \odot (xW_u)$ | $3d \times d_{ff}$ | Llama, Mistral, Qwen |
| **SwiGLU (MoE)** | $\sum g_i \times \text{SwiGLU}_i(x)$ | $3d \times d_{ff} \times E$ | Mixtral, DeepSeek-V2 |

---

## 归一化层

### 6.1 LayerNorm vs RMSNorm

**LayerNorm**:
$$
y = \frac{x - \mu}{\sigma} \times \gamma + \beta
$$

其中 $\mu = \text{mean}(x)$, $\sigma = \text{std}(x)$
参数: $\gamma, \beta \in \mathbb{R}^d$ ($2d$ 参数)
计算: 需要均值和方差

**RMSNorm** (Llama, Mistral):
$$
y = \frac{x}{\text{RMS}(x)} \times \gamma
$$

其中 $\text{RMS}(x) = \sqrt{\text{mean}(x^2)}$
参数: $\gamma \in \mathbb{R}^d$ ($d$ 参数)
计算: 只需要均方根，不需要减均值

为什么用 RMSNorm?
- 计算更快 (不需要减均值)
- 参数更少 (不需要 $\beta$)
- 效果与 LayerNorm 相当 (实验验证)

### 6.2 DeepNorm

**DeepNorm** (用于极深网络, 如 1000 层):

在 Post-Norm 基础上调整残差连接的权重:

$$
x_{l+1} = x_l + \alpha \times f_l(\text{Norm}(x_l))
$$

其中 $\alpha < 1$ (如 $\alpha = 0.5$)

作用: 抑制深层网络的梯度爆炸，使极深 Transformer 可训练

---

## LM Head 与损失函数

### 7.1 交叉熵损失

对于自回归语言模型:

给定序列 $x = [x_1, x_2, ..., x_n]$
模型预测: $P(x_t | x_{<t})$

损失:

$$
\mathcal{L} = -\frac{1}{N} \sum_{t=1}^{N} \log P(x_t | x_{<t}) = -\frac{1}{N} \sum_{t=1}^{N} \log \text{softmax}(\text{logits}_t)[x_t] \tag{1}
$$

其中 $\text{logits}_t \in \mathbb{R}^V$, $V = \text{vocab\_size}$

实现细节:
```python
loss = F.cross_entropy(
    logits.view(-1, vocab_size),
    targets.view(-1),
    ignore_index=pad_token_id  # 忽略 padding
)
```

### 7.2 Perplexity

$$
\text{Perplexity} = \exp(\mathcal{L})
$$

其中 $\mathcal{L}$ 为由式 (1) 定义的交叉熵损失。

解释: 模型在每个位置上的"平均分支因子"
- $\text{PPL} = 10$ → 模型平均在 10 个 token 中犹豫
- $\text{PPL} = 1$ → 模型完全确定下一个 token

PPL 比 Loss 更直观，但优化时仍用 Loss (数值稳定性更好)

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

**Post-Norm** (原始 Transformer):
$$
x \rightarrow \text{Attention}(x) \rightarrow \text{LayerNorm}(x + \text{Attention}(x)) \rightarrow \text{FFN}(x) \rightarrow \text{LayerNorm}(x + \text{FFN}(x))
$$

问题: 深层网络梯度消失，训练不稳定

**Pre-Norm** (当前主流):
$$
x \rightarrow x + \text{Attention}(\text{LayerNorm}(x)) \rightarrow x + \text{FFN}(\text{LayerNorm}(x))
$$

优点: 训练稳定，梯度流动好
缺点: 浅层表示可能不够强 (Norm 在残差之前)

**Sandwich-Norm** (CogView):
$$
x \rightarrow \text{LayerNorm}(x + \text{Attention}(\text{LayerNorm}(x)))
$$
→ 结合两者优点

### 8.2 为什么 Pre-Norm 更稳定

**Pre-Norm 的梯度分析**:

对于 Pre-Norm:
$$
x_{l+1} = x_l + f_l(\text{Norm}(x_l))
$$

梯度:
$$
\frac{\partial \mathcal{L}}{\partial x_l} = \frac{\partial \mathcal{L}}{\partial x_{l+1}} \times (I + \frac{\partial f_l}{\partial x_l}) \tag{2}
$$

- 恒等映射 $I$ 保证了梯度至少有一条直通路径
- 即使 $\partial f_l/\partial x_l$ 很小，梯度也不会消失

对于 Post-Norm:
$$
x_{l+1} = \text{Norm}(x_l + f_l(x_l))
$$

- Norm 操作会缩放梯度
- 深层时梯度可能消失

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

**SGD** (随机梯度下降):
$$
\theta_{t+1} = \theta_t - \eta \cdot g_t
$$

问题: 
- 学习率对所有参数相同
- 稀疏梯度更新慢
- 容易陷入局部最优

**SGD + Momentum**:
$$
v_t = \beta \cdot v_{t-1} + g_t \\
\theta_{t+1} = \theta_t - \eta \cdot v_t
$$

→ 累积历史梯度方向，加速收敛

**Adam**:
$$
\begin{aligned}
m_t &= \beta_1 \cdot m_{t-1} + (1-\beta_1) \cdot g_t \quad \text{(一阶矩)} \\
v_t &= \beta_2 \cdot v_{t-1} + (1-\beta_2) \cdot g_t^2 \quad \text{(二阶矩)} \\
\hat{m}_t &= \frac{m_t}{1-\beta_1^t} \quad \text{(偏差修正)} \\
\hat{v}_t &= \frac{v_t}{1-\beta_2^t} \\
\theta_{t+1} &= \theta_t - \eta \cdot \frac{\hat{m}_t}{\sqrt{\hat{v}_t} + \epsilon}
\end{aligned}
$$

→ 自适应学习率，每个参数有不同的学习率

**AdamW** (当前 LLM 训练标准):

与 Adam 的区别: Weight Decay 与梯度更新解耦

Adam:
$$
\theta_{t+1} = \theta_t - \eta \cdot \left(\frac{\hat{m}_t}{\sqrt{\hat{v}_t} + \epsilon} + \lambda \cdot \theta_t\right)
$$
→ Weight Decay 与自适应学习率耦合

AdamW:
$$
\theta_{t+1} = \theta_t - \eta \cdot \frac{\hat{m}_t}{\sqrt{\hat{v}_t} + \epsilon} - \eta \cdot \lambda \cdot \theta_t
$$
→ Weight Decay 独立于自适应学习率
→ 更好的泛化性能

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

**Warmup + Cosine Decay** (LLM 训练标准):

Step $0 \rightarrow W$ (Warmup):
$$
\text{lr} = \text{lr}_{\text{max}} \times \frac{\text{step}}{W}
$$

Step $W \rightarrow T$ (Total):
$$
\text{lr} = \text{lr}_{\text{min}} + 0.5 \times (\text{lr}_{\text{max}} - \text{lr}_{\text{min}}) \times \left(1 + \cos\left(\pi \times \frac{\text{step}-W}{T-W}\right)\right)
$$

```
  lr
  ▲
  │     ╱‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾╲
  │    ╱                            ╲
  │   ╱                              ╲___
  │  ╱                                    ╲___
  │ ╱                                          ╲___
  └──────────────────────────────────────────────▶ step
       Warmup          Cosine Decay
```

典型参数:
- Warmup steps: 2000 (总步数的 1-2%)
- $\text{lr}_{\text{max}}$: 3e-4 (小模型) ~ 1.5e-4 (大模型)
- $\text{lr}_{\text{min}}$: $\text{lr}_{\text{max}} \times 0.1$ (10%)

### 11.2 其他调度策略

| 策略 | 公式 | 特点 |
|------|------|------|
| **Constant** | $\text{lr} = \text{const}$ | 简单，但效果差 |
| **Linear Decay** | $\text{lr} = \text{lr}_{\text{max}} \times (1 - t/T)$ | 简单有效 |
| **Cosine Decay** | $\text{lr} = \text{lr}_{\text{min}} + 0.5(\text{lr}_{\text{max}}-\text{lr}_{\text{min}})(1+\cos(\pi t/T))$ | 当前主流 |
| **WSD (Warmup-Stable-Decay)** | 先稳定再衰减 | DeepSeek 使用 |
| **Cyclic** | 周期性升降 | 较少用于 LLM |

### 11.3 Batch Size 与 Learning Rate 的关系

**Scaling Rule** (经验):
$$
\text{lr} \propto \sqrt{\text{batch\_size}} \quad (\text{当 batch\_size 变化不大时})
$$

实践:
- 小 batch (128): $\text{lr} = 3 \times 10^{-4}$
- 大 batch (4M): $\text{lr} = 1.5 \times 10^{-4}$

→ 大 batch 需要稍小的学习率
→ 但并非严格线性关系

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

**梯度裁剪**: 防止梯度爆炸

$$
g \leftarrow g \times \min\left(1, \frac{\text{max\_norm}}{||g||}\right)
$$

典型 $\text{max\_norm}$: 1.0

为什么需要:
- LLM 训练中 loss spike 常见
- 一次大的梯度更新可能破坏模型
- 裁剪后训练更稳定

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

---

## 14. Muon 优化器与训练稳定性

> Muon (MomentUm Orthogonalized by Newton-schulz) 由 Keller Jordan 团队提出，Kimi-K2 和 DeepSeek-V4 均在其大规模训练中引入。核心优势：比 AdamW 更高的 token 效率，但需额外的稳定性机制。

### 14.1 为什么用 Muon 替代 AdamW

Moonlight (Moonshot AI, 2025) 的实证结论：**在相同 token 预算下，Muon 训练的模型在下游任务上全面优于 AdamW**。

AdamW 的根本局限：自适应学习率 $1 / \sqrt{\hat{v}_t}$ 按坐标独立缩放梯度，破坏了梯度的方向信息。当梯度矩阵的奇异值分布不均时（某些方向梯度强、某些弱），AdamW 的二阶矩归一化会放大噪声方向。

Muon 的思路：对梯度矩阵 $G \in \mathbb{R}^{m \times n}$ 做 **Newton-Schulz 迭代**，使其逼近正交化后的梯度：

$$
O = \text{NS}(G) \quad \text{满足} \quad O^T O = I
$$

更新规则：

$$
W_{t+1} = W_t - \eta \cdot (O_t + \lambda \cdot W_t)
$$

其中 $\lambda$ 为独立的 weight decay 系数。

```
Muon vs AdamW 对比:

┌─────────────────────────────────────────────────────────────────┐
│                    AdamW                         Muon            │
├─────────────────────────────────────────────────────────────────┤
│  更新方向: 动量 + 逐坐标缩放                    动量 + 矩阵正交化  │
│  梯度信息: 丢失方向结构                         保留奇异向量方向   │
│  内存占用: 2× params (m, v)                     1× params (动量)  │
│  Token 效率: baseline                           Moonlight: +15%   │
│  稳定性:    ⬤ 默认稳定                         需要 QK-Clip 等    │
│  超参数:    β₁, β₂, ε                          仅需 η, λ          │
└─────────────────────────────────────────────────────────────────┘
```

### 14.2 Attention Logit 爆炸问题

Kimi-K2 在 Muon 训练中发现的不稳定现象：

> **Attention Logit Explosion**：训练到一定阶段后，某些 Attention head 的 pre-softmax logit 最大值急剧上升，达到 **max logit > 1000**（正常范围 10-50）。

后果：softmax 退化为 argmax → 梯度消失 → 该 head 永久失效 → 模型容量退化。

根因见 [14.4 节](#144-svd-视角为什么-muon-会导致-attention-logit-爆炸)。

### 14.3 QK-Clip 机制

**QK-Clip** 是 Kimi-K2 针对 Attention Logit 爆炸的防御机制。

核心操作：在每个训练 step 之后，对每个 Attention head 的 Q/K 投影权重做 **per-head 范数重缩放**：

```
QK-Clip 算法（每 head 独立执行）:

1. 计算 Q 投影权重的谱范数（或 Frobenius 范数）:
   σ_Q = ∥W_Q^h∥₂    (head h 的 Q 投影矩阵)
   σ_K = ∥W_K^h∥₂    (head h 的 K 投影矩阵)

2. 如果 σ_Q · σ_K > τ² (默认 τ = 100):
   scale = τ / sqrt(σ_Q · σ_K)
   W_Q^h ← scale · W_Q^h
   W_K^h ← scale · W_K^h

3. V 和 O 投影不参与（仅 Q/K 影响 logit 尺度）
```

阈值 $\tau = 100$ 的选取：在正常的 attention logit 范围（10-50）和爆炸阈值之间留有安全裕度。

**MLA (Multi-head Latent Attention, [见 4.2 节](#42-multi-head-latent-attention-mla)) 的特殊处理**：

MLA 中 K 由压缩 latent $C_{KV}$ 和 Decoupled RoPE 两部分组成。QK-Clip 需分别处理：
- 压缩部分的 $W_{UK}$（up-projection 矩阵）
- RoPE 部分的 $W_{KR}$（RoPE 分量投影矩阵）

只裁剪 Q 投影和这两部分 K 投影的乘积范数。

### 14.4 SVD 视角：为什么 Muon 会导致 Attention Logit 爆炸

从奇异值分解 (SVD, Singular Value Decomposition) 角度理解根因。

Muon 的 Newton-Schulz 迭代中包含 **msign** 操作——将梯度矩阵的所有奇异值替换为 1 或 -1：

$$
\text{msign}(G) = U \cdot \text{sign}(\Sigma) \cdot V^T
$$

其中 $G = U \Sigma V^T$ 是 $G$ 的 SVD。

**三条因果链**：

1. **高有效秩**：msign 将所有非零奇异值统一为 ±1，大幅提高梯度矩阵的 effective rank（有效秩，非零奇异值的数目）。AdamW 会抑制小奇异值方向，而 Muon 平等对待所有方向。

2. **奇异向量跨层对齐**：高有效秩梯度意味着不同层的梯度方向（即左右奇异向量 $U, V$）更容易对齐。多层对齐导致权重变化在相同方向上累积。

3. **权重加性增长**：由于多层在同一方向持续更新，$W_Q$ 和 $W_K$ 的范数在训练中近似线性增长。当 $\|W_Q\| \cdot \|W_K\|$ 超过临界值，attention logit 的方差失控 → logit 爆炸。

形式化：

$$
\text{Attention Logit} = X W_Q (X W_K)^T / \sqrt{d}
$$

若 $\|W_Q\|_2$ 和 $\|W_K\|_2$ 同步增长，logit 方差 $\propto \|W_Q\|_2^2 \cdot \|W_K\|_2^2$，呈四次方增长。

### 14.5 MuonClip 与实证结果

**MuonClip**：在 Muon 更新的同时施加梯度裁剪和 QK-Clip 的组合策略。

Kimi-K2 的实证结果：

| 指标 | 数值 |
|------|------|
| 训练总 token 数 | 15.5T |
| Loss Spike 次数 | **0** |
| QK-Clip 触发频率 | 前 30% 步数频繁触发，之后逐渐归零 |
| QK-Clip 自动停用步数 | 约总步数的 30% |

关键发现：QK-Clip **并非全程需要**。在训练前期（~前 30% 步数），模型处于"权重范数增长期"，QK-Clip 频繁介入。训练后期，权重范数自然稳定，QK-Clip 触发频率降至零。

### 14.6 DeepSeek-V4 的混合 Newton-Schulz 迭代

DeepSeek-V4 对 Muon 的 Newton-Schulz 迭代做了工程优化——**两阶段混合系数迭代**，共 10 次迭代：

```
阶段 1: 快速收敛 (前 8 步)
  使用系数 (a, b, c) = (3.4445, -4.7750, 2.0315)
  目标: 快速逼近正交矩阵方向

阶段 2: 精确稳定 (后 2 步)
  使用系数 (a, b, c) = (2, -1.5, 0.5)
  目标: 将奇异值精确稳定在 1 附近
```

两阶段使用不同的系数而非不同精度——阶段 1 的系数驱动快速收敛，阶段 2 的系数确保最终稳定。

---

## 15. MTP (Multi-Token Prediction)

> MTP 让模型在每个位置同时预测多个未来 token，训练时作为辅助损失，推理时复用为投机解码 (Speculative Decoding) 的草稿模型。

### 15.1 DeepSeek-V3/V4 MTP 架构

DeepSeek-V3 最早引入 MTP，V4 保持相同的 **D=1 深度**（即预测 1 个额外 token）。

**核心设计**：MTP 模块只增加 1 层额外的 Transformer Block，输入为主模型 hidden states 与下一位 token embedding 的拼接。

```
MTP (D=1) 数据流:

  Main Model Output (hidden_states at position i)
         │
         ▼
  ┌─────────────────────────────────────────────┐
  │  MTP Module                                 │
  │  input = RMSNorm([h_i; Emb(token_{i+1})])   │
  │       ↓                                     │
  │  Transformer Block → Shared Output Head     │
  │       ↓                                     │
  │  p(t_{i+1})   (对未来 1 个 token 的预测)      │
  └─────────────────────────────────────────────┘
```

**参数共享**：
- Embedding 层和 Output Head（LM Head）与主模型**完全共享**
- MTP 模块有自己独立的 Transformer Block
- D=1 时增加约 1 层额外 Transformer layer 的参数量

### 15.2 训练与推理

**训练阶段**：

主模型损失 $\mathcal{L}_{\text{main}}$ 与 MTP 损失加权求和：

$$
\mathcal{L} = \mathcal{L}_{\text{main}} + \lambda \cdot \mathcal{L}_{\text{MTP}}
$$

| 训练超参 | DeepSeek-V3 (D=1) | DeepSeek-V4 (D=1) |
|----------|-------------------|-------------------|
| MTP loss weight ($\lambda$) | 0.3 | 0.3 → 0.1 (warmup decay) |
| FLOPs 开销 | ~3% | ~3% |
| 额外参数量 | ~1 层 | ~1 层 |

V4 的 MTP 配置与 V3 相同（D=1），但引入了 $\lambda$ 衰减策略：训练初期 $\lambda=0.3$ 让模型快速学会多 token 预测，随着主模型收敛逐步降至 $\lambda=0.1$，让最终优化目标回归主模型质量。

**推理阶段的投机解码复用**：

训练好的 MTP 模块可直接作为投机解码的草稿模型 (Draft Model)，预测 1 个额外 token：

```
MTP 投机解码流程 (D=1):

1. 主模型生成第一个 token
2. 将主模型 hidden states 送入 MTP 模块 → 预测 token 2（草稿）
3. 主模型一次前向同时验证原 token + 草稿 token
4. 匹配成功则接受草稿 token，失败则回退重采样
```

典型效果：第 2 个 token 接受率约 **85-90%**。

### 15.3 GLM-5 MTP 变体

GLM-5 (Zhipu AI, 2025) 的 MTP 设计强调参数效率：

- **3 个 MTP layer 共享参数**（而非各自独立）
- 训练时走完 3 层深度，推理时**只保留 1 层**
- 通过共享参数，3 层 MTP **零额外参数量**

实测对比：

| 模型 | MTP 策略 | Accept Length (平均接受长度) |
|------|---------|---------------------------|
| DeepSeek-V3.2 | D=1, 独立参数 | 2.55 |
| **GLM-5** | D=3, 共享参数, 推理 D=1 | **2.76** |

GLM-5 用更少的推理参数达到了更高的接受长度，核心原因：共享参数的多层训练提供了更强的正则化，使单层推理时泛化更好。

### 15.4 Step3.5-Flash MTP-3

Step3.5-Flash (StepFun, 2025) 的 MTP-3 方案——参数量最小的 MTP 实现。

**架构设计**：

```
3 个轻量级 MTP Head（非完整 Transformer Block）:

  Main Model Output
         │
    ┌────┴────┬────────┬────────┐
    ▼         ▼        ▼        ▼
  MTP-1    MTP-2    MTP-3    (3 个 head 并行，非顺序)
    │         │        │
    ▼         ▼        ▼
  各自预测 t+1, t+2, t+3
```

| 设计要素 | 说明 |
|---------|------|
| 参数量 | **+0.81B**（主模型 ~200B 的 **0.41%**） |
| Attention 类型 | SWA (Sliding Window Attention) + Dense FFN |
| 初始化 | 从 MTP-1 克隆权重 → 3 个 head 联合 fine-tune |
| 损失函数 | **Fast-MTP**：position-aware loss reweighting |

**Fast-MTP 损失重加权**：

标准 MTP 对所有预测位置等权。Fast-MTP 根据预测位置的难度调整权重：

$$
\mathcal{L}_{\text{Fast-MTP}} = \sum_{k=1}^{3} \gamma_k \cdot \mathcal{L}^{(k)}, \quad \gamma_1 > \gamma_2 > \gamma_3
$$

直觉：近处 token 预测更可靠，应给予更高权重；远处 token 不确定性大，权重降低可减少噪声梯度。

---

## 16. mHC (Manifold-Constrained Hyper-Connections)

> DeepSeek-V4 用流形约束超连接替代标准残差连接，核心约束：Birkhoff Polytope（双随机矩阵流形）保证信号在深层堆叠中稳定传播。

### 16.1 从残差连接到流形约束超连接

**标准残差连接**（[见 8.1 节](#81-两种范式对比)）：

$$
x_{l+1} = x_l + f_l(x_l)
$$

局限：每层只有一个残差通道，信息的混合方式固定。

**Hyper-Connection (超连接)**：将残差流宽度扩展为 $n_{hc}$ 个通道：

$$
x_l \in \mathbb{R}^{d} \quad \rightarrow \quad \mathbf{x}_l \in \mathbb{R}^{n_{hc} \times d}
$$

DeepSeek-V4 取 $n_{hc} = 4$，即残差流扩展为 4 个并行的 $d$ 维向量：

$$
\mathbf{x}_{l+1} = B_l \cdot \mathbf{x}_l + C_l \cdot f_l(A_l \cdot \mathbf{x}_l)
$$

其中：
- $A_l \in \mathbb{R}^{n_{hc} \times n_{hc}}$：输入混合矩阵（sigmoid 约束，$0 < A_l[i,j] < 1$）
- $B_l \in \mathbb{R}^{n_{hc} \times n_{hc}}$：跨越连接矩阵（**Birkhoff Polytope 约束**）
- $C_l \in \mathbb{R}^{n_{hc} \times n_{hc}}$：输出混合矩阵（sigmoid 约束）

**核心约束**：$B_l$ 必须位于 Birkhoff Polytope（双随机矩阵流形）上：

$$
B_l \in \mathcal{B}_{n_{hc}} = \{ M \in \mathbb{R}^{n_{hc} \times n_{hc}} : M_{ij} \geq 0, \sum_i M_{ij} = 1, \sum_j M_{ij} = 1 \}
$$

Birkhoff Polytope 的关键性质：双随机矩阵的谱范数 $\|B_l\|_2 \leq 1$（由 Birkhoff-von Neumann 定理保证），确保信号在前向传播中不会发散。

### 16.2 Sinkhorn-Knopp 算法

将任意矩阵投影到 Birkhoff Polytope 的标准方法——**Sinkhorn-Knopp 算法**：

```python
def sinkhorn_knopp(M, num_iters=20):
    """将 M 投影到双随机矩阵流形"""
    M = torch.exp(M)  # 确保非负
    for _ in range(num_iters):
        M = M / M.sum(dim=0, keepdim=True)  # 列归一化
        M = M / M.sum(dim=1, keepdim=True)  # 行归一化
    return M
```

DeepSeek-V4 使用 **20 次迭代**，每次前向/反向都执行。SK 算法本身可微，梯度通过迭代过程反向传播。

**为什么约束 $B_l$ 而非 $A_l$, $C_l$？**

$B_l$ 控制跨层信号的直接传递（不经过 $f_l$），这是信号传播稳定性的关键路径。$A_l$, $C_l$ 经过非线性变换 $f_l$，$f_l$ 本身有 RMSNorm 提供的隐式范数约束，因此仅需 sigmoid 限制输出范围。

### 16.3 动态参数与工程优化

**动态参数化**：$A_l$, $B_l$, $C_l$ 不是固定的可学习参数，而是**输入依赖的**：

$$
A_l = \sigma(W_A \cdot \text{RMSNorm}(x_l) + b_A)
$$

$$
B_l = \text{SK}(W_B \cdot \text{RMSNorm}(x_l) + b_B)
$$

$$
C_l = \sigma(W_C \cdot \text{RMSNorm}(x_l) + b_C)
$$

其中 $\sigma$ 为 sigmoid 函数，$\text{SK}$ 为 Sinkhorn-Knopp 投影。

- 每个矩阵 $W_A, W_B, W_C \in \mathbb{R}^{n_{hc} \times n_{hc} \times d}$，额外参数量为 $3 \times n_{hc}^2 \times d$ per layer
- 对 $n_{hc}=4, d=7168$（DeepSeek-V4），每层额外 ~337K 参数

**6.7% 开销的缓解措施**：

| 技术 | 说明 |
|------|------|
| **Recomputation** | 前向不保存 $A_l, B_l, C_l$ 中间结果，反向时重新计算 |
| **Fused Kernel** | 将 sigmoid + SK + 矩阵乘法融合为单 CUDA kernel |
| **DualPipe 调度** | 将 mHC 计算与下一个 Transformer layer 的前向重叠 |

三项组合使实际 wall-time 开销从理论 ~12% 降至实测 **6.7%**。

---

## 17. ERNIE 5.0 Uni-RoPE

> 百度 ERNIE 5.0 提出的统一时空位置编码，将 RoPE 从纯文本扩展到视频理解——同时编码帧内空间坐标和帧间时间索引。

### 17.1 统一时空位置编码

ERNIE 5.0 处理视频输入时，每个 token 具有三个维度的位置信息：

| 位置维度 | 符号 | 含义 | 编码方式 |
|---------|------|------|---------|
| 时间 (Temporal) | $t_i$ | 第几帧 | RoPE 旋转 $\theta_t$ |
| 高度 (Height) | $h_i$ | 帧内纵坐标 | RoPE 旋转 $\theta_h$ |
| 宽度 (Width) | $w_i$ | 帧内横坐标 | RoPE 旋转 $\theta_w$ |

**Uni-RoPE 的核心公式**——将三个维度的旋转组合到不同的 head 维度组：

将 $d$ 个 head 维度分为三组（按 RoPE 频率带分配）：
- 组 1 (高频维度): 编码高度 $h_i$
- 组 2 (中频维度): 编码宽度 $w_i$
- 组 3 (低频维度): 编码时间 $t_i$

$$
\text{Uni-RoPE}(x, t_i, h_i, w_i)_g = \begin{cases}
R_{\theta_g^h}(h_i) \cdot x_g & g \in \text{Height Group} \\
R_{\theta_g^w}(w_i) \cdot x_g & g \in \text{Width Group} \\
R_{\theta_g^t}(t_i) \cdot x_g & g \in \text{Temporal Group}
\end{cases}
$$

其中 $R_\theta(p) \in \mathbb{R}^{2 \times 2}$ 为 RoPE 旋转矩阵（[见 3.2 节](#32-rope-rotary-position-embedding)）。

**中心对齐坐标**：空间坐标 $h_i, w_i$ 不是像素绝对值，而是相对于帧中心的归一化坐标：

$$
h_i^{\text{aligned}} = \frac{h_i - H/2}{H/2}, \quad w_i^{\text{aligned}} = \frac{w_i - W/2}{W/2}
$$

跨尺度的中心对齐意味着：不同分辨率的帧中，同一语义位置（如"画面中心偏上"）具有相同或相近的坐标值，使模型能泛化到训练时未见过的新分辨率。

### 17.2 Next-Frame-and-Scale Prediction

Uni-RoPE 服务的核心训练任务：**Next-Frame-and-Scale Prediction**（下一帧与尺度预测）。

```
训练目标:
  给定前 k 帧 + 多个尺度
  → 预测下一帧在不同尺度下的 token 序列

  Frame 1 (256×256)  ─┐
  Frame 1 (512×512)  ─┤
  Frame 2 (256×256)  ─┼─→ 预测 Frame 3 的多尺度表示
  Frame 2 (512×512)  ─┘
```

Uni-RoPE 在这个任务中的价值：时空坐标独立编码使 Attention 可以分别检索"同帧不同位置"（空间 Attention）和"同位置不同帧"（时间 Attention），而不混淆两者。

---

## 18. 新兴技术总结

| 技术 | 提出模型 | 解决的问题 | 核心方法 | 代价 |
|------|---------|-----------|---------|------|
| **Muon 优化器** | Moonlight / Kimi-K2 | 比 AdamW 更高 token 效率 | Newton-Schulz 梯度正交化 | 需要 QK-Clip 防 logit 爆炸 |
| **QK-Clip** | Kimi-K2 | Muon 导致的 attention logit 爆炸 | per-head Q/K 权重范数裁剪 ($\tau=100$) | 前 30% 步数触发，之后自动停用 |
| **Hybrid NS** | DeepSeek-V4 | Newton-Schulz 计算量大 | 两阶段：快收敛 → 精正交 | — |
| **MTP** | DeepSeek-V3/V4 | 提升 token 预测密度 | 顺序预测多个未来 token + 投机解码 | D=3 时 ~7-9% FLOPs |
| **GLM-5 MTP** | GLM-5 | MTP 参数量大 | 3 层共享参数，推理只用 1 层 | 零额外参数 |
| **Fast-MTP** | Step3.5-Flash | MTP 远距离预测噪声大 | position-aware loss 重加权 | — |
| **mHC** | DeepSeek-V4 | 深层网络信号传播不稳定 | Birkhoff Polytope 约束超连接 ($n_{hc}=4$) | 6.7% wall-time |
| **Uni-RoPE** | ERNIE 5.0 | 视频的时空位置编码 | 分维度的统一 RoPE (t/h/w) | 仅影响视频模态 |

> **趋势判断**：
> 1. **优化器演进**：Muon 代表"结构化梯度更新"方向，AdamW 的逐坐标缩放不再是唯一选择，但稳定性机制（QK-Clip 等）是落地前提
> 2. **多 Token 预测**：MTP 正在成为大模型训练标配——训练开销小、推理收益大（~1.8× TPS），"训一得二"
> 3. **连接结构**：标准残差连接已经 8 年未变，mHC 是首次对其做根本性重构并获得正向收益的尝试
> 4. **位置编码泛化**：Uni-RoPE 表明 RoPE 可以自然扩展到多模态，关键是将不同物理维度的频率带分离

---

## 参考资料

- [Attention Is All You Need](https://arxiv.org/abs/1706.03762) — Transformer 原始论文
- [LLaMA: Open and Efficient Foundation Language Models](https://arxiv.org/abs/2302.13971) — Llama 架构设计参考
- [RoFormer](https://arxiv.org/abs/2104.09864) — RoPE 位置编码
- [Moonlight](https://arxiv.org/abs/2502.16982) — Muon 优化器实证
- [DeepSeek-V4 Technical Report](./refs/DeepSeek_V4_Technical_Report.pdf) — MTP/mHC/QK-Clip 参考

> **下一篇**：[LLM 注意力机制发展与演进](./03-LLM注意力机制发展与演进.md) — 深入 Transformer 的核心组件
