# FlashAttention 全栈剖析

> FlashAttention 是 LLM 优化中最具代表性的案例——它从算法层面重新组织计算顺序，消除了 O(N²) 的显存写入，最终映射到 GPU 的 SMEM、Tensor Core、TMA 等硬件特性。本文追踪从标准 Attention 的瓶颈出发，经过分块算法、在线 Softmax、Kernel 实现，直到 Hopper 架构优化的完整链路。

### 关键术语

| 缩写 | 全称 | 含义 |
|------|------|------|
| SMEM | Shared Memory | 共享内存，Block 内共享 |
| RF | Register File | 寄存器文件，线程私有 |
| TMA | Tensor Memory Accelerator | 张量内存加速器（Hopper） |
| WGMMA | Warp-Group Matrix Multiply-Accumulate | Warp 组级矩阵乘加（Hopper） |
| O(N²) | — | 与序列长度平方成正比 |
| Online Softmax | — | 在线 Softmax，无需完整数据即可计算 |
| FlashDecoding | — | 针对 Decode 阶段的 Attention 优化 |
| TMEM | Tensor Memory | 张量内存（Blackwell） |

---

## 1. 标准 Attention 的瓶颈

### 1.1 标准 Attention 的计算流程

```
输入: Q[S, d_head], K[S, d_head], V[S, d_head]

Step 1: S = Q × K^T / sqrt(d_head)    [S, S] 矩阵
Step 2: P = Softmax(S)                 [S, S] 矩阵
Step 3: O = P × V                      [S, d_head] 矩阵
```

02 篇推导过，S=4096 时这个 S×S 矩阵的大小：

```
S 矩阵: 64 heads × 4096 × 4096 × 2 Bytes = 2 GB
P 矩阵: 同上 = 2 GB

总计: 4 GB 的中间结果需要写入 HBM 再读出
```

### 1.2 瓶颈分析

```
标准 Attention 的访存:
  读 Q: S × d_head × sizeof = 4096 × 128 × 2 = 1 MB/head
  读 K: 1 MB/head
  读 V: 1 MB/head
  写 S: S × S × sizeof = 4096 × 4096 × 2 = 32 MB/head
  读 S: 32 MB/head (Softmax 需要)
  写 P: 32 MB/head
  读 P: 32 MB/head
  读 V: 1 MB/head
  写 O: 1 MB/head

  总访存: ~130 MB/head × 64 heads = ~8.3 GB

  对比: Q+K+V+O 只有 ~4 MB/head × 64 = ~256 MB
  中间结果 S+P 的访存是输入输出的 32×!
```

**核心问题**：S 和 P 矩阵必须写入 HBM，因为 Softmax 需要先读取完整的 S 才能计算。这 8 GB 的中间结果访存是 FlashAttention 要消除的目标。

### 1.3 为什么不能简单融合

直觉上，可以把 QK^T + Softmax + PV 融合成一个 Kernel，避免中间结果写回 HBM。但 Softmax 有一个数学约束：

```
Softmax(S_i) = exp(S_i - max(S)) / Σ exp(S_j - max(S))

需要先知道:
  1. max(S) → 需要遍历整个 S 矩阵
  2. Σ exp(S_j) → 需要遍历整个 S 矩阵

→ 必须先算完整个 S 矩阵, 才能算 Softmax
→ 无法逐块计算 → 必须存储完整的 S 矩阵
```

FlashAttention 的突破在于：**重新组织 Softmax 的计算方式，使其可以逐块进行**。

---

## 2. 在线 Softmax：逐块计算的关键

### 2.1 标准 Softmax 的两遍算法

```
Pass 1: 计算 m = max(x) 和 l = Σ exp(x_i - m)
Pass 2: 计算 o_i = exp(x_i - m) / l

问题: 需要两遍遍历 → 中间结果必须存储
```

### 2.2 在线 Softmax 的一遍算法

关键观察：当新的最大值出现时，可以修正之前的累加结果。

```
初始化: m₀ = -∞, l₀ = 0, o₀ = 0

处理第 j 个元素 x_j:
  m_j = max(m_{j-1}, x_j)                     ← 更新最大值
  l_j = l_{j-1} × exp(m_{j-1} - m_j) + exp(x_j - m_j)  ← 修正分母
  o_j = (o_{j-1} × l_{j-1} × exp(m_{j-1} - m_j) + exp(x_j - m_j) × v_j) / l_j

  → 不需要存储所有 x_j, 只需维护 (m, l, o) 三个标量
```

**数值验证**：

```
x = [2, 1, 3]

标准 Softmax:
  m = 3, l = exp(2-3) + exp(1-3) + exp(3-3) = 0.368 + 0.135 + 1 = 1.503
  softmax = [0.368/1.503, 0.135/1.503, 1/1.503] = [0.245, 0.090, 0.665]

在线 Softmax:
  j=1: m=2, l=exp(0)=1, o=exp(0)×v₁=1×v₁
  j=2: m=2, l=1+exp(-1)=1.368, o=(1×v₁+0.368×v₂)/1.368
  j=3: m=3, l=1.368×exp(-1)+exp(0)=0.503+1=1.503
       o=(1.368×exp(-1)×o_prev+1×v₃)/1.503

  最终 l=1.503 ✓, 与标准算法一致
```

### 2.3 从在线 Softmax 到分块 Softmax

在线 Softmax 可以逐元素处理。分块 Softmax 将其推广到逐块处理：

```
将 x 分成 T 块: x = [x₁, x₂, ..., x_T]

处理第 t 块:
  m_new = max(m_old, max(x_t))
  l_new = l_old × exp(m_old - m_new) + Σ exp(x_t - m_new)
  o_new = (o_old × l_old × exp(m_old - m_new) + Σ exp(x_t - m_new) × v_t) / l_new

  → 每块只需存储 (m, l, o), 不需要存储完整的 S 矩阵
  → (m, l, o) 的大小是 O(S), 而不是 O(S²)
```

---

## 3. FlashAttention 算法：分块计算

### 3.1 分块策略

FlashAttention 将 Q、K、V 沿序列维度分块，在 SMEM 中完成分块内的 Attention 计算：

```
分块大小: B_r (Q 的行块), B_c (K/V 的行块)

外层循环: 遍历 K/V 的块 (j = 0, 1, ..., T_c)
  将 K_j, V_j 从 HBM 加载到 SMEM

  内层循环: 遍历 Q 的块 (i = 0, 1, ..., T_r)
    将 Q_i 从 HBM 加载到 SMEM
    计算 S_ij = Q_i × K_j^T / sqrt(d)    ← 在 SMEM 中
    用在线 Softmax 更新 O_i               ← 在 SMEM 中
    将更新后的 O_i 写回 HBM

关键: O_i 在内层循环中逐步更新, 不需要存储完整的 S 矩阵
```

### 3.2 访存量分析

```
FlashAttention 访存:
  读 Q: S × d_head × sizeof = 1 MB/head (与标准相同)
  读 K: T_r × S × d_head × sizeof / T_c  ← K 被重复读取 T_r 次
  读 V: T_r × S × d_head × sizeof / T_c  ← V 被重复读取 T_r 次
  写 O: S × d_head × sizeof = 1 MB/head (与标准相同)
  读写 l, m: O(S) × sizeof (可忽略)

  总访存 ≈ (1 + T_r/T_c + T_r/T_c + 1) × 1 MB/head
         = (2 + 2×T_r/T_c) MB/head

  T_r = T_c 时: ~4 MB/head × 64 = ~256 MB

  对比标准: ~8.3 GB → FlashAttention 访存减少 ~32×!
```

**代价**：K 和 V 被重复读取（外层循环每迭代一次，内层循环重新读取 Q 的不同块）。但重复读取 K/V 的开销远小于避免写入 S/P 矩阵的收益。

### 3.3 SMEM 容量约束

分块大小受 SMEM 容量约束：

```
SMEM 需要存储:
  Q_i: B_r × d_head × sizeof
  K_j: B_c × d_head × sizeof
  V_j: B_c × d_head × sizeof
  S_ij: B_r × B_c × sizeof (中间结果)

H100 SMEM: 228 KB/SM

BF16, d_head=128:
  Q_i + K_j + V_j = (B_r + 2×B_c) × 128 × 2 = (B_r + 2×B_c) × 256 Bytes
  S_ij = B_r × B_c × 2 Bytes

  典型配置: B_r = 64, B_c = 64
  Q+K+V = (64 + 128) × 256 = 49 KB
  S = 64 × 64 × 2 = 8 KB
  总计: ~57 KB → 远小于 228 KB, 有余量做双缓冲
```

---

## 4. FlashAttention-2：优化 Warp 级并行

### 4.1 FlashAttention-1 的问题

FlashAttention-1 的内层循环中，每个 Warp 独立处理一部分 Q 的行：

```
FA-1 的 Warp 分配:
  4 个 Warp, 每个 Warp 处理 B_r/4 行 Q
  Warp 0: Q[0:B_r/4] × K → S[0:B_r/4, :] → Softmax → × V → O[0:B_r/4]
  Warp 1: Q[B_r/4:B_r/2] × K → ...
  ...

问题: Softmax 和 ×V 是串行的 → Warp 必须等待 Softmax 完成
      → Warp 间没有协作 → 无法利用 Tensor Core 做 ×V
```

### 4.2 FlashAttention-2 的改进

FlashAttention-2 重新分配 Warp 的职责，让 4 个 Warp 协作完成同一个分块的计算：

```
FA-2 的 Warp 分配:
  4 个 Warp 协作处理 B_r 行 Q
  Q × K^T: 4 个 Warp 各负责 K 的一部分列 → 并行 GEMM
  Softmax: 每个 Warp 独立完成自己行的 Softmax
  P × V: 4 个 Warp 各负责 V 的一部分行 → 并行 GEMM

改进:
  1. Q×K^T 和 P×V 都使用 Tensor Core → 计算效率更高
  2. 减少了 Warp 间的同步点
  3. 更好的 SMEM 访问模式
```

**性能对比**：

```
A100 上的 Attention 计算时间 (S=4096, d_head=128):
  标准 PyTorch: ~8.5 ms
  FlashAttention-1: ~2.3 ms (3.7× 加速)
  FlashAttention-2: ~1.5 ms (5.7× 加速)
```

---

## 5. FlashAttention-3：Hopper 架构优化

### 5.1 Hopper 的三个关键特性

FlashAttention-3 充分利用 Hopper 的硬件特性：

```
1. WGMMA: 数据来源从 RF 变为 SMEM
   → Q 和 K 可以留在 SMEM, Tensor Core 直接读取
   → 省去了手动加载到 RF 的开销

2. TMA: 异步数据搬运
   → TMA 加载下一块 Q/K/V 的同时, Tensor Core 在计算当前块
   → 实现计算-搬运流水线化

3. Warp 特化:
   → 2 个 Warp 专门做数据搬运 (通过 TMA)
   → 2 个 Warp 专门做计算 (通过 WGMMA)
   → 搬运和计算真正并行
```

### 5.2 双缓冲流水线

```
时间 →  TMA Warp          Compute Warp
t0     [加载 Q0, K0, V0]   [空闲]
t1     [加载 Q1, K1, V1]   [计算 Q0×K0, Softmax, ×V0]
t2     [加载 Q2, K2, V2]   [计算 Q1×K1, Softmax, ×V1]
...

条件: TMA 加载时间 < 计算时间
  TMA 加载 ~57 KB: ~57KB / 3.35TB/s ≈ 0.017 ms
  WGMMA 计算: ~0.05 ms (取决于分块大小)
  → 计算时间 > 加载时间 → 流水线有效
```

### 5.3 异步 Softmax

FA-3 的另一个创新：将 Softmax 的部分操作与 WGMMA 重叠：

```
传统: Q×K → [等待] → Softmax → [等待] → P×V
FA-3:  Q×K → [Softmax 的 exp 和求和与下一次 Q×K 重叠] → P×V

利用 Hopper 的异步执行能力:
  WGMMA 是异步的 → 发起 WGMMA 后可以立即做 Softmax
  → Softmax 的计算被"隐藏"在 WGMMA 的延迟中
```

### 5.4 性能对比

```
H100 上的 Attention 计算时间 (S=4096, d_head=128, BF16):
  FlashAttention-2: ~1.2 ms
  FlashAttention-3: ~0.6 ms (2× 加速)

  H100 FP16 理论峰值: 989 TFLOPS
  FA-3 实际算力: ~620 TFLOPS → MFU ~63%
  (Attention 的 MFU 通常低于 FFN, 因为 Softmax 和归约操作不是 GEMM)
```

---

## 6. FlashDecoding：Decode 阶段的 Attention

### 6.1 Decode 阶段 Attention 的特殊性

```
Decode 阶段:
  Q: [1, d_head] (只有 1 个新 Token)
  K: [S, d_head] (之前所有 Token 的 KV Cache)
  V: [S, d_head]

  QK^T: [1, d_head] × [d_head, S] = [1, S]
  → 不是矩阵乘法, 是向量-矩阵乘
  → Tensor Core 利用率极低 (M=1)
  → 变成访存密集操作
```

### 6.2 FlashDecoding 的策略

FlashDecoding 沿 K/V 的序列维度分块，并行处理：

```
标准 FlashAttention:
  外层循环 K/V 块, 内层循环 Q 块
  → Q 只有 1 行, 内层循环只有 1 次迭代
  → 串行处理 K/V 块 → 无法并行

FlashDecoding:
  将 K/V 沿序列维度分成 T 块
  每个 Block 处理一块 K/V:
    Block i: Q × K_i^T → Softmax_i → × V_i → 部分 O_i
  最后: 合并所有部分 O_i (需要修正 Softmax 的分母)

  合并公式:
    O = Σ (l_i × exp(m_i - m_global) / l_global) × O_i
    m_global = max(m_1, m_2, ..., m_T)
    l_global = Σ l_i × exp(m_i - m_global)
```

**效果**：当 S 很大时（如 128K），可以将 K/V 分成数百块，数百个 Block 并行计算。

---

## 7. 从算法到硬件的完整映射

### 7.1 FlashAttention 的全栈映射

```
算法层:
  在线 Softmax → 逐块更新 (m, l, o) → 消除 O(S²) 中间结果
  ↓
算子层:
  分块策略: Q/K/V 分块 → SMEM 中计算 → 避免写回 HBM
  访存量从 O(S²) 降到 O(S) → 带宽瓶颈缓解
  ↓
编程层:
  FA-1: Warp 级并行 + SMEM 双缓冲
  FA-2: Warp 协作 + Tensor Core GEMM
  FA-3: WGMMA + TMA + Warp 特化 + 异步 Softmax
  ↓
微架构层:
  SMEM 容量 → 决定分块大小
  Tensor Core 吞吐 → 决定计算上限
  TMA 引擎 → 决定数据搬运能否与计算重叠
  Bank Conflict → 影响 SMEM 访问效率
  ↓
芯片层:
  HBM 带宽 → 决定标准 Attention 的瓶颈
  SM 数量 → 决定并行度
  寄存器容量 → 决定 WGMMA 的 Occupancy
```

### 7.2 每一代优化的硬件依赖

| 优化 | 依赖的硬件特性 | 无此特性时的替代 |
|------|---------------|----------------|
| 分块计算 | SMEM | 无法实现 (必须写回 HBM) |
| 在线 Softmax | 无特殊依赖 | 纯算法创新 |
| FA-2 Warp 协作 | Tensor Core (HMMA) | 可用 CUDA Core 但慢 |
| FA-3 WGMMA | Hopper WGMMA | 退化为 FA-2 |
| FA-3 TMA | Hopper TMA | 用 cp.async 替代但效率低 |
| FA-3 异步 Softmax | Hopper 异步执行 | 退化为同步 Softmax |
| FlashDecoding | 大量并行 Block | 串行处理长序列 |

**核心洞察**：算法创新（在线 Softmax）是基础，硬件特性（SMEM、Tensor Core、TMA）是加速器。没有算法创新，硬件再快也要写 O(S²) 中间结果；没有硬件支持，算法再好也无法高效实现。

---

## 8. 要点回顾

| 要点 | 说明 |
|------|------|
| 标准 Attention 瓶颈 | S/P 矩阵 O(S²) 写回 HBM，访存量是输入输出的 32× |
| 在线 Softmax | 逐块更新 (m, l, o)，消除完整 S 矩阵的存储需求 |
| FA 分块策略 | Q/K/V 分块在 SMEM 中计算，访存从 O(S²) 降到 O(S) |
| FA-2 改进 | Warp 协作 + Tensor Core，A100 上 5.7× 加速 |
| FA-3 改进 | WGMMA + TMA + Warp 特化，H100 上 2× 于 FA-2 |
| FlashDecoding | 沿 K/V 序列维度并行，解决 Decode 阶段 M=1 的低效问题 |
| 硬件依赖 | 算法创新是基础，硬件特性是加速器，二者缺一不可 |
| SMEM 约束 | 分块大小受 228 KB SMEM 限制，需精心设计数据布局 |

---

## 参考资料

- [FlashAttention: Fast and Memory-Efficient Exact Attention with IO-Awareness](https://arxiv.org/abs/2205.14135) — FlashAttention-1
- [FlashAttention-2: Faster Attention with Better Parallelism and Work Partitioning](https://arxiv.org/abs/2307.08691) — FlashAttention-2
- [FlashAttention-3: Fast and Accurate Attention with Asynchrony and Low-precision](https://arxiv.org/abs/2407.08608) — FlashAttention-3
- [Online normalizer calculation for softmax](https://arxiv.org/abs/1805.02867) — 在线 Softmax 数学证明
- [FlashDecoding for long-context inference](https://crfm.stanford.edu/2023/10/12/flashdecoding.html) — FlashDecoding
- [NVIDIA Hopper Architecture Whitepaper](https://resources.nvidia.com/en-us-tensor-core) — WGMMA + TMA

> 前置阅读：[02-Transformer计算特征第一性分析](./02-Transformer计算特征第一性分析.md)、[03-GPU架构：从SIMT到TensorCore](./03-GPU架构-从SIMT到TensorCore.md)
> 下一篇：[08-互连拓扑与AI芯片](./08-互连拓扑与AI芯片.md)
