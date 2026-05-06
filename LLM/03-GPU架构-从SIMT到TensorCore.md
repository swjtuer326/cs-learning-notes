# GPU 架构：从 SIMT 到 Tensor Core

> 写出高性能 Kernel 的前提是理解 GPU 硬件如何执行你的代码。本文从一个 GEMM Kernel 的执行过程出发，逐步揭示 SIMT 模型、内存层次、Tensor Core 数据通路背后的硬件约束。

### 关键术语

| 缩写 | 全称 | 含义 |
|------|------|------|
| SIMT | Single Instruction Multiple Threads | 单指令多线程，GPU 执行模型 |
| SM | Streaming Multiprocessor | 流式多处理器，GPU 基本执行单元 |
| Warp | — | 32 线程调度单位 |
| CTA | Cooperative Thread Array | 协作线程数组（即 Thread Block） |
| SMEM | Shared Memory | 共享内存，Block 内共享 |
| RF | Register File | 寄存器文件，线程私有 |
| HBM | High Bandwidth Memory | 高带宽内存，GPU 主存 |
| TC | Tensor Core | 张量核心，矩阵乘加加速单元 |
| WGMMA | Warp-Group Matrix Multiply-Accumulate | Warp 组级矩阵乘加（Hopper） |
| TMA | Tensor Memory Accelerator | 张量内存加速器（Hopper） |
| Occupancy | — | SM 活跃 Warp 占用率 |
| NCCL | NVIDIA Collective Communications Library | NVIDIA 集合通信库 |

---

## 1. 从一个问题出发

上一篇文章推导了 LLaMA-2 70B 的 FFN Gate 投影需要 1.93 TFLOPS（S=4096）。H100 FP16 峰值 989 TFLOPS，理论上 1.95 ms 就能算完。但实际耗时可能是 3-4 ms——**差距从哪来？**

答案藏在 GPU 硬件的执行细节里。让我们追踪一个 GEMM Kernel 在 GPU 上的完整执行过程。

---

## 2. SIMT 模型：GPU 如何执行代码

### 2.1 从 CPU 到 GPU 的思维转换

CPU 的思维：一个线程顺序执行指令，遇到分支走一条路。

GPU 的思维：32 个线程（一个 Warp）**同时**执行同一条指令，但操作不同数据。如果遇到分支，两条路都要走——走 A 路径时 B 线程空闲，走 B 路径时 A 线程空闲。

```
CPU: Thread 0: [if A → path_A] [if B → path_B]  ← 只走一条
GPU: Warp(32T): [16走A, 16空闲] [16走B, 16空闲]  ← 两条都走
```

**对 LLM 的影响**：MoE 的 Token 路由可能导致同一 Warp 内的 Token 被分配到不同专家，造成分支发散。解决方案是按专家排序 Token。

### 2.2 执行层次：Grid → Block → Warp → Thread

```
Kernel 启动: gridDim = (M/128, N/128), blockDim = (128, 1, 1)

Grid (整个 Kernel)
  ├── Block (0,0) → 分配到 SM 0
  │     ├── Warp 0: 线程 0-31   → 计算 C[0:32, 0:128]
  │     ├── Warp 1: 线程 32-63  → 计算 C[32:64, 0:128]
  │     ├── Warp 2: 线程 64-95  → 计算 C[64:96, 0:128]
  │     └── Warp 3: 线程 96-127 → 计算 C[96:128, 0:128]
  ├── Block (1,0) → 分配到 SM 1
  └── ...
```

**关键约束**（H100 SM）：

| 资源 | 限制 | 含义 |
|------|------|------|
| 最大 Warp/SM | 64 | 决定能同时运行多少 Warp |
| 最大线程/SM | 2048 | 64 Warp × 32 线程 |
| 最大 Block/SM | 32 | 一个 SM 最多 32 个 Block |
| SMEM/SM | 228 KB | Block 间分配，超过则 Block 数减少 |
| 寄存器/SM | 65536 | 所有活跃线程共享 |

### 2.3 Occupancy：为什么它重要

Occupancy = 活跃 Warp 数 / 最大 Warp 数。它决定了 SM 能否有效隐藏延迟。

```
场景: 全局内存加载延迟 400 cycles
  如果只有 1 个 Warp: 等待 400 cycles 无事可做
  如果有 32 个 Warp: 调度器在 32 个 Warp 间切换
    → 每个 Warp 等待期间，其他 Warp 在计算
    → 延迟被"隐藏"
```

**Occupancy 的三重约束**：

```
Occupancy = min(
  65536 / (regs_per_thread × threads_per_block),  ← 寄存器约束
  228KB / smem_per_block,                          ← SMEM 约束
  2048 / threads_per_block                          ← 线程数约束
)
```

举例：一个 Block 用 128 线程、每线程 128 寄存器、每 Block 48 KB SMEM：

```
寄存器约束: 65536 / (128 × 128) = 4 Block → 4 × 128/32 = 16 Warp
SMEM 约束:   228 / 48 = 4 Block → 16 Warp
线程约束:    2048 / 128 = 16 Block → 但寄存器和 SMEM 只允许 4

Occupancy = 16/64 = 25% → 偏低
```

**如果每线程只用 64 寄存器**：`65536 / (64 × 128) = 8 Block → 32 Warp → Occupancy 50%`。寄存器用量直接影响 Occupancy，这就是为什么 WGMMA 的大量寄存器消耗是一个需要权衡的问题。

---

## 3. 内存层次：数据搬运是性能的隐形杀手

### 3.1 四级内存的带宽和延迟

```
┌─────────────────────────────────────────────────────────┐
│  HBM (80 GB, 3.35 TB/s, ~400 cycles)                    │
│  ↑ 1× 带宽                                              │
│  │                                                       │
│  L2 Cache (50 MB, ~10 TB/s, ~200 cycles)                │
│  ↑ ~3× 带宽                                             │
│  │                                                       │
│  SMEM (228 KB/SM, ~19 TB/s, ~30 cycles)                 │
│  ↑ ~6× 带宽                                             │
│  │                                                       │
│  RF (256 KB/SM, ~数十 TB/s, ~1 cycle)                   │
│  ↑ ~20× 带宽                                            │
│  │                                                       │
│  Tensor Core (矩阵乘加, 吞吐取决于精度)                  │
└─────────────────────────────────────────────────────────┘
```

**核心洞察**：LLM 优化的本质是把数据从慢层级搬到快层级，并在快层级中最大化复用。

### 3.2 用 GEMM 分块说明数据复用

GEMM `C[M,N] = A[M,K] × B[K,N]` 的分块策略：

```
将 A 按行分为 M/Bm 块，B 按列分为 N/Bn 块，K 维分为 K/Bk 块

对每个输出分块 C[m, n]:
  C[m,n] = Σ_k A[m,k] × B[k,n]    ← K 维度累加

数据复用:
  A[m,k] 被所有 B[k,n] (N/Bn 个) 复用 → 读一次 SMEM, 用 N/Bn 次
  B[k,n] 被所有 A[m,k] (M/Bm 个) 复用 → 读一次 SMEM, 用 M/Bm 次

分块大小选择:
  太小 → SMEM 放得下多块, 但复用次数少, GEMM 效率低
  太大 → SMEM 放不下, 或 Occupancy 降低
  典型: Bm=128, Bn=128, Bk=32 (FP16)
```

### 3.3 Bank Conflict：SMEM 的性能陷阱

SMEM 分为 32 个 Bank，每个 Bank 每 cycle 服务一次 4 字节访问：

```
Bank:    0   1   2   3  ...  31
地址:    0   4   8   12 ...  124
         128 132 136 140 ...  252
         256 260 264 268 ...  380

同一 Warp 内两个线程读同一 Bank 的不同地址 → 串行化 → 带宽减半
```

**对 FlashAttention 的影响**：Q/K/V 分块在 SMEM 中的布局必须精心设计。常见技巧是 Padding——在数据间插入空位改变 Bank 映射：

```
无 Padding: data[32][128] → 第 i 行第 j 列映射到 Bank (j*4/4) % 32 = j % 32
  如果连续 32 个线程读同一行的 32 个 float → Bank 0-31, 无冲突 ✓
  但如果读跨行 → 可能冲突

有 Padding: data[32][129] → 第 129 列为空, 改变了后续行的 Bank 映射
  → 消除特定访问模式的冲突
```

---

## 4. Tensor Core：矩阵乘法的硬件加速器

### 4.1 CUDA Core vs Tensor Core

| 对比维度 | CUDA Core | Tensor Core |
|----------|-----------|-------------|
| 操作 | 标量 FMA: `d = a × b + c` | 矩阵 FMA: `D = A × B + C` |
| 每次操作 | 1 个 FMA | 16×16×16 (HMMA) 或更大 (WGMMA) |
| 吞吐 (H100) | 128 FMA/clk/SM | 4 TC/SM |
| 编程 | `d = a * b + c` | WMMA/WGMMA API 或 PTX |

**为什么 Tensor Core 对 LLM 至关重要**：LLM 的核心计算是矩阵乘法。一个 FP16 的 HMMA 指令完成 16×16×16 = 4096 次 FMA，而 4096 个 CUDA Core 各做 1 次 FMA 才能完成同样的工作。Tensor Core 是 MFU 物理上限的决定因素。

### 4.2 WGMMA：Hopper 的关键改进

Ampere 时代使用 HMMA，数据来源是寄存器（RF）——Warp 需要手动从 SMEM 加载数据到 RF，再调用 HMMA。Hopper 引入 WGMMA，数据来源变为 SMEM：

```
HMMA (Ampere):
  SMEM → [手动加载] → RF → HMMA → RF(累加器)
  问题: 手动加载占用 Warp 时间, RF 容量有限

WGMMA (Hopper):
  SMEM → [硬件自动读取] → Tensor Core → RF(累加器)
  优势: Warp 不参与数据搬运, 可同时做其他计算
  4 个 Warp 协同完成一次更大矩阵乘加
```

**WGMMA 的矩阵尺寸**：

| 精度 | 典型 M×N×K | 单次 FMA 数量 |
|------|-----------|-------------|
| FP16 | 64×256×16 | 262,144 |
| FP8 | 64×128×32 | 262,144 |
| INT8 | 64×256×32 | 524,288 |

一次 WGMMA FP16 指令完成 262K 次 FMA——这就是 Tensor Core 的威力。

### 4.3 WGMMA 的寄存器代价

WGMMA 的累加器存储在 RF 中，占用大量寄存器：

```
FP16 WGMMA 64×256: 累加器需要 64×256 = 16384 个 FP32 值
= 16384 × 4 Bytes = 64 KB 寄存器
= 65536 / 4 = 16384 寄存器 (占 SM 总寄存器的 25%)
= 分摊到 128 线程 (4 Warp), 每线程 128 寄存器

每线程 128 寄存器 → Occupancy 约束:
  65536 / (128 × 128) = 4 Block/SM → 16 Warp → Occupancy 25%
```

**这就是 Blackwell 引入 TMEM 的动机**——将累加器从 RF 迁移到专用的 TMEM，释放寄存器，提高 Occupancy。

### 4.4 TMA：异步数据搬运

Hopper 引入 TMA (Tensor Memory Accelerator)，专用硬件做 HBM→SMEM 的数据搬运：

```
传统 cp.async:
  Warp 计算源地址和目标地址 → 发起异步拷贝 → 等待完成
  Warp 需要参与地址计算

TMA:
  Warp 只需提供"张量描述符"(包含基地址、维度、stride)
  TMA 硬件自动计算地址、搬运数据、处理边界
  Warp 完全不参与 → 可以同时做计算

效果: 实现真正的计算-搬运流水线化
  TMA 加载下一块数据的同时, Tensor Core 在计算当前块
```

---

## 5. 代际对比：A100 → H100 → B200

| 对比维度 | A100 (Ampere) | H100 (Hopper) | B200 (Blackwell) |
|----------|--------------|---------------|------------------|
| 制程 | 7nm | 4nm | 4NP |
| SM 数 | 108 | 132 | 160 (双 die) |
| HBM | 80 GB HBM2e | 80 GB HBM3 | 192 GB HBM3e |
| HBM 带宽 | 2.0 TB/s | 3.35 TB/s | 8 TB/s |
| FP16 Tensor | 312 TFLOPS | 989 TFLOPS | 2250 TFLOPS |
| FP8 Tensor | — | 1979 TFLOPS | 4500 TFLOPS |
| FP4 Tensor | — | — | 9000 TFLOPS |
| TC 指令 | HMMA | WGMMA | WGMMA+ |
| TMA | 否 | 是 | 是 |
| TMEM | 否 | 否 | 是 |
| NVLink | 600 GB/s | 900 GB/s | 1.8 TB/s |

**对 LLM 的实际影响**：

| 变化 | 训练影响 | 推理影响 |
|------|----------|----------|
| HBM 容量 80→192 GB | 单卡容纳更大模型 | 更多 KV Cache → 更多并发 |
| HBM 带宽 3.35→8 TB/s | 通信带宽提升 | Decode 吞吐提升 ~2.4× |
| FP8 支持 | 训练速度 ~2× | 推理吞吐 ~2× |
| FP4 支持 (B200) | — | 推理吞吐 ~4× |
| TMEM (B200) | 更高 Occupancy | 更高 Occupancy |

---

## 6. 回答开头的问题

FFN Gate 投影理论 1.95 ms，实际 3-4 ms，差距来源：

```
理论: 1.93 TFLOPS / 989 TFLOPS = 1.95 ms

实际损失:
1. Tensor Core 利用率: ~85% → 1.95/0.85 = 2.29 ms
   (分块策略、数据布局、WGMMA 配置影响)

2. 数据供给延迟: ~5-10% → 2.29/0.90 = 2.55 ms
   (HBM→SMEM 加载延迟, 双缓冲不能完全隐藏)

3. Kernel Launch + 同步: ~3-5% → 2.55/0.95 = 2.68 ms
   (CPU 发起 Kernel 的延迟, 多 Stream 同步)

4. 非 GEMM 操作: ~5% → 2.68/0.95 = 2.82 ms
   (RMSNorm, 残差加法, 激活函数)

5. 通信 (TP=8): ~8% → 2.82/0.92 = 3.07 ms
   (All-Reduce 通信开销)

总计: ~3.1 ms → 与实际 3-4 ms 吻合
```

**每个损失项都对应一个硬件约束**：Tensor Core 利用率受分块策略约束，数据供给受 HBM 带宽约束，通信受 NVLink 带宽约束。理解这些约束，才知道该优化什么。

---

## 7. 要点回顾

| 要点 | 说明 |
|------|------|
| SIMT | 32 线程/Warp 同时执行同一指令，分支发散导致性能损失 |
| Occupancy | 受寄存器、SMEM、线程数三重约束，决定延迟隐藏能力 |
| 内存层次 | HBM→L2→SMEM→RF，带宽比约 1:3:6:20+ |
| Bank Conflict | SMEM 32 Bank，冲突导致串行化，需 Padding 消除 |
| Tensor Core | 矩阵乘加加速器，一次 WGMMA 完成 262K FMA |
| WGMMA | Hopper 关键改进：数据来源从 RF 变为 SMEM，4 Warp 协同 |
| TMA | 异步数据搬运，释放 Warp 做计算 |
| 寄存器代价 | WGMMA 累加器占大量 RF，限制 Occupancy |
| 理论 vs 实际 | 3-4 ms vs 1.95 ms，差距来自 TC 利用率+数据供给+通信+非 GEMM |

---

## 参考资料

- [NVIDIA Hopper Architecture Whitepaper](https://resources.nvidia.com/en-us-tensor-core) — H100 微架构
- [NVIDIA Blackwell Architecture Overview](https://www.nvidia.com/en-us/data-center/dgx-b200/) — B200 架构
- [CUDA Programming Guide](https://docs.nvidia.com/cuda/cuda-c-programming-guide/) — SIMT 模型
- [CUDA Best Practices Guide](https://docs.nvidia.com/cuda/cuda-c-best-practices-guide/) — Occupancy 优化
- [NVIDIA-GPU架构演进与LLM](./NVIDIA-GPU架构演进与LLM.md) — GPU 代际演进

> 前置阅读：[02-Transformer计算特征第一性分析](./02-Transformer计算特征第一性分析.md)
> 下一篇：[04-分布式训练并行策略](./04-分布式训练并行策略.md)
