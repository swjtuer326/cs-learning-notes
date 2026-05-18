# LLM 量化技术：PTQ/QAT 到 FP8 推理

> **核心命题**：量化是 LLM 推理部署的核心技术——将 FP16/BF16 模型压缩到 8-bit、4-bit 甚至更低，在保持精度的前提下大幅降低显存和带宽需求。理解量化 = 理解数值精度与模型质量的权衡。

### 关键术语

| 缩写 | 全称 | 含义 |
|------|------|------|
| PTQ | Post-Training Quantization | 训练后量化，不重新训练直接压缩模型精度 |
| QAT | Quantization-Aware Training | 量化感知训练，训练中模拟低精度效果 |
| GPTQ | Generative Pre-trained Transformer Quantization | 基于 Hessian 的逐层最优权重量化方法 |
| AWQ | Activation-Aware Weight Quantization | 激活感知权重量化，保留显著权重通道精度 |
| NF4 | NormalFloat 4-bit | 信息论最优的 4-bit 浮点数据类型，QLoRA 基础 |
| FP8 | Floating Point 8-bit | 8-bit 浮点格式（E4M3/E5M2），Hopper+ 硬件支持 |
| TE | Transformer Engine | NVIDIA 的 FP8 精度管理库，自动选择 scaling factor |
| GGUF | GPT-Generated Unified Format | llama.cpp 使用的量化模型文件格式 |

### 前置知识

| 需要了解 | 参考文档 |
|----------|----------|
| Transformer 结构与训练算法 | [02-Transformer完整结构](./02-Transformer完整结构与训练算法.md) |
| GPU 架构与精度格式 | [deep-dive: NVIDIA GPU架构演进](./deep-dive/11-NVIDIA-GPU架构演进与LLM.md) |

## 目录

1. [量化基础](#量化基础)
2. [数值格式](#数值格式)
3. [量化方法分类](#量化方法分类)
4. [GPTQ：基于 Hessian 的权重量化](#gptq基于-hessian-的权重量化)
5. [AWQ：激活感知的权重量化](#awq激活感知的权重量化)
6. [SmoothQuant：激活量化的平滑方案](#smoothquant激活量化的平滑方案)
7. [KV Cache 量化](#kv-cache-量化)
8. [FP8 推理与训练](#fp8-推理与训练)
9. [实践工具与选择指南](#实践工具与选择指南)

---

## 量化基础

### 1.1 为什么需要量化

```
LLM 推理的瓶颈:

  显存:
    70B FP16 模型: 140GB 权重
    + KV Cache: 数十 GB
    → 需要多张 H100 (80GB)
  
  带宽:
    每个 token 需要读取全部 140GB 权重
    H100 带宽 3.35TB/s → 理论最大 ~24 tokens/s
    → 实际更低 (计算开销)

量化收益:
  8-bit: 显存减半, 带宽减半 → 吞吐 ~2×
  4-bit: 显存 1/4, 带宽 1/4 → 吞吐 ~4×
```

### 1.2 量化基本公式

线性量化（Uniform Quantization）：

$$
x \to x_q
$$

$$
x_q = \text{round}\left(\frac{x - \text{zero\_point}}{\text{scale}}\right)
$$

$$
x \approx (x_q - \text{zero\_point}) \cdot \text{scale}
$$

其中 $x$ 是原始浮点值，$x_q$ 是量化后的定点值。

$$
\text{scale} = \frac{x_{\max} - x_{\min}}{2^{\text{bits}} - 1}
$$

$$
\text{zero\_point} = \text{round}\left(\frac{-x_{\min}}{\text{scale}}\right)
$$

对称量化特例（scale = 绝对值最大值映射）：

$$
\text{zero\_point} = 0, \quad \text{scale} = \frac{\max(|x|)}{2^{\text{bits}-1} - 1}
$$

→ 更简单，硬件友好
→ 但对非对称分布效果差

### 1.3 量化粒度

```
Per-Tensor:
  整个张量共享一个 scale 和 zero_point
  → 最简单，但精度损失大

Per-Channel (Per-Row/Per-Column):
  每个通道 (如矩阵的每一行) 有独立的 scale
  → 精度更好，计算稍复杂

Per-Group:
  每 N 个元素共享一个 scale (如 N=128)
  → 精度最好，但需要更多存储
  → GPTQ/AWQ 的默认选择

Group Size 对精度的影响（数据来自 GPTQ/AWQ 论文）:
  group=128: 精度损失 ~0.5%
  group=64:  精度损失 ~0.2%
  group=32:  精度损失 ~0.1%
  → 但 group 越小，scale 存储越多
```

---

## 数值格式

### 2.1 常见格式对比

| 格式 | 总位数 | 指数位 | 尾数位 | 动态范围 | 精度 | 硬件支持 |
|------|--------|--------|--------|---------|------|---------|
| **FP32** | 32 | 8 | 23 | ~3.4e38 | 最高 | 所有 GPU |
| **FP16** | 16 | 5 | 10 | ~65504 | 高 | V100+ |
| **BF16** | 16 | 8 | 7 | ~3.4e38 | 中 | A100+ |
| **FP8 E4M3** | 8 | 4 | 3 | ~448 | 中 | H100+ |
| **FP8 E5M2** | 8 | 5 | 2 | ~57344 | 低 | H100+ |
| **INT8** | 8 | - | - | [-128,127] | 中 | 所有 GPU |
| **INT4** | 4 | - | - | [-8,7] | 低 | 部分 GPU |
| **NF4** | 4 | - | - | [-1,1] | 中 | 软件实现 |

### 2.2 NF4 (NormalFloat4)

NF4: 专为正态分布数据设计的 4-bit 格式

假设权重服从 $N(0, \sigma^2)$，其中 $\sigma^2$ 为权重分布的方差:
  将正态分布的 CDF 等分为 16 个区间
  每个区间的期望值作为量化值

NF4 量化值:
  [-1.0, -0.6962, -0.5251, -0.3949, -0.2844, -0.1848, 
   -0.0911, 0.0, 0.0796, 0.1609, 0.2461, 0.3379, 
   0.4407, 0.5626, 0.7230, 1.0]

→ 对 LLM 权重的量化效果优于 INT4
→ QLoRA 的核心技术之一

---

## 量化方法分类

### 3.1 PTQ vs QAT

```
PTQ (Post-Training Quantization):
  训练完成后直接量化，不需要重新训练
  代表: GPTQ, AWQ, SmoothQuant
  优点: 快速，不需要重新训练（但需少量校准数据确定 clip range）
  缺点: 精度损失可能较大

QAT (Quantization-Aware Training):
  在训练过程中模拟量化效果
  代表: LLM-QAT, BitNet
  优点: 精度损失小
  缺点: 需要重新训练，成本高
```

### 3.2 量化对象

```
LLM 中可以量化的对象:

1. 权重 (Weight Quantization):
   - 最常用，收益最大
   - 方法: GPTQ, AWQ, bitsandbytes

2. 激活 (Activation Quantization):
   - 进一步减少显存和计算
   - 挑战: 激活值分布变化大 (不同 token 不同)
   - 方法: SmoothQuant, ZeroQuant

3. KV Cache (KV Cache Quantization):
   - 减少推理时的显存占用
   - 方法: KIVI, KVQuant, FlexGen

4. 梯度 (Gradient Quantization):
   - 训练时减少通信量
   - 方法: QSGD, 1-bit Adam
```

---

## GPTQ：基于 Hessian 的权重量化

### 4.1 核心思想

```
GPTQ (GPT Post-Training Quantization):

  基于 OBQ (Optimal Brain Quantization) 的逐层量化方法

核心思想:
  1. 逐列量化权重矩阵
  2. 每次量化一列后，用 Hessian 信息补偿剩余列的误差
  3. 不需要重新训练，只需要少量校准数据
```

数学：

$$
E = \frac{(w_q - \text{quant}(w_q))^2}{[H^{-1}]_{qq}} \tag{1}
$$

其中 $E$ 是量化误差，$w_q$ 是当前列权重，$[H^{-1}]_{qq}$ 是 Hessian 逆矩阵的第 $q$ 个对角元。

$$
\delta_F = -\frac{w_q - \text{quant}(w_q)}{[H^{-1}]_{qq}} \cdot H^{-1}_{:,q} \tag{2}
$$

其中 $\delta_F$ 是补偿项向量，$H^{-1}_{:,q}$ 是 Hessian 逆矩阵的第 $q$ 列。

### 4.2 GPTQ 算法流程

GPTQ 算法:

输入: 权重矩阵 $\mathbf{W} \in \mathbb{R}^{d_{\text{row}} \times d_{\text{col}}}$, 校准数据 X
输出: 量化后的权重 W_q

1. 计算 Hessian: $\mathbf{H} = 2 \mathbf{X}^T \mathbf{X} + \lambda \mathbf{I}$，其中 $\lambda$ 是阻尼系数，$\mathbf{I}$ 是单位矩阵
2. 计算 H^{-1} (Cholesky 分解)
3. 对权重矩阵的每一列 (按固定顺序):
   a. 量化当前列: w_q = quant(w)
   b. 计算量化误差: Δw = w - w_q
   c. 更新剩余列: W[:, q+1:] -= Δw / [H^{-1}]_{qq} × H^{-1}_{q, q+1:}
4. 重复直到所有列量化完成

关键优化:
  - Lazy Batch Update: 批量更新减少内存访问
  - Cholesky Reformulation: 数值更稳定
  - 固定顺序: 避免随机顺序的不确定性

### 4.3 GPTQ 使用

```python
from auto_gptq import AutoGPTQForCausalLM, BaseQuantizeConfig

# 配置
quantize_config = BaseQuantizeConfig(
    bits=4,                # 4-bit 量化
    group_size=128,        # 每 128 个元素共享 scale
    desc_act=False,        # 是否按激活值排序 (True 精度更好但更慢)
    damp_percent=0.01,     # Hessian 阻尼系数
)

# 加载并量化
model = AutoGPTQForCausalLM.from_pretrained(
    "meta-llama/Llama-2-7b-hf",
    quantize_config=quantize_config,
)

# 准备校准数据
model.quantize(calibration_dataset)

# 保存
model.save_quantized("llama-2-7b-gptq-4bit")
```

---

## AWQ：激活感知的权重量化

### 5.1 核心洞察

```
AWQ (Activation-aware Weight Quantization):

关键发现:
  并非所有权重同等重要!
  
  与大幅值激活对应的权重通道 (salient channels)
  对模型精度影响更大 → 应该保留更高精度

观察:
  权重矩阵 W 中，某些列的激活值幅值特别大
  → 这些列对应的权重对输出贡献大
  → 量化这些列的误差影响也大
```

### 5.2 AWQ 方法

```
AWQ 的解决方案: Per-Channel Scaling

1. 找到 salient channels (激活幅值大的通道)
2. 在量化前对这些通道的权重乘以一个缩放因子 s > 1
3. 量化后再除以 s (在激活侧补偿)
```

数学：

$$
\mathbf{y} = \mathbf{W} \mathbf{x} = (\mathbf{W} \cdot \operatorname{diag}(\mathbf{s})) \cdot (\operatorname{diag}(\mathbf{s})^{-1} \cdot \mathbf{x})
$$

其中 $\mathbf{s}$ 是 per-channel 缩放因子向量，$\operatorname{diag}(\mathbf{s})$ 是以 $\mathbf{s}$ 为对角元的对角矩阵。

$$
\mathbf{y} \approx \text{quant}(\mathbf{W}_{\text{scaled}}) \cdot \mathbf{x}_{\text{scaled}}
$$

为什么有效:
  - 放大 salient 通道的权重 → 量化相对误差减小
  - 缩小对应的激活 → 激活量化误差也减小
  - 缩放因子通过网格搜索找到最优值

### 5.3 AWQ vs GPTQ

| 维度 | GPTQ | AWQ |
|------|------|-----|
| **原理** | Hessian-based 误差补偿 | 激活感知的 per-channel scaling |
| **校准数据** | 128 × 2048 tokens | 少量样本 (甚至 1 个) |
| **量化速度** | 慢 (需要 Hessian 计算) | 快 (只需统计激活) |
| **精度 (4-bit)** | 好 | 更好 (尤其小模型) |
| **硬件友好** | 需要 group-wise 反量化 | 支持 INT4 GEMM |
| **生态** | AutoGPTQ, vLLM | vLLM, TensorRT-LLM |

---

## SmoothQuant：激活量化的平滑方案

### 6.1 激活量化的挑战

```
激活量化的困难:

  权重: 分布稳定，容易量化
  激活: 分布随输入变化，且存在 outlier

Outlier 问题:
  某些通道的激活值比其他通道大 100-1000×
  → 如果 per-tensor 量化，outlier 主导 scale
  → 大部分正常值被量化到 0
  → 精度崩溃

解决方案:
  - Per-token 量化: 每个 token 独立 scale (但计算复杂)
  - SmoothQuant: 将量化难度从激活转移到权重
```

### 6.2 SmoothQuant 原理

```
SmoothQuant 核心: 数学等价变换
```

原始变换：

$$
\mathbf{y} = \mathbf{X} \mathbf{W} = (\mathbf{X} \cdot \operatorname{diag}(\mathbf{s})^{-1}) \cdot (\operatorname{diag}(\mathbf{s}) \cdot \mathbf{W})
$$

$$
s_j = \frac{\max(|X_j|)^{\alpha}}{\max(|W_j|)^{1-\alpha}} \tag{3}
$$

其中 $\alpha \in [0, 1]$ 是迁移控制参数：$\alpha = 0.5$ 表示均衡分配，$\alpha = 1$ 表示全部迁移到权重，$\alpha = 0$ 表示全部迁移到激活。

α 控制迁移量:
  α = 0: 全部迁移到权重 (W_smooth 有 outlier)
  α = 1: 全部迁移到激活 (X_smooth 有 outlier)
  α = 0.5: 均衡 (推荐)

效果:
  - 激活的 outlier 被平滑
  - 权重的分布变化不大 (权重本来就容易量化)
  - 可以同时量化权重和激活 (W8A8)

### 6.3 SmoothQuant 使用

```python
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

model = AutoModelForCausalLM.from_pretrained("meta-llama/Llama-2-7b-hf")

# 收集激活统计
def collect_activation_stats(model, calibration_data):
    stats = {}
    # ... 前向传播，记录每层的激活值
    return stats

# 计算平滑因子
def compute_smooth_scales(act_stats, weight, alpha=0.5):
    act_max = act_stats.abs().max(dim=0).values
    weight_max = weight.abs().max(dim=1).values
    scales = (act_max.pow(alpha) / weight_max.pow(1 - alpha))
    return scales

# 应用平滑
for layer in model.model.layers:
    # Q, K, V 投影
    scales = compute_smooth_scales(act_stats[layer], layer.self_attn.q_proj.weight)
    layer.self_attn.q_proj.weight.data *= scales[:, None]
    # 对应的激活在 forward 时除以 scales
```

---

## KV Cache 量化

### 7.1 为什么需要 KV Cache 量化

```
KV Cache 显存分析 (Llama-3-8B, 推理):

  单个 token 的 KV Cache:
    K: h_kv × d_h × 2 bytes = 8 × 128 × 2 = 2KB
    V: h_kv × d_h × 2 bytes = 8 × 128 × 2 = 2KB
    每层: 4KB
    32 层: 128KB per token

  并发 100 请求, 平均 4096 tokens:
    100 × 4096 × 128KB = 50GB!
    
  → KV Cache 可能比模型权重还大!
  → 量化 KV Cache 是提升并发能力的关键
```

### 7.2 KV Cache 量化方法

| 方法 | 量化精度 | 特点 |
|------|---------|------|
| **KIVI** | KV 各 4-bit | 按通道量化，key 和 value 分开处理 |
| **KVQuant** | KV 各 4-bit | per-channel + per-token 混合 |
| **FlexGen** | KV 各 4-bit | 与 offload 结合 |
| **WKVQuant** | W4-KV4 | 权重和 KV Cache 联合量化 |
| **GEAR** | KV 各 4-bit + 稀疏 | 量化 + 稀疏化组合 |

### 7.3 KIVI 方法

```
KIVI (Key-Value cache quantization with Importance-aware):

核心思想:
  1. Key 和 Value 的分布不同 → 需要不同的量化策略
  2. 新生成的 token 更重要 → 保留更高精度

Key 量化:
  - Per-channel 量化 (每个 head 的每个 channel 独立 scale)
  - Key 的 channel 间方差大

Value 量化:
  - Per-token 量化 (每个 token 独立 scale)
  - Value 的 token 间方差大

精度保留:
  - 最近的 N 个 token 保留 FP16
  - 更早的 token 使用 4-bit
  → 兼顾精度和压缩
```

---

## FP8 推理与训练

### 8.1 FP8 格式

```
FP8 两种格式 (IEEE 标准):

E4M3 (用于前向):
  1 sign + 4 exponent + 3 mantissa
  范围: ±448, 精度: ~0.07%
  → 精度更高，适合前向传播

E5M2 (用于反向):
  1 sign + 5 exponent + 2 mantissa
  范围: ±57344, 精度: ~0.15%
  → 范围更大，适合梯度 (可能出现大值)

FP8 vs INT8:
  FP8 优势:
  - 动态范围大 (不需要 per-channel scaling)
  - 对 outlier 更鲁棒
  - 硬件原生支持 (H100+)
  
  INT8 优势:
  - 精度更高 (对均匀分布)
  - 更成熟的生态
```

### 8.2 FP8 推理流程

```
FP8 推理 (NVIDIA TensorRT-LLM):

1. 量化:
   - 权重: FP16 → FP8 (离线，per-tensor scaling)
   - 激活: FP16 → FP8 (在线，动态 scaling)

2. GEMM:
   - FP8 × FP8 → FP32 累加
   - H100 FP8 Tensor Core: 2× FP16 吞吐

3. 反量化:
   - 输出: FP8 → FP16/BF16

性能:
  Llama-2-70B on H100:
    FP16: ~15 tokens/s
    FP8:  ~28 tokens/s (1.9× 加速)
```

### 8.3 FP8 训练

```
FP8 训练 (NVIDIA Transformer Engine):

关键挑战:
  1. 梯度范围大 → 需要 E5M2 格式
  2. 权重更新小 → 需要 FP16/FP32 master weights
  3. Scaling factor 需要动态更新

流程:
  1. 前向: FP8 E4M3 GEMM
  2. 反向: FP8 E5M2 GEMM
  3. 梯度 All-Reduce: FP16 (保持精度)
  4. 权重更新: FP32 master weights → 量化到 FP8

延迟 Scaling:
  不立即更新 scaling factor
  而是用历史统计量
  → 减少 scaling 更新开销
```

---

## FP4 量化与 Blackwell 推理

> Blackwell (B200/B100) 引入 FP4 Tensor Core 和 Tensor Memory (TMEM)，将权重量化推至 4-bit 浮点。FP4 不是简单的"FP8 再砍一半"——其极低的尾数位（1 bit）要求全新的缩放策略和硬件协同。

### 9.1 FP4 数据格式

FP4 (E2M1) 的位分配：

$$
\text{FP4}: \underbrace{1}_{\text{sign}} \cdot \underbrace{2}_{\text{exponent}} \cdot \underbrace{1}_{\text{mantissa}} = 4 \text{ bits}
$$

| 格式 | 符号 | 指数 | 尾数 | 可表示值数量 | 动态范围 |
|------|------|------|------|------------|---------|
| FP8 (E4M3) | 1 | 4 | 3 | 448 | ±448 |
| FP8 (E5M2) | 1 | 5 | 2 | 576 | ±57344 |
| **FP4 (E2M1)** | 1 | 2 | 1 | **12** | ±6 |

仅 12 个可表示值 → 必须配合 per-block scaling factor 使用。

### 9.2 FP4 的缩放策略

FP4 量化的核心挑战：1-bit 尾数无法区分同一量级内的不同值。

**Block-wise Scaling**：

$$
x_q = \text{FP4}\left(\frac{x}{s_{\text{block}}}\right), \quad s_{\text{block}} = \max(|x_{\text{block}}|)
$$

| 缩放粒度 | block 大小 | 精度 | 额外存储 |
|---------|-----------|------|---------|
| Per-tensor | 整个张量 | 差 | 1 个 scalar |
| Per-channel | 每行/列 | 中 | $N$ 个 scalar |
| **Per-block** | 16-32 元素 | 好 | $N \times C / 32$ 个 scalar |

Blackwell 的 Tensor Core 原生支持 per-block FP4 GEMM：硬件自动在 32 元素 block 内应用缩放因子。

### 9.3 Blackwell 的 FP4 Tensor Core

```
Blackwell FP4 GEMM 流程:

  Weight (FP4 + block scaling factors)
      │
      ▼
  TMEM (Tensor Memory): 存储解缩放后的中间结果
      │
      ▼
  Tensor Core: FP4 × FP4 → FP32 累加
      │
      ▼
  输出: FP32 / BF16

关键硬件特性:
- TMEM: Tensor Core 近端存储 (~1MB/SM), 减少寄存器溢出
- FP4 GEMM 吞吐: ~2.5 PFLOPS (B200), 是 FP8 的 ~2×
- 自动缩放: 硬件内建 block scaling factor 应用逻辑
```

### 9.4 FP4 量化实践

| 方法 | 原理 | 精度损失 | 适用场景 |
|------|------|---------|---------|
| **NVFP4** | NVIDIA 官方 FP4 量化方案，per-block scaling + 校准数据 | ~1-2% (vs BF16) | Blackwell 推理 |
| **QoQ (Quattor)** | 4-bit 量化 + 2-bit 激活 + 在线缩放 | ~2-3% | 极致压缩推理 |
| **FP4 QAT** | 训练中模拟 FP4 量化效果 | <1% | 需要重新训练 |

**NVFP4 量化流程**：

```
1. 校准: 用 128-512 样本统计每 block 的缩放因子
2. 量化: 权重从 BF16 → FP4 (per-block scaling)
3. 验证: 在 benchmark 上对比 BF16 基线
4. 部署: TensorRT-LLM / vLLM 加载 FP4 模型

典型结果 (Llama-3-70B):
- 模型大小: 140GB (BF16) → 35GB (FP4), 4× 压缩
- 推理吞吐: ~2× 提升 (vs FP8)
- 精度: MMLU -1.2%, HumanEval -0.8%
```

### 9.5 FP4 vs FP8 vs INT4 对比

| 维度 | FP4 (E2M1) | FP8 (E4M3) | INT4 |
|------|-----------|-----------|------|
| **动态范围** | ±6 | ±448 | [-8, 7] |
| **精度** | 极低 (1-bit mantissa) | 中 (3-bit mantissa) | 低 (均匀量化) |
| **硬件支持** | Blackwell+ | Hopper+ | 通用 |
| **缩放策略** | 必须 per-block | per-tensor 可接受 | per-channel |
| **压缩比** | 4× (vs BF16) | 2× (vs BF16) | 4× (vs BF16) |
| **推理加速** | ~2× (vs FP8) | ~2× (vs BF16) | ~2-3× (vs BF16) |
| **成熟度** | 早期 (2025) | 成熟 | 成熟 |

---

## 实践工具与选择指南

### 9.1 工具对比

| 工具 | 支持精度 | 方法 | 特点 |
|------|---------|------|------|
| **bitsandbytes** | 4-bit, 8-bit | NF4, FP4, INT8 | 最简单，transformers 原生集成 |
| **AutoGPTQ** | 2/3/4/8-bit | GPTQ | 最成熟的 GPTQ 实现 |
| **llama.cpp** | 2/3/4/5/6/8-bit | GGUF (K-quant) | CPU 推理首选 |
| **AWQ** | 4-bit | AWQ | 精度好，vLLM 支持 |
| **TensorRT-LLM** | FP8, INT8, INT4 | 多种 | 性能极致，NVIDIA 官方 |
| **vLLM** | GPTQ, AWQ, FP8 | 多种 | 推理框架内置支持 |
| **Quanto** | 2/4/8-bit | 多种 | HuggingFace 官方，灵活 |
| **HQQ** | 1/2/3/4/8-bit | Half-Quadratic | 极低 bit 量化 |

### 9.2 选择指南

```
场景 → 推荐方案:

快速体验 (单卡 24GB, 7B 模型):
  → bitsandbytes 4-bit (load_in_4bit=True)
  → 一行代码搞定

生产部署 (追求吞吐):
  → TensorRT-LLM + FP8 (H100)
  → 或 vLLM + AWQ 4-bit

CPU 推理:
  → llama.cpp + Q4_K_M (GGUF)
  → 平衡速度和精度

移动端/边缘:
  → llama.cpp + Q2_K 或 Q3_K_S
  → 极致压缩

训练中量化:
  → bitsandbytes 8-bit (load_in_8bit=True)
  → 或 QLoRA (4-bit base + LoRA)

追求最低精度损失:
  → AWQ 4-bit + group_size=64
  → 或 FP8 (H100+)
```

### 9.3 量化效果参考

```
Llama-3-8B 量化效果 (Perplexity on WikiText-2):

  FP16:      6.14 (baseline)
  INT8:      6.15 (+0.01)
  FP8:       6.15 (+0.01)
  INT4-GPTQ: 6.28 (+0.14)
  INT4-AWQ:  6.24 (+0.10)
  NF4:       6.30 (+0.16)
  INT3:      7.12 (+0.98)
  INT2:      12.45 (+6.31)

→ 8-bit: 几乎无损
→ 4-bit: 轻微损失，可接受
→ 3-bit: 明显退化
→ 2-bit: 严重退化，不推荐
```

---

> **关键原则**：
> 1. **8-bit 是安全区**：几乎无损，应作为默认选择
> 2. **4-bit 是甜点区**：精度损失可控，显存减半
> 3. **权重量化 > 激活量化**：权重量化收益最大，实现最简单
> 4. **KV Cache 量化是高并发关键**：KV Cache 可能比权重还大
> 5. **FP8 是未来**：硬件原生支持，兼顾精度和性能

---

## FP8 混合精度训练（DeepSeek-V3）

### 10.1 为什么需要 FP8 训练

```
传统混合精度训练 (BF16/FP16):
  前向: BF16 → 反向: BF16 → 权重更新: FP32
  问题: BF16 计算吞吐有限，H100 FP8 Tensor Core 可达 2× BF16 吞吐

FP8 训练的挑战:
  1. 动态范围: E4M3 仅 ±448，训练中激活和梯度可能溢出
  2. 累加精度: FP8 Tensor Core 累加器仅 ~14 bits，大 K 维度 GEMM 累积误差显著
  3. 敏感层: Embedding、Output Head、Attention softmax 等对精度敏感，直接 FP8 会发散
```

### 10.2 整体框架：混合精度策略

DeepSeek-V3 将训练中的算子按精度敏感性分为两类：

```
精度敏感算子 (保留 BF16/FP32):
  - Embedding 层
  - Output Head (lm_head)
  - MoE Gating 网络
  - RMSNorm / LayerNorm
  - Attention Softmax
  - 残差加法

GEMM 密集算子 (FP8):
  - QKV 投影 (Linear)
  - Attention Output 投影
  - FFN 上/下投影 (含 MoE Expert)
  → 训练中 ~95% 的 FLOPs 在 FP8 中完成
```

### 10.3 细粒度量化方案

核心思路：**per-group scaling + tile-wise quantization**，以小粒度缩放弥补 FP8 动态范围不足。

激活量化：`1 × 128` tile，采用 **per-token-per-128-channels** 策略：

```
激活矩阵 X: [T, H] (T=tokens, H=hidden_dim)

Step 1 — Per-token-per-128-channel 分组:
  X 沿 H 维度切分为 H/128 个组，每组 128 个 channel
  每个 token 在每个组内独立计算 scale (max-abs)

Step 2 — Online Quantization:
  scale = max(|x_group|) / max(E4M3)   # 实时计算，无历史统计
  x_fp8 = round(x_fp16 / scale)        # 缩放后取整到 E4M3
```

权重量化：`128 × 128` block，采用 **per-128-in-per-128-out** 策略：

```
权重矩阵 W: [H_out, H_in]

Step 1 — 分块:
  W 切分为多个 128×128 block
  每个 block 独立计算 scale

Step 2 — Per-group Scaling:
  scale 沿 GEMM 内维度 K 方向定义
  → K 方向 sum 时各 group 的 scale 保持一致
  → 方便 Tensor Core 高效实现
```

细粒度量化的核心公式。对矩阵乘法 $\mathbf{C} = \mathbf{A} \mathbf{B}$，将内维度 K 分为 $N_G$ 个 group：

$$
\mathbf{C} = \sum_{g=0}^{N_G-1} \mathbf{A}_g \mathbf{B}_g
$$

FP8 量化后：

$$
\mathbf{C} \approx \sum_{g=0}^{N_G-1} (\text{scale}_A^g \cdot \text{scale}_B^g) \cdot (\mathbf{A}_g^{\text{fp8}} \times \mathbf{B}_g^{\text{fp8}})
$$

### 10.4 CUDA Core Promotion：解决累加精度瓶颈

```
问题:
  H800 FP8 Tensor Core MMA 累加器精度 ~14 bits (FP16 级别)
  当 K 维度较大 (如 8192)，128 次累加后累计误差 ~O(√K)
  → 直接输出 FP8 精度不够

方案: CUDA Core Promotion
  每隔 N_C = 128 个 K 维度元素:
    1. Tensor Core 完成 128 次 MMA 累加 (FP16 累加器)
    2. 将部分结果 Promote 到 CUDA Core 的 FP32 寄存器
    3. 在 FP32 中进行 group 间累加
    4. 下个 128 元素的 MMA 使用新的清零 FP16 累加器
```

Warpgroup 交替调度实现 MMA 与 Promotion 的流水线重叠：

```
  时间线:
    Warpgroup 0: [MMA 128] → [Promotion] → [MMA 128] → ...
    Warpgroup 1:    [Promotion] → [MMA 128] → [Promotion] → ...
  
  → MMA 和 Promotion 在两组 warpgroup 间交替执行
  → 隐藏 Promotion 延迟，吞吐几乎无损
```

### 10.5 尾数优先策略

DeepSeek-V3 全部采用 **E4M3** 格式（不混用 E5M2）：

| 策略 | 说明 |
|------|------|
| **只用 E4M3** | 前向、反向均用 E4M3，不引入 E5M2 |
| **细粒度缩放补偿动态范围** | tile-wise scale 使每个 tile 的实际数值范围适配 FP8 |
| **尾数精度优先** | E4M3 的 3-bit 尾数 > E5M2 的 2-bit 尾数 → 量化误差更小 |

### 10.6 Online Quantization

与 NVIDIA Transformer Engine 的延迟 scaling（delay scaling）不同，DeepSeek-V3 采用 **完全在线量化**：

```
Transformer Engine (Delay Scaling):
  用历史 amax 的指数移动平均更新 scale
  → 有滞后，需要维护状态

DeepSeek-V3 (Online Quantization):
  每个 tile/block 实时计算 max-abs
  当前 forward/backward 即刻使用当前 scale
  → 无状态，无历史依赖
  → 精度更优，但需要在线 max-abs reduction kernel
```

### 10.7 低精度存储与通信

训练中非 GEMM 部分的精度也做了系统性的降低：

```
BF16 AdamW Moments:
  优化器状态 (m, v) 存储为 BF16 而非 FP32
  → 优化器显存减半
  → 对收敛无显著影响 (AdamW 指数衰减本身有平滑效果)

FP8 Activation Caching (E5M6):
  前向激活缓存使用自定义 FP8 E5M6 格式 (1-5-6-6 非标准)
    1 sign + 5 exponent + 6 mantissa = 12 bits (打包策略存储为 8 bits/元素)
  → 用于 Wgrad 的反向重计算
  → 比 BF16 节省 50% 激活显存

FP8 MoE Dispatch:
  All-to-All 通信使用 FP8 格式
  → MoE 专家间 token 分发通信量减半
  → 大集群 MoE 训练的关键优化
```

### 10.8 验证结果

BF16 和 FP8 混合精度对比，在 DeepSeek-V3 训练至 1T tokens 时：

```
训练 Loss 对比:
  BF16 Baseline:    loss = L
  FP8 Mixed:        loss = L + ΔL,  ΔL/L < 0.25%

关键指标:
  - 1T tokens 全程无发散
  - 下游 Benchmark 得分与 BF16 等效 (±0.1%)
  - 训练吞吐提升 ~1.4× (H800, 相比 BF16)
```

---

## FP4 QAT（DeepSeek-V4）

### 11.1 FP4(E2M1) 格式

```
FP4 E2M1 格式:
  1 sign + 2 exponent + 1 mantissa
  可表示值: {-6.0, -4.0, -3.0, -2.0, -1.5, -1.0, -0.75, -0.5,
              0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0}
  总共 16 个离散值 (与 INT4 数量相同)
  → 更大的动态范围 + 非均匀分布 → 对 MoE expert 权重拟合更好
```

### 11.2 训练流程：FP4 → FP8 无损反量化

核心思想：**训练时用 FP4 存储参数，计算时无损提升至 FP8**，完全复用 FP8 训练框架。

```
FP4 → FP8 Lossless Dequantization 条件:

FP4(E2M1):  2-bit exponent
FP8(E4M3):  4-bit exponent
→ FP8 比 FP4 多 2 个 exponent bits

在 128×128 block 内，对 1×32 tile 做 per-tile scaling:
  scale_i = max(|W_tile_i|) / max(E4M3)
  反量化: W_fp8 = W_fp4 × scale_i

无损条件:
  scale_max / scale_min < 2^2 = 4  (FP8 额外的 2 个指数位范围)
  → 即同一 128×128 block 内各 tile 的 scale 差异不超出 4×
  → 训练中自然满足 (权重分布平滑)
```

训练时正向：

1. 权重以 FP4 存储
2. 反量化到 FP8（无损）
3. FP8 GEMM 计算
4. 反向：STE（Straight-Through Estimator）通过量化节点回传梯度

### 11.3 与 FP8 训练框架的复用

```
DeepSeek-V4 的 FP4 训练架构:
  ┌─────────────────────────────────────────┐
  │          FP8 Training Framework          │
  │  (GEMM kernel / Comm / Optimizer)        │
  ├─────────────────────────────────────────┤
  │  FP4 Storage Layer                       │
  │  ├─ FP4 weight buffer                   │
  │  └─ FP4 → FP8 lossless dequant          │
  ├─────────────────────────────────────────┤
  │  Quantization Targets:                   │
  │  ├─ MoE Expert Weights  → FP4(E2M1)     │
  │  ├─ Indexer Q/K          → FP4(E2M1)     │
  │  └─ Dense Layers         → BF16/FP8      │
  └─────────────────────────────────────────┘
```

### 11.4 推理时的原生 FP4 加载

RL rollout 和推理阶段直接使用原生 FP4 权重：

```
FP4 QAT vs FP4 PTQ (模拟):
  PTQ: 训练完 BF16 模型 → 离线量化到 FP4 → 推理时反量化到 FP8/FP16 计算
  QAT: 训练中 FP4 存储 → 推理时直接加载 FP4 → 原生 FP4 计算 kernel

QAT 优势:
  - Kernel 加载量: FP4 weight 仅为 FP8 的 50%
  - 推理显存: MoE expert 权重减半
  - 模型已适应 FP4 精度 → 无需反量化补偿
```

### 11.5 Indexer Q/K 量化

DeepSeek-V4 的 MoE 路由中使用 Indexer 机制，QK (Query-Key) 也参与 FP4 量化：

```
Indexer 的 QK 运算:
  原本: Query (BF16) × Key (BF16) → 路由分数
  量化后: Query (FP4) × Key (FP4)

特殊处理:
  QK 矩阵维度小但数量多 (每个 expert 一组)
  → FP4 量化显著减少 Indexer 参数量
  → Indexer QK 同类共用 scale tile
```

---

## 2-bit QAT（Apple Foundation Model）

### 12.1 核心问题：2-bit 量化的极端挑战

```
2-bit 量化的本质困难:
  仅 4 个取值 → 信息容量极低
  传统 INT2: {-2, -1, 0, 1} 或 {-1, 0, 1, 2}
  → 0 占据一个量化值，浪费表达能力
  → 权重分布中心化在 0 附近 → 大量值被量化到 0
```

### 12.2 Balanced Quantization Set

Apple FM 使用**平衡量化集**，避免 0 值的浪费：

$$
\mathcal{Q} = \{-1.5, -0.5, 0.5, 1.5\}
$$

```
平衡量化集的设计:
  无 0 值 → 4 个量化级全部用于有效值
  对称分布 → 硬件友好
  步长 = 1.0 → 简化计算
```

量化公式：

$$
w_q = s \cdot \operatorname{quant}(w / s), \quad \operatorname{quant}(x) = \arg\min_{q \in \mathcal{Q}} |x - q|
$$

其中 $s$ 是可学习的 per-channel scale factor。

### 12.3 可学习 Scale Factor：Newton-Raphson 初始化

Scale factor 不再是统计量（如 max-abs），而是**可学习参数**：

```
Scale 初始化 (Newton-Raphson):
  目标: 找到 s 使得量化误差 ||w - s * quant(w/s)|| 最小
  
  迭代过程:
    1. s_0 = max(|w|) / 1.5  (覆盖所有权重)
    2. s_{t+1} = s_t - f(s_t) / f'(s_t)
       其中 f(s) = w - s * round(w/s)，round 通过 STE 反传梯度
  
    → 3-5 次迭代收敛到局部最优

训练中:
  Scale 随 AdamW 一起更新 (梯度来自量化误差)
```

### 12.4 训练配置细节

| 配置项 | 选择 | 原因 |
|--------|------|------|
| **优化器** | AdamW | 相比 Adafactor，AdamW 的二阶矩估计对 2-bit 权重更新更稳定 |
| **Weight Decay** | 0 | 2-bit 权重表达能力有限，正则化会加速精度退化 |
| **EMA Smoothing** | 训练中维护 EMA 参数 | 推理时使用 EMA 权重，缓解 2-bit 训练的噪声波动 |
| **嵌入表** | 4-bit 量化 | 嵌入表参数量大但分布平滑，4-bit 足够 |
| **KV-Cache** | 8-bit 量化 | 平衡精度与显存 |

### 12.5 训练稳定性分析

```
2-bit QAT 的收敛挑战:
  - 4 个量化值 → 梯度信号极稀疏
  - 权重在量化值间跳跃 → loss 曲线波动大

Apple FM 的应对:
  1. Balanced Set (-1.5/-0.5/0.5/1.5): 避免 0 值陷阱
  2. 可学习 Scale: 自适应调整量化步长
  3. AdamW + 0 weight decay: 保留足够更新自由度
  4. EMA: 推理权重平滑，抑制训练噪声
  5. Warm-up: 前 N 步用 FP16 训练，再逐步引入 2-bit 量化
```

---

## W4A8 混合精度（GLM-5 on Ascend）

### 13.1 硬件背景：昇腾 NPU 的量化能力

```
昇腾 910B NPU 的矩阵计算能力:
  - 原生支持 INT8/FP16/BF16/FP32 GEMM
  - INT4 需要软件层面拆解为 INT8 + 反量化
  - FP8 支持有限 (910B，910C 原生支持)

GLM-5 的策略:
  充分利用昇腾 INT8 算力，INT4 用于极致压缩
  → 混合精度部署：W8A8 + W4A8
```

### 13.2 混合精度分配策略

```
GLM-5 每层的精度分配:

┌─────────────┬──────────────┬──────────────┐
│   模块       │  权重精度     │  激活精度     │
├─────────────┼──────────────┼──────────────┤
│ Attention QKV│ W8A8 (INT8)  │ INT8         │
│ Attention O  │ W8A8 (INT8)  │ INT8         │
│ MLP (Dense)  │ W8A8 (INT8)  │ INT8         │
│ MoE Experts  │ W4A8 (INT4)  │ INT8         │
│ MoE Gate     │ W8A8 (INT8)  │ INT8         │
│ Norm / Residual│ FP16       │ FP16         │
└─────────────┴──────────────┴──────────────┘

设计原则:
  - Dense 层计算量大 → 8-bit 保精度
  - MoE Expert 数量多 (数百个) → 4-bit 省显存是主要矛盾
  - 所有激活统一 INT8 → 简化 kernel 实现
```

### 13.3 QuaRot 离群值抑制

MoE W4A8 的核心挑战：激活中的 outlier 在 INT4 量化时会导致严重精度损失。

```
QuaRot (Quantization-aware Rotation) 原理:

  对权重和激活同时做正交旋转:
    W' = R @ W,  X' = X @ R^T
    其中 R 是随机正交矩阵 (Hadamard 或 Random)

  效果:
    旋转后的 W' 和 X' 中，outlier 被分散到所有通道
    → 每个通道的幅值分布更均匀
    → INT4 per-channel 量化误差大幅降低
```

在 GLM-5 中的应用：

1. 对 MoE expert 权重和输入激活同时旋转
2. 旋转矩阵 R 融合进相邻的 Norm/RMSNorm 参数中（无额外开销）
3. 旋转后做 INT4 量化 → 精度损失 < 0.5%

### 13.4 Flex_AWQ_SSZ 尺度校准

```
Flex_AWQ_SSZ (Search Scale with Zero-point) 校准流程:

  输入: 校准数据集 D, 权重 W, 目标 bit-width
  输出: per-channel scale s, per-channel zero-point z

  Step 1 — 统计激活分布:
    通过 D 跑前向，收集每个 channel 的激活统计

  Step 2 — Search Scale:
    对每个 channel，在 [0.5×s_0, 2×s_0] 范围内搜索最优 scale
    s_0 = max(|W_channel|) / max(INT4)
    目标: min ||WX - quant(W)X||_F

  Step 3 — Zero-point 校准:
    对非对称分布 channel 计算 zero-point
    z = round(mean(W_channel) / s)
    仅对 skew 显著的 channel 启用 (减少开销)
```

### 13.5 单节点部署 750B 模型

```
GLM-5 750B on 单台昇腾节点 (8 × 910B, 64GB/卡):

显存分解:
  Dense 权重 (W8):  ~80GB  (INT8)
  MoE 权重 (W4):    ~180GB (INT4, 原本 720GB FP16)
  KV-Cache:         ~80GB
  Overhead:         ~80GB
  ─────────────────
  总计:             ~420GB / 512GB (8 × 64GB)

关键收益:
  - 1 台昇腾节点 → 跑 750B 模型 (FP16 需 8+ 台)
  - MoE W4A8 使 Expert 权重成为 W8A8 的 1/2 → 突破显存瓶颈
  - 推理延迟: ~15 tokens/s (prefill ~200 tokens/s)
```

---

> **进阶要点**：
> 1. **FP8 训练已工程化**：DeepSeek-V3 证明 FP8 混合精度训练的 loss 偏差 < 0.25%，CuDA Core Promotion 解决了累加精度瓶颈
> 2. **FP4 QAT 是 MoE 的方向**：训练中 FP4 存储 + 无损反量化到 FP8 计算，推理时原生 FP4 加载，显存和带宽双收益
> 3. **2-bit 需要新的范式**：可学习 scale + balanced quantization set + EMA + 精心调参，是当前 2-bit 可行性的关键路径
> 4. **W4A8 是异构硬件的实用方案**：INT8 激活统一 + INT4 MoE 权重，使单节点部署千亿模型成为可能

---

## 参考资料

- [GPTQ](https://arxiv.org/abs/2210.17323) — 基于 Hessian 的权重量化
- [AWQ](https://arxiv.org/abs/2306.00978) — 激活感知权重量化
- [SmoothQuant](https://arxiv.org/abs/2211.10438) — 激活量化平滑方案
- [FP8 Formats for Deep Learning](https://arxiv.org/abs/2209.05433) — FP8 标准规格

> **下一篇**：[LLM 蒸馏与模型压缩](./10-LLM蒸馏与模型压缩.md) — 从量化走向更广泛的模型压缩
