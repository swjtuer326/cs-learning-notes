# LLM 数据工程：从采集到 Tokenization

> **核心命题**：数据质量决定模型上限，数据工程是 LLM 训练中最被低估的环节。Chinchilla 定律告诉我们，一个 70B 的模型需要约 1.4T tokens 的高质量训练数据——如何获取、清洗、配比这些数据，是比模型架构更影响最终效果的因素。

### 关键术语

| 缩写 | 全称 | 含义 |
|------|------|------|
| BPE | Byte-Pair Encoding | 字节对编码，从字符级逐步合并高频子词的 Tokenizer 算法 |
| LSH | Locality-Sensitive Hashing | 局部敏感哈希，用于近似最近邻搜索和模糊去重 |
| PII | Personally Identifiable Information | 个人身份信息，数据清洗中需检测和移除的隐私数据 |
| PPL | Perplexity | 困惑度，语言模型对文本的预测不确定度，用于质量过滤 |
| NER | Named Entity Recognition | 命名实体识别，用于知识重述中的保真度验证 |
| FSM | Finite State Machine | 有限状态机，Step3.5-Flash 用于建模工具调用的合法意图转移 |

### 前置知识

| 需要了解 | 参考文档 |
|----------|----------|
| LLM 训练推理全景框架 | [00-LLM训练推理全景学习框架](./00-LLM训练推理全景学习框架.md) |
| Transformer 结构与训练算法 | [02-Transformer 完整结构](./02-Transformer完整结构与训练算法.md) |

## 目录

1. [数据工程全景](#数据工程全景)
2. [数据来源与采集](#数据来源与采集)
3. [数据清洗 Pipeline](#数据清洗-pipeline)
4. [去重技术深入](#去重技术深入)
5. [质量过滤](#质量过滤)
6. [数据混合与配比策略](#数据混合与配比策略)
7. [Tokenizer 训练与选型](#tokenizer-训练与选型)
8. [合成数据生成](#合成数据生成)
9. [数据去污染](#数据去污染)
10. [工程实践与工具链](#工程实践与工具链)
11. [合成数据方法论 — Phi-4/GLM-5/Step3.5-Flash](#合成数据方法论)
12. [知识重述 (Knowledge Rephrasing) — Kimi-K2](#知识重述-knowledge-rephrasing)
13. [多智能体数据生成 — Phi-4/Kimi-K2.5](#多智能体数据生成)
14. [Data Experiment 范式 — MiniMax-01](#data-experiment-范式)
15. [PDF OCR 与质量标注 — Qwen3](#pdf-ocr-与质量标注)

---

## 数据工程全景

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         数据工程全链路                                    │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐            │
│  │ 数据采集  │──▶│ 数据清洗  │──▶│ 数据配比  │──▶│Tokenization│           │
│  │          │   │          │   │          │   │          │            │
│  │ Common   │   │ 语言检测  │   │ 启发式    │   │ BPE/      │            │
│  │ Crawl    │   │ 质量过滤  │   │ DoReMi    │   │ Unigram   │            │
│  │ 代码仓库  │   │ 去重      │   │ 课程学习  │   │ 词表设计  │            │
│  │ 书籍     │   │ PII 去除  │   │ 退火      │   │ Chat      │            │
│  │ 合成数据  │   │ 去污染    │   │          │   │ Template  │            │
│  └──────────┘   └──────────┘   └──────────┘   └──────────┘            │
│                                                                         │
│  关键指标:                                                              │
│  - 数据保留率: 原始数据 → 清洗后 (通常 5-15%)                           │
│  - 去重率: 文档级 ~30-50%, 段落级 ~10-20%                               │
│  - Token 膨胀率: 非英语文本的 token/char 比值                           │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 数据来源与采集

### 2.1 主要数据源

| 来源 | 规模 | 质量 | 获取难度 | 代表数据集 | 关键特征 |
|------|------|------|---------|-----------|---------|
| **Common Crawl** | PB 级 (~250B+ pages) | 低（噪声极大） | 低（公开下载） | C4, RefinedWeb, Dolma, FineWeb | 覆盖面最广，是几乎所有 LLM 的主力数据源 |
| **代码仓库** | TB 级 | 中-高 | 中（需处理许可证） | The Stack v2 (67TB), StarCoderData | 对推理能力至关重要，结构化强 |
| **学术论文** | TB 级 | 高 | 中 | S2ORC (81M papers), ArXiv, PubMed | 高质量但领域窄，科学推理能力来源 |
| **书籍** | TB 级 | 高 | 高（版权问题） | Books3, Gutenberg, PG-19 | 长文本、高质量叙事，但版权争议大 |
| **百科/知识库** | GB-TB 级 | 高 | 低 | Wikipedia, Wikidata, Baidu Baike | 事实性强，但覆盖面有限 |
| **对话/论坛** | TB 级 | 中 | 中 | Reddit (PushShift), StackExchange | 对话风格多样，但质量参差 |
| **新闻媒体** | TB 级 | 中-高 | 中 | NewsCrawl, GlobalVoices | 时效性强，写作规范 |
| **法律/金融文档** | TB 级 | 高 | 高 | EDGAR, PACER, SEC filings | 专业领域知识 |

### 2.2 Common Crawl 处理详解

Common Crawl 是 LLM 训练数据的绝对主力（Llama-3 使用了约 15T tokens 的 web 数据），但其原始数据质量极差。

```
Common Crawl 原始数据格式:
  WARC (Web ARChive) 文件:
  ├── WARC Header (URL, 抓取时间, 内容长度)
  ├── HTTP Response Header (Content-Type, Server, ...)
  └── HTTP Response Body (原始 HTML)
  
  WET (WARC Encapsulated Text) 文件:
  └── 提取后的纯文本 (已去除 HTML 标签)
  
  WAT (WARC Annotation Text) 文件:
  └── 元数据 (链接, 标题, 描述)
```

**处理流程**：

```
WARC 文件
    │
    ▼
HTML 解析 (trafilatura / readability / beautifulsoup)
    │  - 去除导航栏、广告、页脚 (boilerplate removal)
    │  - 提取正文内容
    │  - 保留段落结构
    ▼
语言检测 (FastText / CLD3)
    │  - 分类 176+ 种语言
    │  - 保留目标语言 (通常英语为主)
    │  - 置信度阈值 > 0.7
    ▼
初步质量过滤
    │  - 文档长度: 50-100,000 字符
    │  - 特殊字符比例 < 30%
    │  - 单词重复率 < 某个阈值
    ▼
存储为 JSONL
    {"text": "...", "url": "...", "timestamp": "...", "language": "en"}
```

### 2.3 代码数据处理

代码数据对 LLM 的推理能力有显著提升。StarCoder 和 DeepSeek-Coder 证明了代码训练的重要性。

| 来源 | 语言覆盖 | 规模 | 处理要点 |
|------|---------|------|---------|
| **GitHub (The Stack v2)** | 600+ 语言 | 67TB (去重后) | 许可证过滤 (opt-out), 近重复去重 |
| **GitLab** | 多语言 | TB 级 | 同上 |
| **Jupyter Notebooks** | Python 为主 | GB-TB 级 | 需提取 code cell + markdown cell |
| **StackOverflow** | 多语言 | ~50M Q&A | 问答格式保留，代码块提取 |
| **竞赛平台** | 多语言 | GB 级 | Codeforces, LeetCode, Kaggle |

**代码数据特殊处理**：
- **注释比例过滤**：注释过多或过少的文件可能质量低
- **可执行性检查**：Python 代码可尝试 AST 解析
- **重复文件检测**：fork/模板项目产生大量重复
- **许可证合规**：The Stack 提供 opt-out 机制

---

## 数据清洗 Pipeline

### 3.1 清洗流程总览

```
原始数据 (100%)
    │
    ▼
┌──────────────────────┐
│ 1. 语言检测与分类     │  → 保留率 ~60-80%
│    FastText / CLD3   │
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│ 2. 基础规则过滤       │  → 保留率 ~70-90%
│    长度/重复/特殊字符  │
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│ 3. 质量评分过滤       │  → 保留率 ~50-80%
│    Perplexity/分类器  │
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│ 4. 去重               │  → 保留率 ~50-70%
│    文档级 + 段落级    │
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│ 5. 毒性/PII 过滤      │  → 保留率 ~90-95%
│    安全与隐私         │
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│ 6. 去污染             │  → 保留率 ~95-99%
│    Benchmark 防泄露   │
└──────────┬───────────┘
           ▼
清洗后数据 (通常 5-15% 原始数据)
```

### 3.2 语言检测

| 工具 | 原理 | 速度 | 准确率 | 语言数 |
|------|------|------|--------|--------|
| **FastText (Meta)** | 基于 n-gram 特征的线性分类器 | 极快 (百万文档/秒) | >99% (长文本) | 176 |
| **CLD3 (Google)** | 神经网络语言检测 | 快 | >98% | 107 |
| **langdetect** | 朴素贝叶斯 + n-gram | 慢 | ~95% | 55 |
| **lingua-py** | 规则 + n-gram | 中 | >99% (短文本也准) | 75 |

**实践建议**：
- 使用 FastText 作为主力（速度最快，生态最好）
- 对短文本（<50 字符）使用 lingua-py 补充
- 多语言模型需要保留多种语言，单语言模型只保留目标语言

### 3.3 基础规则过滤

```python
# 典型的规则过滤逻辑
def rule_based_filter(text: str) -> bool:
    # 长度过滤
    if len(text) < 100 or len(text) > 1_000_000:
        return False
    
    # 单词数过滤
    words = text.split()
    if len(words) < 20 or len(words) > 200_000:
        return False
    
    # 平均词长 (过滤乱码)
    avg_word_len = sum(len(w) for w in words) / max(len(words), 1)
    if avg_word_len < 3 or avg_word_len > 15:
        return False
    
    # 特殊字符比例
    special_chars = sum(1 for c in text if not c.isalnum() and not c.isspace())
    if special_chars / max(len(text), 1) > 0.3:
        return False
    
    # 行重复率 (过滤日志/错误信息)
    lines = text.split('\n')
    if len(set(lines)) / max(len(lines), 1) < 0.3:
        return False
    
    # 大写字母比例
    alpha_chars = [c for c in text if c.isalpha()]
    if alpha_chars:
        upper_ratio = sum(1 for c in alpha_chars if c.isupper()) / len(alpha_chars)
        if upper_ratio > 0.5:  # 全大写文本
            return False
    
    # 省略号/符号行比例 (过滤目录/列表页)
    bullet_lines = sum(1 for l in lines if l.strip().startswith(('•', '-', '*', '·')))
    if bullet_lines / max(len(lines), 1) > 0.5:
        return False
    
    return True
```

---

## 去重技术深入

### 4.1 去重的三个层次

```
层次 1: URL 去重
  └── 同一 URL 只保留一次 (最简单，但不够)

层次 2: 文档级去重 (Document-level Deduplication)
  └── 检测高度相似的文档对
  └── 方法: MinHash + LSH, SimHash

层次 3: 段落/句子级去重 (Sub-document Deduplication)
  └── 检测跨文档的重复段落
  └── 方法: Suffix Array, Exact Substring Matching
```

### 4.2 MinHash + LSH (文档级模糊去重)

这是工业界最常用的文档级去重方案（RefinedWeb, Dolma, FineWeb 均使用）。

MinHash 原理:

1. 将文档表示为 n-gram 集合 (通常 n=5-13)
   Doc → {ngram_1, ngram_2, ..., ngram_m}

2. 用 K 个哈希函数计算每个 n-gram 的最小哈希值
   MinHash(Doc) = [min(h1(ngrams)), min(h2(ngrams)), ..., min(hK(ngrams))]

3. Jaccard 相似度 ≈ MinHash 签名中相同位置的比例
   $$J(D_1, D_2) \approx \frac{|\{i: \text{MinHash}(D_1)[i] = \text{MinHash}(D_2)[i]\}|}{K}$$
   其中 $J(D_1, D_2)$ 是 Jaccard 相似度估计，$D_1, D_2$ 是两个文档，$K$ 是哈希函数数量。

4. LSH (Locality Sensitive Hashing) 加速:
   将 K 个签名分成 B 个 band，每个 band 有 R 行（$K = B \times R$）
   两个文档只要有一个 band 完全匹配，就认为是候选对
   
   参数选择:
   - K = 128-256 (签名长度)
   - B = 16-32, R = 8-16
   - 相似度阈值 $s \approx (1/B)^{1/R}$
   - 例如 $B=20, R=10 \Rightarrow s \approx 0.74$

**实践参数**：

| 数据集规模 | K (签名数) | B (bands) | R (rows) | 近似阈值 |
|-----------|-----------|-----------|----------|---------|
| < 1TB | 128 | 16 | 8 | ~0.71 |
| 1-10TB | 256 | 20 | 12.8 | ~0.79 |
| > 10TB | 256 | 32 | 8 | ~0.65 |

### 4.3 Suffix Array (精确子串去重)

用于去除跨文档的精确重复段落（如模板文本、转载内容）。

Suffix Array 去重流程:

1. 将所有文档拼接为一个超长字符串 S (用特殊分隔符隔开)
2. 构建 S 的后缀数组 (Suffix Array)
3. 计算相邻后缀的 LCP (Longest Common Prefix)
4. 标记 LCP ≥ 阈值 (如 50 tokens) 的重复区间
5. 移除重复区间 (保留第一次出现)

复杂度: $O(N \log N)$ 时间, $O(N)$ 空间 (N = 总字符数)

实际使用:
- Dolma: 使用 Rust 实现的 suffix array, 阈值 13 tokens
- RefinedWeb: 先 MinHash 去重, 再精确行去重
- FineWeb: 使用 datasketch 库的 MinHashLSH

### 4.4 去重效果

| 数据集 | 原始文档数 | 文档级去重后 | 段落级去重后 | 总去重率 |
|--------|-----------|-------------|-------------|---------|
| C4 | ~365M | ~200M | - | ~45% |
| RefinedWeb | ~200B tokens | ~100B tokens | ~80B tokens | ~60% |
| Dolma | ~12TB | ~8TB | ~6TB | ~50% |
| FineWeb | ~100T tokens | ~45T tokens | ~36T tokens | ~64% |

---

## 质量过滤

### 5.1 Perplexity-based 过滤

用训练好的语言模型（通常是 KenLM n-gram 模型）对文本打分。

KenLM 质量过滤:

1. 在高质量文本 (如 Wikipedia) 上训练 KenLM 5-gram 模型
2. 对每个待过滤文档计算 perplexity:
   $$\text{PPL} = \exp\left(-\frac{1}{N} \sum_{i=1}^{N} \log P(\text{token}_i \mid \text{token}_{i-4}, \dots, \text{token}_{i-1})\right)$$
   其中 $N$ 是 token 总数，$P(\text{token}_i \mid \dots)$ 是给定前 4 个 token 的条件下第 $i$ 个 token 的概率。
3. 过滤 PPL 过高或过低的文档:
   - PPL 过高 → 文本质量差 (乱码、非自然语言)
   - PPL 过低 → 文本过于简单/重复 (模板生成)

典型阈值:
- 低 PPL 阈值: 10-50 (过滤过于简单的文本)
- 高 PPL 阈值: 1000-5000 (过滤低质量文本)

### 5.2 Classifier-based 过滤

训练一个二分类器判断文本是否"高质量"。

```
分类器训练流程:

1. 正样本: Wikipedia, 书籍, 学术论文
2. 负样本: 随机 Common Crawl 页面
3. 特征: n-gram TF-IDF, 文本统计特征
4. 模型: 线性分类器 (FastText) 或轻量 BERT

优点: 比 perplexity 更灵活，可以针对特定质量维度
缺点: 需要标注数据，可能引入偏差
```

### 5.3 启发式质量指标

| 指标 | 计算方式 | 过滤逻辑 |
|------|---------|---------|
| **词重复率** | 非重复词数 / 总词数 | < 0.3 过滤 |
| **停用词比例** | 停用词数 / 总词数 | < 0.2 或 > 0.8 过滤 |
| **句子长度变异** | 句子长度的标准差 | 过低表示模板化 |
| **段落结构** | 平均段落长度、段落数 | 无段落结构过滤 |
| **URL/邮箱比例** | URL+邮箱数 / 总字符数 | > 0.05 过滤 |
| **HTML 残留** | HTML 标签数 / 总字符数 | > 0.01 过滤 |

---

## 数据混合与配比策略

### 6.1 启发式配比

大多数开源模型使用人工设定的配比：

| 模型 | Web | Code | Wiki/Books | 学术 | 对话 | 其他 |
|------|-----|------|-----------|------|------|------|
| **Llama-1** | 67% | 4.5% | 4.5% (Books) + 4.5% (Wiki) | 2.5% | - | 17% |
| **Llama-2** | ~80% | ~5% | ~5% | ~5% | - | ~5% |
| **Llama-3** | ~50% | ~17% | ~5% | ~5% | - | ~23% (含合成) |
| **Falcon** | 83% (RefinedWeb) | 7% | 5% | 3% | 2% | - |
| **DeepSeek-V2** | ~60% | ~15% | ~5% | ~5% | - | ~15% (含数学) |

### 6.2 DoReMi: 自动配比搜索

DoReMi (Domain Reweighting with Minimax Optimization) 用小模型自动搜索最优配比。

```
DoReMi 工作流程:

1. 训练一个小的 Reference Model (如 280M 参数)
   - 在所有领域数据上均匀采样训练

2. 训练 Domain Weights (通过 minimax 优化)
   - 目标: 找到一组领域权重，使得在该权重下训练的模型
           在所有领域上的 worst-case loss 最小
   - 方法: 迭代更新权重 → 训练代理模型 → 评估 per-domain loss

3. 将搜索到的权重用于大模型训练

关键发现:
- DoReMi 找到的权重与均匀权重差异显著
- 使用 DoReMi 权重训练的 8B 模型效果优于均匀权重
- 代码和学术数据的权重通常被调高
```

### 6.3 数据课程学习 (Data Curriculum)

| 策略 | 描述 | 代表工作 |
|------|------|---------|
| **长度课程** | 从短序列逐步过渡到长序列 | 几乎所有 LLM 训练 |
| **质量课程** | 从高质量数据开始，逐步加入更多样数据 | Phi 系列 |
| **难度课程** | 从简单任务到复杂任务 | 数学/代码专项训练 |
| **领域课程** | 先通用后专业 | Llama-3 的多阶段训练 |

### 6.4 退火 (Annealing)

训练末期使用极高质量的数据进行少量步数的训练。

```
退火策略 (Llama-3, Phi-4):

1. 训练最后 5-10% 步数
2. 使用经过严格筛选的高质量数据
3. 学习率线性衰减到接近 0
4. 效果: 显著提升 benchmark 表现 (MMLU +2-5%)

数据选择:
- 人工标注的高质量样本
- 合成的高难度推理数据
- Benchmark 风格的数据 (但要去污染!)
```

---

## Tokenizer 训练与选型

### 7.1 主流 Tokenizer 算法对比

| 算法 | 训练方式 | 编码方式 | 特点 | 代表 |
|------|---------|---------|------|------|
| **BPE** | 自底向上合并 | 贪心最长匹配 | 简单高效，从字符开始 | GPT 系列, Llama |
| **WordPiece** | 自底向上合并 | 贪心最长匹配 | 基于似然而非频率 | BERT |
| **Unigram** | 自顶向下剪枝 | Viterbi 最优路径 | 概率化，多路径编码 | XLNet, ALBERT |
| **SentencePiece** | BPE/Unigram | BPE/Unigram | 直接处理原始文本 | Llama-1/2, Mistral |

### 7.2 BPE 训练详解

```
BPE (Byte-Pair Encoding) 训练流程:

1. 初始化词表: 所有单字节 (256 个) + 特殊 token
2. 将训练语料表示为字节序列
3. 重复直到词表达到目标大小:
   a. 统计所有相邻 token 对的频率
   b. 选择频率最高的对进行合并
   c. 将新合并的 token 加入词表
   d. 更新语料中的 token 序列
4. 输出: 词表 + merge rules

示例 (词表大小 5):
  语料: "low lower lowest"
  初始: l, o, w, _, e, r, s, t
  Step 1: l+o → lo (频率最高)
  Step 2: lo+w → low
  Step 3: e+r → er
  Step 4: er+s → ers (如果还有空间)
```

### 7.3 词表大小选择

| 词表大小 | 优点 | 缺点 | 代表模型 |
|---------|------|------|---------|
| **32K** | 训练快，Embedding 小 | 非英语 token 膨胀严重 | Llama-1/2, Mistral-7B |
| **50-64K** | 平衡点 | - | GPT-3, Falcon |
| **100-128K** | 多语言友好，序列短 | Embedding 层大 (~100M 参数) | Llama-3, Qwen2.5, DeepSeek-V2 |
| **256K+** | 极致压缩序列长度 | Embedding 层过大，训练困难 | 少数多语言模型 |

**Token 膨胀问题**：

```
同一句话在不同词表下的 token 数:

英语: "The quick brown fox jumps over the lazy dog"
  32K 词表: 9 tokens
  128K 词表: 9 tokens (英语已充分覆盖)

中文: "敏捷的棕色狐狸跳过了懒狗"
  32K 词表: ~20 tokens (中文字符被拆成多个 byte-level token)
  128K 词表: ~8 tokens (中文词汇被充分覆盖)

韩语: "빠른 갈색 여우가 게으른 개를 뛰어넘었다"
  32K 词表: ~40 tokens
  128K 词表: ~12 tokens

→ 词表太小导致非英语文本 token 膨胀 2-5×
→ 同等计算量下，非英语性能显著下降
```

### 7.4 Chat Template 设计

```
Llama-3 Chat Template:
<|begin_of_text|>
<|start_header_id|>system<|end_header_id|>
You are a helpful assistant.
<|eot_id|>
<|start_header_id|>user<|end_header_id|>
What is the capital of France?
<|eot_id|>
<|start_header_id|>assistant<|end_header_id|>
The capital of France is Paris.
<|eot_id|>

关键设计:
- 特殊 token 不在常规文本中出现
- loss 只在 assistant 部分计算
- 支持多轮对话 (交替 user/assistant)
- system prompt 可选
```

---

## 合成数据生成

### 8.1 合成数据的价值

> 2024-2025 年最重要的数据趋势：互联网数据接近枯竭，合成数据成为突破瓶颈的关键。

| 优势 | 说明 |
|------|------|
| **无限扩展** | 不受互联网数据量限制 |
| **质量控制** | 可以精确控制难度、格式、领域 |
| **隐私安全** | 不包含真实用户数据 |
| **针对性** | 可以针对模型弱点生成训练数据 |

### 8.2 主要方法

#### Self-Instruct

```
Self-Instruct 流程:

1. 种子任务池: 175 个人工编写的 (instruction, input, output) 样本
2. 迭代生成:
   a. 从任务池随机采样 8 个作为 few-shot 示例
   b. 让 LLM 生成新的 instruction
   c. 分类: 分类任务 or 生成任务
   d. 让 LLM 生成对应的 input/output
   e. 过滤低质量样本
   f. 加入任务池
3. 输出: 52K 指令数据 (Alpaca 数据集)
```

#### Evol-Instruct

```
Evol-Instruct (WizardLM):

深度进化 (In-Depth Evolving):
  原始: "What is machine learning?"
  → 增加约束: "Explain machine learning to a 10-year-old"
  → 增加推理: "Explain why machine learning is important for modern AI"
  → 增加复杂度: "Compare and contrast supervised, unsupervised, 
                  and reinforcement learning with concrete examples"

广度进化 (In-Breadth Evolving):
  原始: "What is machine learning?"
  → 生成相关但不同的主题:
    "What is deep learning?"
    "What is the difference between AI and ML?"
    "How does a neural network work?"
```

#### Phi-4 合成策略

```
Phi-4 的多智能体合成 Pipeline:

1. 种子数据收集: 网页、书籍、代码、学术论文
2. 多智能体生成:
   - Generator Agent: 生成初始内容
   - Critic Agent: 评估质量、指出问题
   - Refiner Agent: 根据反馈改进
3. 验证与过滤:
   - 事实性验证 (用搜索引擎/知识库)
   - 代码可执行性验证
   - 数学答案正确性验证
4. 去污染: 确保不与 benchmark 重叠
```

### 8.3 合成数据的风险

| 风险 | 描述 | 缓解措施 |
|------|------|---------|
| **模型坍塌 (Model Collapse)** | 用模型生成的数据训练模型，导致多样性丧失 | 混合真实数据，控制合成数据比例 |
| **幻觉放大** | 合成数据中的错误被模型学习 | 事实性验证，多源交叉校验 |
| **风格单一化** | 合成数据的写作风格趋同 | 多样化 prompt，多模型生成 |
| **Benchmark 污染** | 合成数据无意中包含 benchmark 内容 | 严格的去污染检测 |

---

## 数据去污染

### 9.1 为什么需要去污染

训练数据与 benchmark 的重叠会导致虚假的高分，无法反映模型真实能力。

```
去污染检测方法:

1. N-gram 重叠检测
   - 将 benchmark 样本切分为 13-gram
   - 检查训练数据中是否存在相同的 13-gram
   - 如果重叠比例 > 阈值 (如 80%)，移除该训练样本

2. 最长公共子串 (LCS)
   - 计算训练样本与 benchmark 样本的 LCS
   - 如果 LCS 长度 / 样本长度 > 阈值，移除

3. 语义相似度
   - 用 embedding 模型计算相似度
   - 过滤高相似度的训练样本
```

### 9.2 常见 Benchmark 去污染

| Benchmark | 去污染策略 |
|-----------|-----------|
| **MMLU** | 移除与 MMLU 问题有 13-gram 重叠的训练样本 |
| **HumanEval** | 移除包含相同函数签名和描述的代码 |
| **GSM8K** | 移除包含相同数值和问题结构的数学题 |
| **HellaSwag** | 移除包含相同上下文和选项的样本 |

---

## 工程实践与工具链

### 10.1 推荐工具栈

| 环节 | 工具 | 特点 |
|------|------|------|
| **HTML 解析** | trafilatura, readability-lxml | 专业的 boilerplate 去除 |
| **语言检测** | fasttext-langdetect, lingua-py | 快速准确 |
| **文本处理** | datatrove (HuggingFace) | 大规模文本处理 pipeline |
| **去重** | text-dedup, datasketch | MinHash + LSH 实现 |
| **质量过滤** | KenLM, fasttext | perplexity + 分类器 |
| **Tokenizer 训练** | tokenizers (HuggingFace), sentencepiece | 工业级实现 |
| **数据存储** | Apache Parquet, WebDataset | 高效的列式存储 |
| **Pipeline 编排** | Apache Beam, Ray, Spark | 大规模分布式处理 |

### 10.2 典型数据工程 Pipeline (以 FineWeb 为例)

```
FineWeb (HuggingFace) 的处理流程:

1. 下载 Common Crawl WARC 文件 (90+ snapshots)
2. trafilatura 提取正文
3. FastText 语言检测 (保留英语, 置信度 > 0.65)
4. 基础规则过滤 (长度, 重复, 特殊字符)
5. MinHash + LSH 文档级去重 (K=112, B=14, R=8)
6. KenLM perplexity 质量过滤
7. 个人身份信息 (PII) 检测与移除
8. 输出: ~36T tokens 高质量英语文本

处理规模:
- 输入: ~500TB 原始 WARC
- 输出: ~36T tokens (~72TB 文本)
- 处理时间: ~数天 (数百台机器)
- 保留率: ~7%
```

### 10.3 数据版本管理

```
数据版本管理最佳实践:

1. 记录每次过滤的保留率
2. 保存过滤日志 (哪些文档被过滤、原因)
3. 使用内容哈希标识数据集版本
4. 支持数据溯源 (每个训练样本可追溯到原始 URL)

示例 metadata:
{
  "text_hash": "sha256:abc123...",
  "source_url": "https://example.com/article",
  "crawl_timestamp": "2024-01-15",
  "language": "en",
  "quality_score": 0.85,
  "dedup_cluster_id": "cluster_42",
  "processing_pipeline": "fineweb-v1.2"
}
```

---

> **关键原则**：
> 1. **数据质量 > 数据数量**：1T 高质量 tokens 胜过 10T 低质量 tokens
> 2. **多样性是生命线**：避免数据来源单一化，覆盖多领域、多风格、多语言
> 3. **去重是性价比最高的优化**：30-60% 的数据是重复的，去重等于免费获得更多有效数据
> 4. **Tokenizer 是隐形的性能杀手**：词表太小导致非英语性能崩溃，词表太大浪费计算
> 5. **合成数据是未来但不是银弹**：需要严格的验证和过滤机制

---

## 合成数据方法论

2024-2025 年，头部模型团队将合成数据工程化推向新高度。本节聚焦 Phi-4、GLM-5、Step3.5-Flash 三家的核心方法论。

### 11.1 Phi-4 合成数据体系

Phi-4 构建了迄今最系统的合成数据工程，涵盖 **50 种合成数据集类型**，总规模约 **400B tokens**。其核心围绕四个设计原则：

| 原则 | 含义 | 实现方式 |
|------|------|---------|
| **Diversity (多样性)** | 覆盖广泛的主题、格式、难度 | 多源种子 + 模板式 Prompt 多样化 |
| **Nuance (细粒度)** | 避免模糊或过于简单的问题 | Seed Curation 中引入难度投票筛选 |
| **Accuracy (准确性)** | 合成内容必须事实正确 | Self-Revision + 多轮交叉校验 |
| **Chain-of-Thought (思维链)** | 推理过程显式化、可验证 | 强制要求逐步推理输出 |

#### Seed Curation（种子筛选）

Phi-4 使用两阶段筛选确保种子数据有足够难度：

```
阶段 1 — 粗筛:
  从 Web/书籍/代码/论文中提取原始段落
  → 启发式规则过滤 (长度、格式、毒性)
  → 分类器质量评分
  → 保留 Top-60% 文档

阶段 2 — 精筛 + 难度投票:
  对每个候选段落, LLM 生成 3-5 个相关问题
  → 用弱模型 (如 Phi-3-mini) 逐题作答
  → Majority Voting: ≥3 个弱模型答对 → 标记 "简单", 丢弃
  → 弱模型答错 → 标记 "困难", 保留作为种子
  → 目的: 确保种子数据有足够的信息增量, 值得对强模型合成
```

#### Rewrite & Augment 工作流

种子数据经过四类核心操作扩充：

| 操作 | 描述 | 示例 |
|------|------|------|
| **Rewrite** | 改变表述风格、调整复杂度 | 学术段落 → 教科书风格 |
| **Expand** | 补充背景、推导细节、相关概念 | "E=mc²" → 含历史背景和推导的完整讲解 |
| **Fragment** | 长文本拆分为多个独立 QA | 一篇综述 → 20 个独立问答对 |
| **Combine** | 分散信息整合为综合问题 | 两篇相关论文 → 对比分析题 |

#### Self-Revision 机制

```
Self-Revision 循环 (每样本 2-3 轮):

  Generate (生成初稿)
      │
      ▼
  Self-Criticize (自我批评)
      检查维度:
      - 事实准确性 (搜索引擎交叉验证)
      - 推理逻辑完整性
      - 教学清晰度
      - 代码可执行性 (如含代码)
      │
      ▼
  Improve (根据反馈改进)
      │
      └── 循环直至质量收敛 ──┘
```

#### Instruction Reversal（指令反转）

```
代码 → 指令的反向生成:

  原始代码片段
      │
      ▼
  "识别这段代码实现的功能, 生成一条编程指令"
      │
      ▼
  指令: "Write a function that computes the nth Fibonacci number
         recursively, handling base cases n=0 and n=1."

  → 生成变体:
    "Implement an iterative Fibonacci and explain why it's
     more efficient than recursion."

  价值: 代码易得, 高质量指令稀缺 → 反向生成多样化编程指令
        Phi-4 中该类数据占合成数据的 ~15%
```

### 11.2 GLM-5 合成数据方案

**Web Corpus → Terminal Tasks**：

GLM-5 提出将互联网教程/文档转化为可自动验证的终端任务，通过闭环自验证 Pipeline 生成训练数据：

```
闭环自验证 Pipeline:

  Web 语料 (教程/文档/博客)
      │
      ▼
  Step 1: LLM 提取操作步骤
      │
      ▼
  Step 2: LLM 转换为终端命令序列
      │
      ▼
  Step 3: 沙箱执行
      │
      ├── ✓ 成功 → 保留 (指令, 命令, 输出)
      │
      └── ✗ 失败 → Agent 修正 (最多 3 轮)
                      │
                      ▼
              Step 4: Agent 作为首轮评估器
                独立 Agent 验证: 命令合理? 输出正确?
                → 通过则加入训练集
```

Agent 作为首轮评估器的设计要点：生成者和评估者使用不同模型避免自我偏好，评估者拥有搜索和代码执行工具，评估维度包括可复现性、安全性、效率。

**知识图谱拓扑驱动的多跳问答**：

GLM-5 利用知识图谱的拓扑结构控制问答的推理深度：

| 推理深度 | 示例 | 生成方式 |
|---------|------|---------|
| **1-hop** | "法国的首都是什么?" → 巴黎 | 直接查询 KG 单条边 |
| **2-hop** | "流经法国首都的河流?" → 巴黎 → 塞纳河 | KG 两条边路径采样 |
| **3-hop** | "流经法国首都的河流最终汇入哪个海域?" → 巴黎 → 塞纳河 → 英吉利海峡 | 多路径聚合验证 |
| **4-5 hop** | 需要 4-5 步推理链的复合问答 | 引入干扰路径增加难度 |

关键设计：拓扑距离控制难度，多路径聚合作为答案验证（不同路径应到达同一实体），引入干扰实体增加干扰项。

### 11.3 Step3.5-Flash 工具使用数据生成

Step3.5-Flash 提出 **Execution-driven Tool-use Data Generation**，核心思路是用 FSM 建模原子意图，通过采样-执行-验证闭环生成高质量工具调用数据。

**FSM 建模原子意图**：

```
有限状态机定义合法意图转移:

  状态: Idle, Searching, Reading, Executing, Verifying
  转移: Idle → Search/Execute
        Searching → Reading
        Reading → Verifying/Execute
        Executing → Verifying
        Verifying → Idle/Search

  每个状态对应一类原子意图, 转移定义了合法序列
```

**Sample → Execute → Verify 闭环**：

```
1. Sample (采样)
   从 FSM 中采样意图序列 + 从种子库采样上下文
   → 生成 (instruction, planned_tool_calls)

2. Execute (执行)
   沙箱中实际执行工具调用
   → 获得真实 tool outputs

3. Verify (验证)
   检查执行结果是否达成原始意图:
   - Search: 搜索结果相关?
   - Code: 代码成功运行? 输出正确?
   - Read: 提取的信息准确?
   ✓ 通过 → 保留完整轨迹
   ✗ 失败 → 丢弃或修正后重试

累计生成 100K+ 高质量工具使用轨迹
```

**PR/Issue/Commit → PR-Dialogue (90B tokens)**：

从开源社区记录中构建大规模代码对话数据：

| 数据源 | 处理方式 | 产出 |
|--------|---------|------|
| **PR 描述 + Diff** | PR 描述作为 issue → Diff + Review 作为解决方案 | 代码修复对话 |
| **Issue + 关联 PR** | Issue 作为需求 → PR 作为实现过程 | 需求驱动编程对话 |
| **Commit Message + Diff** | Commit Message 作为目标 → Diff 对应实现 | 代码变更解释 |
| **Code Review Comment** | Review 评论 + 代码上下文 | 代码审查反馈 |

最终产出约 90B tokens 的 PR-Dialogue，涵盖真实软件开发中的完整协作流程。

---

## 知识重述 (Knowledge Rephrasing)

Kimi-K2 提出知识重述技术：在不改变语义的前提下，通过改变表达方式增加数据多样性。实践证明这比"原始数据多 epoch"更有效。

### 12.1 分块自回归重述

```
分块自回归重述 (Chunked Autoregressive Rephrasing):

  原始文档 (4096 tokens)
      │
      ▼ 切分为 16 块, 每块 256 tokens
      │
      ├── Chunk 1 → LLM 改写 (风格 A)
      ├── Chunk 2 → LLM 改写 (风格 B, 参考 Chunk 1 重述结果)
      ├── Chunk 3 → LLM 改写 (风格 C, 参考 Chunk 1-2 重述结果)
      └── ...
      │
      ▼ 拼接 + 保真度验证
  重述后文档
```

关键设计：块大小 256 tokens 在连贯性和多样性间取得平衡，自回归方式保证上下文衔接，每块独立指定改写风格引入多样性。

**风格 Prompt 设计**：

| 风格维度 | Prompt 描述 |
|---------|------------|
| **教科书风格** | 结构化、逐步解释，含标题层级 |
| **对话风格** | 口语化，师生问答形式 |
| **笔记风格** | 要点式、精简，含关键公式和记忆技巧 |
| **叙事风格** | 将知识融入故事或历史背景叙述 |

**保真度验证 (Fidelity Verification)**：

重述后文本需通过保真度检查。先用 NER + 关系抽取提取原文关键事实，再检查改写文本是否全部保留。定义：

$$\text{Fidelity} = \frac{|\text{关键事实在改写前后均出现}|}{|\text{原文中的关键事实总数}|}$$

Fidelity < 0.95 的重述结果丢弃。

### 12.2 实验验证

**SimpleQA 实验**：

| 训练策略 | SimpleQA 准确率 | 说明 |
|---------|---------------|------|
| 原始数据 × 1 epoch | ~20.15 | 基线 |
| 原始数据 × 10 epoch | ~23.76 | 简单重复训练，收益递减 |
| 原始 + 10× 重述 (各不同风格) | **~28.94** | 同等数据量，效果显著提升 |

关键结论：重复训练同一表述的收益迅速递减，而知识重述让模型从不同角度、不同语境理解同一知识，在同等计算量下效果远超简单重复。

**数学重述实验**：

将数学题重述为"学习笔记"风格——包含关键概念回顾、逐步推导、常见陷阱提示。笔记风格的数学数据帮助模型不仅学会解题，更学会了教学和解释，在 reasoning 任务上提升显著。

**跨语言数学翻译**：将英文数学数据翻译为中文、日文等版本。关键原则：数学公式和 LaTeX 符号不做翻译（保持原样），自然语言部分完整翻译，翻译后由数学验证器检查解题步骤一致性。

---

## 多智能体数据生成

### 13.1 Phi-4 AgentKit 框架

Phi-4 使用 AgentKit 生成长程推理数据，让多个 Agent 协作模拟复杂推理过程。

**长程推理模式**：

```
AgentKit 推理四阶段循环:

  Planning (规划) → Execution (执行) → Reflection (反思) → Correction (修正)
       ↑                                                       │
       └─────────────────── 循环直至收敛 ───────────────────────┘

  各阶段职责:
  - Planning:  分解复杂问题为子任务序列
  - Execution: 逐步解决每个子任务, 记录中间结果
  - Reflection: 审视已完成步骤, 发现矛盾/错误/遗漏
  - Correction: 根据反思结果回溯修正之前的推理
```

**对话数据 Incremental Generation**：

```
递增复杂度对话生成:

  Step 1: 从知识库采样 N 个相关事实片段

  Step 2: 多智能体对话轮次生成
    Agent A (提问者): 基于事实片段提出自然追问
    Agent B (回答者): 基于同一组事实作答
    Agent C (验证者): 核对对话中是否出现事实错误

  Step 3: Self-Correction
    验证者发现错误 → 回答者修正 → 重新验证

  Step 4: Incrementally Complex
    从 3 个事实片段的简单对话开始
    → 逐步增加事实片段至 5-8 个
    → 最终产出需要综合多步推理的复杂对话
```

### 13.2 Kimi K2.5 Agent Swarm

Kimi K2.5 的 Agent Swarm 方法让多个 Agent 自然并行协作，无需中央协调器显式分配任务，分解是自然发生的。

**Wide Search + Deep Search**：

```
Agent Swarm 双阶段搜索:

  Wide Search (广度):
    提出复杂问题 → N 个 Agent 从不同角度独立搜索
    → 收集所有初步发现

  Deep Search (深度):
    对 Wide Search 中的每个有价值线索
    → 分配专门 Agent 深入追踪引用链/验证数据来源/对比矛盾
    → 交叉验证 + 综合 → 最终答案

  关键: 并行分解自然发生, 产生的轨迹本身就是高质量多步推理训练数据
```

**7 类视觉数据**：

Kimi K2.5 Agent Swarm 同时生成 7 类视觉-语言训练数据：

| 类别 | 描述 | 生成方式 |
|------|------|---------|
| **图表解读** | 提取图表数据和趋势 | Agent 搜索图表 → 多轮问答 |
| **UI 理解** | 界面元素和交互 | 截图 → Agent 标注 + 问答 |
| **文档解析** | PDF/扫描件结构化 | OCR → Agent 验证 + 纠错 |
| **科学图表** | 分子结构、电路图 | 专业数据库 → 生成描述 |
| **地图推理** | 空间关系和导航 | 地图 API → Agent 轨迹 |
| **代码截图** | 截图还原代码 | 合成截图 → OCR → Agent 验证 |
| **多图对比** | 跨图片比较推理 | Agent 搜索多图 → 对比分析 |

---

## Data Experiment 范式

MiniMax-01 提出系统化的数据实验方法论，将数据工程从"经验驱动"升级为"统计检验驱动"。

### 14.1 统计假设检验框架

核心思想：每次数据策略变更，都作为一次统计假设检验来设计。

```
标准实验流程:

  1. 提出假设:
     H₀: 新数据策略不优于当前策略
     H₁: 新数据策略优于当前策略

  2. 选择指标: log_acc_norm2 (主) + benchmark score (辅)

  3. Power Analysis 计算最小样本量:
     - α = 0.05 (95% 置信)
     - 1-β = 0.80 (80% 统计功效)
     - 估计效应量 δ → 计算 N_min

  4. 执行实验: 控制变量, 不同随机种子 × 3

  5. 统计推断:
     p < 0.05 → 拒绝 H₀, 新策略显著更好
     p ≥ 0.05 → 不能拒绝 H₀
```

### 14.2 关键指标与框架

**log_acc_norm2 指标**：

$$\text{log\_acc\_norm2} = \frac{\log(\text{accuracy})}{\|\text{bytes}\|_2}$$

其中 $\|\text{bytes}\|_2$ 是输出答案的字节长度的 L2 范数。

| 特性 | 说明 |
|------|------|
| **Byte-normalized** | 以字节归一化，消除不同 tokenizer 的影响，不同词表大小不干扰对比 |
| **Tokenizer-agnostic** | 不依赖任何特定 tokenizer，可在不同模型间公平对比 |
| **对数变换** | $\log$ 使准确率差异更线性，避免天花板效应 |
| **L2 归一化** | 惩罚冗长输出，避免模型通过堆砌 token 刷分 |

**Power Analysis**：

Power Analysis 确定实验所需最小样本量，避免样本不足导致假阴性或样本过多浪费计算。以两样本 t 检验为例，若预期准确率提升 $\delta = 0.02$、标准差 $\sigma \approx 0.15$，则 $\alpha = 0.05, 1-\beta = 0.80$ 下 $N_{\text{min}} \approx 500$。

**Repetition-Aware 框架**：

联合优化去重与重复训练的框架：

```
Step 1: 全局模糊去重 (MinHash + LSH)
Step 2: 按重复频率分层
  Tier 1 (低频, 1-3 次): 高质量, ≤ 4 epochs
  Tier 2 (中频, 4-7 次): 中等质量, ≤ 3 epochs
  Tier 3 (高频, 8+ 次): 低质量模板, < 2 epochs

核心原则: 先去重, 再按层配比 epoch 数;
         避免 "高质量数据训练不足 + 低质量模板数据过度训练"
```

---

## PDF OCR 与质量标注

Qwen3 提出了从 PDF 文档通过 OCR + 质量精炼提取训练数据的完整 Pipeline，并建立了 30T tokens 级别的大规模多维标注体系。

### 15.1 OCR + 质量精炼 Pipeline

```
PDF 数据提取流程:

  PDF 文档
      │
      ▼
  Qwen2.5-VL OCR (视觉语言模型做 OCR)
  - 识别正文/标题/页眉/页脚
  - 识别表格结构 (行列关系和单元格内容)
  - 识别公式 (LaTeX 格式输出)
  - 识别图表 (自然语言描述)
      │
      ▼
  Qwen2.5 质量精炼 (Refine)
  - 纠正 OCR 错误 (尤其专业术语)
  - 格式化: 统一段落、修复断行
  - 结构化: 提取章节层级
  - 质量评分
      │
      ▼
  质量过滤 → 高质量训练数据 (数万亿额外 tokens)
```

关键技术决策：

| 决策 | 理由 |
|------|------|
| VL 模型替代传统 OCR 引擎 | VL 理解页面布局，能区分正文/边栏/表格/页脚 |
| OCR 与 Refine 分离 | 两阶段独立优化：OCR 专注识别，Refine 专注质量 |
| 公式保留 LaTeX 格式 | 避免公式在后续处理中损坏，保留精确数学语义 |

### 15.2 多维标注体系

Qwen3 对 **30T tokens** 训练数据进行实例级多维标注，每条数据携带 3+ 个标签：

| 标注维度 | 层级 | 用途 |
|---------|------|------|
| **教育价值 (Educational Value)** | L1 基础 → L5 前沿研究 (5 级) | 控制训练各阶段的知识深度配比 |
| **领域 (Domain)** | 数学/物理/CS/医学/法律/金融等 50+ 类 | 领域配比控制和定向能力提升 |
| **安全等级 (Safety)** | S0 完全安全 → S3 不安全 (4 级) | 安全过滤和对齐训练 |

标注方式：Qwen2.5 作为标注器，每个维度独立多标签分类，随机抽样 1% 人工复核，一致性 > 95% 视为可靠。

**基于标注的分阶段数据配比**：

| 训练阶段 | 教育价值侧重 | 说明 |
|---------|-------------|------|
| **预训练早期** | L1-L2 为主 (~70%) | 打好知识基础 |
| **预训练中期** | L1-L3 均衡 | 逐步引入分析推理 |
| **预训练后期 + 退火** | L3-L5 为主 (~60%) | 重点提升推理和前沿知识 |
| **SFT** | L2-L4 为主 | 以应用和分析为主 |

这种基于标注的精细化配比，使 Qwen3 在不同训练阶段使用最适合的数据，避免了传统"一刀切"配比的局限性。

---

## 参考资料

- [FineWeb](https://huggingface.co/datasets/HuggingFaceFW/fineweb) — 大规模网页数据清洗 Pipeline 参考
- [Dolma](https://allenai.org/dolma) — OLMo 的开放数据集和清洗工具链
- [RefinedWeb](https://huggingface.co/datasets/tiiuae/falcon-refinedweb) — Falcon 模型的去重策略参考
- [Phi-4 Technical Report](https://arxiv.org/abs/2412.08905) — 合成数据方法论参考
- [DoReMi](https://arxiv.org/abs/2305.10429) — 自动数据配比搜索

> **下一篇**：[Transformer 完整结构与训练算法](./02-Transformer完整结构与训练算法.md) — 从数据工程走向模型架构
