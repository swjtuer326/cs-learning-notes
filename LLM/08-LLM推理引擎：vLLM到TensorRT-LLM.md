# LLM 推理引擎：vLLM 到 TensorRT-LLM

> **核心命题**：推理引擎是 LLM 落地的"最后一公里"——它将训练好的模型转化为高性能的在线服务。vLLM 和 TensorRT-LLM 代表了两种不同的设计哲学：灵活性与极致性能。

## 目录

1. [推理引擎全景](#推理引擎全景)
2. [vLLM 深入](#vllm-深入)
3. [TensorRT-LLM 深入](#tensorrt-llm-深入)
4. [其他推理引擎](#其他推理引擎)
5. [推理引擎对比](#推理引擎对比)
6. [部署实践](#部署实践)

---

## 推理引擎全景

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        推理引擎技术栈                                     │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  应用层                                                                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐                  │
│  │ OpenAI   │ │ 聊天应用  │ │ 代码助手  │ │ RAG 应用  │                  │
│  │ API 兼容  │ │          │ │          │ │          │                  │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘                  │
│       └─────────────┴─────────────┴─────────────┘                      │
│                         │                                               │
│  调度层                                                                  │
│  ┌──────────────────────────────────────────┐                          │
│  │  Continuous Batching / Disaggregated     │                          │
│  │  Prefix Caching / Speculative Decoding   │                          │
│  └────────────────────┬─────────────────────┘                          │
│                       │                                                 │
│  执行层                                                                  │
│  ┌──────────────────────────────────────────┐                          │
│  │  vLLM / TensorRT-LLM / SGLang / LMDeploy │                          │
│  │  FlashAttention / PagedAttention / CUDA  │                          │
│  └────────────────────┬─────────────────────┘                          │
│                       │                                                 │
│  硬件层                                                                  │
│  ┌──────────────────────────────────────────┐                          │
│  │  NVIDIA GPU / AMD GPU / TPU / ...        │                          │
│  └──────────────────────────────────────────┘                          │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## vLLM 深入

### 2.1 vLLM 架构

```
vLLM 核心组件:

┌─────────────────────────────────────────┐
│              vLLM Engine                 │
├─────────────────────────────────────────┤
│                                         │
│  ┌─────────────┐   ┌─────────────────┐  │
│  │  Scheduler   │──▶│  Block Manager  │  │
│  │             │   │  (PagedAttention)│  │
│  └──────┬──────┘   └─────────────────┘  │
│         │                                │
│         ▼                                │
│  ┌─────────────┐   ┌─────────────────┐  │
│  │  Model      │   │  Cache Engine   │  │
│  │  Runner     │   │  (Prefix Cache) │  │
│  └──────┬──────┘   └─────────────────┘  │
│         │                                │
│         ▼                                │
│  ┌─────────────────────────────────────┐ │
│  │  GPU Workers (TP/PP)               │ │
│  │  - FlashAttention Kernel           │ │
│  │  - PagedAttention Kernel           │ │
│  │  - Quantized Kernels (AWQ/GPTQ)    │ │
│  └─────────────────────────────────────┘ │
│                                         │
└─────────────────────────────────────────┘
```

### 2.2 vLLM 调度器

```
vLLM 调度策略:

1. 请求到达 → 加入等待队列
2. 调度器决定:
   - 哪些请求可以加入当前 batch
   - 每个请求分配多少 KV Cache blocks
3. 约束:
   - 显存: KV Cache blocks 总数有限
   - 计算: batch 中 token 总数有限 (max_num_batched_tokens)
   - 序列: max_num_seqs

调度算法 (FCFS with Preemption):
  1. 按到达时间排序
  2. 尽可能多地加入请求 (直到显存或 token 上限)
  3. 如果显存不够:
     - Preemption: 将部分请求的 KV Cache swap 到 CPU
     - 或等待当前请求完成释放显存

关键参数:
  --max-num-batched-tokens: 最大 batch token 数 (如 8192)
  --max-num-seqs: 最大并发序列数 (如 256)
  --gpu-memory-utilization: GPU 显存使用比例 (如 0.90)
```

### 2.3 vLLM 的 KV Cache 管理

```
PagedAttention Block Manager:

Block 大小: 16 tokens (默认)

操作:
  1. 分配: 为新请求分配 blocks
  2. 扩展: 生成新 token 时追加 blocks
  3. 释放: 请求完成后释放 blocks
  4. 共享: Prefix Caching 时多个请求共享 blocks

Copy-on-Write (CoW):
  当多个请求共享 prefix blocks 时:
  - 读取: 共享同一份物理 blocks
  - 写入: 新 token 写入新的 blocks
  → 安全共享，无数据竞争

显存管理:
  - 预分配一个大的 block pool
  - 使用 bitmap 跟踪空闲 blocks
  - 支持 defragmentation (碎片整理)
```

### 2.4 vLLM 部署

```bash
# 安装
pip install vllm

# 启动 OpenAI 兼容 API 服务
vllm serve meta-llama/Llama-3-8B-Instruct \
    --host 0.0.0.0 \
    --port 8000 \
    --tensor-parallel-size 1 \
    --max-model-len 8192 \
    --gpu-memory-utilization 0.90 \
    --max-num-batched-tokens 8192 \
    --max-num-seqs 256 \
    --enable-prefix-caching

# Python API
from vllm import LLM, SamplingParams

llm = LLM(
    model="meta-llama/Llama-3-8B-Instruct",
    tensor_parallel_size=1,
    max_model_len=8192,
    enable_prefix_caching=True,
)

sampling_params = SamplingParams(
    temperature=0.7,
    top_p=0.9,
    max_tokens=512,
)

outputs = llm.generate(
    ["What is the capital of France?"],
    sampling_params,
)
```

### 2.5 vLLM 性能优化

```
vLLM 性能调优:

1. Prefix Caching:
   --enable-prefix-caching
   → 对 system prompt 固定的场景效果显著

2. Chunked Prefill:
   --enable-chunked-prefill
   → 将长 prefill 分成多个 chunk
   → 减少 prefill 对 decode 的延迟影响

3. FP8 KV Cache:
   --kv-cache-dtype fp8
   → KV Cache 显存减半

4. Speculative Decoding:
   --speculative-model <draft_model>
   → 用 draft model 加速生成

5. Multi-Step Scheduling:
   --num-scheduler-steps 8
   → 一次调度执行多步 decode
   → 减少 CPU-GPU 同步开销
```

---

## TensorRT-LLM 深入

### 3.1 TensorRT-LLM 架构

```
TensorRT-LLM 工作流程:

┌─────────────────────────────────────────────────────────┐
│                    TensorRT-LLM                          │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  1. 模型定义 (Python API)                                │
│     ┌──────────────────────────────────────┐            │
│     │  定义模型结构 (类似 PyTorch)          │            │
│     │  指定并行策略 (TP/PP)                │            │
│     │  指定量化配置 (FP8/INT8/INT4)        │            │
│     └──────────────┬───────────────────────┘            │
│                    ▼                                    │
│  2. 图编译 (Graph Optimization)                         │
│     ┌──────────────────────────────────────┐            │
│     │  算子融合 (LayerNorm + Quant + GEMM) │            │
│     │  内存优化 (显存复用, 预分配)         │            │
│     │  Kernel 自动调优 (Tactic Selection)  │            │
│     └──────────────┬───────────────────────┘            │
│                    ▼                                    │
│  3. 运行时 (C++ Runtime)                                │
│     ┌──────────────────────────────────────┐            │
│     │  In-Flight Batching (类似 CB)        │            │
│     │  KV Cache 管理                       │            │
│     │  ￿Plugin 系统 (自定义 Kernel)        │            │
│     └──────────────────────────────────────┘            │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### 3.2 TensorRT-LLM 编译流程

```python
# TensorRT-LLM 模型构建示例

import tensorrt_llm
from tensorrt_llm import Builder
from tensorrt_llm.network import net_guard
from tensorrt_llm.functional import (
    Tensor, concat, shape, slice, unsqueeze, view,
)

# 1. 构建模型图
def build_llama_model():
    builder = Builder()
    
    with net_guard(builder):
        # 定义输入
        input_ids = Tensor(
            name='input_ids',
            dtype=tensorrt_llm.str_dtype_to_trt('int32'),
            shape=(-1, -1),  # 动态 shape
        )
        
        # 构建 Transformer 层
        hidden_states = embedding(input_ids)
        for layer_idx in range(num_layers):
            hidden_states = transformer_layer(
                hidden_states, layer_idx
            )
        
        # LM Head
        logits = lm_head(hidden_states)
        
        # 标记输出
        logits.mark_output('logits', dtype)

# 2. 编译优化
builder_config = BuilderConfig(
    max_batch_size=8,
    max_input_len=4096,
    max_output_len=2048,
    max_beam_width=1,
    # 量化
    quant_mode=QuantMode.use_fp8_kv_cache(),
    # 并行
    tensor_parallel=4,
    pipeline_parallel=1,
)

# 3. 序列化
engine = builder.build_engine(builder_config)
engine.save("llama3_8b_fp8.engine")
```

### 3.3 In-Flight Batching

```
In-Flight Batching (TensorRT-LLM 的 Continuous Batching):

与 vLLM 的 Continuous Batching 类似，但实现更底层:

特点:
  1. 请求可以在任意时刻加入/离开 batch
  2. 支持 prefill 和 decode 混合
  3. 动态管理 KV Cache blocks

与 vLLM 的区别:
  - vLLM: Python 调度 + CUDA kernel
  - TRT-LLM: C++ 调度 + 编译优化的 kernel
  → TRT-LLM 调度开销更低
```

### 3.4 TensorRT-LLM 部署

```bash
# 安装
pip install tensorrt_llm

# 构建引擎
python convert_checkpoint.py \
    --model_dir meta-llama/Llama-3-8B-Instruct \
    --output_dir trt_ckpt \
    --dtype float16 \
    --tp_size 1

trtllm-build \
    --checkpoint_dir trt_ckpt \
    --output_dir trt_engines \
    --gemm_plugin float16 \
    --max_batch_size 8 \
    --max_input_len 4096 \
    --max_output_len 2048

# 启动服务
python run.py \
    --engine_dir trt_engines \
    --tokenizer_dir meta-llama/Llama-3-8B-Instruct \
    --max_output_len 2048
```

---

## 其他推理引擎

### 4.1 SGLang

```
SGLang (Stanford):

特点:
  1. RadixAttention: 基于 Radix Tree 的 Prefix Caching
     → 比 vLLM 的 hash-based 更灵活
     → 自动匹配任意长度的公共前缀

  2. 结构化生成:
     - JSON mode
     - Regex 约束
     - 语法约束 (CFG)

  3. SGLang DSL:
     用于复杂 LLM 编程 (多轮调用、并行、分支)

性能: 与 vLLM 相当或略优 (尤其在 Prefix Caching 场景)
```

### 4.2 LMDeploy

```
LMDeploy (InternLM/上海 AI Lab):

特点:
  1. TurboMind: 自研推理引擎 (C++)
  2. 支持 PyTorch 后端 (兼容性好)
  3. 量化: W4A16 (AWQ/GPTQ), KV8
  4. 持久化 batch: 减少 kernel launch 开销
  5. 与 OpenCompass 评估集成

优势: 中文模型支持好，InternLM 系列官方推理引擎
```

### 4.3 llama.cpp

```
llama.cpp:

特点:
  1. 纯 C/C++ 实现，无 Python 依赖
  2. CPU 推理 (也支持 GPU via CUDA/Metal/Vulkan)
  3. GGUF 格式: 自研量化格式
  4. 极低资源消耗 (树莓派也能跑)

量化格式 (K-quant):
  Q2_K, Q3_K_S, Q3_K_M, Q3_K_L,
  Q4_K_S, Q4_K_M, Q5_K_S, Q5_K_M,
  Q6_K, Q8_0

适用场景:
  - 本地/边缘推理
  - 低资源环境
  - 批量离线推理
```

### 4.4 其他引擎

| 引擎 | 特点 | 适用场景 |
|------|------|---------|
| **Text Generation Inference (TGI)** | HuggingFace 官方，生态好 | 与 HF 生态集成 |
| **MLC LLM** | 多平台 (iOS/Android/WebGPU) | 移动端/浏览器推理 |
| **Ollama** | 一键部署，用户体验好 | 本地使用 |
| **Ray Serve + vLLM** | 分布式调度 | 大规模生产 |
| **NVIDIA Triton Inference Server** | 通用推理服务器 | 多模型服务 |

---

## 推理引擎对比

### 5.1 综合对比

| 维度 | vLLM | TensorRT-LLM | SGLang | TGI | llama.cpp |
|------|------|-------------|--------|-----|-----------|
| **性能** | 高 | 最高 | 高 | 中-高 | 中 (CPU) |
| **易用性** | 高 | 低 | 中 | 高 | 高 |
| **模型支持** | 广泛 | 有限 (需适配) | 广泛 | 广泛 | 广泛 (GGUF) |
| **量化** | AWQ/GPTQ/FP8 | FP8/INT8/INT4 | AWQ/GPTQ/FP8 | GPTQ/bitsandbytes | K-quant |
| **Prefix Caching** | ✅ | ✅ | ✅ (Radix) | ❌ | ❌ |
| **Speculative Decoding** | ✅ | ✅ | ✅ | ❌ | ❌ |
| **OpenAI API** | ✅ | ✅ (via Triton) | ✅ | ✅ | ✅ (via server) |
| **开发语言** | Python/C++ | C++/Python | Python | Rust/Python | C/C++ |
| **硬件** | NVIDIA GPU | NVIDIA GPU | NVIDIA GPU | NVIDIA GPU | CPU/GPU |

### 5.2 性能基准 (Llama-3-8B, A100 80GB)

```
吞吐 (tokens/s, batch=256, output_len=128):

  vLLM (FP16):          ~4500 tokens/s
  vLLM (AWQ 4-bit):     ~7200 tokens/s
  TensorRT-LLM (FP16):  ~5200 tokens/s
  TensorRT-LLM (FP8):   ~8500 tokens/s
  SGLang (FP16):        ~4600 tokens/s
  TGI (FP16):           ~3800 tokens/s

→ TensorRT-LLM 性能最高 (15-20% vs vLLM)
→ 但 vLLM 更易用，模型支持更广
```

---

## 部署实践

### 6.1 生产部署架构

```
推荐生产部署架构:

                    ┌──────────────┐
                    │   Nginx/     │
                    │   Envoy      │  (负载均衡)
                    └──────┬───────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │ vLLM     │ │ vLLM     │ │ vLLM     │  (多实例)
        │ Instance │ │ Instance │ │ Instance │
        │ GPU 0-7  │ │ GPU 0-7  │ │ GPU 0-7  │
        └──────────┘ └──────────┘ └──────────┘
              │            │            │
              └────────────┼────────────┘
                           ▼
                    ┌──────────────┐
                    │   Redis      │  (Prefix Cache 共享)
                    │   / 共享存储  │
                    └──────────────┘
```

### 6.2 Docker 部署

```dockerfile
# Dockerfile
FROM nvidia/cuda:12.4.0-runtime-ubuntu22.04

RUN pip install vllm

EXPOSE 8000

CMD ["vllm", "serve", "meta-llama/Llama-3-8B-Instruct", \
     "--host", "0.0.0.0", "--port", "8000"]
```

```yaml
# docker-compose.yml
version: '3.8'
services:
  vllm:
    build: .
    ports:
      - "8000:8000"
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    environment:
      - CUDA_VISIBLE_DEVICES=0
```

### 6.3 监控指标

```
关键监控指标:

  延迟:
  - TTFT (Time to First Token): P50, P95, P99
  - TPOT (Time per Output Token): P50, P95, P99
  - E2E Latency: P50, P95, P99

  吞吐:
  - Requests per second
  - Tokens per second (total)
  - Tokens per second per GPU

  资源:
  - GPU 利用率
  - GPU 显存使用
  - KV Cache 使用率
  - Queue length (等待队列长度)

  质量:
  - KV Cache hit rate
  - Preemption rate
  - Error rate
```

---

> **关键原则**：
> 1. **vLLM 是默认选择**：易用、性能好、生态活跃
> 2. **TensorRT-LLM 是性能极致**：适合对延迟/吞吐有极致要求的场景
> 3. **量化是必选项**：4-bit 权重 + FP8 KV Cache 是最佳性价比
> 4. **Prefix Caching 是免费午餐**：对 system prompt 固定的场景效果显著
> 5. **先跑通再优化**：默认配置能跑通 > 手动调优到极致
