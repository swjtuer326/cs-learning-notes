# FlashAttention 论文精读与 Triton 实践

> **核心命题**：Attention 是 Transformer 的性能瓶颈——O(n²) 的显存和计算。FlashAttention 通过 IO-aware 的算法设计，将 Attention 从显存瓶颈转变为计算瓶颈。理解 FlashAttention = 理解 GPU 的显存层次如何塑造算法设计。

## 目录

1. [Attention 的性能瓶颈](#attention-的性能瓶颈)
2. [FlashAttention-1：Tiling + Online Softmax](#flashattention-1tiling--online-softmax)
3. [FlashAttention-2：减少非矩阵运算](#flashattention-2减少非矩阵运算)
4. [FlashAttention-3：Hopper 架构特化](#flashattention-3hopper-架构特化)
5. [PagedAttention：KV Cache 管理](#pagedattentionkv-cache-管理)
6. [FlexAttention：可编程 Attention](#flexattention可编程-attention)
7. [Triton 入门与实践](#triton-入门与实践)
8. [手写 FlashAttention (Triton)](#手写-flashattention-triton)
9. [性能分析与调优](#性能分析与调优)

---

## Attention 的性能瓶颈

### 1.1 标准 Attention 的显存问题

```
标准 Attention 计算:

  Q, K, V ∈ R^{N × d}  (N = seq_len, d = head_dim)
  
  S = Q × K^T          ∈ R^{N × N}  ← O(N²) 显存!
  P = softmax(S)       ∈ R^{N × N}  ← O(N²) 显存!
  O = P × V            ∈ R^{N × d}

显存分析 (N=4096, d=128, FP16):
  S: 4096 × 4096 × 2 = 32MB
  P: 4096 × 4096 × 2 = 32MB
  → 每个 head 64MB, 32 heads = 2GB!
  → 仅 Attention 中间结果就 2GB

对于 N=128K:
  S: 128K × 128K × 2 = 32GB (单个 head!)
  → 完全不可行
```

### 1.2 GPU 显存层次

```
GPU 显存层次 (H100):

  ┌──────────────────────────────────────────────┐
  │  HBM (High Bandwidth Memory)                 │
  │  80GB, 3.35 TB/s                             │
  │  ┌──────────────────────────────────────┐    │
  │  │  L2 Cache                            │    │
  │  │  50MB, ~12 TB/s                      │    │
  │  │  ┌──────────────────────────────┐    │    │
  │  │  │  SM (Streaming Multiprocessor)│    │    │
  │  │  │  ┌────────────────────┐      │    │    │
  │  │  │  │  Shared Memory     │      │    │    │
  │  │  │  │  228KB/SM          │      │    │    │
  │  │  │  │  ~128 TB/s         │      │    │    │
  │  │  │  └────────────────────┘      │    │    │
  │  │  │  ┌────────────────────┐      │    │    │
  │  │  │  │  Register File     │      │    │    │
  │  │  │  │  256KB/SM          │      │    │    │
  │  │  │  │  ~256 TB/s         │      │    │    │
  │  │  │  └────────────────────┘      │    │    │
  │  │  └──────────────────────────────┘    │    │
  │  └──────────────────────────────────────┘    │
  └──────────────────────────────────────────────┘

关键洞察:
  HBM 带宽 << 计算吞吐 → Attention 是 IO-bound!
  
  H100: 989 TFLOPS (BF16) vs 3.35 TB/s HBM
  → 每个 byte 需要 ~300 FLOPS 才能计算 bound
  → Attention 的 FLOPs/byte 远低于此
```

---

## FlashAttention-1：Tiling + Online Softmax

### 2.1 核心思想

```
FlashAttention 的两个关键技术:

1. Tiling (分块):
   将 Q, K, V 分成小块，每次只加载一块到 SRAM
   → 避免将完整的 N×N 矩阵写入 HBM

2. Online Softmax (在线 Softmax):
   不需要完整 S 矩阵就能计算 softmax
   → 分块计算 softmax 并增量合并

效果:
  - 显存: O(N²) → O(N) (不再存储 S 和 P)
  - 速度: 2-4× 加速 (减少了 HBM 读写)
  - 精度: 与标准 Attention 数值等价
```

### 2.2 Online Softmax 推导

```
标准 Softmax:
  m = max(S)                    # 全局最大值
  P = exp(S - m) / Σ exp(S - m)

问题: 需要完整的 S 才能计算 max 和 sum

Online Softmax (分块):

对于第 i 块 S_i:
  m_i = max(S_i)                # 当前块的最大值
  m_new = max(m_old, m_i)       # 更新全局最大值
  
  # 重新缩放旧的 sum
  sum_new = sum_old × exp(m_old - m_new) + Σ exp(S_i - m_new)
  
  # 更新旧的输出
  O_new = O_old × (sum_old × exp(m_old - m_new) / sum_new) 
        + P_i × V_i / sum_new

→ 只需要当前块和旧的统计量 (m_old, sum_old, O_old)
→ 不需要完整的 S 矩阵!
```

### 2.3 Tiling 策略

```
FlashAttention 的分块策略:

将 Q 分成 T_r 块 (每块 B_r 行)
将 K, V 分成 T_c 块 (每块 B_c 行)

算法:
  for i in range(T_r):          # 遍历 Q 的块
    加载 Q_i 到 SRAM
    
    # 初始化 online softmax 统计量
    m_i = -inf, sum_i = 0, O_i = 0
    
    for j in range(T_c):        # 遍历 K, V 的块
      加载 K_j, V_j 到 SRAM
      
      S_ij = Q_i × K_j^T        # 在 SRAM 中计算
      
      # Online Softmax 更新
      m_new = max(m_i, rowmax(S_ij))
      P_ij = exp(S_ij - m_new)
      sum_new = sum_i × exp(m_i - m_new) + rowsum(P_ij)
      
      O_i = O_i × (sum_i × exp(m_i - m_new) / sum_new) 
          + P_ij × V_j / sum_new
      
      m_i = m_new
      sum_i = sum_new
    
    将 O_i 写入 HBM

显存访问分析:
  Q: N×d (读 1 次)
  K, V: N×d (读 T_r 次)
  O: N×d (写 1 次)
  
  总 HBM 访问: Θ(N²d²/M)  (M = SRAM 大小)
  → 比标准 Attention 的 Θ(N²) 少得多!
```

---

## FlashAttention-2：减少非矩阵运算

### 3.1 FA2 的改进

```
FlashAttention-2 相比 FA1 的改进:

1. 调整循环顺序:
   FA1: 外层 Q, 内层 K,V
   FA2: 外层 K,V, 内层 Q
   
   好处: 减少对 Q 的重复读取

2. 减少非矩阵运算:
   FA1: 每个 inner loop 有 rescaling 操作
   FA2: 将 rescaling 推迟到 outer loop 结束
   
   → 减少非 GEMM 操作的比例

3. 增加并行度:
   FA1: 在 batch 和 head 维度并行
   FA2: 额外在序列维度并行 (对 Q 的块)
   
   → 更好的 GPU 利用率

4. 优化 Warp 调度:
   不同 warp 负责不同的 Q 块
   → 减少 warp 间同步
```

### 3.2 FA2 性能

```
FlashAttention-2 性能 (A100, 80GB):

  seq_len=8K, d=64, causal:
    标准:  ~40 TFLOPS (4% 利用率)
    FA1:   ~120 TFLOPS (12% 利用率)
    FA2:   ~225 TFLOPS (23% 利用率)
  
  → FA2 比 FA1 快 ~2×
  → 但仍远未达到计算 bound (312 TFLOPS)
```

---

## FlashAttention-3：Hopper 架构特化

### 3.1 Hopper 的新特性

```
H100/H200 的新特性 (用于 FA3):

1. WGMMA (Warp Group Matrix Multiply-Accumulate):
   - 异步 Tensor Core 指令
   - 一个 warp group (4 warps) 可以独立执行 GEMM
   - 不需要 shared memory 中转

2. TMA (Tensor Memory Accelerator):
   - 异步数据拷贝引擎
   - 可以直接从 HBM 拷贝到 shared memory
   - 支持多维张量拷贝

3. 更高的 Shared Memory:
   - 228KB/SM (vs A100 164KB)
   - 更大的 tile 尺寸

4. FP8 Tensor Core:
   - 2× 吞吐 vs FP16
```

### 3.2 FA3 的关键技术

```
FlashAttention-3 的三大技术:

1. Producer-Consumer 异步:
   - Producer (TMA): 异步加载下一块数据
   - Consumer (WGMMA): 计算当前块
   → 完全隐藏数据加载延迟

2. 低精度 FP8:
   - 前向: FP8 GEMM
   - Softmax: FP32 (保持精度)
   - 输出: BF16/FP16
   → 2× 吞吐提升

3. 更细粒度的调度:
   - 利用 Hopper 的 Thread Block Cluster
   - 不同 block 协作处理同一 Attention
   → 更好的 SM 利用率
```

---

## PagedAttention：KV Cache 管理

### 4.1 KV Cache 的内存碎片问题

```
传统 KV Cache 管理:

  为每个请求预分配连续的显存空间 (max_seq_len)
  
  问题:
  ┌─────────────────────────────────────┐
  │ Req1: ████████░░░░░░░░░░░░░░░░░░░░ │  预分配 2048, 实际用 500
  │ Req2: ██████████████░░░░░░░░░░░░░░ │  预分配 2048, 实际用 800
  │ Req3: ██████░░░░░░░░░░░░░░░░░░░░░░ │  预分配 2048, 实际用 300
  └─────────────────────────────────────┘
  
  浪费: (2048-500) + (2048-800) + (2048-300) = 3540 tokens
  利用率: (500+800+300) / (2048×3) = 26%!
```

### 4.2 PagedAttention 原理

```
PagedAttention (vLLM):

  将 KV Cache 分成固定大小的 blocks (如 16 tokens)
  
  ┌─────────────────────────────────────┐
  │ Block Table:                        │
  │ Req1: [B0, B1, B3]                  │
  │ Req2: [B2, B4, B5, B7, B8]         │
  │ Req3: [B6, B9]                      │
  └─────────────────────────────────────┘
  
  ┌─────────────────────────────────────┐
  │ KV Cache Blocks:                    │
  │ B0: Req1[0:16]                      │
  │ B1: Req1[16:32]                     │
  │ B2: Req2[0:16]                      │
  │ B3: Req1[32:48]                     │
  │ ...                                 │
  └─────────────────────────────────────┘

优点:
  - 按需分配: 只分配实际使用的 blocks
  - 零碎片: blocks 大小固定，无外部碎片
  - 共享: 多个请求可以共享相同的 KV Cache blocks (Prefix Caching)
  
显存利用率: 从 26% → ~96%
```

### 4.3 PagedAttention 的 Attention Kernel

```
PagedAttention Kernel 的挑战:

  标准 Attention: K, V 是连续内存
  PagedAttention: K, V 是非连续的 blocks

解决方案:
  1. 查询 Block Table 获取物理地址
  2. 按 block 加载 K, V
  3. 在 SRAM 中累积计算

伪代码:
  for each block in request:
    load K_block, V_block from physical address
    S += Q × K_block^T
    # online softmax update
    O += P_block × V_block
```

---

## FlexAttention：可编程 Attention

### 5.1 为什么需要 FlexAttention

```
问题: 不同的 Attention 变体需要不同的 kernel

  - Causal Attention: 下三角 mask
  - Sliding Window: 局部窗口
  - Block-Sparse: 稀疏模式
  - Document Masking: 跨文档不 attend
  - Prefix LM: 前缀双向 + 后缀单向
  - 自定义: ALiBi, 相对位置偏置, ...

传统方案: 为每种变体写一个 CUDA kernel
  → 组合爆炸，维护困难

FlexAttention (PyTorch 2.5+):
  用户定义 score_mod 函数 → 编译器自动生成高效 kernel
```

### 5.2 FlexAttention API

```python
from torch.nn.attention.flex_attention import flex_attention

# 定义 score modification 函数
def causal_mask(score, b, h, q_idx, kv_idx):
    return torch.where(q_idx >= kv_idx, score, -float("inf"))

def sliding_window(score, b, h, q_idx, kv_idx):
    return torch.where(
        torch.abs(q_idx - kv_idx) <= 1024, 
        score, 
        -float("inf")
    )

def alibi_bias(score, b, h, q_idx, kv_idx):
    return score + (kv_idx - q_idx) * slopes[h]

# 使用
output = flex_attention(
    query, key, value,
    score_mod=causal_mask  # 或 sliding_window, alibi_bias, ...
)
```

---

## Triton 入门与实践

### 6.1 Triton 简介

```
Triton (OpenAI): Python-like DSL for GPU kernels

特点:
  - 用 Python 写 GPU kernel
  - 自动优化 (tiling, memory coalescing, ...)
  - 比 CUDA 简单，比 torch.compile 可控
  - 性能接近手写 CUDA

编程模型:
  - 用户定义 block-level 操作
  - Triton 编译器处理 thread-level 调度
  - 自动处理 shared memory 分配和同步
```

### 6.2 Triton 基础示例

```python
import triton
import triton.language as tl

@triton.jit
def vector_add(x_ptr, y_ptr, output_ptr, n_elements, BLOCK_SIZE: tl.constexpr):
    # 获取当前 block 的起始位置
    pid = tl.program_id(0)
    block_start = pid * BLOCK_SIZE
    offsets = block_start + tl.arange(0, BLOCK_SIZE)
    
    # 创建 mask (处理边界)
    mask = offsets < n_elements
    
    # 从 HBM 加载数据
    x = tl.load(x_ptr + offsets, mask=mask)
    y = tl.load(y_ptr + offsets, mask=mask)
    
    # 计算
    output = x + y
    
    # 写回 HBM
    tl.store(output_ptr + offsets, output, mask=mask)

# 使用
def add(x: torch.Tensor, y: torch.Tensor):
    output = torch.empty_like(x)
    n_elements = output.numel()
    grid = lambda meta: (triton.cdiv(n_elements, meta['BLOCK_SIZE']),)
    vector_add[grid](x, y, output, n_elements, BLOCK_SIZE=1024)
    return output
```

### 6.3 Triton 矩阵乘法

```python
@triton.jit
def matmul_kernel(
    a_ptr, b_ptr, c_ptr,
    M, N, K,
    stride_am, stride_ak,
    stride_bk, stride_bn,
    stride_cm, stride_cn,
    BLOCK_M: tl.constexpr, BLOCK_N: tl.constexpr, BLOCK_K: tl.constexpr,
):
    pid_m = tl.program_id(0)
    pid_n = tl.program_id(1)
    
    # 当前 block 负责 C[pid_m*BLOCK_M:(pid_m+1)*BLOCK_M, 
    #                         pid_n*BLOCK_N:(pid_n+1)*BLOCK_N]
    
    offs_m = pid_m * BLOCK_M + tl.arange(0, BLOCK_M)
    offs_n = pid_n * BLOCK_N + tl.arange(0, BLOCK_N)
    offs_k = tl.arange(0, BLOCK_K)
    
    a_ptrs = a_ptr + offs_m[:, None] * stride_am + offs_k[None, :] * stride_ak
    b_ptrs = b_ptr + offs_k[:, None] * stride_bk + offs_n[None, :] * stride_bn
    
    # 累加器 (在 register 中)
    accumulator = tl.zeros((BLOCK_M, BLOCK_N), dtype=tl.float32)
    
    for k in range(0, K, BLOCK_K):
        a = tl.load(a_ptrs, mask=offs_k[None, :] < K - k, other=0.0)
        b = tl.load(b_ptrs, mask=offs_k[:, None] < K - k, other=0.0)
        
        accumulator += tl.dot(a, b)
        
        a_ptrs += BLOCK_K * stride_ak
        b_ptrs += BLOCK_K * stride_bk
    
    c = accumulator.to(tl.float16)
    
    offs_m = pid_m * BLOCK_M + tl.arange(0, BLOCK_M)
    offs_n = pid_n * BLOCK_N + tl.arange(0, BLOCK_N)
    c_ptrs = c_ptr + offs_m[:, None] * stride_cm + offs_n[None, :] * stride_cn
    c_mask = (offs_m[:, None] < M) & (offs_n[None, :] < N)
    tl.store(c_ptrs, c, mask=c_mask)
```

---

## 手写 FlashAttention (Triton)

### 7.1 简化版实现

```python
@triton.jit
def _fwd_kernel(
    Q, K, V, sm_scale,
    L, M,
    Out,
    stride_qz, stride_qh, stride_qm, stride_qk,
    stride_kz, stride_kh, stride_kn, stride_kk,
    stride_vz, stride_vh, stride_vk, stride_vn,
    stride_oz, stride_oh, stride_om, stride_on,
    Z, H,
    N_CTX,
    BLOCK_M: tl.constexpr, BLOCK_DMODEL: tl.constexpr,
    BLOCK_N: tl.constexpr,
):
    start_m = tl.program_id(0)
    off_hz = tl.program_id(1)
    
    # 初始化
    offs_m = start_m * BLOCK_M + tl.arange(0, BLOCK_M)
    offs_n = tl.arange(0, BLOCK_N)
    offs_d = tl.arange(0, BLOCK_DMODEL)
    
    q_ptrs = Q + off_hz * stride_qh + offs_m[:, None] * stride_qm + offs_d[None, :] * stride_qk
    k_ptrs = K + off_hz * stride_kh + offs_n[:, None] * stride_kn + offs_d[None, :] * stride_kk
    v_ptrs = V + off_hz * stride_vh + offs_n[:, None] * stride_qk + offs_d[None, :] * stride_qk
    
    # 初始化 online softmax 统计量
    m_i = tl.zeros([BLOCK_M], dtype=tl.float32) - float("inf")
    l_i = tl.zeros([BLOCK_M], dtype=tl.float32)
    acc = tl.zeros([BLOCK_M, BLOCK_DMODEL], dtype=tl.float32)
    
    # 加载 Q
    q = tl.load(q_ptrs, mask=offs_m[:, None] < N_CTX, other=0.0)
    
    # 遍历 K, V 的块
    for start_n in range(0, N_CTX, BLOCK_N):
        # 加载 K, V
        k = tl.load(k_ptrs, mask=offs_n[:, None] < N_CTX - start_n, other=0.0)
        v = tl.load(v_ptrs, mask=offs_n[:, None] < N_CTX - start_n, other=0.0)
        
        # QK^T
        qk = tl.zeros([BLOCK_M, BLOCK_N], dtype=tl.float32)
        qk += tl.dot(q, tl.trans(k))
        qk *= sm_scale
        
        # Causal mask
        qk += tl.where(
            offs_m[:, None] >= (start_n + offs_n[None, :]), 
            0, 
            -float("inf")
        )
        
        # Online softmax
        m_ij = tl.max(qk, 1)
        p = tl.exp(qk - m_ij[:, None])
        l_ij = tl.sum(p, 1)
        
        m_new = tl.maximum(m_i, m_ij)
        alpha = tl.exp(m_i - m_new)
        beta = tl.exp(m_ij - m_new)
        
        l_i = l_i * alpha + l_ij * beta
        
        # 更新累加器
        acc = acc * alpha[:, None]
        acc += tl.dot(p.to(tl.float16), v)
        
        m_i = m_new
        
        k_ptrs += BLOCK_N * stride_kn
        v_ptrs += BLOCK_N * stride_vk
    
    # 最终 rescaling
    acc = acc / l_i[:, None]
    
    # 写回
    o_ptrs = Out + off_hz * stride_oh + offs_m[:, None] * stride_om + offs_d[None, :] * stride_on
    tl.store(o_ptrs, acc.to(tl.float16), mask=offs_m[:, None] < N_CTX)
    
    # 存储 L 和 M (用于反向传播)
    l_ptrs = L + off_hz * N_CTX + offs_m
    m_ptrs = M + off_hz * N_CTX + offs_m
    tl.store(l_ptrs, l_i, mask=offs_m < N_CTX)
    tl.store(m_ptrs, m_i, mask=offs_m < N_CTX)
```

### 7.2 关键设计决策

```
1. Block 大小选择:
   BLOCK_M = 128 (Q 的行数)
   BLOCK_N = 64  (K, V 的行数)
   
   约束: BLOCK_M × BLOCK_DMODEL + BLOCK_N × BLOCK_DMODEL < SRAM

2. Causal Mask:
   在 QK^T 之后立即应用 mask
   → 被 mask 的位置在 exp 后为 0

3. 精度管理:
   QK^T 和 softmax: FP32 (精度关键)
   矩阵乘法: FP16/BF16 (性能关键)
   累加器: FP32 (避免累积误差)
```

---

## 性能分析与调优

### 8.1 使用 torch.profiler

```python
import torch.profiler as profiler

with profiler.profile(
    activities=[
        profiler.ProfilerActivity.CPU,
        profiler.ProfilerActivity.CUDA,
    ],
    with_stack=True,
) as prof:
    output = attention_fn(q, k, v)

# 查看结果
print(prof.key_averages().table(sort_by="cuda_time_total", row_limit=10))

# Chrome trace
prof.export_chrome_trace("trace.json")
```

### 8.2 使用 NSight Compute

```bash
# 分析单个 kernel
ncu --set full -o profile python script.py

# 查看结果
ncu --import profile.ncu-rep
```

### 8.3 关键性能指标

| 指标 | 含义 | 目标 |
|------|------|------|
| **Occupancy** | 活跃 warp / 最大 warp | > 50% |
| **SM Efficiency** | SM 活跃周期比例 | > 80% |
| **Compute Throughput** | 实际 / 峰值 FLOPS | > 50% |
| **Memory Throughput** | 实际 / 峰值带宽 | > 70% |
| **L1/L2 Hit Rate** | 缓存命中率 | > 80% |

---

> **关键原则**：
> 1. **IO-aware 是核心**：FlashAttention 的成功在于理解显存层次，而非新数学
> 2. **Tiling 是通用技术**：几乎所有大矩阵运算都可以用 tiling 优化
> 3. **Triton 是学习利器**：比 CUDA 简单 10×，性能可达 80-90%
> 4. **先跑通再优化**：正确性 > 性能，用 torch.profiler 找到真正的瓶颈
> 5. **硬件在进化**：FA1→FA2→FA3 的演进 = 算法适配新硬件特性
