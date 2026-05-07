# LLM Post-Training：SFT、RLHF 与 DPO

> **核心命题**：Pre-training 赋予模型知识和能力，Post-Training 赋予模型行为和对齐。一个只经过 Pre-training 的模型是"什么都懂但不会聊天"的原始智能体，Post-Training 让它变成有用的助手。

## 目录

1. [Post-Training 全景](#post-training-全景)
2. [SFT：监督微调](#sft监督微调)
3. [RLHF：基于人类反馈的强化学习](#rlhf基于人类反馈的强化学习)
4. [DPO：直接偏好优化](#dpo直接偏好优化)
5. [其他对齐方法](#其他对齐方法)
6. [推理增强：从 Chain-of-Thought 到 o1/R1](#推理增强从-chain-of-thought-到-o1r1)
7. [参数高效微调 (PEFT)](#参数高效微调-peft)
8. [实践指南与工具](#实践指南与工具)

---

## Post-Training 全景

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        Post-Training 技术全景                            │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  Pre-trained Base Model                                                 │
│         │                                                               │
│         ├──▶ SFT (Supervised Fine-Tuning)                               │
│         │    ├── 指令微调: 让模型学会遵循指令                             │
│         │    ├── 对话微调: 让模型学会多轮对话                             │
│         │    └── 领域微调: 注入特定领域知识                               │
│         │                                                               │
│         ├──▶ Alignment (对齐)                                           │
│         │    ├── RLHF: Reward Model + PPO                               │
│         │    ├── DPO: 直接在偏好数据上优化                               │
│         │    ├── ORPO: SFT + DPO 联合优化                               │
│         │    └── SimPO: 以序列概率为参考                                 │
│         │                                                               │
│         └──▶ Reasoning Enhancement (推理增强)                            │
│              ├── Chain-of-Thought (CoT)                                 │
│              ├── Rejection Sampling + Fine-Tuning                       │
│              ├── GRPO (Group Relative Policy Optimization)              │
│              └── RL on Verifiable Rewards (o1, DeepSeek-R1)             │
│                                                                         │
│  关键问题:                                                               │
│  - 如何获取高质量的对齐数据？                                            │
│  - 如何平衡有用性 (Helpfulness) 和安全性 (Safety)？                      │
│  - 如何避免对齐税 (Alignment Tax)？                                     │
│  - 如何让模型学会"思考"？                                                │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## SFT：监督微调

### 2.1 SFT 的本质

```
SFT = 在 (instruction, response) 对上继续做 next-token prediction

与 Pre-training 的区别:
  - 数据: 高质量指令-回复对 vs 海量原始文本
  - 目标: 行为塑造 vs 知识学习
  - 规模: 10K-1M 样本 vs 1T+ tokens
  - Loss: 只在 response 部分计算 (mask instruction)
```

### 2.2 SFT 数据格式

```
标准 Chat 格式 (Llama-3):

<|begin_of_text|>
<|start_header_id|>system<|end_header_id|>
You are a helpful AI assistant.
<|eot_id|>
<|start_header_id|>user<|end_header_id|>
What is the capital of France?
<|eot_id|>
<|start_header_id|>assistant<|end_header_id|>
The capital of France is Paris. It is located in the north-central part 
of the country along the Seine River.
<|eot_id|>

Loss 计算:
  - system prompt: mask (不计算 loss)
  - user message: mask (不计算 loss)
  - assistant response: 计算 loss
```

### 2.3 SFT 数据来源

| 来源 | 规模 | 质量 | 特点 |
|------|------|------|------|
| **人工标注** | 1K-100K | 最高 | 成本高，多样性有限 |
| **Self-Instruct** | 50K-1M | 中-高 | 自动生成，可扩展 |
| **Evol-Instruct** | 100K-1M | 高 | 复杂度递增 |
| **ShareGPT** | ~100K | 中 | 真实用户对话，但质量参差 |
| **UltraChat** | ~1.4M | 中-高 | 大规模合成对话 |
| **OpenHermes** | ~1M | 高 | 精选多源数据 |
| **WildChat** | ~1M | 中 | 真实用户交互 |

### 2.4 SFT 训练技巧

```
1. 数据质量 > 数据数量
   - 1K 高质量样本 > 10K 低质量样本
   - LIMA 论文: 仅 1000 个精心设计的样本就能达到很好的效果

2. 数据多样性
   - 覆盖多种任务类型: 问答、写作、代码、推理、翻译...
   - 覆盖多种难度: 简单到复杂
   - 覆盖多种风格: 正式、随意、创意...

3. 学习率选择
   - SFT 学习率通常比 Pre-training 低 10-100×
   - 典型: 2e-5 ~ 5e-5 (Pre-training 用 3e-4)

4. Epoch 数
   - 通常 1-3 epochs
   - 过多 epoch 导致过拟合和灾难性遗忘

5. Packing (序列打包)
   - 将多个短对话打包到一个序列中
   - 用 attention mask 防止跨对话 attention
   - 提升 GPU 利用率 2-5×
```

---

## RLHF：基于人类反馈的强化学习

### 3.1 RLHF 三阶段

```
RLHF 完整流程 (InstructGPT / ChatGPT):

阶段 1: SFT
  Base Model → 在人工标注的 (prompt, response) 上微调 → SFT Model

阶段 2: Reward Model (RM) 训练
  SFT Model → 对同一 prompt 生成多个 response
           → 人工标注偏好 (chosen vs rejected)
           → 训练 Reward Model 预测人类偏好

阶段 3: PPO 强化学习
  SFT Model → 用 PPO 优化，Reward Model 提供奖励信号
           → 加入 KL 惩罚防止偏离 SFT Model 太远
```

### 3.2 Reward Model 训练

```
Reward Model 架构:
  通常使用 SFT Model 去掉 LM Head，加一个线性层输出标量

训练数据格式:
  {
    "prompt": "Explain quantum computing",
    "chosen": "Quantum computing uses quantum bits...",
    "rejected": "It's like regular computing but quantum..."
  }

损失函数 (Bradley-Terry Model):
  P(chosen > rejected) = σ(r_chosen - r_rejected)
  
  L = -E[log σ(r_chosen - r_rejected)]
  
  其中 r = RewardModel(prompt, response)

关键细节:
  - 需要大量偏好标注数据 (通常 100K+ 对比)
  - 标注一致性是瓶颈 (不同标注者偏好不同)
  - Reward Hacking: RM 可能被 PPO 利用漏洞
```

### 3.3 PPO 训练

```
PPO (Proximal Policy Optimization) 目标:

  max E[r(x,y) - β × KL(π_θ(y|x) || π_ref(y|x))]

其中:
  - r(x,y): Reward Model 给出的奖励
  - KL 项: 防止策略偏离参考模型太远
  - β: KL 惩罚系数 (通常 0.01-0.1)

PPO 训练挑战:
  1. 需要同时加载 4 个模型:
     - Policy Model (训练中)
     - Reference Model (冻结, 计算 KL)
     - Reward Model (冻结, 计算奖励)
     - Value Model (训练中, 估计优势函数)
     → 显存需求巨大

  2. 训练不稳定:
     - Reward Model 可能不准确
     - KL 系数需要仔细调参
     - 容易出现 Reward Hacking

  3. 计算开销大:
     - 需要在线生成 response
     - 需要多次 PPO 迭代
```

### 3.4 RLHF 的变体

| 方法 | 改进点 | 代表工作 |
|------|--------|---------|
| **RRHF** | 用 ranking loss 替代 PPO | Allen AI |
| **RAFT** | 用 reward 对 response 排序后 SFT | - |
| **ReST** | 迭代采样-过滤-训练 | DeepMind |
| **RLCD** | 用对比 prompt 生成偏好对 | - |
| **Constitutional AI** | 用 AI 反馈替代人类反馈 | Anthropic |

---

## DPO：直接偏好优化

### 4.1 DPO 的核心洞察

```
DPO 的关键发现:

RLHF 的 PPO 目标可以重新参数化，直接用偏好数据优化策略，
不需要显式训练 Reward Model!

推导:
  PPO 目标: max E[r(x,y) - β × KL(π_θ || π_ref)]
  
  最优策略: π*(y|x) ∝ π_ref(y|x) × exp(r(x,y)/β)
  
  反解: r(x,y) = β × log(π*(y|x)/π_ref(y|x)) + β × log Z(x)
  
  代入 Bradley-Terry:
  P(y_w > y_l) = σ(β × log(π_θ(y_w)/π_ref(y_w)) - β × log(π_θ(y_l)/π_ref(y_l)))

DPO 损失函数:
  L_DPO = -E[log σ(β × log(π_θ(y_w|x)/π_ref(y_w|x)) 
                     - β × log(π_θ(y_l|x)/π_ref(y_l|x)))]
```

### 4.2 DPO vs RLHF

| 维度 | RLHF (PPO) | DPO |
|------|-----------|-----|
| **需要 RM** | 是 | 否 |
| **训练稳定性** | 不稳定，需要大量调参 | 稳定，类似 SFT |
| **显存需求** | 4 个模型 | 2 个模型 (policy + ref) |
| **计算开销** | 高 (在线生成 + 多轮 PPO) | 低 (离线数据) |
| **效果** | 上限更高 (可在线探索) | 稳定但可能不如 RLHF |
| **Reward Hacking** | 有风险 | 风险较低 |

### 4.3 DPO 的改进

| 方法 | 改进 | 核心思想 |
|------|------|---------|
| **IPO** | 解决 DPO 的过拟合 | 加正则化项 |
| **KTO** | 不需要偏好对 | 只需要二元反馈 (好/坏) |
| **ORPO** | SFT + DPO 联合 | 一个阶段完成对齐 |
| **SimPO** | 以序列概率为参考 | 不需要 reference model |
| **R-DPO** | 控制 response 长度 | 加长度正则化 |
| **Iterative DPO** | 多轮迭代 | 在线生成新偏好对 |

---

## 其他对齐方法

### 5.1 Constitutional AI (Anthropic)

```
Constitutional AI 流程:

阶段 1: Supervised (用 AI 反馈修订有害输出)
  1. 生成有害 response
  2. 用 Constitution (规则列表) 让模型自我批评
  3. 让模型根据批评修订 response
  4. 用修订后的 (prompt, revised_response) 做 SFT

阶段 2: RL (用 AI 反馈做偏好)
  1. 用阶段 1 的模型生成 response 对
  2. 用 Constitution 让 AI 选择更好的 response
  3. 用 AI 偏好数据训练 RM + PPO

Constitution 示例:
  "Choose the response that is most harmless and least toxic."
  "Choose the response that is most honest and truthful."
```

### 5.2 Rejection Sampling + Fine-Tuning

```
流程:
  1. 对每个 prompt 生成 N 个 response (如 N=64)
  2. 用 Reward Model 评分
  3. 选择得分最高的 response
  4. 用选出的 (prompt, best_response) 做 SFT

优点:
  - 简单直接
  - 不需要 RL
  - 效果显著 (Llama-2 使用)

缺点:
  - 推理成本高 (每个 prompt 生成 N 次)
  - 受限于生成多样性
```

---

## 推理增强：从 Chain-of-Thought 到 o1/R1

### 6.1 Chain-of-Thought (CoT)

```
CoT Prompting:
  Q: Roger has 5 tennis balls. He buys 2 more cans of tennis balls. 
     Each can has 3 tennis balls. How many tennis balls does he have now?
  
  A: Roger started with 5 balls. 2 cans of 3 tennis balls each is 6 
     tennis balls. 5 + 6 = 11. The answer is 11.

CoT 训练:
  在 SFT 数据中加入推理步骤
  → 模型学会"展示工作过程"
  → 显著提升数学和推理任务表现
```

### 6.2 DeepSeek-R1 的推理增强路线

```
DeepSeek-R1 训练流程:

阶段 1: Cold Start SFT
  - 收集数千条高质量 CoT 数据
  - 对 DeepSeek-V3-Base 做 SFT

阶段 2: Reasoning RL (GRPO)
  - 使用 GRPO (Group Relative Policy Optimization)
  - 奖励: 规则验证 (数学答案正确性, 代码通过测试)
  - 无 Reward Model! 纯规则奖励
  - 结果: DeepSeek-R1-Zero (纯 RL, 无 SFT)

阶段 3: Rejection Sampling + SFT
  - 用 R1-Zero 生成大量推理数据
  - 过滤 + 人工标注
  - 加入非推理数据 (写作, 问答等)
  - SFT 得到更强的模型

阶段 4: 全场景 RL
  - 推理任务: 规则奖励
  - 通用任务: Reward Model 奖励
  - 安全任务: 安全 RM 奖励
  - 最终: DeepSeek-R1
```

### 6.3 GRPO (Group Relative Policy Optimization)

```
GRPO vs PPO:

PPO:
  - 需要 Value Model 估计优势
  - 需要 Reference Model 计算 KL
  → 4 个模型同时加载

GRPO:
  - 对每个 prompt 生成 G 个 response (如 G=64)
  - 用组内相对奖励作为优势:
    A_i = (r_i - mean(r)) / std(r)
  - 不需要 Value Model!
  - KL 直接计算 (不需要 Reference Model):
    KL = exp(log π_θ - log π_old) - (log π_θ - log π_old) - 1
  
  → 只需 2 个模型 (Policy + Old Policy)
  → 显存减半，训练更稳定
```

### 6.4 OpenAI o1 的推理范式

```
o1 的核心思想: Test-time Compute Scaling

传统 LLM: 固定计算量 → 直接输出
o1: 可变计算量 → 内部思考链 → 输出

关键特征:
  1. 内部 CoT (用户不可见)
  2. 思考时间与问题难度成正比
  3. 可以自我纠错和回溯
  4. 使用 RL 训练思考过程

训练方法 (推测):
  1. 在数学/代码等可验证领域用 RL 训练
  2. 奖励: 最终答案正确性
  3. 模型学会分配更多"思考 token"给难题
  4. 涌现出自我反思、回溯、验证等行为
```

---

## 参数高效微调 (PEFT)

### 7.1 LoRA (Low-Rank Adaptation)

```
LoRA 核心思想:
  冻结原始权重 W ∈ R^{d×k}
  添加低秩分解: ΔW = B × A, 其中 B ∈ R^{d×r}, A ∈ R^{r×k}, r << min(d,k)
  
  前向: h = Wx + BAx = Wx + s × B(Ax)
  
  其中 s = α/r (缩放因子)

参数量:
  原始: d × k
  LoRA: r × (d + k)
  
  例如 d=4096, k=4096, r=16:
    原始: 16.8M
    LoRA: 16 × 8192 = 131K  → 减少 128×!

典型配置:
  - r = 8-64 (rank)
  - α = 16-32 (缩放)
  - target_modules: Q, K, V, O (有时也加 Gate, Up, Down)
  - dropout = 0.05-0.1
```

### 7.2 QLoRA

```
QLoRA = 4-bit 量化基础模型 + LoRA

技术要点:
  1. NF4 (NormalFloat4): 针对正态分布优化的 4-bit 格式
  2. Double Quantization: 量化常数量化 (再省 0.4 bits/param)
  3. Paged Optimizers: 用 unified memory 处理 OOM

效果:
  - 65B 模型在单张 48GB GPU 上微调
  - 效果接近全精度微调
  - 训练速度比 FP16 LoRA 慢 ~30%
```

### 7.3 其他 PEFT 方法

| 方法 | 原理 | 参数量 | 特点 |
|------|------|--------|------|
| **Adapter** | 在层间插入小网络 | 中 | 最早提出，推理有额外延迟 |
| **Prefix Tuning** | 学习可训练的 prefix token | 少 | 只调 prefix，灵活性低 |
| **Prompt Tuning** | 学习 soft prompt | 极少 | 效果有限 |
| **IA³** | 学习缩放向量 | 极少 | 极省参数 |
| **VeRA** | 共享随机矩阵 + 缩放向量 | 极少 | 比 LoRA 更省 |
| **DoRA** | 分解幅度和方向 | 与 LoRA 相近 | 效果更好 |

### 7.4 PEFT 选择指南

```
场景 → 方法:

单卡微调 7B 模型:
  → LoRA (r=16, 显存 ~16GB)

单卡微调 70B 模型:
  → QLoRA (4-bit, 显存 ~24GB)

追求最佳效果:
  → Full Fine-Tuning (需要多卡)

追求最快训练:
  → LoRA + Unsloth (优化 kernel)

追求最少参数:
  → VeRA 或 IA³
```

---

## 实践指南与工具

### 8.1 推荐工具栈

| 工具 | 用途 | 特点 |
|------|------|------|
| **TRL (HuggingFace)** | SFT, DPO, PPO, GRPO | 统一接口，与 transformers 集成 |
| **Axolotl** | SFT, DPO, LoRA/QLoRA | 配置驱动，易于使用 |
| **Unsloth** | 加速微调 | 2-5× 加速，省显存 |
| **LLaMA-Factory** | 全流程微调 | Web UI，支持多种方法 |
| **OpenRLHF** | RLHF 训练 | Ray 分布式，支持大规模 |
| **veRL** | RLHF 训练 | 字节跳动开源，高性能 |

### 8.2 典型 SFT 训练配置

```yaml
# Axolotl 配置示例
base_model: meta-llama/Meta-Llama-3-8B
model_type: LlamaForCausalLM
tokenizer_type: AutoTokenizer

load_in_8bit: false
load_in_4bit: false
strict: false

datasets:
  - path: your_dataset
    type: sharegpt
    conversation: chatml  # Llama-3 格式

dataset_prepared_path: last_run_prepared
val_set_size: 0.01
output_dir: ./outputs/lora-out

sequence_len: 4096
sample_packing: true
pad_to_sequence_len: true

adapter: lora
lora_model_dir:
lora_r: 16
lora_alpha: 32
lora_dropout: 0.05
lora_target_modules:
  - q_proj
  - k_proj
  - v_proj
  - o_proj
  - gate_proj
  - up_proj
  - down_proj

wandb_project: my-llm-sft
wandb_watch:
wandb_run_id:

gradient_accumulation_steps: 4
micro_batch_size: 2
num_epochs: 3
optimizer: adamw_torch
lr_scheduler: cosine
learning_rate: 2e-5

train_on_inputs: false
group_by_length: false
bf16: auto
fp16: false

gradient_checkpointing: true
early_stopping_patience:
resume_from_checkpoint:
logging_steps: 1
xformers_attention:
flash_attention: true

warmup_steps: 100
evals_per_epoch: 4
eval_table_size:
eval_max_new_tokens: 128
saves_per_epoch: 1
debug:
deepspeed:
weight_decay: 0.0
special_tokens:
  pad_token: <|end_of_text|>
```

### 8.3 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| **灾难性遗忘** | 学习率太高 / epoch 太多 | 降低 lr, 减少 epoch, 加入 KL 约束 |
| **过拟合** | 数据太少 / 多样性不足 | 增加数据多样性, 加 dropout |
| **对齐税** | 对齐过程损害了知识能力 | 混合 Pre-training 数据, 降低 KL 系数 |
| **Reward Hacking** | RM 被利用漏洞 | 改进 RM, 加 KL 约束, 多 RM 集成 |
| **输出变短** | DPO 偏好短回复 | 加长度正则化 (R-DPO) |

---

> **关键原则**：
> 1. **SFT 是基础**：好的 SFT 数据 + 合适的训练配置 = 80% 的效果
> 2. **DPO 是性价比之选**：不需要 RM，训练稳定，效果接近 RLHF
> 3. **RLHF 是上限之选**：需要更多资源，但上限更高
> 4. **推理增强是新范式**：o1/R1 证明了 RL + 可验证奖励的巨大潜力
> 5. **数据质量决定一切**：Post-Training 中数据比算法更重要
