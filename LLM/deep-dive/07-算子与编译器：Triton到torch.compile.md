# 算子与编译器：Triton 到 torch.compile

> **核心命题**：LLM 训练和推理的性能瓶颈，往往不在算法本身，而在算子实现和编译器能否将算法高效映射到硬件。一个 Transformer 层在 eager mode 下需要 10+ 次 kernel launch 和 3× 的冗余显存带宽——算子融合和编译优化是消除这些"税"的关键手段。
> **工程师视角**：大多数场景下你不需要手写 kernel，但你需要理解编译器在做什么，才能诊断性能问题、选择正确的优化策略。

### 关键术语

| 缩写 | 全称 | 含义 |
|------|------|------|
| DSL | Domain-Specific Language | 领域特定语言，如 Triton 专为 GPU 编程设计 |
| IR | Intermediate Representation | 中间表示，编译器内部的表达形式 |
| PTX | Parallel Thread Execution | NVIDIA GPU 的低级虚拟指令集 |
| SASS | Streaming Assembly | GPU 硬件原生指令，PTX 编译后的最终形式 |
| JIT | Just-In-Time Compilation | 即时编译，运行时将代码编译为机器码 |
| FX | PyTorch FX | PyTorch 的 Python-to-Python 代码变换框架 |
| MLIR | Multi-Level Intermediate Representation | 多级中间表示，LLVM 生态的编译器基础设施 |
| XLA | Accelerated Linear Algebra | Google 的线性代数编译器，JAX/TensorFlow 后端 |

### 前置知识

| 需要了解 | 参考文档 |
|----------|----------|
| GPU 架构与显存层级 | [11-NVIDIA GPU架构演进](./11-NVIDIA-GPU架构演进与LLM.md) |
| FlashAttention 算子实践 | [06-FlashAttention论文精读](./06-FlashAttention论文精读与Triton实践.md) |
| 推理引擎优化 | [12-推理引擎](../12-LLM推理引擎：vLLM到TensorRT-LLM.md) |

---

## 1. 问题起源：Eager Mode 的"隐形成本"

### 1.1 一个 Transformer 层到底花了多少时间在等？

以 Llama-2-7B 的一个 Transformer 层为例，eager mode 下的前向传播：

```
一个 Transformer 层 (eager mode):

  HBM ─→ [QKV Proj] ─→ HBM ─→ [RoPE] ─→ HBM ─→ [Attn] ─→ HBM ─→ [O Proj] ─→ HBM
         launch 1         launch 2     launch 3       launch 4
  HBM ─→ [Residual] ─→ HBM ─→ [RMSNorm] ─→ HBM ─→ [FFN] ─→ HBM ─→ [Residual] ─→ HBM
         launch 5          launch 6      launch 7     launch 8

  共 8 次 kernel launch, 8+ 次 HBM 读写
  其中 RoPE, Residual, RMSNorm 都是 memory-bound (计算量极小, 全在等带宽)
```

**关键洞察**：这 8 个 kernel 中，RoPE、Residual Add、RMSNorm 都是 memory-bound 算子——它们几乎不做计算，时间全花在等数据从 HBM 搬进搬出。而每次搬进搬出之间，数据都要写回 HBM 再读出来，纯粹浪费带宽。

用 Roofline Model 量化：

| 算子 | FLOPs | HBM 访问量 | 算术强度 (FLOPs/Byte) | 瓶颈类型 | 理论耗时 (A100) |
|------|-------|-----------|----------------------|---------|---------------|
| QKV Proj (4096→3×4096) | 201M | 0.4MB | ~500 | Compute-bound | ~0.1ms |
| RoPE | 0.1M | 0.3MB | ~0.3 | **Memory-bound** | ~0.07ms |
| Residual Add | 0.008M | 0.06MB | ~0.13 | **Memory-bound** | ~0.01ms |
| RMSNorm | 0.008M | 0.06MB | ~0.13 | **Memory-bound** | ~0.01ms |

RoPE + Residual + RMSNorm 的计算量不到 QKV Proj 的 0.1%，但耗时占了 ~15%。原因：**它们各自独立执行，每次都要从 HBM 读数据、写结果，而数据本可以在 SRAM 中直接传递**。

### 1.2 融合能省多少？

将 memory-bound 算子与相邻的 compute-bound 算子融合：

```
融合后:

  HBM ─→ [QKV Proj + RoPE] ─→ HBM ─→ [Attn] ─→ HBM ─→ [O Proj + Residual] ─→ HBM
         launch 1                  launch 2       launch 3
  HBM ─→ [RMSNorm + FFN + Residual] ─→ HBM
         launch 4

  4 次 kernel launch, 4 次 HBM 读写
  → launch 开销减半, HBM 带宽节省 ~40%
```

这就是算子融合的核心动机：**不是让每个算子更快，而是让算子之间的"等待"消失**。

### 1.3 GPU 编程的层次与取舍

```
GPU 编程层次 (从高层到低层):

  ┌─────────────────────────────────────┐
  │ Python (PyTorch/JAX)               │  用户层: 算法工程师
  ├─────────────────────────────────────┤
  │ torch.compile / XLA / JAX JIT      │  图编译层: 自动融合
  ├─────────────────────────────────────┤
  │ Triton DSL / CuDNN / CUTLASS       │  算子层: 手动融合
  ├─────────────────────────────────────┤
  │ CUDA C / CUDA C++                  │  GPU 编程层: 完全控制
  ├─────────────────────────────────────┤
  │ PTX (Parallel Thread Execution)    │  虚拟 ISA: 极端优化
  ├─────────────────────────────────────┤
  │ SASS (Streaming Assembly)           │  硬件 ISA: 理论极限
  └─────────────────────────────────────┘
```

核心取舍：**控制粒度 vs 开发效率**。越底层控制越精细，但开发成本指数增长。Triton 和 torch.compile 分别从"算子层"和"图编译层"提供了两个不同的甜蜜点。

---

## 2. Triton：为什么需要一个新语言？

### 2.1 CUDA 编程的痛点

写一个高性能 CUDA kernel 需要手动处理：

| 任务 | CUDA C 中的做法 | 代码量占比 |
|------|----------------|-----------|
| Shared Memory 管理 | 手动声明 `__shared__`，手动加载/同步 | ~30% |
| 线程映射 | 计算 `threadIdx + blockIdx * blockDim` | ~20% |
| Memory Coalescing | 安排线程访问连续地址 | ~15% |
| Bank Conflict 避免 | shared memory padding | ~10% |
| 边界检查 | 每个 load/store 加 if 判断 | ~10% |
| **实际计算逻辑** | — | **~15%** |

一个 RMSNorm kernel 的 CUDA 实现约 80 行，其中只有 ~12 行是"计算逻辑"，其余全是内存管理和线程映射。**CUDA 编程的 85% 工作量在"把数据搬到正确的地方"，而不是"对数据做正确的计算"**。

Triton 的核心设计决策：**让编译器自动处理内存管理，让程序员只关注计算逻辑**。

### 2.2 Triton 的编程模型：Block-Level Abstraction

Triton 的核心抽象是 **block**——程序员只描述"一个 block 的数据怎么处理"，编译器自动将 block 映射到 GPU 的 thread block 和 shared memory。

```python
import triton
import triton.language as tl

@triton.jit
def add_kernel(
    x_ptr, y_ptr, output_ptr,
    n_elements,
    BLOCK_SIZE: tl.constexpr,
):
    pid = tl.program_id(axis=0)
    offsets = pid * BLOCK_SIZE + tl.arange(0, BLOCK_SIZE)
    mask = offsets < n_elements
    x = tl.load(x_ptr + offsets, mask=mask)
    y = tl.load(y_ptr + offsets, mask=mask)
    output = x + y
    tl.store(output_ptr + offsets, output, mask=mask)
```

对比等价的 CUDA C：

```c
__global__ void add_kernel(float* x, float* y, float* out, int n) {
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx < n) out[idx] = x[idx] + y[idx];
}
```

看起来差不多？但当计算变复杂时差距就出现了——Triton 的 block-level 抽象让 shared memory 管理、memory coalescing、bank conflict 避免全部自动化：

| 你需要写的 | CUDA C | Triton |
|-----------|--------|--------|
| "从 HBM 加载一个 tile" | `__shared__` 声明 + `__syncthreads()` + 循环加载 | `tl.load(ptr + offsets)` |
| "在 tile 上做归约" | warp shuffle + shared memory reduction | `tl.sum(x, axis=0)` |
| "避免 bank conflict" | 手动 padding | 编译器自动处理 |
| "合并内存访问" | 安排线程索引使访问连续 | 编译器自动重排 |

### 2.3 Triton 的自动优化：编译器替你做了什么？

Triton 编译器 (基于 MLIR) 在将 DSL 降级到 PTX 的过程中执行以下优化：

**案例：RMSNorm 的 shared memory 自动分配**

```
Triton 代码:
  x = tl.load(X_ptr + offsets)       ← 从 HBM 加载
  variance = tl.sum(x * x) / N       ← 需要多次访问 x

编译器推断:
  x 被 x * x 和后续操作重复使用
  → 自动将 x 放入 shared memory (而非每次从 HBM 重读)
  → 自动插入 __syncthreads() 在需要同步的位置
  → 自动 padding 避免 bank conflict
```

如果手写 CUDA，你需要自己声明 `__shared__ float x_shared[BLOCK_SIZE + PADDING]`，手动加载、手动同步、手动计算 padding 大小。Triton 把这些全部自动化了。

**代价**：Triton 的自动优化不是万能的。对于需要精细控制 warp 调度、异步内存拷贝（如 Hopper 的 TMA）、或自定义同步策略的场景，Triton 的抽象层级过高，无法表达。FlashAttention-2/3 的核心 kernel 仍然用 CUDA C 编写，正是因为需要手动控制 WGMMA 和 TMA 的异步流水线。

### 2.4 实战案例：Fused RMSNorm 的性能剖析

RMSNorm 是 LLM 中最频繁执行的算子之一（每个 Transformer 层调用 2 次），是算子融合的典型目标。

**问题**：Eager mode 下 RMSNorm 需要 3 次 HBM 读写：

```python
def rms_norm_eager(x, weight, eps=1e-6):
    variance = x.pow(2).mean(-1, keepdim=True)   # HBM 读 x, 写 variance
    x_normed = x * torch.rsqrt(variance + eps)    # HBM 读 x + variance, 写 x_normed
    return weight * x_normed                       # HBM 读 weight + x_normed, 写 output
    # 3 次 HBM 读 + 3 次 HBM 写 = 6 次显存访问
```

**融合方案**：将三步合并为一个 kernel，x 只从 HBM 读一次，中间结果全部在寄存器/shared memory 中传递：

```python
@triton.jit
def rms_norm_kernel(
    X_ptr, W_ptr, O_ptr,
    stride, N,
    BLOCK_SIZE: tl.constexpr,
    eps: tl.constexpr,
):
    row = tl.program_id(0)
    offsets = row * stride + tl.arange(0, BLOCK_SIZE)
    mask = tl.arange(0, BLOCK_SIZE) < N

    x = tl.load(X_ptr + offsets, mask=mask)        # 1次 HBM 读
    variance = tl.sum(x * x, axis=0) / N            # 寄存器内计算
    rrms = 1.0 / tl.sqrt(variance + eps)            # 寄存器内计算
    w = tl.load(W_ptr + tl.arange(0, BLOCK_SIZE), mask=mask)  # 1次 HBM 读
    out = x * rrms * w                              # 寄存器内计算
    tl.store(O_ptr + offsets, out, mask=mask)       # 1次 HBM 写
    # 2 次 HBM 读 + 1 次 HBM 写 = 3 次显存访问 (节省 50%)
```

**性能对比 (A100, hidden_size=4096)**：

| 实现 | HBM 访问次数 | Kernel Launch | 有效带宽 | 达到理论带宽比例 |
|------|------------|--------------|---------|---------------|
| Eager (PyTorch) | 6 | 3 | ~200 GB/s | ~10% |
| torch.compile | 3 | 1 | ~750 GB/s | ~37% |
| Triton 融合 | 3 | 1 | ~800 GB/s | ~39% |
| CUDA 手写 | 3 | 1 | ~820 GB/s | ~40% |

Triton 融合实现达到手写 CUDA 性能的 ~97.5%，而开发时间从天级降到小时级。**这就是 Triton 的价值主张**。

---

## 3. torch.compile：零代码改动的编译优化

### 3.1 设计动机：为什么不能让每个用户都手写 Triton？

Triton 解决了"写高性能 kernel"的问题，但没解决"让现有 PyTorch 代码自动变快"的问题。一个 LLM 训练脚本有数千行 Python 代码，不可能全部用 Triton 重写。

torch.compile 的目标：**不改动任何用户代码，通过 JIT 编译自动获得 10-30% 的性能提升**。

### 3.2 架构：Dynamo + Inductor 的两级编译

```
torch.compile 编译流程:

  PyTorch Model (nn.Module)
      │
      ▼
  ┌──────────────────────────────────────────┐
  │ TorchDynamo                               │
  │  拦截 Python 字节码, 构建 FX Graph         │
  │  遇到不支持的特性 → Graph Break             │
  └──────────────┬───────────────────────────┘
                 │ FX Graph (算子 DAG)
                 ▼
  ┌──────────────────────────────────────────┐
  │ TorchInductor                             │
  │  分析 FX Graph, 执行融合和优化              │
  │  GPU: 生成 Triton kernel 代码              │
  │  CPU: 生成 C++ / OpenMP 代码              │
  └──────────────┬───────────────────────────┘
                 │ 编译后的融合 Kernel
                 ▼
  缓存到磁盘, 后续调用直接执行
```

关键设计决策：**为什么 Inductor 选择生成 Triton 而不是 CUDA C？**

1. Triton 代码更短 → 编译更快（秒级 vs 分钟级）
2. Triton 的自动优化覆盖了 90% 场景 → 生成的 kernel 性能足够好
3. Triton 跨 GPU 厂商（NVIDIA + AMD）→ 一套代码两种硬件

### 3.3 Graph Break：编译器的"逃生舱"

Dynamo 通过拦截 Python 字节码来捕获计算图，但 Python 的动态特性意味着不是所有代码都能被编译。当 Dynamo 遇到无法静态分析的操作时，会产生 **graph break**：

```python
@torch.compile
def forward(x):
    y = x * 2                   # ✅ 可编译: 纯张量操作
    if y.sum().item() > 0:      # ❌ Graph Break: 数据依赖控制流
        z = y + 1               # ⚠️ 回退 eager mode
    else:
        z = y - 1               # ⚠️ 回退 eager mode
    w = z * 3                   # ✅ 可编译: 新的编译段开始
    return w

# 实际执行: [CompiledSegment] → [EagerSegment] → [CompiledSegment]
# 每次跨越 graph break 边界都有一次 HBM 写出 + 读入的开销
```

**Graph Break 的性能影响**：

| 场景 | Graph Break 数量 | 相比 eager 的加速 |
|------|-----------------|-----------------|
| 纯前向传播 (无控制流) | 0 | 20-30% |
| 含 1-2 个数据依赖分支 | 2-4 | 10-20% |
| 含大量动态逻辑 | 10+ | 可能负优化 |

**LLM 中的典型 Graph Break 来源**：

| 来源 | 示例 | 解决方案 |
|------|------|---------|
| 数据依赖控制流 | `if loss > threshold` | 用 `torch.where` 替代 |
| 动态形状 | `x[:variable_len]` | 用 padding + mask 替代 |
| 外部库调用 | `numpy_ops(x)` | 用 `torch.ops` 替代 |
| Python 副作用 | `print(x.shape)` | 移除或用 `torch._dynamo.graph_break` 标记 |

### 3.4 Inductor 的融合策略：什么融合、什么不融合

Inductor 的融合决策基于算术强度 (Arithmetic Intensity) 分析：

**总是融合**：连续的 elementwise 算子（逐元素操作），因为它们都是 memory-bound，融合后只读一次输入、写一次输出。

```
MatMul → BiasAdd → SiLU  →  FusedKernel
(compute)  (memory)  (memory)   (compute+memory 融合)
```

**谨慎融合**：Reduction 算子（如 LayerNorm/RMSNorm），因为归约需要完整的输入数据，不能与上游的输出分块融合。

```
RMSNorm = Square → Mean → Rsqrt → Mul
→ 融合为单个 kernel (1次 HBM 读写)
→ 但不能与上游的 MatMul 融合 (MatMul 的输出需要完整写出)
```

**不融合**：以下情况融合反而更慢：

| 不融合的原因 | 示例 | 为什么不融合 |
|-------------|------|------------|
| 输出被多个下游算子使用 | MatMul 的输出同时给 Attention 和 Residual | 融合后需要重复计算 |
| 融合后 register spill | 超过 ~255 个寄存器/thread | spill 到 local memory 反而更慢 |
| 上游是 compute-bound | 两个 MatMul 之间 | 融合不减少计算量，反而增加 register 压力 |

### 3.5 LLM 场景下的 torch.compile 实践

**训练场景**：

```python
model = AutoModelForCausalLM.from_pretrained("meta-llama/Llama-2-7b")
model = torch.compile(model)
```

典型加速：10-30% 吞吐提升。主要来源：
- QKV Proj + RoPE 融合 → 减少 2 次 HBM 读写
- RMSNorm + Residual 融合 → 减少 1 次 HBM 读写
- CUDA Graphs → 减少 kernel launch 开销

**推理场景**：

```python
model = torch.compile(model, mode="reduce-overhead")
```

`reduce-overhead` 模式使用 CUDA Graphs 将整个推理流程封装为单个 GPU 提交，消除逐 kernel 的 launch 开销。对小 batch 推理（单用户场景）效果最显著，可达 20-50% 延迟降低。

**编译模式的取舍**：

| 模式 | 编译时间 | 运行性能 | 适用场景 |
|------|---------|---------|---------|
| `default` | ~30s | 基线 | 开发调试 |
| `reduce-overhead` | ~1min | +10-20% vs default | 推理部署 |
| `max-autotune` | ~5-10min | +5-10% vs reduce-overhead | 生产部署 |

`max-autotune` 的额外 5-10% 来自尝试多种 kernel 配置（不同 BLOCK_SIZE、向量化宽度等），选择最快的。编译时间换运行性能。

---

## 4. XLA 与 JAX：另一条编译路径

### 4.1 设计哲学差异

torch.compile 和 XLA 解决同一个问题（自动融合和优化），但设计哲学截然不同：

| 维度 | torch.compile | XLA (JAX) |
|------|--------------|-----------|
| **图捕获方式** | 从 Python 字节码逆向提取 | 从函数式变换正向构建 |
| **IR 设计** | FX Graph (Python-level) | HLO (lower-level, 更严格) |
| **分布式** | 外部库 (FSDP/DeepSpeed) | 原生 GSPMD (编译器内建) |
| **编译速度** | 秒级 | 分钟级 |
| **优化深度** | 中等 (依赖 Triton 后端) | 深 (XLA 有 10+ 年优化积累) |

**核心差异**：torch.compile 是"渐进式"的——能编译的编译，不能编译的回退 eager；XLA 是"全有或全无"的——要么整个计算图被编译，要么报错。前者更灵活，后者优化空间更大。

### 4.2 GSPMD：XLA 的杀手级特性

GSPMD (Generalized Sharded Data Parallel) 是 XLA 的内建分布式策略，用户只需标注张量的分片方式，编译器自动推导所有中间张量的分片和通信：

```python
from jax.sharding import PartitionSpec as P, Mesh

mesh = Mesh(jax.devices(), ('tpu',))
spec = P('tpu', None)  # 第一维按 TPU 分片, 第二维复制

@jax.jit
def train_step(batch):
    x = jax.random.normal(jax.random.key(0), (8192, 4096))
    x = jax.lax.with_sharding_constraint(x, spec)  # 标注分片
    return x @ x.T
    # XLA 自动推导: x.T 的分片方式, matmul 的通信策略
```

对比 PyTorch 中需要手动编排的 TP/PP/DP 策略（见 [05-分布式训练](../05-LLM分布式训练：并行策略与ZeRO.md)），GSPMD 将分布式复杂性从用户代码移到了编译器。代价是编译时间更长、调试更困难。

### 4.3 何时选择 JAX/XLA？

| 选择 JAX/XLA 的场景 | 选择 PyTorch/torch.compile 的场景 |
|--------------------|--------------------------------|
| TPU 集群训练 | GPU 集群训练 |
| 需要自动分片推导 | 需要灵活的分布式策略 |
| 模型结构固定、长期训练 | 快速迭代、频繁改模型 |
| 团队有 JAX 经验 | 团队有 PyTorch 经验 |

Google 内部的 Gemini 系列模型使用 JAX + TPU 训练，而 Meta 的 Llama 系列使用 PyTorch + GPU 训练——两者都能训练出顶级模型，选择取决于硬件和团队生态。

---

## 5. MLIR：编译器的"乐高积木"

### 5.1 为什么需要 MLIR？

LLVM IR 是通用编译器基础设施，但它只有一个抽象层级——太低级，无法表达"融合两个算子"这样的高层优化。MLIR 的解决方案：**提供可扩展的多级 IR 框架，每级 IR 专注一类优化**。

```
MLIR 的 Dialect 层次 (从高层到低层):

  ┌───────────────────────────┐
  │ Torch Dialect             │  表达 PyTorch 语义 (如 torch.matmul)
  ├───────────────────────────┤
  │ TOSA Dialect              │  标准算子集 (跨框架通用)
  ├───────────────────────────┤
  │ Linalg Dialect            │  线性代数操作 (generic contraction)
  ├───────────────────────────┤
  │ Affine Dialect            │  循环变换 (tiling, fusion)
  ├───────────────────────────┤
  │ GPU Dialect               │  GPU 编程原语 (thread, block, barrier)
  ├───────────────────────────┤
  │ NVVM / ROCDL Dialect      │  GPU ISA (接近 PTX/AMDGCN)
  └───────────────────────────┘

  每层只做自己层级的优化, 然后降级到下一层
  → 各层解耦, 可以独立开发和替换
```

### 5.2 MLIR 在 LLM 编译链中的角色

MLIR 本身不是编译器，而是**构建编译器的框架**。当前 LLM 生态中的主要使用者：

| 项目 | 如何使用 MLIR | 价值 |
|------|-------------|------|
| **torch-mlir** | PyTorch → Torch Dialect → TOSA → Linalg | 让 PyTorch 模型可以编译到多种后端 |
| **XLA** | HLO → MHLO → Linalg | JAX/TensorFlow 的后端优化路径 |
| **IREE** | 全 dialect 链 → 多硬件后端 | 端到端 ML 编译部署 |
| **Triton** | Triton DSL → Triton Dialect → LLVM IR | Triton 编译器本身基于 MLIR 构建 |

**关键洞察**：Triton 和 torch.compile 的底层都依赖 MLIR。Triton 的编译器将 DSL 先降级为 Triton Dialect (MLIR)，再降级为 LLVM IR → PTX。torch.compile 的 Inductor 后端也使用 MLIR 做图优化。MLIR 是当前 GPU 编译器生态的"共同语言"。

---

## 6. TensorRT-LLM：推理部署的终极编译器

### 6.1 TensorRT 的优化逻辑

TensorRT 是 NVIDIA 的推理优化编译器，它的核心假设是：**模型结构在编译时已知、权重在编译时固定**。这个假设使得 TensorRT 可以做 PyTorch eager 无法做的优化：

| 优化 | 需要编译时信息的原因 | 效果 |
|------|-------------------|------|
| **权重预布局** | 知道权重值 → 重排内存布局适配 Tensor Core 访问模式 | 10-20% GEMM 加速 |
| **精度校准** | 知道权重和激活分布 → 选择最优量化参数 | 2-4× 吞吐 |
| **Kernel Auto-Tuning** | 知道所有维度 → 在编译时 benchmark 所有候选 kernel | 平台最优 |
| **显存复用规划** | 知道完整计算图 → 规划中间张量的生命周期和复用 | 减少 30-50% 显存 |

### 6.2 TensorRT-LLM 的 LLM 专用优化

TensorRT-LLM 在 TensorRT 基础上增加了 LLM 推理特有的优化，这些优化无法用通用的编译器框架自动推导：

| 优化 | 为什么通用编译器做不到 | 参考 |
|------|---------------------|------|
| **Inflight Batching** | 需要运行时动态管理请求队列，不是纯计算图优化 | [13-推理服务架构](../13-LLM推理服务架构：调度、缓存与投机解码.md) |
| **Paged KV Cache** | 需要自定义内存管理器，超出编译器的内存模型 | 同上 |
| **FP8/FP4 GEMM** | 需要硬件特定的缩放因子校准和 kernel 实现 | [09-量化技术](../09-LLM量化技术：PTQ、QAT到FP8推理.md) |
| **Speculative Decoding** | 需要运行时的推测-验证循环，不是静态计算图 | [13-推理服务架构](../13-LLM推理服务架构：调度、缓存与投机解码.md) |

---

## 7. 决策框架：何时用什么？

### 7.1 按场景选择

| 场景 | 推荐方案 | 关键理由 | 避免的坑 |
|------|---------|---------|---------|
| 快速原型验证 | PyTorch eager | 开发效率最高 | 不要过早优化 |
| 训练加速 | `torch.compile(model)` | 零代码改动，10-30% 提升 | 注意 graph break 导致编译失败 |
| 自定义融合算子 | Triton | 性能接近 CUDA，开发快 10× | 极端场景（TMA/WGMMA）仍需 CUDA |
| NVIDIA 推理部署 | TensorRT-LLM | 最成熟的 LLM 推理方案 | 编译时间长（分钟级），动态形状支持差 |
| 通用推理部署 | vLLM + torch.compile | 灵活性好，社区活跃 | 性能不如 TensorRT-LLM |
| TPU 训练 | JAX + XLA | TPU 原生支持 | 调试困难，编译时间长 |
| 跨平台部署 | IREE / ONNX Runtime | 多硬件后端 | 生态不成熟，LLM 支持有限 |

### 7.2 按瓶颈类型选择

| 瓶颈类型 | 诊断方法 | 优化手段 | 工具 |
|---------|---------|---------|------|
| **Kernel Launch 开销** | Nsight Systems 显示大量短 kernel | CUDA Graphs / torch.compile | `torch.compile(mode="reduce-overhead")` |
| **显存带宽瓶颈** | Roofline 分析，算术强度 < 峰值比 | 算子融合 | Triton / torch.compile |
| **计算瓶颈** | GPU 利用率 > 80% | 更高精度量化 / 更大 batch | TensorRT-LLM |
| **显存容量不足** | OOM 或接近 HBM 上限 | 量化 / 梯度检查点 | [09-量化技术](../09-LLM量化技术：PTQ、QAT到FP8推理.md) |

---

## 参考资料

- [Triton: An Intermediate Language and Compiler for Tiled Neural Network Computations](https://dl.acm.org/doi/10.1145/3315508.3329973) — Triton 原始论文，阐述 block-level 编程模型的设计动机
- [torch.compile](https://pytorch.org/tutorials/intermediate/torch_compile_tutorial.html) — PyTorch 官方编译教程
- [TorchDynamo](https://pytorch.org/docs/stable/torch.compiler_dynamo.html) — 字节码拦截图捕获机制
- [XLA: Optimizing Compiler for Machine Learning](https://www.tensorflow.org/xla) — XLA 编译器文档
- [MLIR: Scaling Compiler Infrastructure for Domain Specific Computation](https://arxiv.org/abs/2002.11054) — MLIR 多级 IR 框架论文
- [TensorRT-LLM](https://github.com/NVIDIA/TensorRT-LLM) — NVIDIA LLM 推理引擎

> **下一篇**：回到 [LLM 训练推理全景学习框架](../00-LLM训练推理全景学习框架.md) — 全栈串联
