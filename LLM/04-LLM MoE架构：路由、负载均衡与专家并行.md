# LLM MoE 架构：路由、负载均衡与专家并行

> MoE (Mixture of Experts, 混合专家模型) 通过稀疏激活实现参数规模与计算量的解耦，是 2024-2026 年主流 LLM 的核心架构范式。DeepSeek-V4-Pro (1.6T)、Kimi-K2 (1.04T)、GLM-5 (744B) 均基于 MoE。
> **工程师视角**：MoE 不是"把 FFN 复制几份加个 Router"那么简单。路由策略、负载均衡、通信模式、专家数量四者深度耦合——一个维度的设计失误会沿着耦合链放大，最终导致训练崩溃或推理吞吐骤降。

### 关键术语

| 缩写 | 全称 | 含义 |
|------|------|------|
| MoE | Mixture of Experts | 混合专家模型，每个 token 仅激活部分 FFN 参数 |
| EP | Expert Parallelism | 专家并行，将不同 expert 分布到不同 GPU |
| A2A | All-to-All | 全交换通信，MoE 中 token dispatch/combine 的核心通信模式 |
| ETP | Expert Tensor Parallel | 专家张量并行，将单个 expert 的权重切分到多 GPU |
| SMoE | Sparse Mixture of Experts | 稀疏 MoE，强调稀疏激活特性 |
| CF | Capacity Factor | 容量因子，每个 expert 能处理的最大 token 数的控制参数 |

### 前置知识

| 需要了解 | 参考文档 |
|----------|----------|
| Transformer FFN 结构与 SwiGLU | [02-Transformer 完整结构](./02-Transformer完整结构与训练算法.md) |
| 分布式训练基本并行策略 (DP/TP/PP) | [05-LLM 分布式训练](./05-LLM分布式训练：并行策略与ZeRO.md) |
| All-to-All 通信与 NVLink/IB 带宽 | [deep-dive: GPU 集群互联](./deep-dive/GPU集群互联：NVLink到InfiniBand.md) |

---

## 一、MoE 技术演进：从学术概念到工业支柱

MoE 经历了从 1991 年的概念提出到 2026 年成为超大规模模型标配的演进。理解这一脉络有助于把握当前设计选择的来龙去脉。

### 1.1 里程碑时间线

| 年份 | 工作 | 核心贡献 | 演进意义 |
|------|------|---------|---------|
| 1991 | Jacobs & Hinton *Adaptive Mixtures of Local Experts* | 提出"多个子网络 + 门控协调"基本框架 | 概念奠基，但停留于小规模实验 |
| 2017 | Shazeer et al. *Sparsely-Gated MoE* | 稀疏 Top-K 门控 + 可学习噪声，扩展到 137B LSTM | **首次证明 MoE 可在大规模 NN 中工作**，引入辅助损失做负载均衡 |
| 2020 | Google *GShard* | 将 MoE 集成到 Transformer，提出 Expert Parallelism 自动分片 | **MoE + Transformer + 分布式训练**三位一体，开启 LLM 时代 |
| 2021 | Google *Switch Transformer* | Top-1 路由极简化，训练 1.6T 参数模型 | 证明极稀疏路由可行，但暴露训练稳定性问题 |
| 2022 | Google *ST-MoE* | Z-loss 稳定训练，Router 权重衰减，容量因子调优 | 将 MoE 从"能训练"提升到"稳定训练" |
| 2022 | *Expert Choice* (Zhou et al.) | 反转路由方向：专家主动挑选 token | 提供负载均衡的新思路，直接影响后续 EP-Group 设计 |
| 2024.1 | *Mixtral 8x7B* (Mistral AI) | 开源 SMoE，47B 总参 / 13B 激活，性能超越 Llama-2 70B | **首个工业级开源 MoE**，证明 MoE 的工程可行性 |
| 2024.1 | *DeepSeekMoE* | 细粒度专家分割 + 共享专家隔离 | 提出"专家专精化"理论，v2 架构影响后续几乎所有 MoE 设计 |
| 2024.12 | *DeepSeek-V3* | 671B 总参 / 37B 激活，Aux-Loss-Free 负载均衡，FP8 训练 | **无辅助损失均衡**成为新范式，FP8 证明大规模 MoE 训练可行 |
| 2025.4 | *DeepSeek-V3.2* | 引入 DSA (DeepSeek Sparse Attention) | 将注意力稀疏化与 MoE 稀疏化形成统一设计语言 |
| 2026.4 | *DeepSeek-V4* | 1.6T 总参 / 49B 激活，Hash Routing + MegaMoE + mHC | **迄今最大开源 MoE**，极稀疏 + 混合注意力深度协同 |

### 1.2 演进趋势总结

回顾 30 年的演进，可以提炼五条核心趋势，它们是理解当前各种设计选择的关键线索：

**趋势一：专家粒度从粗到细。** GShard/Switch Transformer 时代每层 8-16 个大专家 → DeepSeek-V3/V4 时代每层 256-384 个小专家。细粒度带来的核心收益不是参数数量，而是**组合灵活性**——更多专家意味着更多可能的激活组合，每个 token 可以找到更匹配的专家子集。代价是路由难度上升（256 选 8 vs 8 选 2）和通信开销增加。

**趋势二：路由激活比持续下降。** Mixtral 时期激活比例约 28%（2/8），V3 时期降到约 5.5%（8/256），V4-Pro 降到 3.1%（6/384）。每降一个百分点，意味着在相同推理 FLOPs 下可以容纳 1.3-1.5× 的总参数。但更低的激活比例也意味着每个 expert 在单个 micro-batch 中看到的 token 更少——梯度噪声更大，训练稳定性更难保证。

**趋势三：负载均衡从"损失惩罚"走向"无损失偏置"。** 第一代用辅助损失（auxiliary loss）强制均衡，但辅助损失与主任务 loss 之间存在根本冲突——过大则损害模型质量，过小则负载失衡。DeepSeek-V3 提出的偏置更新机制（bias-based adjustment）解耦了均衡目标与梯度优化：偏置项仅影响路由决策（谁被选中），不影响 gating 权重和 loss 计算。这成为 2025 年后的事实标准。

**趋势四：路由从纯可学习走向确定性+可学习混合。** 传统 MoE 的路由决策完全由可学习的 Router 网络决定。DeepSeek-V4 在前 3 层引入 Hash Routing——基于 token ID 的哈希值确定目标 expert，完全不需要 Router 参数。这种混合策略的直觉是：浅层语义尚未形成，可学习的路由容易退化为频率偏好（高频 token 独占某些 expert），而确定性哈希天然保证浅层负载均衡。

**趋势五：MoE 与稀疏注意力形成协同设计。** 从 DeepSeek-V3.2 开始，MoE 的 sparse FFN 与 sparse attention（DSA/CSA/HCA）不再是独立的设计维度，而是联合优化——两者共享"稀疏选择"的设计范式，并且在通信和计算资源上做统一调度。GLM-5 的 DSA Indexer 与 MoE Router 的联合设计是另一个例子。

---

## 二、为什么需要 MoE：参数-计算解耦

### 2.1 Dense 模型的 Scaling 瓶颈

Dense Transformer 每次前向传播激活全部参数。给定一个 $N$ 参数的模型，单 token 推理 FLOPs ≈ $2N$。当参数从 7B 增长到 405B（如 Llama-3.1-405B），推理成本线性增长约 58 倍。

这意味着**"训得起"不等于"用得起"**。即使你能用更多 GPU 训练更大的 Dense 模型，推理阶段的成本和延迟可能让它无法实际部署。

### 2.2 MoE 的解耦原理

MoE 用多个并行的 Expert FFN 替代单个 Dense FFN，通过 Router 选择性激活：

$$y = \sum_{i=1}^{E} g_i \cdot \text{Expert}_i(x)$$

其中 Router 输出 gating 分数 $g_i$，但只有 Top-$k$ 个 expert 的 $g_i \neq 0$。

**关键数值**（2025-2026 年主流 MoE 模型）：

| 模型 | 总参数 | 每 Token 激活 | 激活率 | 专家数 | 激活数 | 来源 |
|------|--------|-------------|--------|--------|--------|------|
| DeepSeek-V3 / V3.2 | 671B | 37B | 5.5% | 256+1 | 8 | DeepSeek-V3 |
| DeepSeek-V4-Flash | 284B | 13B | 4.6% | 256+1 | 6 | DeepSeek-V4 |
| DeepSeek-V4-Pro | 1.6T | 49B | 3.1% | 384+1 | 6 | DeepSeek-V4 |
| Kimi-K2 | 1.04T | 32B | 3.1% | 384 | 8 | Kimi-K2 |
| GLM-5 | 744B | 40B | 5.4% | 256+1 | 8 | GLM-5 |
| Qwen3-235B | 235B | 22B | 9.4% | 128 | 8 | Qwen3 |
| Step3.5-Flash | 196B | 11B | 5.6% | 288+1 | 8 | Step3.5-Flash |
| ERNIE 5.0 | ~1T | <3% 激活 | <3% | — | — | ERNIE 5.0 |

注意：DeepSeek-V3/V3.2 是 671B/37B，而 **V4 有两个版本**——Flash (284B/13B) 和 Pro (1.6T/49B)。V4 相比 V3 不仅缩放了参数规模，还降低了激活专家数（8→6），将激活率从 5.5% 压到 3.1%-4.6%。

### 2.3 MoE 的优劣分析

**优势：**

1. **训练效率**：相同训练计算量下，MoE 模型可以容纳数倍于 Dense 模型的参数。Kimi-K2 的稀疏度缩放律实验表明，固定激活参数，增加专家数和稀疏度持续降低 loss；稀疏度从 8 提升到 48，实现同等 loss 所需 FLOPs 降低到原来的 59%。
2. **推理性价比**：推理 FLOPs 仅由激活参数决定，总参数决定知识容量。用 37B 激活参数的计算量获得 671B 模型的知识容量。
3. **专家专精化**：不同 expert 在训练中自然分化出领域专长（代码、数学、多语言等），使得模型在特定任务上的表现超过同等激活参数的 Dense 模型。
4. **可扩展性**：增加专家数量不需要对应的计算量增长（前提是通信开销可控）。

**劣势与挑战：**

1. **显存需求与 Dense 同级**：虽然每 token 只激活部分 expert，但全部 expert 的权重必须驻留在显存中。1.6T 参数的 MoE 在 FP8 下仍需约 1.6TB 显存来存放权重（不考虑量化）。
2. **小 Batch 效率低**：当 batch size 很小时，大部分 expert 闲置，GPU 利用率急剧下降。这是 MoE 推理的主要痛点——单用户交互时，384 个 expert 中只有 6 个在工作。
3. **通信开销巨大**：每个 token 需要跨 GPU dispatch 和 combine，All-to-All 通信带宽经常成为瓶颈。EP 度越大，通信量越大。
4. **训练稳定性难控**：路由崩塌、expert collapse、activation blow-up 等问题需要专门的监控和干预机制。
5. **微调易过拟合**：MoE 在预训练时效率高，但微调阶段参数量大而数据量相对小，容易过拟合。

---

## 三、MoE 核心机制

### 3.1 MoE 层结构

MoE 将标准 Transformer 的 FFN 层替换为 MoE 层：

```
标准 Block:  Attention → Add&Norm → FFN → Add&Norm
MoE Block:   Attention → Add&Norm → Router → [Expert₁ ... Expertₙ] → Add&Norm
```

每个 MoE 层包含：
- $E$ 个 Expert FFN（结构相同、参数独立）
- 1 个 Router 网络（通常是一个线性层 $W_r \in \mathbb{R}^{d \times E}$）
- （可选）1 个 Shared Expert（始终激活，不经过 Router）

### 3.2 路由数学：从 Naive 到现代

**Shazeer 2017 的 Noisy Top-K Gating**（奠基工作）：

$$\text{logits} = x \cdot W_g + \epsilon \cdot \text{Softplus}(x \cdot W_{\text{noise}})$$

$$\text{weights} = \text{Softmax}(\text{KeepTopK}(\text{logits}, k))$$

$$y = \sum_{i \in \text{TopK}} \text{weights}_i \cdot \text{Expert}_i(x)$$

其中 $\epsilon \sim \mathcal{N}(0,1)$ 是注入的探索噪声——训练时防止 Router 过早收敛到固定子集，推理时关闭（$\epsilon = 0$）。

**Gating 函数的演化**：Router 输出原始 logits 后，如何映射到 gating 权重？

| 方案 | 计算流程 | 动机 | 代表模型 |
|------|---------|------|---------|
| Softmax Gating | logits → Softmax(全专家) → Top-k | 简单直观 | GShard, Switch, Mixtral |
| Sigmoid + 选择性 Softmax | logits → Sigmoid → Top-k → Softmax(仅 k 个) | 避免全专家 Softmax 的计算开销（E 较大时） | DeepSeek-V2/V3 |
| Sqrt(Softplus) + 选择性 Softmax | logits → Sqrt(Softplus) → Top-k → Softmax(仅 k 个) | 正半轴接近线性避免 Sigmoid 饱和 + Sqrt 压缩动态范围 | DeepSeek-V4 |

**为什么 V4 要从 Sigmoid 换到 Sqrt(Softplus)？** DeepSeek-V4 报告指出，随着专家数从 V3 的 256 扩大到 384，Sigmoid 在极端 logits 处的饱和问题变得更严重——饱和区域的梯度几乎为零，Router 难以学习。Softplus 在正半轴接近线性（无饱和），再通过 Sqrt 压缩动态范围，使路由分数分布更均匀。

### 3.3 Token Choice vs Expert Choice

传统的 Token Choice（token 选 expert）是主流范式。但 Expert Choice（Zhou et al., 2022）提出了一个对称的设计：让每个 expert 主动挑选它想处理的 top-$k$ 个 token。

$$
\text{Expert}_i \text{ 选择: } \{x_j \mid \text{score}(x_j, i) \in \text{TopK}(\text{scores}[:, i], C)\}
$$

其中 $C$ 为 expert capacity（每个 expert 的容量上限）。

**两种范式的对比：**

| 维度 | Token Choice | Expert Choice |
|------|-------------|---------------|
| 负载均衡 | 需额外机制保证 | 天然保证（每个 expert 选固定数量 token） |
| 计算效率 | 部分 token 可能被丢弃（drop）或部分 expert 过载 | 每个 expert 处理固定数量 token，无丢弃 |
| 实现复杂度 | 简单 | 需全局排序，分布式下通信复杂 |
| 当前采用 | 主流（几乎所有模型） | 思路影响后续 EP-Group 均衡设计 |

当前实践中，纯 Expert Choice 并未被主流模型直接采用，但其"专家主动控制负载"的思想影响了 Step3.5-Flash 的 EP-Group 均衡损失和 DeepSeek-V4 的 Hash Routing 设计。

---

## 四、专家架构设计空间

### 4.1 粗粒度 vs 细粒度专家

这是 MoE 设计中最根本的选择之一，影响模型容量、推理效率和训练难度。

| 维度 | 粗粒度（GShard/Mixtral） | 细粒度（DeepSeekMoE/Step3.5） |
|------|------------------------|------------------------------|
| 每层专家数 | 8-16 | 128-384 |
| 每 expert 参数量 | 大（≈ Dense FFN） | 小（隐层 1280-3072） |
| 激活数 (k) | 2 | 6-8 |
| 激活率 | 12.5%-25% | 2%-10% |
| 优势 | 实现简单，通信量小 | 组合灵活，专家专精化程度高 |
| 劣势 | 专家能力不够专精 | 路由难度高，通信开销大 |

**细粒度的核心洞察**（DeepSeekMoE, 2024）：常规 FFN 的隐层维度远大于 token 表示维度（如 $d_{\text{model}}=4096$，$d_{\text{ff}}=14336$），这种"膨胀→压缩"的结构本身就是冗余的。将一个大 expert 拆分为多个小 expert（如 $d_{\text{ff}}$ 从 14336 拆成 7 个 2048），既保持了相同的总参数量，又获得了更灵活的组合方式。

Step3.5-Flash 将这一逻辑推向极致：expert 隐层仅 1280 维（对比 Dense FFN 的 11264 维），288 个专家 + 1 个共享专家，使 196B 总参数仅激活 11B。

### 4.2 共享专家 (Shared Expert)：通用能力 vs 专精化的解耦

DeepSeekMoE 和 GLM-5 使用共享专家——该 expert 对所有 token 始终激活，不经过 Router。

**设计动机**：路由专家在训练中趋向专精化（代码、数学、多语言等），但某些**跨领域的通用知识**（基础语法、常识推理、安全规范）需要被所有 token 访问。共享专家承担这部分，让路由专家可以更极致地专精。

```
MoE 层输出 = SharedExpert(x) + Σ g_i · RoutedExpert_i(x)
```

**共享专家的取舍：**

|  | 有共享专家 (DeepSeek-V4, GLM-5) | 无共享专家 (Qwen3) |
|--|-------------------------------|-------------------|
| 通用知识 | 共享专家保证覆盖 | 靠 Router 自然分配到各专家 |
| 负载均衡 | 减少路由竞争，简化均衡问题 | 全部 expert 参与均衡，需更强策略 |
| 额外计算 | 1 个恒激活 expert 的 FLOPs | 无 |
| 模型结构 | 多一个特殊 expert | 纯路由结构，实现简单 |

DeepSeek-V4 在推理部署时将共享专家视为第 $k+1$ 个激活 expert（即 6 个路由 + 1 个共享 = 7 个激活），简化了路由逻辑。

### 4.3 主流 MoE 配置对照

| 模型 | 层数 | 专家数 | 共享 | 激活/token | Expert 隐层 | $d_{\text{model}}$ | 来源 |
|------|------|--------|------|-----------|------------|-------------------|------|
| DeepSeek-V3 | 61 (58 MoE) | 256 | 1 | 8 | 2048 | 7168 | V3 |
| DeepSeek-V4-Flash | 43 (全 MoE) | 256 | 1 | 6 | 2048 | 4096 | V4 |
| DeepSeek-V4-Pro | 61 (全 MoE) | 384 | 1 | 6 | 3072 | 7168 | V4 |
| Kimi-K2 | — | 384 | — | 8 | 2048 | — | K2 |
| GLM-5 | 80 (75 MoE) | 256 | 1 | 8 | 2048 | 6144 | GLM-5 |
| Qwen3-235B | 94 | 128 | 0 | 8 | — | — | Qwen3 |
| Step3.5-Flash | 45 (42 MoE) | 288 | 1 | 8 | 1280 | — | Step3.5 |
| Mixtral 8x7B | 32 | 8 | 0 | 2 | 14336 | 4096 | Mixtral |

V4 相比 V3 的关键变化：不再在前几层使用 Dense FFN，而是对所有层使用 MoE（前 3 层用 Hash Routing 代替 Dense）。这反映了 DeepSeek 的路线修正——ERNIE 5.0 的实验也显示首层不需要 Dense 设计。

### 4.4 Kimi-K2 的稀疏度缩放律

Kimi-K2 通过受控实验量化了稀疏度的边际收益：

| 稀疏度 | 专家总数 | 等同 loss 所需相对 FLOPs | FLOPs 节省 |
|--------|---------|------------------------|-----------|
| 8 | 64 | 1.69× | — |
| 16 | 128 | 1.39× | 18% vs 8 |
| 32 | 256 | 1.15× | 17% vs 16 |
| **48** | **384** | **1.00×** (基线) | 13% vs 32 |

Kimi-K2 选择稀疏度 48（384/8），这个选择平衡了稀疏度收益与通信开销/训练稳定性。从稀疏度 32 到 48 的边际收益（13%）已经小于从 8 到 16 的边际收益（18%），说明收益递减——继续提高稀疏度的收益可能被通信和稳定性代价所抵消。

---

## 五、路由机制的技术演进

### 5.1 从 Noisy Top-K 到现代路由

**Shazeer 2017 的 Noisy Top-K** 是现代 MoE 路由的起点，核心特征：
- 训练时注入可学习的高斯噪声 $\epsilon \cdot \text{Softplus}(x \cdot W_{\text{noise}})$，防止 router 过早收敛
- 推理时关闭噪声
- 用 Softmax 归一化全部 $E$ 个专家的分数，然后取 Top-K

这个方案在大规模（$E > 100$）时有两个问题：(1) 对全部 $E$ 个专家做 Softmax 计算量大；(2) 噪声幅度的调优依赖经验。

**DeepSeek-V2 的改进**：先对原始 logits 做 Sigmoid（而非全专家 Softmax），再对 Top-k 做 Softmax。这样避免了全专家 Softmax 的计算开销，且 Sigmoid 独立对待每个专家（一个专家的分数变化不影响其他）。

### 5.2 Hash Routing：零参数、零计算的路由

DeepSeek-V4 在前 3 个 MoE 层使用 Hash Routing：

$$\text{expert\_id} = \text{Hash}(\text{token\_id}) \bmod E$$

**完全不需要 Router 网络**：参数为零、计算为零、dispatch 可预计算（因为 token ID 在序列开始时就已知）。

**为什么放前 3 层？** 浅层的 token 表示尚未形成清晰的语义，可学习的路由容易退化为频率偏好——高频 token（如 "the"、"is"）因为出现次数多而占据某些 expert 的全部容量。确定性哈希天然避免这种退化，保证浅层负载的自然均衡。深层语义形成后切换回可学习路由，让专家真正按语义专精化。

### 5.3 Anticipatory Routing：用历史参数预路由

DeepSeek-V4 提出 Anticipatory Routing：在训练步 $t$，不等当前 batch 的 Router 计算完成，而是用历史参数 $\theta_{t-\Delta t}$ 预计算路由索引。

**动机**：在 DualPipe 流水线调度下，Router 计算和 All-to-All dispatch 之间存在依赖链。如果等 Router 算完再 dispatch，A2A 通信期间 GPU 空闲。预计算路由索引让 dispatch 可以提前启动，与计算重叠——这是大规模 EP 场景下隐藏通信延迟的关键技术。

---

## 六、负载均衡技术演进

负载均衡是 MoE 训练的"第一难题"。理想的 MoE 需要每个 expert 被均匀使用——如果某些 expert 过载（token 被丢弃）或闲置（梯度接近零），不仅浪费容量，还可能导致训练崩溃。

### 6.1 问题根源：正反馈死亡螺旋

负载不均的根本原因是正反馈：

```
某 expert 初始分数略高
  → 获得更多 token → 梯度更新更多 → 能力更强
  → 吸引更多 token → 梯度更新更多 → 更强
  → ...其他 expert 被"饿死" → 模型退化为少数几个 expert
```

这个过程在训练早期尤为危险——Router 初始化时的微小不对称会被指数放大。

### 6.2 第一代：Auxiliary Loss（辅助损失）

在训练 loss 中添加辅助项惩罚不均衡：

$$L_{\text{aux}} = \alpha \cdot E \cdot \sum_{i=1}^{E} f_i \cdot P_i$$

其中 $f_i$ 是 expert $i$ 实际接收的 token 比例，$P_i$ 是 Router 分配给 expert $i$ 的平均概率。系数 $\alpha$ 通常设为 $10^{-2} \sim 10^{-3}$。

**根本矛盾**：$\alpha$ 大了损害模型质量（强行均衡违背专家专精化的本质需求），$\alpha$ 小了均衡效果不足。DeepSeek-V3 报告的消融实验显示，纯 auxiliary loss 方案在所有指标上均劣于 aux-loss-free 方案。

### 6.3 第二代：Auxiliary-Loss-Free（偏置更新机制）

DeepSeek-V3 的关键创新——**不用辅助损失，而是为每个 expert 引入可动态调整的偏置项** $b_i$：

$$g'_{i,t} = \begin{cases} s_{i,t}, & s_{i,t} + b_i \in \text{TopK}(\{s_{j,t} + b_j\}, K_r) \\ 0, & \text{otherwise} \end{cases}$$

**核心设计决策**：
- $b_i$ 仅影响路由决策（谁被选中），不影响 gating 权重（仍用原始 $s_{i,t}$）——**均衡与梯度优化解耦**
- 每步结束后根据负载更新：过载 → $b_i = b_i - \gamma$；闲置 → $b_i = b_i + \gamma$（$\gamma$ ≈ 0.001）
- 补充一个极小的序列级平衡损失（$\alpha=0.0001$），防止单序列内极端不均衡

**为什么这个设计更好？** 因为偏置项只改变"谁被选中"而不改变"按什么权重计算输出"。这允许不同领域的 token 自然分化到不同 expert 子集（实现"批次级均衡"而非"序列级均衡"），既保持全局均衡又允许专家专精化。

### 6.4 第三代扩展：各模型的针对性改进

Aux-loss-free 解决了全局均衡问题，但在大规模 EP 场景下仍暴露了新问题：

| 方法 | 针对的问题 | 机制 | 模型 |
|------|-----------|------|------|
| Global Router | EP 组之间存在信息不对称（各组不知道其他组的负载） | token dispatch 前对 EP 组间做 allgather 共享负载信息 | MiniMax-01 |
| EP-Group 均衡损失 | micro-batch 级别不同 EP rank 的 token 数不同 → straggler | 在 EP 分组级别引入额外均衡损失 ($\alpha=0.001$)，仅在预训练阶段使用 | Step3.5-Flash |
| Global Batch Balancing | aux-loss-free 偏置更新依赖 batch 内统计，小 batch 时不稳定 | 在全 batch 层面做负载均衡，鼓励专家特化 | Qwen3 |
| 随机路由预热 | 训练初期 Router 未学习 → token 聚集到初始化最优的几个 expert → OOM | 训练初期从均匀分布到学习分布的平滑过渡：$\alpha = \min(i/W, 1.0)$ 插值 | Ling-MoE |

**Step3.5-Flash 的 EP-Group 均衡**值得展开：Aux-loss-free 保证的是"一段时间内的全局均衡"，但在单次 micro-batch 中，不同 EP rank 分配到的 token 数可以相差数倍。当某个 rank 的 expert 收到远超平均的 token 时，它成为 straggler——其他 rank 等待它完成计算。EP-Group 均衡在单次 micro-batch 内约束这种不均衡，将 straggler 问题从"偶尔严重"降级为"基本消除"。

---

## 七、训练侧工程挑战

### 7.1 All-to-All 通信：MoE 训练的带宽瓶颈

MoE 训练的核心通信模式是 All-to-All：
1. **Dispatch**：每个 GPU 将其 token 按 Router 决策发送到托管对应 expert 的 GPU
2. **Combine**：各 GPU 计算完成后，将 expert 输出发回原 GPU

**通信量量化**（以 V4-Pro，EP=64，$d=7168$，BF16 为例）：

- 单 token 的激活向量：$7168 \times 2 = 14\text{KB}$
- Dispatch 发送量：$14\text{KB} \times \text{batch\_size} \times \text{seq\_len}$
- Combine 接收量：同上

在典型训练配置（micro-batch=1，seq_len=4096，4 DP）下，每个 GPU 每步的 A2A 通信量约 $14\text{KB} \times 4096 \times \frac{256}{64} \approx 2.3\text{MB}$。看似不大，但频率极高（每个 MoE 层都要做），且 A2A 的 cross-rank 特性意味着 NVLink 域内带宽和跨节点 IB 带宽都需要考虑。

### 7.2 MegaMoE：通信-计算融合 Kernel

DeepSeek-V4 提出的 MegaMoE 将 MoE 层的五个阶段融合为单个 kernel：

```
朴素方案（5 个独立 kernel，串行）:
  Dispatch → Linear-1 → Activation → Linear-2 → Combine

MegaMoE（融合 + wave 流水线）:
  Wave1: Dispatch → [Linear-1 → Act → Linear-2]    │
  Wave2:           Dispatch → [Linear-1 → Act → Linear-2] → Combine
                  ↑ 通信                   ↑ 计算         ↑ 通信
```

核心思想：将 experts 切分为多个 wave，当前 wave 的计算与下一 wave 的通信重叠执行。理论加速 1.92×（对比朴素），实际加速：
- 通用推理：1.50-1.73×
- RL rollout：可达 1.96×（batch 更大，重叠效果更好）
- 兼容 NVIDIA GPU 和华为昇腾 NPU

### 7.3 Expert Collapse 的隐蔽性与诊断

Step3.5-Flash 报告详细披露了大规模 MoE 训练中的隐蔽稳定性问题：

**Expert Collapse（专家坍缩）**：
- **现象**：即使路由分发看起来健康（每个 expert 都有 token），专家侧的激活 RMS 和参数 Frobenius 范数持续衰减
- **为什么隐蔽**：训练 loss 完全正常！只能通过监控暴露
- **必需的监控指标**：专家激活 RMS/均值、参数 Frobenius 范数、min-to-median 比率
- **根因**：激活→归一化→路由的正反馈环：某个 expert 激活幅值偏小 → Router 给它的分数偏低 → 分配 token 减少 → 梯度更小 → 越来越大越弱

**Localized Activation Blow-up（局部激活爆发）**：
- **现象**：深层 MoE 层中极少数（1-2 个）专家的激活范数爆炸式增长（max/median 比急剧扩大）
- **根因链**：高频 Bi-gram 触发特定专家短路 → Pre-Norm 下 SwiGLU 的 Gate/Up 对齐产生极端稀疏激活 → Muon 优化器放大低秩持续更新
- **对策**：**激活裁剪优于权重裁剪**——在 MoE FFN 中间激活上做逐元素裁剪可彻底抑制爆发；权重裁剪仅能延缓
- **必选监控**：max-to-median 比率（DeepSeek-V4 将其设为默认告警项）

---

## 八、推理侧工程挑战

### 8.1 显存悖论：参数在但不用

MoE 推理的核心矛盾：

| 资源 | MoE (671B/37B) | Dense (70B) | 对比 |
|------|---------------|-------------|------|
| 权重显存 (FP8) | ~671GB | ~70GB | 9.6× |
| 单 token FLOPs | ~74G | ~140G | 0.53× |
| 单 token 推理延迟 | 取决于 batch | 取决于 batch | batch=1 时 MoE 更慢 |

**本质**：MoE 把 Dense 模型的两难（"要知识容量还是要推理速度"）转化为了另一种两难（"要知识容量还是要显存"。但显存可以通过量化降低（V4 的 MoE 权重用 FP4），而 Dense 的 FLOPs 是刚性的。

### 8.2 小 Batch 的 Expert 利用率危机

当 batch size 小于 EP 度时，MoE 推理效率急剧下降：

以 V4-Pro（384 expert，EP=32，每 GPU 12 个 expert，激活 6 个/token）为例：
- Batch=1，seq_len=1（decode 的典型场景）：仅激活 6 个 expert，32 个 GPU 中约 26 个可能空闲
- Batch=32：平均每 GPU 激活约 6 个 expert → 利用率大幅提升

这就是为什么 MoE 模型的单用户交互延迟往往高于同等 FLOPs 的 Dense 模型——GPU 的计算资源被闲置的 expert 参数占据。

### 8.3 冗余专家部署 + DP-aware Routing

**DeepSeek-V4 的方案：冗余部署 + 动态调整**
- 检测高负载 expert，每 10 分钟复制其权重到更多 GPU
- Prefill 最小部署：4 节点 32 GPU（Attn TP4+SP+DP8, MoE EP32）
- Decode 最小部署：40 节点 320 GPU（每 GPU 仅 1 个 expert，IBGDA 降低延迟）

**GLM-5 的方案：DP-aware Routing**
- 同一对话的所有请求通过一致性哈希路由到同一 DP rank
- 好处：最大化 KV-cache 复用（同一对话的请求共享 KV cache）
- 结合轻量级动态负载均衡，prefill 成本与增量 token 成比例而非总上下文长度

---

## 九、模态无关路由（ERNIE 5.0）

ERNIE 5.0 是支持文本+图像+视频+音频的原生统一自回归模型，其 MoE 路由的核心特征是**模态无关**：路由决策基于统一 token 表示，不依赖显式模态标识符，所有模态共享一个专家池。

**关键实证发现**（ERNIE 5.0 报告 Sec 6.4.1）：

1. **专家利用率高度非均匀**：部分专家跨模态高频激活（通用型），其余呈现强烈模态特化（专用型）——路由天然发现了模态边界，不需要人工划分
2. **任务需求比模态边界更决定专家特化**："理解"和"生成"之间的专家重叠度低——即使是同一模态（如文本），理解和生成也使用不同的专家子集
3. **首层 MoE 不存在严重负载不均衡**：反驳了 MoE 首层需要用 Dense FFN "保底"的常见假设
4. **深度方向呈现"特化→重新整合→特化"交替模式**：视觉生成和音频任务在深度方向不是一直特化，而是周期性地通过共享专家重新整合

相比 ERNIE 4.5 的"模态隔离路由"（每个模态独占部分 expert），模态无关路由的优势：
- 促进跨模态知识泛化（如"理解图像中的数学公式"在数学 expert 和视觉 expert 之间自然共享）
- 避免启发式模态专家划分的工程复杂度

---

## 参考资料

- [DeepSeek-V3 Technical Report](https://arxiv.org/abs/2412.19437) — DeepSeekMoE 架构、Aux-Loss-Free 负载均衡、FP8 训练
- [DeepSeek-V4 Technical Report](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro/blob/main/DeepSeek_V4.pdf) — CSA+HCA 混合注意力、Hash Routing、MegaMoE 通信优化
- [Qwen3 Technical Report](https://arxiv.org/abs/2505.09388) — 128 Expert 无共享设计、Global Batch 负载均衡
- [Kimi-K2 Technical Report](https://arxiv.org/abs/2507.06262) — 384 Expert / Sparsity=48 / Sparsity Scaling Law
- [GLM-5 Technical Report](https://arxiv.org/abs/2512.16046) — 256 Expert + Shared、DP-aware Routing、DSA Indexer
- [Step3.5-Flash Technical Report](https://arxiv.org/abs/2604.08874) — 288 Expert 细粒度、Expert Collapse 诊断、EP-Group 均衡
- [ERNIE 5.0 Technical Report](https://arxiv.org/abs/2604.08249) — 模态无关路由、超稀疏 MoE、弹性训练
- [Sparsely-Gated MoE (Shazeer et al., 2017)](https://arxiv.org/abs/1701.06538) — 稀疏门控 MoE 的奠基工作
- [Switch Transformer (Fedus et al., 2021)](https://arxiv.org/abs/2101.03961) — Top-1 路由极简化
- [DeepSeekMoE (Dai et al., 2024)](https://arxiv.org/abs/2401.06066) — 细粒度专家 + 共享专家
- [Mixtral of Experts (Mistral AI, 2024)](https://mistral.ai/news/mixtral-of-experts/) — 首个工业级开源 SMoE

> **下一篇**：[LLM 分布式训练：并行策略与 ZeRO](./05-LLM分布式训练：并行策略与ZeRO.md) — MoE 的并行策略基础