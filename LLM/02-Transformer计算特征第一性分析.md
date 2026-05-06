# Transformer 计算特征第一性分析

> LLM 优化的起点是定量理解"每个算子算多少、访多少"。本文从公式推导出发，用 LLaMA-2 70B 的具体数值，逐层拆解 Transformer 的计算量和访存量，建立算术强度的直觉。

### 关键术语

| 缩写 | 全称 | 含义 |
|------|------|------|
| GEMM | General Matrix Multiply | 通用矩阵乘法 |
| FFN | Feed-Forward Network | 前馈网络 |
| SwiGLU | Swish-Gated Linear Unit | 门控线性单元激活函数 |
| MHA | Multi-Head Attention | 多头注意力 |
| GQA | Grouped-Query Attention | 分组查询注意力 |
| MLA | Multi-head Latent Attention | 多头潜在注意力 |
| MoE | Mixture of Experts | 混合专家模型 |
| KV Cache | Key-Value Cache | 推理时缓存的注意力键值对 |
| AI | Arithmetic Intensity | 算术强度，FLOPS/Byte |
| RMSNorm | Root Mean Square Normalization | 均方根归一化 |
| RoPE | Rotary Position Embedding | 旋转位置编码 |

---

## 1. 为什么要做第一性分析

优化 LLM 训练推理，首先要回答两个问题：

1. **计算量有多大？** → 决定了需要多少算力（GPU 数量、训练时间）
2. **访存量有多大？** → 决定了是否受带宽约束（计算密集 vs 访存密集）

这两个问题的答案不是查表得到的，而是从 Transformer 的数学定义推导出来的。本文用 LLaMA-2 70B 作为贯穿全篇的具体案例：

| 参数 | 符号 | 值 |
|------|------|-----|
| 隐藏维度 | d_model | 8192 |
| 注意力头数 | n_heads | 64 |
| KV 头数 (GQA) | n_kv_heads | 8 |
| 每头维度 | d_head | 128 |
| FFN 中间维度 | d_ff | 28672 |
| 层数 | n_layers | 80 |
| 序列长度 | S | 4096 |
| 词表大小 | V | 32000 |
| 精度 | — | BF16 (2 Bytes) |

---

## 2. 矩阵乘法的计算量与访存量

Transformer 的核心计算是矩阵乘法。先建立 GEMM 的一般公式，后面逐层套用。

### 2.1 GEMM 的一般公式

对于 `C[M,N] = A[M,K] × B[K,N]`：

**计算量**：每个输出元素需要 K 次乘加（2K FLOPS），共 M×N 个输出元素：

```
FLOPS = 2 × M × K × N
```

**访存量**：需要读取 A、B，写入 C：

```
Bytes = (M×K + K×N + M×N) × sizeof(dtype)
```

**算术强度**：

```
AI = FLOPS / Bytes = 2×M×K×N / ((M×K + K×N + M×N) × sizeof)
```

### 2.2 两种极端情况

**情况一：M 和 N 很大，K 较小**（典型：Attention 的 QK^T）

```
A[S, d_head], B[d_head, S] → C[S, S]
FLOPS = 2 × S × d_head × S = 2 × S² × d_head
Bytes ≈ 2 × S² × sizeof  (S² 项主导)
AI ≈ d_head / sizeof

d_head=128, BF16: AI ≈ 64 → 计算密集（但 S 很小时可能不够）
```

**情况二：M=1，N 和 K 很大**（典型：Decode 阶段的线性投影）

```
A[1, K], B[K, N] → C[1, N]
FLOPS = 2 × K × N
Bytes ≈ (K + K×N + N) × sizeof ≈ K×N × sizeof  (B 矩阵主导)
AI ≈ 2 / sizeof = 1  (BF16)

AI = 1 → 极度访存密集！
```

**这就是训练和推理优化方向不同的数学根源**：同一个线性投影算子，训练时 M=S（AI≈S/2），推理时 M=1（AI≈1）。

---

## 3. 逐层推导：Attention

### 3.1 QKV 投影

```
Q = X × Wq    [S, d_model] × [d_model, n_heads × d_head]
K = X × Wk    [S, d_model] × [d_model, n_kv_heads × d_head]
V = X × Wv    [S, d_model] × [d_model, n_kv_heads × d_head]
```

三个 GEMM，但 K/V 的输出维度不同（GQA 下 n_kv_heads < n_heads）：

```
Q 投影: FLOPS = 2 × S × d_model × n_heads × d_head
K 投影: FLOPS = 2 × S × d_model × n_kv_heads × d_head
V 投影: FLOPS = 2 × S × d_model × n_kv_heads × d_head

总计: 2 × S × d_model × d_head × (n_heads + 2 × n_kv_heads)
```

代入 LLaMA-2 70B 数值：

```
= 2 × 4096 × 8192 × 128 × (64 + 2×8)
= 2 × 4096 × 8192 × 128 × 80
= 2 × 4096 × 8192 × 10240
= 687,194,767,360 ≈ 687 GFLOPS
```

访存量（BF16）：

```
权重: (d_model × n_heads × d_head + 2 × d_model × n_kv_heads × d_head) × 2
     = (8192 × 8192 + 2 × 8192 × 1024) × 2
     = (67,108,864 + 16,777,216) × 2
     = 167,772,160 Bytes ≈ 160 MB

输入+输出: (S × d_model + S × (n_heads + 2×n_kv_heads) × d_head) × 2
         = (4096 × 8192 + 4096 × 10240) × 2
         ≈ 151 MB

总计: ~311 MB
AI = 687 GFLOPS / 311 MB ≈ 2210 → 计算密集
```

### 3.2 QK^T 和 Attention × V

```
Attn = Q × K^T / sqrt(d_head)    [n_heads, S, d_head] × [n_heads, d_head, S]
Output = Attn × V                 [n_heads, S, S] × [n_heads, S, d_head]
```

这两个操作的计算量：

```
QK^T:  FLOPS = 2 × n_heads × S² × d_head
Attn×V: FLOPS = 2 × n_heads × S² × d_head
总计: 4 × n_heads × S² × d_head
```

代入数值：

```
= 4 × 64 × 4096² × 128
= 4 × 64 × 16,777,216 × 128
= 550,731,776,000 ≈ 551 GFLOPS
```

**关键观察：计算量与 S² 成正比**。S 从 4096 增长到 128K 时：

```
S=128K: 4 × 64 × (128000)² × 128 = 5.4 × 10¹⁴ ≈ 540 TFLOPS/层
80 层: 43,200 TFLOPS → 单步仅 Attention 就需要 43 PFLOPS
```

这就是长上下文训练如此昂贵的原因——Attention 的 O(S²) 计算量。

### 3.3 Softmax：访存密集的"小"操作

Softmax 的计算量不大，但算术强度极低：

```
计算量: ~5 × n_heads × S² (减最大值、指数、求和、除法、乘V)
访存量: ~4 × n_heads × S² × sizeof(dtype) (读 Attn、写 Attn)

AI ≈ 5 / (4 × 2) ≈ 0.625 → 极度访存密集
```

**为什么 Softmax 必须与前后 GEMM 融合？** 因为它单独执行时，需要将整个 S×S 矩阵写回 HBM 再读出来，而 S=4096 时这个矩阵有 64×4096×4096×2 = 2 GB。FlashAttention 的核心价值就是**避免这个写回-读出**。

### 3.4 Output 投影

```
Output = Concat(Attn×V) × Wo    [S, n_heads × d_head] × [n_heads × d_head, d_model]
FLOPS = 2 × S × n_heads × d_head × d_model = 2 × S × d_model²
```

代入数值：`2 × 4096 × 8192² = 549,755,813,888 ≈ 550 GFLOPS`

### 3.5 Attention 层汇总

| 操作 | FLOPS | 占比 | AI | 类型 |
|------|-------|------|-----|------|
| QKV 投影 | 687G | 38% | ~2210 | 计算密集 |
| QK^T | 275G | 15% | ~64 | 计算密集 |
| Softmax | ~5G | <1% | ~0.6 | 访存密集 |
| Attn×V | 275G | 15% | ~64 | 计算密集 |
| Output 投影 | 550G | 30% | ~2210 | 计算密集 |
| **总计** | **~1.8T** | — | — | — |

**核心结论**：Attention 层 99% 的计算量在 GEMM（计算密集），但 Softmax 是访存密集的"刺头"——必须融合。

---

## 4. 逐层推导：FFN

### 4.1 SwiGLU FFN

现代 LLM 使用 SwiGLU 激活函数，FFN 包含 3 个 GEMM：

```
gate = X × Wgate    [S, d_model] × [d_model, d_ff]
up   = X × Wup      [S, d_model] × [d_model, d_ff]
out  = (silu(gate) ⊙ up) × Wdown    [S, d_ff] × [d_ff, d_model]
```

计算量：

```
Gate: 2 × S × d_model × d_ff
Up:   2 × S × d_model × d_ff
Down: 2 × S × d_ff × d_model
总计: 6 × S × d_model × d_ff
```

代入数值：

```
= 6 × 4096 × 8192 × 28672
= 5,764,607,518,720 ≈ 5.76 TFLOPS
```

**对比 Attention 的 1.8 TFLOPS，FFN 是 Attention 的 3.2 倍**——FFN 占了 Transformer 层约 76% 的计算量。

### 4.2 SiLU + Element-wise Mul：另一个访存密集操作

```
计算量: 3 × S × d_ff (silu + 乘法)
访存量: 4 × S × d_ff × sizeof (读 gate + 读 up + 写中间 + 读中间)
AI ≈ 3 / (4 × 2) ≈ 0.375 → 极度访存密集
```

与 Softmax 一样，必须与前后 GEMM 融合。

### 4.3 FFN 层汇总

| 操作 | FLOPS | 占比 | AI | 类型 |
|------|-------|------|-----|------|
| Gate + Up 投影 | 3.84T | 67% | ~2048 | 计算密集 |
| SiLU + Mul | ~0.35G | <0.1% | ~0.4 | 访存密集 |
| Down 投影 | 1.92T | 33% | ~2048 | 计算密集 |
| **总计** | **~5.76T** | — | — | — |

---

## 5. 整层汇总与关键比例

### 5.1 单层 Transformer 的计算量

```
Attention: ~1.8 TFLOPS
FFN:       ~5.8 TFLOPS
其他:      ~0.1 TFLOPS (RMSNorm, Residual)
总计:      ~7.7 TFLOPS/层

80 层总计: ~616 TFLOPS/步
```

**Attention vs FFN 的比例**：约 24% vs 76%。这意味着优化 FFN 的 GEMM 效率比优化 Attention 更能提升整体性能——但 Attention 有 O(S²) 的内存问题，所以 FlashAttention 仍然是最重要的优化。

### 5.2 全模型训练一步的计算量

```
FLOPS_per_step = 6 × P × tokens_per_step
               = 6 × 70 × 10⁹ × (batch_size × S)

batch_size=4, S=4096:
  = 6 × 70 × 10⁹ × 16384
  = 6.88 × 10¹⁵ ≈ 6.88 PFLOPS/步
```

系数 6 的来源：前向传播 2P（每个参数参与 2 次 FLOPS 的矩阵乘），反向传播 4P（梯度对输入和对权重各 2P）。

### 5.3 全模型参数量验证

```
Embedding: V × d_model = 32000 × 8192 = 262M
每层:
  QKV 投影: d_model × (n_heads + 2×n_kv_heads) × d_head = 8192 × 10240 = 84M
  Output 投影: d_model × d_model = 67M
  Gate + Up: 2 × d_model × d_ff = 2 × 8192 × 28672 = 470M
  Down: d_ff × d_model = 235M
  每层小计: ~856M
80 层: 80 × 856M = 68.5B
LM Head: d_model × V = 262M (通常与 Embedding 共享权重)

总计: 262M + 68.5B + 262M ≈ 69B (与官方 70B 接近，差异来自舍入)
```

---

## 6. KV Cache：推理的显存瓶颈

### 6.1 KV Cache 大小推导

推理时，每个新 Token 的 Attention 需要读取之前所有 Token 的 K 和 V。为避免重复计算，将已计算的 K/V 缓存：

```
每 Token 的 KV Cache 大小:
  = 2 × n_kv_heads × d_head × n_layers × sizeof(dtype)
  = 2 × 8 × 128 × 80 × 2
  = 327,680 Bytes ≈ 320 KB/Token
```

**不同上下文长度的 KV Cache 总量**：

| 上下文长度 | KV Cache 大小 | 说明 |
|-----------|--------------|------|
| 4K | 1.25 GB | 短对话 |
| 32K | 10 GB | 长文档 |
| 128K | 40 GB | 极长上下文 |

### 6.2 KV Cache 如何限制并发

以 2×H100 (TP=2) 运行 LLaMA-2 70B 为例：

```
每卡 HBM: 80 GB
每卡权重: 70 GB / 2 = 35 GB
剩余给 KV Cache: 80 - 35 - 5(运行时) = 40 GB

4K 上下文每请求 KV: 1.25 GB / 2 = 0.625 GB/卡
最大并发: 40 / 0.625 ≈ 64 请求

128K 上下文每请求 KV: 40 GB / 2 = 20 GB/卡
最大并发: 40 / 20 = 2 请求!
```

**128K 上下文只能同时服务 2 个请求**——KV Cache 是推理并发的硬限制。

### 6.3 GQA 和 MLA 如何缓解

| 机制 | n_kv_heads | 每 Token KV | 128K 并发数 |
|------|-----------|-------------|------------|
| MHA (n_kv=64) | 64 | 2.56 MB | <1 |
| GQA (n_kv=8) | 8 | 320 KB | 2 |
| GQA (n_kv=1, MQA) | 1 | 40 KB | 16 |
| MLA (压缩维度 512) | — | 20 KB | 32 |

MLA 的 KV Cache 只存储压缩后的潜在向量（512 维 vs 原始 128×128=16384 维），压缩比 ~32×。这是 DeepSeek-V3 能在有限显存下服务长上下文的关键架构创新。

---

## 7. MoE 的计算特征

### 7.1 MoE 如何改变计算量

Dense FFN 每个 Token 激活全部参数，MoE 只激活 top_k 个专家：

```
Dense FFN 计算量/Token: 6 × d_model × d_ff
MoE FFN 计算量/Token:   6 × d_model × d_ff × top_k

Dense FFN 参数量: 3 × d_model × d_ff
MoE FFN 参数量:   n_experts × 3 × d_model × d_ff
```

**DeepSeek-V3 的 MoE**：256 个路由专家 + 1 个共享专家，每 Token 激活 8 个路由专家 + 1 个共享专家：

```
总参数: (256+1) × 3 × 7168 × 18432 + 其他 ≈ 671B
激活参数/Token: (8+1) × 3 × 7168 × 18432 + 其他 ≈ 37B

参数效率: 37B / 671B = 5.5% → 用 5.5% 的参数量获得接近 Dense 模型的效果
```

### 7.2 MoE 引入的通信代价

MoE 的代价是 All-to-All 通信。每层每个 Token 需要：

```
1. 计算 Token → 专家的路由
2. All-to-All Dispatch: 将 Token 发送到目标专家所在 GPU
3. 计算专家输出
4. All-to-All Combine: 将输出返回原 GPU

通信量 ≈ 2 × S × d_ff × sizeof(dtype) / EP
```

DeepSeek-V3 报告 All-to-All 通信占比约 20%——这是 MoE 相比 Dense 模型 MFU 更低的主要原因。

---

## 8. 要点回顾

| 要点 | 说明 |
|------|------|
| GEMM 公式 | FLOPS = 2MK N, AI ≈ 2/sizeof (M=1 时) |
| 训练 vs 推理 | 同一算子训练时 AI≈S/2（计算密集），推理时 AI≈1（访存密集） |
| Attention O(S²) | QK^T 和 Attn×V 与 S² 成正比，长上下文的核心瓶颈 |
| FFN 占 76% | FFN 计算量是 Attention 的 3.2×，优化 FFN GEMM 效率收益更大 |
| Softmax/SiLU | 算术强度 <1，必须与前后 GEMM 融合 |
| KV Cache | 推理并发的硬限制，128K 上下文只能并发 2 请求 (70B, TP=2) |
| GQA/MLA | KV Cache 压缩 4-32×，是架构层面的推理优化 |
| MoE 代价 | 用 5.5% 激活参数获得大参数量效果，但 All-to-All 通信占 20% |

---

## 参考资料

- [LLaMA: Open and Efficient Foundation Language Models](https://arxiv.org/abs/2302.13971) — LLaMA 架构定义
- [GQA: Training Generalized Multi-Query Transformer Models](https://arxiv.org/abs/2305.13245) — GQA
- [DeepSeek-V3 Technical Report](https://arxiv.org/abs/2412.19437) — MLA + MoE
- [FlashAttention: Fast and Memory-Efficient Exact Attention](https://arxiv.org/abs/2205.14135) — 算术强度分析
- [Mixtral of Experts](https://arxiv.org/abs/2401.04088) — MoE 计算特征

> 前置阅读：[01-全景导论](./01-全景导论.md)
> 下一篇：[03-GPU架构：从SIMT到TensorCore](./03-GPU架构-从SIMT到TensorCore.md)
