# LLM 评估体系与 Scaling Laws

> **核心命题**：如何衡量一个 LLM 的好坏？如何预测更大模型的性能？评估体系和 Scaling Laws 是 LLM 研发的"导航系统"——没有它们，模型开发就是盲人摸象。

## 目录

1. [评估体系全景](#评估体系全景)
2. [知识能力评估](#知识能力评估)
3. [推理能力评估](#推理能力评估)
4. [代码能力评估](#代码能力评估)
5. [对齐与安全评估](#对齐与安全评估)
6. [评估的陷阱与挑战](#评估的陷阱与挑战)
7. [Scaling Laws](#scaling-laws)
8. [Chinchilla 定律与最优计算分配](#chinchilla-定律与最优计算分配)
9. [Emergent Abilities](#emergent-abilities)

---

## 评估体系全景

```
┌─────────────────────────────────────────────────────────────────────────┐
│                       LLM 评估体系全景                                    │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  评估维度:                                                               │
│                                                                         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐    │
│  │  知识能力    │  │  推理能力    │  │  代码能力    │  │  对齐安全    │    │
│  │             │  │             │  │             │  │             │    │
│  │ MMLU        │  │ GSM8K       │  │ HumanEval   │  │ TruthfulQA  │    │
│  │ MMLU-Pro    │  │ MATH        │  │ MBPP        │  │ ToxiGen     │    │
│  │ ARC         │  │ BBH         │  │ LiveCode    │  │ RealToxicity│    │
│  │ HellaSwag   │  │ GPQA        │  │  Bench       │  │ Prompts     │    │
│  │ TriviaQA    │  │ MuSR        │  │ SWE-bench   │  │ MT-Bench    │    │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘    │
│                                                                         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                     │
│  │  多语言      │  │  长上下文    │  │  多模态      │                     │
│  │             │  │             │  │             │                     │
│  │ MGSM        │  │ Needle in   │  │ MMMU        │                     │
│  │ XQuAD       │  │  Haystack   │  │ MMBench     │                     │
│  │ FLORES      │  │ RULER       │  │ SEED-Bench  │                     │
│  └─────────────┘  └─────────────┘  └─────────────┘                     │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 知识能力评估

### 2.1 MMLU (Massive Multitask Language Understanding)

```
MMLU: 最广泛使用的 LLM 知识基准

内容:
  - 57 个学科 (数学、物理、历史、法律、医学...)
  - 约 15,000 道选择题 (4 选 1)
  - 覆盖高中到专业级别

评估方式:
  - 0-shot 或 5-shot
  - 计算每个学科的正确率
  - 最终得分 = 所有学科平均

关键分数 (5-shot):
  Random: 25%
  GPT-3.5: ~70%
  GPT-4: ~86.4%
  Claude 3.5 Sonnet: ~88.7%
  Llama-3-70B: ~82%
  Llama-3-405B: ~88.6%

MMLU-Pro (2024):
  - 更难的版本 (10 选 1)
  - 过滤了简单题目
  - 区分度更好
```

### 2.2 其他知识基准

| 基准 | 内容 | 形式 | 特点 |
|------|------|------|------|
| **ARC** | 科学推理 | 选择题 | 分 Easy 和 Challenge |
| **HellaSwag** | 常识推理 | 完形填空 | 对抗性生成 |
| **TriviaQA** | 事实知识 | 问答 | 维基百科来源 |
| **WinoGrande** | 代词消歧 | 选择题 | 常识推理 |
| **PIQA** | 物理常识 | 选择题 | 日常物理 |

---

## 推理能力评估

### 3.1 数学推理

```
GSM8K (Grade School Math 8K):
  - 8,500 道小学数学应用题
  - 需要多步推理
  - 评估 chain-of-thought 能力

MATH:
  - 12,500 道竞赛数学题
  - 难度: AMC 10/12, AIME 级别
  - 需要 LaTeX 格式输出
  - 比 GSM8K 难得多

关键分数:
  GSM8K:
    GPT-4: ~92%
    Claude 3.5: ~96.4%
    Llama-3-70B: ~93%
  
  MATH:
    GPT-4: ~52.9%
    Claude 3.5: ~71.1%
    Llama-3-70B: ~50.4%
```

### 3.2 复杂推理

```
BBH (BIG-Bench Hard):
  - 23 个困难任务
  - 包括逻辑推理、算法、自然语言理解
  - 需要 CoT (Chain-of-Thought)

GPQA (Graduate-Level Google-Proof Q&A):
  - 研究生级别的问答
  - 物理、化学、生物
  - "Google-Proof": 无法通过搜索直接找到答案
  - 区分度极高

MuSR (Multistep Soft Reasoning):
  - 长上下文多步推理
  - 谋杀谜案、导航、目标规划
  - 评估长文本推理能力
```

---

## 代码能力评估

### 4.1 代码生成

```
HumanEval:
  - 164 道 Python 编程题
  - 函数补全形式
  - 评估指标: pass@k
  - 最广泛使用的代码基准

MBPP (Mostly Basic Python Programming):
  - 974 道 Python 编程题
  - 入门级难度
  - 评估基本编程能力

关键分数 (pass@1):
  GPT-4: ~67% (HumanEval)
  Claude 3.5: ~92% (HumanEval)
  Llama-3-70B: ~81.7% (HumanEval)
  DeepSeek-Coder-V2: ~90.2% (HumanEval)
```

### 4.2 软件工程

```
SWE-bench:
  - 真实 GitHub issue → 代码修复
  - 需要理解大型代码库
  - 评估实际软件工程能力
  - 2024 年最受关注的代码基准

SWE-bench Verified:
  - 过滤后的 500 个问题
  - 更可靠的评估

关键分数 (SWE-bench Verified):
  GPT-4: ~1.7%
  Claude 3.5 Sonnet: ~49%
  Devin (Cognition): ~13.86%
  SWE-agent + GPT-4: ~12.47%
```

---

## 对齐与安全评估

### 5.1 安全评估

```
TruthfulQA:
  - 评估模型是否倾向于生成虚假信息
  - 817 道问题
  - 涵盖常见误解和阴谋论

ToxiGen:
  - 评估模型生成有害内容的倾向
  - 13 个少数群体
  - 隐式和显式有害言论

RealToxicityPrompts:
  - 100K 自然句子
  - 评估模型续写的毒性
  - Perspective API 评分
```

### 5.2 人类偏好评估

```
MT-Bench (Multi-Turn Benchmark):
  - 80 个多轮对话问题
  - GPT-4 作为评判者
  - 8 个类别: 写作、角色扮演、推理、数学、编码、提取、STEM、人文

Chatbot Arena (LMSYS):
  - 众包人类偏好
  - Elo 评分系统
  - 最权威的 LLM 排名
  - 盲测 (用户不知道模型身份)

AlpacaEval:
  - 805 个指令
  - GPT-4 作为评判者
  - 长度控制版本 (LC) 减少长度偏差
```

---

## 评估的陷阱与挑战

### 6.1 数据污染

```
数据污染 (Data Contamination):

问题: 训练数据中包含了测试数据
  → 评估分数虚高
  → 无法反映真实泛化能力

检测方法:
  1. N-gram 重叠检测
  2. 语义相似度检测
  3. 对抗性变体测试

案例:
  - GPT-4 在某些基准上可能被污染
  - 新基准 (如 MMLU-Pro) 试图避免污染
```

### 6.2 评估偏差

```
常见评估偏差:

1. 长度偏差:
   - 长回答更容易被 LLM 评判者打高分
   - 解决: 长度控制 (AlpacaEval LC)

2. 位置偏差:
   - LLM 评判者偏好特定位置的回答
   - 解决: 交换位置多次评估

3. 风格偏差:
   - 特定风格 (如 markdown 格式) 得分更高
   - 解决: 标准化输出格式

4. 自我增强偏差:
   - LLM 评判者偏好自己生成的文本
   - 解决: 使用不同模型作为评判者
```

### 6.3 评估的局限性

```
当前评估体系的局限:

1. 静态基准:
   - 题目固定，可能被"刷榜"
   - 需要动态更新的基准 (如 LiveCodeBench)

2. 覆盖不全:
   - 创造力、情感智能难以量化
   - 实际应用场景与基准差距大

3. 语言偏差:
   - 大多数基准是英语
   - 多语言评估不充分

4. 评估成本:
   - 人类评估昂贵
   - LLM 评判者不可靠
```

---

## Scaling Laws

### 7.1 Kaplan Scaling Laws (OpenAI, 2020)

```
Kaplan Scaling Laws:

核心发现:
  模型性能 (Loss) 与三个因素呈幂律关系:
  
  L(N) ∝ N^(-α_N)    模型参数量 N
  L(D) ∝ D^(-α_D)    训练数据量 D
  L(C) ∝ C^(-α_C)    计算量 C

关键结论:
  1. 模型大小增加时，数据量不需要等比例增加
     N ∝ C^0.73, D ∝ C^0.27
     → 增大模型比增加数据更有效

  2. 大模型更"样本高效"
     → 大模型用更少数据达到同样性能

  3. 最优 batch size 随计算量增长
     B_opt ∝ C^0.21
```

### 7.2 Chinchilla Scaling Laws (DeepMind, 2022)

```
Chinchilla Laws (Hoffmann et al., 2022):

核心修正:
  Kaplan 低估了数据的重要性!

Chinchilla 最优:
  N_opt ∝ C^0.50
  D_opt ∝ C^0.50
  → 模型大小和数据量应该等比例增长!

具体数字:
  给定计算预算 C (FLOPs):
  N_opt ≈ 0.73 × C^0.50 (参数)
  D_opt ≈ 1.69 × C^0.50 (tokens)

Chinchilla 模型:
  - 70B 参数
  - 1.4T tokens 训练
  - 性能超过 Gopher (280B, 300B tokens)
  - 但计算量相同!

启示:
  - 大多数模型"欠训练" (undertrained)
  - Llama 系列遵循 Chinchilla 定律
  - 小模型 + 多数据 > 大模型 + 少数据
```

### 7.3 Scaling Laws 对比

```
Kaplan vs Chinchilla:

  ┌─────────────────────────────────────────────┐
  │                                             │
  │  Loss                                       │
  │   │                                         │
  │   │  Kaplan: 增大模型更有效                  │
  │   │    \                                    │
  │   │     \    Chinchilla: 等比例增长          │
  │   │      \    \                             │
  │   │       \    \                            │
  │   │        \    \                           │
  │   │         \    \                          │
  │   │          \    \                         │
  │   │           \____\____________________    │
  │   │                 \                       │
  │   │                  \  实际最优?            │
  │   └──────────────────────────────▶ 计算量    │
  │                                             │
  └─────────────────────────────────────────────┘

最新观点 (2024):
  - Chinchilla 可能也不是最终答案
  - Llama-3 用 15T tokens 训练 405B (远超 Chinchilla)
  - "数据越多越好" 似乎是新共识
  - 但数据质量比数量更重要
```

---

## Chinchilla 定律与最优计算分配

### 8.1 实际应用

```
Chinchilla 定律的实际应用:

给定 GPU 预算:
  1000 × H100, 训练 30 天
  总计算量 ≈ 1000 × 989 TFLOPS × 30 × 86400 × 0.5 (利用率)
           ≈ 1.28 × 10^9 PFLOPs
           ≈ 1.28 × 10^24 FLOPs

Chinchilla 最优:
  N_opt ≈ 0.73 × (1.28×10^24)^0.5 ≈ 82B 参数
  D_opt ≈ 1.69 × (1.28×10^24)^0.5 ≈ 1.9T tokens

→ 应该训练一个 ~82B 的模型，用 ~1.9T tokens

实际案例:
  Llama-2-70B: 2T tokens → 接近 Chinchilla 最优
  Llama-3-70B: 15T tokens → 远超 Chinchilla (数据更多)
  Llama-3-405B: 15T tokens → 远超 Chinchilla
```

### 8.2 超越 Chinchilla

```
为什么实际训练可能超越 Chinchilla:

1. 数据质量提升:
   - Chinchilla 用的是相对低质量数据
   - 高质量数据可以训练更久

2. 推理能力需求:
   - 推理能力需要更多数据
   - 代码/数学数据特别有价值

3. 多轮训练:
   - Pre-training → Annealing → Post-training
   - 每个阶段需要不同数据

4. 合成数据:
   - 高质量合成数据可以无限生成
   - 突破了自然数据的限制
```

---

## Emergent Abilities

### 9.1 什么是涌现能力

```
Emergent Abilities (涌现能力):

定义: 小模型没有，但模型大到一定程度后突然出现的能力

特征:
  - 小模型: 随机水平
  - 达到某个规模阈值: 突然大幅提升
  - 不是平滑增长，而是阶跃式

典型涌现能力:
  1. Chain-of-Thought 推理
  2. 指令遵循
  3. 多步算术
  4. 代码执行
  5. 多语言翻译
  6. 校准 (Calibration)
```

### 9.2 涌现的争议

```
关于涌现的争议:

支持方:
  - 多个基准上观察到明显的相变
  - 某些能力确实在小模型上完全不存在

反对方 (Schaeffer et al., 2023):
  - 涌现可能是评估指标的假象
  - 如果用连续指标 (如 token-level probability)
    而非离散指标 (如 exact match)
  - 能力增长是平滑的，没有阶跃

当前共识:
  - 部分"涌现"确实是度量假象
  - 但某些能力确实存在非线性增长
  - 预训练 + Post-Training 的组合是关键
```

---

> **关键原则**：
> 1. **评估要全面**：单一基准不可靠，需要多维度交叉验证
> 2. **Chinchilla 是起点不是终点**：数据越多越好，质量越高越好
> 3. **数据污染是真实威胁**：新基准需要持续更新
> 4. **人类评估仍是金标准**：LLM 评判者只是近似
> 5. **Scaling Laws 指导方向**：但实际训练需要结合经验和资源
