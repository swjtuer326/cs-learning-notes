# LLM 推理服务架构：调度、缓存与投机解码

> **核心命题**：单次推理的性能优化有上限，推理服务架构的优化才是提升整体吞吐和用户体验的关键。Continuous Batching、Disaggregated Serving、Prefix Caching 和 Speculative Decoding 是 2024-2025 年推理服务架构的四大核心技术。

### 关键术语

| 缩写 | 全称 | 含义 |
|------|------|------|
| CB | Continuous Batching | 连续批处理，请求可在任意时刻加入/离开 batch |
| PD | Prefill-Decode Disaggregation | Prefill/Decode 分离式部署，两类计算使用不同 GPU |
| TTFT | Time To First Token | 首 token 延迟，用户提交 prompt 到收到第一个 token 的时间 |
| TPOT | Time Per Output Token | 每输出 token 的平均生成间隔 |
| SLO | Service Level Objective | 服务水平目标，如 P99 延迟 < 2s |
| KV Cache | Key-Value Cache | 注意力机制的键值缓存，避免 Decode 阶段重复计算 |

### 前置知识

| 需要了解 | 参考文档 |
|----------|----------|
| 推理资源定量分析 | [11-推理资源分析](./11-LLM推理资源分析.md) |
| 推理引擎实现 | [12-推理引擎](./12-LLM推理引擎：vLLM到TensorRT-LLM.md) |
| 注意力机制变体 | [03-注意力机制发展](./03-LLM注意力机制发展与演进.md) |

## 目录

1. [推理服务架构全景](#推理服务架构全景)
2. [Continuous Batching](#continuous-batching)
3. [Disaggregated Serving](#disaggregated-serving)
4. [Prefix Caching](#prefix-caching)
5. [Speculative Decoding](#speculative-decoding)
6. [请求调度策略](#请求调度策略)
7. [SLA/SLO 管理](#slaslo-管理)
8. [全局架构设计](#全局架构设计)

---

## 推理服务架构全景

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      推理服务架构全景                                     │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  客户端请求                                                              │
│     │                                                                   │
│     ▼                                                                   │
│  ┌──────────────┐                                                       │
│  │  API Gateway  │  认证、限流、路由                                      │
│  └──────┬───────┘                                                       │
│         │                                                               │
│         ▼                                                               │
│  ┌──────────────┐                                                       │
│  │  Request      │  请求排队、优先级管理                                  │
│  │  Queue        │                                                       │
│  └──────┬───────┘                                                       │
│         │                                                               │
│    ┌────┴────┐                                                          │
│    ▼         ▼                                                          │
│  ┌──────────────┐   ┌──────────────┐                                   │
│  │  Prefill     │   │  Decode      │  Disaggregated (可选)              │
│  │  Pool        │   │  Pool        │                                   │
│  └──────┬───────┘   └──────┬───────┘                                   │
│         │                  │                                            │
│         └────────┬─────────┘                                            │
│                  ▼                                                      │
│  ┌──────────────────────────────────────┐                              │
│  │  KV Cache Pool (PagedAttention)      │                              │
│  │  ┌────┐ ┌────┐ ┌────┐ ┌────┐        │                              │
│  │  │Blk │ │Blk │ │Blk │ │Blk │ ...    │                              │
│  │  └────┘ └────┘ └────┘ └────┘        │                              │
│  └──────────────────────────────────────┘                              │
│                                                                         │
│  核心技术:                                                              │
│  1. Continuous Batching: 动态组 batch                                   │
│  2. Disaggregated Serving: Prefill/Decode 分离                          │
│  3. Prefix Caching: 共享公共前缀的 KV Cache                              │
│  4. Speculative Decoding: 投机解码加速                                   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Continuous Batching

### 2.1 为什么需要 Continuous Batching

```
传统 Static Batching:

  ┌─────────────────────────────────────────────┐
  │ Batch 1: [Req1, Req2, Req3, Req4]           │
  │ ████████████████████████████████████████████ │
  │                                             │
  │ Batch 2: [Req1, Req2, Req3, Req4]           │
  │ ████████████████████████████████████████████ │
  │                                             │
  │ Req3 提前完成 → GPU 空闲等待!                │
  │ Req5 在排队 → 无法加入当前 batch!            │
  └─────────────────────────────────────────────┘

Continuous Batching:

  ┌─────────────────────────────────────────────┐
  │ Step 1: [Req1, Req2, Req3, Req4]            │
  │ Step 2: [Req1, Req2, Req4, Req5]  ← Req3 完成, Req5 加入 │
  │ Step 3: [Req1, Req2, Req5, Req6]  ← Req4 完成, Req6 加入 │
  │ Step 4: [Req2, Req5, Req6, Req7]  ← Req1 完成, Req7 加入 │
  └─────────────────────────────────────────────┘

→ GPU 利用率从 ~60% 提升到 ~95%
→ 吞吐提升 2-10× (取决于请求长度分布)
```

### 2.2 Continuous Batching 实现

```
Continuous Batching 的核心操作:

1. 请求加入 (Add Request):
   - 分配 KV Cache blocks
   - 执行 prefill (处理所有 input tokens)
   - 将请求加入 running batch

2. 请求完成 (Remove Request):
   - 释放 KV Cache blocks
   - 从 running batch 中移除
   - 返回生成的 tokens

3. 每步迭代:
   for each step:
     # 检查是否有完成的请求
     for req in running:
       if req.finished or req.hit_eos:
         remove(req)
     
     # 检查是否可以加入新请求
     while can_add():
       req = queue.pop()
       prefill(req)  # 或 chunked prefill
       add_to_batch(req)
     
     # 执行一步 decode
     decode(running_batch)
```

### 2.3 Chunked Prefill

```
Chunked Prefill: 将长 prefill 分成多个 chunk

问题: 一个长 prefill (如 32K tokens) 会阻塞所有 decode
  → TTFT 飙升

Chunked Prefill 解决:
  ┌─────────────────────────────────────────────┐
  │ Step 1: Prefill Req1 [0:2048] + Decode      │
  │ Step 2: Prefill Req1 [2048:4096] + Decode   │
  │ Step 3: Prefill Req1 [4096:6144] + Decode   │
  │ ...                                         │
  │ Step N: Prefill Req1 完成 → 加入 decode     │
  └─────────────────────────────────────────────┘

参数:
  --max-num-batched-tokens: 控制每个 step 的 prefill token 数
  → 平衡 prefill 延迟和 decode 延迟
```

---

## Disaggregated Serving

### 3.1 核心思想

```
Disaggregated Serving (分离式服务):

将 Prefill 和 Decode 分配到不同的 GPU:

  ┌─────────────────┐     ┌─────────────────┐
  │  Prefill Pool   │────▶│  Decode Pool    │
  │  (GPU 0-3)      │     │  (GPU 4-7)      │
  │                 │     │                 │
  │  高计算需求      │     │  高显存需求      │
  │  大 batch 友好   │     │  小 batch 友好   │
  └─────────────────┘     └─────────────────┘

为什么分离?

  Prefill 特征:
  - 计算密集 (大量 GEMM)
  - 显存需求低 (不需要存 KV Cache)
  - 延迟敏感 (TTFT)
  
  Decode 特征:
  - 显存密集 (KV Cache 大)
  - 计算需求低 (每次只算 1 个 token)
  - 吞吐敏感 (TPOT)

→ 分离后可以独立优化两种负载
→ Prefill 用大 TP, Decode 用小 TP
```

### 3.2 分离架构的数据流

```
Disaggregated Serving 流程:

1. 请求到达 Prefill Pool
2. Prefill 计算:
   - 处理所有 input tokens
   - 生成 KV Cache
3. KV Cache 传输:
   - 通过 NVLink/InfiniBand 将 KV Cache 发送到 Decode Pool
4. Decode 计算:
   - 从 KV Cache 开始自回归生成
5. 返回结果

KV Cache 传输优化:
  - 压缩传输 (量化 KV Cache)
  - 流水线传输 (边 prefill 边传输)
  - 局部 Decode (部分层在 Prefill 侧完成)
```

### 3.3 为什么分离：Prefill 与 Decode 的资源冲突

Prefill 和 Decode 在 GPU 资源需求上存在根本性冲突。以 DeepSeek-V4-Pro (1.6T, 激活 49B) 在 H100 (80GB, 67 TFLOPS FP8, 3.35 TB/s HBM Bandwidth) 上的分析为例：

| 维度 | Prefill | Decode | 冲突本质 |
|------|---------|--------|---------|
| **计算模式** | 矩阵-矩阵乘法 (GEMM)，高算术强度 | 矩阵-向量乘法 (GEMV)，低算术强度 | Prefill 可充分用 Tensor Core；Decode 主要瓶颈在 HBM 带宽 |
| **单 token FLOPs** | $2 \times 49\text{B} \approx 98\text{G}$ (每 token 处理一次) | $2 \times 49\text{B} \approx 98\text{G}$ × seq_len (每 decode step 需处理全 KV Cache) | Prefill FLOPs = O(seq_len)；Decode FLOPs = O(seq_len²) (attention 部分) |
| **算术强度** | ~100-300 FLOPs/Byte (高) | ~1-5 FLOPs/Byte (极低) | Prefill 是 compute-bound；Decode 是 **memory-bound** |
| **HBM 带宽利用率** | 30-50%（Tensor Core 有时间预热） | **80-95%**（全在从 HBM 读 KV Cache） | Decode 对 HBM 带宽的需求是 Prefill 的 2-3× |
| **Batch 友好度** | 大 batch 收益显著 (GEMM 维度增加) | 大 batch 收益有限 (KV Cache → HBM 带宽瓶颈) | Prefill batch=32→64 吞吐翻倍；Decode batch 增加几乎不提升吞吐 |
| **KV Cache 显存** | 仅存当前 batch × 1 层 | 需要存所有活跃请求 × 所有层的 KV Cache | 单 Decode GPU 仅 KV Cache 就可能占 10-30 GB |

**核心结论**：在一台 GPU 上同时做 Prefill 和 Decode 时，两者对"计算 vs 带宽"的需求截然相反——优化一个会损害另一个。PD 分离允许为两类负载使用不同的硬件配置和并行策略（Prefill 用大 TP 充分并行 GEMM，Decode 用小 TP 节省 KV Cache 空间）。

### 3.4 DeepSeek-V4 的 PD 分离部署实例

DeepSeek-V4 在生产环境中采用的分离方案（来自 V4 技术报告）：

| 角色 | 节点数 | GPU 数 | 配置 | 设计依据 |
|------|--------|--------|------|---------|
| **Prefill Pool** | 4 | 32 | Attn TP=4, SP+DP=8, MoE EP=32 | 需要大 TP 加速 Attention GEMM；EP 度与专家数匹配 |
| **Decode Pool** | 40 | 320 | 每 GPU 1 个 expert，IBGDA 降低延迟 | 极低 TP 节省 KV Cache 空间；384 专家分到 320 GPU（12 GPU 有 2 专家） |
| **总部署** | 44 | 352 | Prefill:Decode ≈ 1:10 | Decode 的 GPU 需求远大于 Prefill（KV Cache 显存是主因） |

**关键设计决策**：
- Decode pool 的 GPU 数是 Prefill 的 10 倍——因为 Decode 需要存储海量 KV Cache 且是 memory-bound，不可通过增加 batch 来补偿
- 每 GPU 只放 1-2 个 expert，最大化 KV Cache 可用显存（专家权重在 FP4 下仅 ~2 GB/expert）
- **冗余部署**：高负载 expert 被复制到多个 GPU（定期检测，每 10 分钟调整），DP-aware routing 确保请求路由到正确的副本

> **对比**：纯 PD 共享部署（不分池）在 V4 规模下几乎不可行——单 GPU 无法同时满足"大批量 GEMM (Prefill) + 海量 KV Cache (Decode)"的内存需求。

### 3.5 代表系统

| 系统 | 分离方式 | 特点 |
|------|---------|------|
| **Splitwise (Microsoft)** | Prefill/Decode 分离 | 吞吐提升 2× |
| **DistServe** | Prefill/Decode 分离 | 延迟优化 |
| **Tetriserve** | Prefill/Decode 分离 | 资源利用率优化 |
| **Mooncake (字节)** | 以 KV Cache 为中心的分离 | 大规模生产验证 |
| **Sarathi-Serve** | Stitch (部分分离) | 平衡延迟和吞吐 |

---

## Prefix Caching

### 4.1 原理

```
Prefix Caching: 共享公共前缀的 KV Cache

场景: System Prompt 固定
  Req1: [System: "You are helpful..."] + "What is AI?"
  Req2: [System: "You are helpful..."] + "What is ML?"
  Req3: [System: "You are helpful..."] + "What is DL?"

无 Prefix Caching:
  每个请求都要 prefill System Prompt (如 1000 tokens)
  → 3 × 1000 = 3000 tokens 的 prefill 计算

有 Prefix Caching:
  第一个请求 prefill System Prompt → 缓存 KV Cache
  后续请求直接复用 → 0 prefill 计算!
  → 节省 2000 tokens 的 prefill
```

### 4.2 实现方式

```
Hash-based (vLLM):
  1. 计算 prefix 的 hash
  2. 查找 hash table 中是否有匹配的 KV Cache blocks
  3. 如果有 → 直接复用
  4. 如果没有 → prefill 并缓存

Radix Tree (SGLang):
  1. 将所有缓存的 prefix 组织成 Radix Tree
  2. 新请求在树中查找最长匹配前缀
  3. 自动匹配任意长度的公共前缀
  4. 支持 LRU 淘汰

Copy-on-Write:
  多个请求共享 prefix blocks 时:
  - 读取: 共享同一份物理 blocks
  - 写入: 新 token 写入新的 blocks
  → 安全共享，无数据竞争
```

### 4.3 Prefix Caching 效果

```
Prefix Caching 收益分析:

场景: System Prompt 1000 tokens, 用户输入 100 tokens

无缓存:
  Prefill: 1100 tokens per request
  1000 QPS → 1.1M tokens/s prefill

有缓存 (命中率 90%):
  Prefill: 100 tokens per request (90% 命中)
  1000 QPS → 100K tokens/s prefill
  → 节省 10× prefill 计算!

适用场景:
  ✅ System prompt 固定
  ✅ Few-shot examples 固定
  ✅ RAG 的公共 context
  ✅ 多轮对话的历史消息
  ❌ 每次 prompt 完全不同
```

---

## Speculative Decoding

### 5.1 核心思想

```
Speculative Decoding (投机解码):

问题: 自回归解码每次只生成 1 个 token
  → 无法利用 GPU 的并行计算能力
  → 显存带宽成为瓶颈

解决: 用小模型 (Draft Model) 快速生成 K 个候选 token
      用大模型 (Target Model) 并行验证

流程:
  1. Draft Model 生成 K 个候选 token (快)
  2. Target Model 一次前向验证所有 K 个 token (并行)
  3. 接受匹配的 token，拒绝不匹配的
  4. 从第一个被拒绝的位置重新采样

加速比: 取决于 Draft Model 的准确率
  - 准确率 80% → ~2-3× 加速
  - 准确率 90% → ~3-4× 加速
```

### 5.2 Speculative Decoding 算法

**Speculative Decoding 详细流程：**

输入: prefix tokens $\mathbf{x}$，参数: K (候选 token 数)

**1. Draft（草稿生成）：**

for $k = 1$ to $K$:

$$q_k(\mathbf{x}) = \text{DraftModel}(\mathbf{x} \cup \{y_1, \dots, y_{k-1}\})$$

$$y_k \sim q_k(\mathbf{x})$$

→ 生成 K 个候选 token

**2. Verify（目标模型验证）：**

$$\{p(\mathbf{x}), p(\mathbf{x} \cup \{y_1\}), \dots, p(\mathbf{x} \cup \{y_1, \dots, y_K\})\} = \text{TargetModel}(\mathbf{x})$$

→ 一次前向得到 K+1 个分布

**3. Accept/Reject（接受/拒绝）：**

for $k = 1$ to $K$:

$$r \sim \text{Uniform}(0, 1)$$

若 $$r < \min\left(1, \frac{p(y_k)}{q(y_k)}\right) \tag{1}$$ ，则接受 $y_k$；否则拒绝 $y_k$，从 $$\text{norm}\left(\max(0, p - q)\right) \tag{2}$$ 采样，并退出循环。

→ 接受的 token 不需要重新计算，被拒绝的位置从修正分布采样

### 5.3 Draft Model 选择

| Draft 方式 | 原理 | 加速比 | 代表 |
|-----------|------|--------|------|
| **小模型** | 用更小的模型做 draft | 2-3× | SpecInfer |
| **Medusa** | 多个 LM Head 并行预测 | 2-3× | Medusa |
| **Eagle** | 基于特征的预测 | 3-4× | Eagle |
| **MTP (Multi-Token Prediction)** | 额外 Transformer 层预测后续 token | 1.5-2× | DeepSeek-V3/V4, GLM-5, Step3.5-Flash |
| **Self-Speculative** | 模型自身做 draft (跳过层) | 1.5-2× | LayerSkip |
| **Lookahead** | n-gram 匹配 | 1.5-2× | - |
| **Sequoia** | 树形 speculative decoding | 3-5× | Sequoia |

> **MTP vs Speculative Decoding**：MTP 与 speculative decoding 共享"一次前向预测多个 token"的核心思想，但 MTP 是训练时目标（通过独立的 MTP 层或 Head 预测后续 token），而 speculative decoding 专注于推理时加速（通过 accept/reject 保证分布等价）。两者在实践中可以结合——DeepSeek-V4 用 MTP 模块产生的额外 token 作为 speculative candidate，通过 accept/reject 验证后保留。

### 5.4 Medusa

```
Medusa: 在模型上添加多个 LM Head

架构:
  ┌─────────────────────────────────────┐
  │  Base Model (Llama-3-8B)            │
  │  ┌─────────────────────────────┐    │
  │  │  Transformer Layers         │    │
  │  └──────────────┬──────────────┘    │
  │                 │                   │
  │     ┌───────────┼───────────┐       │
  │     ▼           ▼           ▼       │
  │  ┌──────┐  ┌──────┐   ┌──────┐     │
  │  │Head 0│  │Head 1│   │Head 2│     │
  │  │(t+1) │  │(t+2) │   │(t+3) │     │
  │  └──────┘  └──────┘   └──────┘     │
  └─────────────────────────────────────┘

训练:
  1. 冻结 Base Model
  2. 只训练 Medusa Heads
  3. 每个 Head 预测不同位置的 token

推理:
  1. Base Model 前向一次
  2. 所有 Medusa Heads 并行预测
  3. Tree Attention 验证所有候选
```

### 5.5 Eagle

```
Eagle: 基于特征的投机解码

核心思想:
  不依赖小模型，而是用特征预测下一个 token

Eagle-1:
  Draft Model = 1 层 Transformer + LM Head
  输入: Target Model 的 hidden states
  → 利用 Target Model 已经计算好的特征

Eagle-2:
  改进:
  - 更好的特征利用
  - 多步预测
  - 自适应 K 值

性能:
  - 加速比 3-4× (vs 标准解码)
  - 不需要额外的小模型
  - 训练成本低 (只需训练 draft head)
```

---

## 请求调度策略

### 6.1 调度目标

```
调度器的多目标优化:

  1. 吞吐最大化: 尽可能多地处理请求
  2. 延迟最小化: TTFT 和 TPOT 尽可能低
  3. 公平性: 不同请求获得公平的 GPU 时间
  4. 优先级: 高优先级请求优先处理
```

### 6.2 调度策略

| 策略 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| **FCFS** | 先到先服务 | 公平，简单 | 长请求阻塞短请求 |
| **Priority** | 按优先级排序 | 保证 SLA | 低优先级可能饿死 |
| **Shortest First** | 短请求优先 | 平均延迟低 | 长请求可能饿死 |
| **Fair Share** | 按权重分配 | 多租户公平 | 实现复杂 |
| **SJF (Shortest Job First)** | 预估长度，短优先 | 吞吐高 | 预估不准 |

### 6.3 Preemption (抢占)

```
Preemption: 当显存不够时，将部分请求的 KV Cache 换出

策略:
  1. Swap: 将 KV Cache 从 GPU 显存换到 CPU 内存
  2. Recomputation: 丢弃 KV Cache，需要时重新 prefill

选择哪个请求 Preempt:
  - 最长序列 (释放最多显存)
  - 最低优先级
  - 最新到达 (最少已投入计算)

vLLM Preemption:
  - 默认: 不 preempt (等待显存释放)
  - 可选: swap to CPU
```

---

## SLA/SLO 管理

### 7.1 关键指标

```
LLM 推理 SLA 指标:

  TTFT (Time to First Token):
    - P50: < 200ms
    - P95: < 500ms
    - P99: < 1000ms

  TPOT (Time per Output Token):
    - P50: < 30ms
    - P95: < 80ms
    - P99: < 150ms

  吞吐:
    - 最低 QPS: 100
    - 目标 QPS: 500+

  可用性:
    - 99.9% uptime
```

### 7.2 过载保护

```
过载保护策略:

1. 请求队列限制:
   - 最大队列长度: 1000
   - 超过 → 返回 429 (Too Many Requests)

2. 自适应批处理:
   - 延迟升高 → 减小 batch size
   - 延迟降低 → 增大 batch size

3. 优先级降级:
   - 高负载时降低低优先级请求的服务质量

4. 熔断:
   - 错误率 > 阈值 → 拒绝新请求
   - 恢复后自动重新接受
```

---

## 全局架构设计

### 8.1 多模型服务架构

```
                    ┌──────────────┐
                    │   Nginx      │  (路由)
                    └──────┬───────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │ Router   │ │ Router   │ │ Router   │
        │ (model   │ │ (model   │ │ (model   │
        │  routing)│ │  routing)│ │  routing)│
        └────┬─────┘ └────┬─────┘ └────┬─────┘
             │            │            │
    ┌────────┼────────┐   │   ┌────────┼────────┐
    ▼        ▼        ▼   ▼   ▼        ▼        ▼
  ┌────┐  ┌────┐  ┌────┐   ┌────┐  ┌────┐  ┌────┐
  │vLLM│  │vLLM│  │vLLM│   │TRT │  │TRT │  │TRT │
  │7B  │  │7B  │  │7B  │   │70B │  │70B │  │70B │
  └────┘  └────┘  └────┘   └────┘  └────┘  └────┘
  
  模型路由:
  - 简单请求 → 7B 模型
  - 复杂请求 → 70B 模型
  - 代码请求 → Code Model
```

### 8.2 监控与可观测性

```
监控体系:

┌─────────────────────────────────────────────────────┐
│                    Grafana Dashboard                  │
├─────────────────────────────────────────────────────┤
│                                                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │
│  │ QPS /       │  │ Latency     │  │ GPU         │  │
│  │ Throughput  │  │ (P50/P95/  │  │ Utilization │  │
│  │             │  │  P99)       │  │ / Memory    │  │
│  └─────────────┘  └─────────────┘  └─────────────┘  │
│                                                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │
│  │ KV Cache    │  │ Queue       │  │ Error       │  │
│  │ Usage /     │  │ Length /    │  │ Rate /      │  │
│  │ Hit Rate    │  │ Wait Time   │  │ Timeout     │  │
│  └─────────────┘  └─────────────┘  └─────────────┘  │
│                                                     │
└─────────────────────────────────────────────────────┘

数据源:
  - vLLM metrics endpoint (Prometheus)
  - GPU metrics (DCGM / nvidia-smi)
  - Application logs (ELK / Loki)
```

---

> **关键原则**：
> 1. **Continuous Batching 是必选项**：任何生产级推理服务都应该使用
> 2. **Prefix Caching 是免费午餐**：对 system prompt 固定的场景效果显著
> 3. **Speculative Decoding 是加速利器**：3-4× 加速，但需要额外训练
> 4. **Disaggregated Serving 是未来方向**：分离 Prefill/Decode 独立优化
> 5. **监控是生产的基础**：没有监控就没有 SLA 保障

---

## 参考资料

- [vLLM](https://arxiv.org/abs/2309.06180) — PagedAttention 和 Continuous Batching
- [DistServe](https://arxiv.org/abs/2401.09670) — Disaggregated Serving
- [Speculative Decoding](https://arxiv.org/abs/2302.01318) — 投机解码加速
- [SGLang](https://arxiv.org/abs/2312.07104) — RadixAttention

> **下一篇**：[LLM 多模态架构](./14-LLM多模态架构.md) — 从推理服务走向多模态扩展
