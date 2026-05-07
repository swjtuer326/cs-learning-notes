# LLM 数据工程：从采集到 Tokenization

> **核心命题**：数据质量决定模型上限，数据工程是 LLM 训练中最被低估的环节。Chinchilla 定律告诉我们，一个 70B 的模型需要约 1.4T tokens 的高质量训练数据——如何获取、清洗、配比这些数据，是比模型架构更影响最终效果的因素。

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

```
MinHash 原理:

1. 将文档表示为 n-gram 集合 (通常 n=5-13)
   Doc → {ngram_1, ngram_2, ..., ngram_m}

2. 用 K 个哈希函数计算每个 n-gram 的最小哈希值
   MinHash(Doc) = [min(h1(ngrams)), min(h2(ngrams)), ..., min(hK(ngrams))]

3. Jaccard 相似度 ≈ MinHash 签名中相同位置的比例
   J(D1, D2) ≈ |{i: MinHash(D1)[i] == MinHash(D2)[i]}| / K

4. LSH (Locality Sensitive Hashing) 加速:
   将 K 个签名分成 B 个 band，每个 band 有 R 行 (K = B × R)
   两个文档只要有一个 band 完全匹配，就认为是候选对
   
   参数选择:
   - K = 128-256 (签名长度)
   - B = 16-32, R = 8-16
   - 相似度阈值 s ≈ (1/B)^(1/R)
   - 例如 B=20, R=10 → s ≈ 0.74
```

**实践参数**：

| 数据集规模 | K (签名数) | B (bands) | R (rows) | 近似阈值 |
|-----------|-----------|-----------|----------|---------|
| < 1TB | 128 | 16 | 8 | ~0.71 |
| 1-10TB | 256 | 20 | 12.8 | ~0.79 |
| > 10TB | 256 | 32 | 8 | ~0.65 |

### 4.3 Suffix Array (精确子串去重)

用于去除跨文档的精确重复段落（如模板文本、转载内容）。

```
Suffix Array 去重流程:

1. 将所有文档拼接为一个超长字符串 S (用特殊分隔符隔开)
2. 构建 S 的后缀数组 (Suffix Array)
3. 计算相邻后缀的 LCP (Longest Common Prefix)
4. 标记 LCP ≥ 阈值 (如 50 tokens) 的重复区间
5. 移除重复区间 (保留第一次出现)

复杂度: O(N log N) 时间, O(N) 空间 (N = 总字符数)

实际使用:
- Dolma: 使用 Rust 实现的 suffix array, 阈值 13 tokens
- RefinedWeb: 先 MinHash 去重, 再精确行去重
- FineWeb: 使用 datasketch 库的 MinHashLSH
```

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

```
KenLM 质量过滤:

1. 在高质量文本 (如 Wikipedia) 上训练 KenLM 5-gram 模型
2. 对每个待过滤文档计算 perplexity:
   PPL = exp(-1/N × Σ log P(token_i | token_{i-4}, ..., token_{i-1}))
3. 过滤 PPL 过高或过低的文档:
   - PPL 过高 → 文本质量差 (乱码、非自然语言)
   - PPL 过低 → 文本过于简单/重复 (模板生成)

典型阈值:
- 低 PPL 阈值: 10-50 (过滤过于简单的文本)
- 高 PPL 阈值: 1000-5000 (过滤低质量文本)
```

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
