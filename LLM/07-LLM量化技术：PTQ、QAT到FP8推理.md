# LLM 量化技术：PTQ/QAT 到 FP8 推理

> **核心命题**：量化是 LLM 推理部署的核心技术——将 FP16/BF16 模型压缩到 8-bit、4-bit 甚至更低，在保持精度的前提下大幅降低显存和带宽需求。理解量化 = 理解数值精度与模型质量的权衡。

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

```
线性量化 (Uniform Quantization):

  浮点值 x → 定点值 x_q:
    x_q = round((x - zero_point) / scale)
    
  反量化:
    x ≈ (x_q - zero_point) × scale

其中:
  scale = (x_max - x_min) / (2^bits - 1)
  zero_point = round(-x_min / scale)

对称量化 (Symmetric):
  zero_point = 0
  scale = max(|x|) / (2^(bits-1) - 1)
  
  → 更简单，硬件友好
  → 但对非对称分布效果差
```

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

Group Size 对精度的影响:
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

```
NF4: 专为正态分布数据设计的 4-bit 格式

假设权重服从 N(0, σ²):
  将正态分布的 CDF 等分为 16 个区间
  每个区间的期望值作为量化值

NF4 量化值:
  [-1.0, -0.6962, -0.5251, -0.3949, -0.2844, -0.1848, 
   -0.0911, 0.0, 0.0796, 0.1609, 0.2461, 0.3379, 
   0.4407, 0.5626, 0.7230, 1.0]

→ 对 LLM 权重的量化效果优于 INT4
→ QLoRA 的核心技术之一
```

---

## 量化方法分类

### 3.1 PTQ vs QAT

```
PTQ (Post-Training Quantization):
  训练完成后直接量化，不需要重新训练
  代表: GPTQ, AWQ, SmoothQuant
  优点: 快速，不需要训练数据
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

数学:
  量化第 q 列后的误差:
    E = (w_q - quant(w_q))² / [H^{-1}]_{qq}
  
  补偿剩余列:
    δ_F = -(w_q - quant(w_q)) / [H^{-1}]_{qq} × H^{-1}_{:,q}
```

### 4.2 GPTQ 算法流程

```
GPTQ 算法:

输入: 权重矩阵 W ∈ R^{d_row × d_col}, 校准数据 X
输出: 量化后的权重 W_q

1. 计算 Hessian: H = 2 × X^T X + λI
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
```

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

数学:
  原始: y = W × x
  AWQ:  y = (W × diag(s)) × (diag(s)^{-1} × x)
        = W_scaled × x_scaled
  
  量化: y ≈ quant(W_scaled) × x_scaled

为什么有效:
  - 放大 salient 通道的权重 → 量化相对误差减小
  - 缩小对应的激活 → 激活量化误差也减小
  - 缩放因子通过网格搜索找到最优值
```

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

原始: y = X × W

变换: y = (X × diag(s)^{-1}) × (diag(s) × W)
      = X_smooth × W_smooth

其中 s_j = max(|X_j|)^α / max(|W_j|)^(1-α)

α 控制迁移量:
  α = 0: 全部迁移到权重 (W_smooth 有 outlier)
  α = 1: 全部迁移到激活 (X_smooth 有 outlier)
  α = 0.5: 均衡 (推荐)

效果:
  - 激活的 outlier 被平滑
  - 权重的分布变化不大 (权重本来就容易量化)
  - 可以同时量化权重和激活 (W8A8)
```

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
