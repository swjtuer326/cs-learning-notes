# LLM 推理性能调优实战

> **核心命题**：推理性能调优不是玄学，而是一套系统化的方法论。从基准测试到瓶颈分析，从参数调优到架构优化，每一步都有章可循。

## 目录

1. [性能调优方法论](#性能调优方法论)
2. [基准测试工具](#基准测试工具)
3. [瓶颈分析](#瓶颈分析)
4. [vLLM 调优实战](#vllm-调优实战)
5. [TensorRT-LLM 调优实战](#tensorrt-llm-调优实战)
6. [量化调优](#量化调优)
7. [多 GPU 调优](#多-gpu-调优)
8. [常见问题排查](#常见问题排查)

---

## 性能调优方法论

### 1.1 调优流程

```
系统化调优流程:

  1. 建立基线 (Baseline)
     └─ 默认配置，测量基准性能

  2. 识别瓶颈 (Bottleneck)
     └─ 计算瓶颈? 显存瓶颈? 通信瓶颈?

  3. 制定策略 (Strategy)
     └─ 根据瓶颈选择优化方向

  4. 单变量调优 (One at a Time)
     └─ 每次只改一个参数，测量效果

  5. 验证与回归 (Validation)
     └─ 确保精度没有显著下降

  6. 文档化 (Document)
     └─ 记录配置和结果
```

### 1.2 关键指标

```
推理性能关键指标:

  TTFT (Time to First Token):
    - 用户感知延迟的关键
    - 目标: < 200ms (P95)

  TPOT (Time per Output Token):
    - 生成速度的体感
    - 目标: < 50ms (P95)

  Throughput (吞吐):
    - tokens/s (总)
    - requests/s
    - 目标: 最大化 (在 SLA 约束下)

  GPU 利用率:
    - SM 利用率 (计算)
    - 显存带宽利用率
    - 目标: > 80%

  KV Cache 命中率:
    - Prefix Caching 的效果
    - 目标: > 50% (取决于场景)
```

---

## 基准测试工具

### 2.1 vLLM Benchmarks

```bash
# vLLM 内置 benchmark
python -m vllm.entrypoints.openai.api_server \
    --model meta-llama/Llama-3-8B-Instruct \
    --port 8000

# 另一终端运行 benchmark
python benchmarks/benchmark_serving.py \
    --backend vllm \
    --model meta-llama/Llama-3-8B-Instruct \
    --dataset-name sharegpt \
    --dataset-path ShareGPT_V3_unfiltered_cleaned_split.json \
    --num-prompts 1000 \
    --request-rate 10

# 输出示例:
# ======== Serving Benchmark Result ========
# Successful requests:                     1000
# Benchmark duration (s):                  120.45
# Total input tokens:                      256000
# Total generated tokens:                  128000
# Request throughput (req/s):              8.30
# Input token throughput (tok/s):          2125.78
# Output token throughput (tok/s):         1062.89
# ---------------Time to First Token----------------
# Mean TTFT (ms):                          85.23
# Median TTFT (ms):                        78.45
# P99 TTFT (ms):                           245.67
# -----Time per Output Token (excl. 1st token)------
# Mean TPOT (ms):                          32.15
# Median TPOT (ms):                        30.12
# P99 TPOT (ms):                           78.90
```

### 2.2 其他基准工具

```bash
# GenAI-Perf (NVIDIA Triton)
genai-perf \
    -m llama-3-8b \
    --backend tensorrtllm \
    --endpoint http://localhost:8000/v1 \
    --num-prompts 100 \
    --concurrency 10

# llmperf (Anyscale)
python llmperf/benchmark.py \
    --model meta-llama/Llama-3-8B-Instruct \
    --num-requests 100 \
    --max-num-completed-requests 100

# 自定义 benchmark (Python)
import time
import asyncio
from openai import AsyncOpenAI

client = AsyncOpenAI(base_url="http://localhost:8000/v1", api_key="dummy")

async def benchmark_single(prompt):
    start = time.time()
    ttft = None
    total_tokens = 0
    
    stream = await client.completions.create(
        model="meta-llama/Llama-3-8B-Instruct",
        prompt=prompt,
        max_tokens=256,
        stream=True,
    )
    
    async for chunk in stream:
        if ttft is None:
            ttft = time.time() - start
        total_tokens += 1
    
    total_time = time.time() - start
    tpot = (total_time - ttft) / (total_tokens - 1) if total_tokens > 1 else 0
    
    return {
        "ttft": ttft,
        "tpot": tpot,
        "total_tokens": total_tokens,
        "total_time": total_time,
    }
```

---

## 瓶颈分析

### 3.1 使用 PyTorch Profiler

```python
import torch
from torch.profiler import profile, ProfilerActivity

def profile_inference(model, input_ids):
    with profile(
        activities=[ProfilerActivity.CPU, ProfilerActivity.CUDA],
        record_shapes=True,
        with_stack=True,
    ) as prof:
        with torch.no_grad():
            output = model(input_ids)
    
    # 打印关键统计
    print(prof.key_averages().table(
        sort_by="cuda_time_total", row_limit=20
    ))
    
    # 导出 Chrome trace
    prof.export_chrome_trace("trace.json")
    
    return output

# 分析结果:
# 1. 看 cuda_time_total 最大的算子
# 2. 看显存分配/释放模式
# 3. 看 kernel launch overhead
```

### 3.2 使用 NVIDIA Nsight

```bash
# Nsight Systems (系统级)
nsys profile \
    --trace=cuda,nvtx,osrt \
    --output=profile \
    python inference_script.py

# Nsight Compute (kernel 级)
ncu \
    --kernel-name regex:attention \
    --launch-count 1 \
    --set full \
    python inference_script.py

# 关键指标:
# - Compute Utilization: SM 利用率
# - Memory Utilization: 显存带宽利用率
# - Occupancy: 每个 SM 的活跃 warp 数
# - Registers: 寄存器使用量
```

### 3.3 瓶颈分类

```
瓶颈分类与对策:

1. 计算瓶颈 (Compute Bound):
   症状: SM 利用率 > 80%, 显存带宽利用率 < 60%
   对策:
   - 使用 FP8/INT8 量化
   - 增大 batch size
   - 使用 TensorRT-LLM 编译优化

2. 显存带宽瓶颈 (Memory Bandwidth Bound):
   症状: 显存带宽利用率 > 80%, SM 利用率 < 60%
   对策:
   - 权重量化 (4-bit)
   - KV Cache 量化
   - 减少 KV Cache 大小

3. 显存容量瓶颈 (Memory Capacity Bound):
   症状: OOM 或频繁 swap
   对策:
   - 量化 (4-bit)
   - 减少 max_model_len
   - 增加 GPU 数量 (TP)

4. 通信瓶颈 (Communication Bound):
   症状: 多 GPU 时 GPU 利用率低
   对策:
   - 优化 TP/PP 配置
   - 使用 NVLink (而非 PCIe)
   - 减少跨节点通信
```

---

## vLLM 调优实战

### 4.1 核心参数

```bash
python -m vllm.entrypoints.openai.api_server \
    --model meta-llama/Llama-3-8B-Instruct \
    --tensor-parallel-size 1 \
    --max-model-len 8192 \
    --max-num-seqs 256 \
    --max-num-batched-tokens 8192 \
    --gpu-memory-utilization 0.90 \
    --enable-prefix-caching \
    --enable-chunked-prefill \
    --max-num-on-the-fly 32
```

### 4.2 参数详解

```
vLLM 关键参数调优:

1. max-num-seqs (最大并发序列数):
   - 默认: 256
   - 增大 → 更高吞吐，但延迟可能升高
   - 减小 → 更低延迟，但吞吐下降
   - 建议: 从 64 开始，逐步增大

2. max-num-batched-tokens (每步最大 token 数):
   - 控制 prefill chunk 大小
   - 增大 → prefill 更快，但 decode 延迟升高
   - 减小 → decode 延迟更低，但 prefill 更慢
   - 建议: 2048-8192

3. gpu-memory-utilization (GPU 显存利用率):
   - 默认: 0.90
   - 增大 → 更多 KV Cache 空间
   - 减小 → 更安全 (避免 OOM)
   - 建议: 0.85-0.95

4. enable-prefix-caching:
   - 开启 → 共享前缀的请求复用 KV Cache
   - 适用: system prompt 固定的场景
   - 不适用: 每次 prompt 完全不同

5. enable-chunked-prefill:
   - 开启 → 长 prefill 分块处理
   - 改善 TTFT (避免长 prefill 阻塞)
   - 建议: 始终开启
```

### 4.3 调优案例

```
案例 1: 高吞吐场景 (批量处理)

目标: 最大化 tokens/s

配置:
  --max-num-seqs 256
  --max-num-batched-tokens 16384
  --gpu-memory-utilization 0.95
  --enable-prefix-caching
  --enable-chunked-prefill

结果:
  吞吐: 2500 → 4500 tokens/s (+80%)
  P99 TTFT: 200ms → 450ms (可接受)

案例 2: 低延迟场景 (在线聊天)

目标: P95 TTFT < 200ms

配置:
  --max-num-seqs 32
  --max-num-batched-tokens 4096
  --gpu-memory-utilization 0.85
  --enable-chunked-prefill

结果:
  P95 TTFT: 450ms → 180ms (-60%)
  吞吐: 4500 → 2800 tokens/s (可接受)
```

---

## TensorRT-LLM 调优实战

### 5.1 编译优化

```bash
# TensorRT-LLM 模型编译
trtllm-build \
    --checkpoint_dir ./llama3_8b_fp16 \
    --output_dir ./llama3_8b_trt \
    --gemm_plugin float16 \
    --gpt_attention_plugin float16 \
    --max_batch_size 256 \
    --max_input_len 8192 \
    --max_output_len 2048 \
    --max_beam_width 1 \
    --context_fmha enable \
    --use_paged_context_fmha enable \
    --use_fp8_context_fmha enable \
    --paged_kv_cache enable \
    --remove_input_padding enable \
    --multiple_profiles enable
```

### 5.2 关键优化选项

```
TensorRT-LLM 编译优化选项:

1. gemm_plugin:
   - float16 / bfloat16 / fp8
   - 使用优化的 GEMM kernel
   - fp8 可提升 2× 性能

2. gpt_attention_plugin:
   - 使用优化的 Attention kernel
   - 支持 FlashAttention / FlashDecoding
   - 建议: 始终开启

3. context_fmha (Fused Multi-Head Attention):
   - 融合 prefill 阶段的 Attention
   - 减少 kernel launch overhead
   - 建议: enable

4. paged_kv_cache:
   - 类似 vLLM 的 PagedAttention
   - 减少 KV Cache 碎片
   - 建议: enable

5. remove_input_padding:
   - 去除 padding tokens
   - 减少无效计算
   - 建议: enable

6. multiple_profiles:
   - 为不同 batch size 编译多个优化版本
   - 运行时选择最优
   - 增加编译时间但提升运行时性能
```

### 5.3 运行时调优

```python
# TensorRT-LLM 运行时配置
from tensorrt_llm.runtime import ModelRunner, ModelRunnerCpp

runner = ModelRunnerCpp.from_dir(
    engine_dir="./llama3_8b_trt",
    rank=0,
    max_batch_size=256,
    max_input_len=8192,
    max_output_len=2048,
    max_beam_width=1,
    max_attention_window_size=None,
    sink_token_length=None,
    max_tokens_in_paged_kv_cache=256000,
    kv_cache_enable_block_reuse=True,
    kv_cache_free_gpu_memory_fraction=0.90,
    enable_chunked_context=True,
)

# 关键参数:
# - max_tokens_in_paged_kv_cache: KV Cache 容量
# - kv_cache_free_gpu_memory_fraction: KV Cache 显存占比
# - enable_chunked_context: Chunked Prefill
```

---

## 量化调优

### 6.1 量化方案选择

```
量化方案决策树:

  模型大小 < 10B:
    → FP16/BF16 (如果显存够)
    → INT8 权重量化 (如果需要省显存)

  模型大小 10B-70B:
    → 4-bit 权重量化 (AWQ/GPTQ)
    → FP8 KV Cache
    → 单卡可部署

  模型大小 > 70B:
    → 4-bit 权重量化 + TP
    → FP8 KV Cache
    → 多卡部署

  极致性能:
    → FP8 权重 + FP8 激活 (H100+)
    → TensorRT-LLM 编译
```

### 6.2 量化精度验证

```python
# 量化精度验证
from lm_eval import evaluator
from lm_eval.models import vllm_causallms

# 原始模型
results_fp16 = evaluator.simple_evaluate(
    model=vllm_causallms.VLLM(
        pretrained="meta-llama/Llama-3-8B-Instruct",
        dtype="float16",
    ),
    tasks=["mmlu", "gsm8k", "humaneval"],
)

# 量化模型
results_int4 = evaluator.simple_evaluate(
    model=vllm_causallms.VLLM(
        pretrained="meta-llama/Llama-3-8B-Instruct-AWQ",
        dtype="float16",
        quantization="awq",
    ),
    tasks=["mmlu", "gsm8k", "humaneval"],
)

# 对比
for task in ["mmlu", "gsm8k", "humaneval"]:
    diff = results_fp16["results"][task]["acc"] - \
           results_int4["results"][task]["acc"]
    print(f"{task}: FP16={results_fp16['results'][task]['acc']:.4f}, "
          f"INT4={results_int4['results'][task]['acc']:.4f}, "
          f"Diff={diff:.4f}")
```

---

## 多 GPU 调优

### 7.1 TP 调优

```
TP (张量并行) 调优:

原则:
  - TP 在 NVLink 域内 (同一节点)
  - TP size 不宜过大 (通信开销)

H100 (NVLink 900 GB/s):
  TP=2: 通信开销 ~5%
  TP=4: 通信开销 ~10%
  TP=8: 通信开销 ~15-20%

A100 (NVLink 600 GB/s):
  TP=2: 通信开销 ~8%
  TP=4: 通信开销 ~15%
  TP=8: 通信开销 ~25%

建议:
  - 7B 模型: TP=1 (单卡)
  - 13B 模型: TP=1 (单卡, 量化后)
  - 70B 模型: TP=2 或 TP=4 (量化后 TP=1)
  - 405B 模型: TP=8
```

### 7.2 Pipeline Parallelism

```
PP (流水线并行) 调优:

vLLM PP 配置:
  --pipeline-parallel-size 2

关键参数:
  - PP size: 流水线阶段数
  - 每阶段 GPU 数 = TP size

PP 的 bubble 问题:
  bubble_size = (PP_size - 1) / num_micro_batches
  
  例: PP=4, micro_batches=32
  bubble = 3/32 ≈ 9.4%

减少 bubble:
  - 增大 num_micro_batches
  - 使用交错调度 (interleaved schedule)
  - 但会增加通信次数
```

---

## 常见问题排查

### 8.1 问题速查表

| 问题 | 可能原因 | 解决方案 |
|------|---------|---------|
| **OOM** | 显存不足 | 量化、减小 max_model_len、增加 GPU |
| **TTFT 高** | Prefill 慢 | Chunked prefill、减小 max-num-batched-tokens |
| **TPOT 高** | Decode 慢 | 量化、检查 batch size |
| **吞吐低** | GPU 利用率低 | 增大 max-num-seqs、检查瓶颈 |
| **延迟抖动** | 调度不均 | 调整 max-num-seqs、检查 preemption |
| **精度下降** | 量化过度 | 换用更高精度量化、检查校准数据 |
| **启动慢** | 模型加载慢 | 使用量化模型、检查磁盘 I/O |
| **通信慢** | 跨节点通信 | 优化拓扑、使用 NVLink |

### 8.2 调试命令

```bash
# 查看 GPU 状态
nvidia-smi -l 1

# 查看 GPU 拓扑
nvidia-smi topo -m

# 查看 NCCL 通信
NCCL_DEBUG=INFO python inference_script.py

# 查看显存使用
torch.cuda.memory_summary()

# vLLM 日志级别
VLLM_LOGGING_LEVEL=DEBUG python -m vllm.entrypoints.openai.api_server ...

# 检查 KV Cache 使用
curl http://localhost:8000/metrics | grep vllm:gpu_cache_usage
```

---

> **关键原则**：
> 1. **先测量再优化**：没有基准数据的优化是盲目的
> 2. **单变量调优**：一次只改一个参数
> 3. **量化是性价比之王**：4-bit 量化 + FP8 KV Cache 是最佳起点
> 4. **SLA 优先于吞吐**：满足延迟要求的前提下最大化吞吐
> 5. **文档化一切**：记录每次调优的配置和结果
