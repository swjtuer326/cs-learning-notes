# Kimi K3 混合注意力:KDA 与 Gated MLA

承接 [01 架构全貌](./01-architecture-full-picture.md) 的 sequence 轴。K3 沿序列维的信息流由两种注意力混合承担:KDA 用一个固定大小的循环状态换掉随序列增长的 KV cache,Gated MLA 把每 token 的 key/value 压成低维 latent 再缓存。

**"KDA 固定大小状态 vs MLA 逐 token 压缩 KV"这一根本差异,决定后面 KV Cache 显存账、前缀缓存设计与 decode kernel 的全部分野**(05/09 篇)。

机制细节出自报告 §2.1 与 Kimi Linear 论文,部署影响出自报告 §5.1、§5.4,正文引用统一写"报告 §X.X"或"Kimi Linear 论文"。

## 1. 为什么混合:长程便宜与全局精确回溯的折中

K3 的注意力层不是单一机制:每 block 3 个 KDA + 1 个 Gated MLA,3:1 交错,backbone 末尾再放 1 个 MLA(报告 §2.1)。这个比例是对两个极端各自代价的折中——全 KDA 只有压缩状态、缺全局精确回溯;全 MLA 在 1M 上下文下 KV 缓存不可承受。

两个极端为什么都不行,是后面一切部署账本的前提:

- **全 KDA 不可行于回溯**。KDA 是 delta-rule 循环(§2),每 token 只往一个固定大小的状态里写,读时用 query 与状态点积。状态大小固定意味着信息是有损压缩,无法精确 recall 远处某个具体 token;需要全局 token-to-token 交互的场景(长文档精确引用、细粒度检索)能力不足,所以要在层间保留全局注意力层(报告 §2、§5.1)。
- **全 MLA 不可行于显存**。MLA 虽压缩每 token 的 KV,缓存仍随序列线性增长(报告 §5.4.1);1M token 下即使单层压得很小,乘上层数与序列长也不可承受。此外 MLA 是全局 softmax 注意力,逐 token 全量点积在长序列下计算同样昂贵。

3:1 因此是"长程便宜 + 全局精确回溯"的折中:MLA 每 4 层设一个全局回溯锚点,锚点之间的 3 层 KDA 提供便宜的长程混合(报告 §2.1)。

按表 1 推算,69 KDA ÷ 3 = 23 block,23 × (3 KDA + 1 MLA) = 92 层,加末尾 1 个 MLA 正好 93 = 69 KDA + 24 MLA。这个分组落到部署端:24 个 MLA 层是 KV 显存大头,69 个 KDA 层近似 O(1),是 05 篇账本的分母。

## 2. KDA:固定大小状态的 delta-rule 递推

KDA 把 softmax 注意力"随序列增长的 key–value cache"换成一个固定大小的循环状态 $S \in \mathbb{R}^{d_k \times d_v}$(报告 §5.1)。每个 head 一份状态,每 token 先衰减再写入,输出时用 query 从状态读。

**状态不随序列增长**是 KV 缓存近似 O(1) 的根;但递推是串行的,部署端要把串行递推摊平成并行。

### 2.1 delta-rule 递推与固定状态

单头情形下,状态更新与输出为(报告 §2.1.1):

$$S_t = \left(I - \beta_t k_t k_t^\top\right)\operatorname{Diag}(\alpha_t)\,S_{t-1} + \beta_t k_t v_t^\top, \qquad \tilde{o}_t = S_t^\top q_t$$

式中三个量各管一件事:

| 量 | 取值范围 | 作用 |
| --- | --- | --- |
| $\alpha_t \in (0,1)^{d_k}$ | channel-wise 一步保留因子 | 对状态每个 key 通道独立衰减(遗忘门) |
| $\beta_t \in (0,1)$ | 标量写强度 | 控制 $k_t v_t^\top$ 写入状态的力度 |
| $S_t \in \mathbb{R}^{d_k \times d_v}$ | 固定大小状态 | 压缩历史,输出 $\tilde{o}_t = S_t^\top q_t$ |

"delta-rule"指增量更新:每步先对旧状态做通道衰减,再写入一个外积增量 $k_t v_t^\top$,等价于对衰减后的状态做一次梯度下降式更新(Kimi Linear 论文)。

$q_t, k_t \in \mathbb{R}^{d_k}$、$v_t \in \mathbb{R}^{d_v}$ 由 $x_t$ 经 ShortConv + Swish 投影得到,q/k 再经 L2 Norm(报告 §2.1.1)。

关键性质:**不管序列多长,$S_t$ 始终是 $d_k \times d_v$**,不随 token 数增长。这是后面 05 篇说"KDA 层缓存近似 O(1)"的机制来源。

### 2.2 chunkwise 并行形式与结果修正

上式每步依赖上一步,GPU 上纯串行不可行;训练与 prefill 用 chunkwise 形式——chunk 内并行、chunk 间串行(报告 §2.1.1)。一个 chunk 内所有位置的输出一次算出,拆成两项:

$$O_{[t]} = \underbrace{(\Gamma_{[t]}^{1\to C} \odot Q_{[t]})\,S_{[t]}}_{\text{inter-chunk}} + \underbrace{A_{[t]}\,\tilde{V}_{[t]}}_{\text{intra-chunk}}$$

- inter-chunk 项:来自上一个 chunk 进入的状态 $S_{[t]}$ 的贡献,携带 chunk 之前的上下文;
- intra-chunk 项:chunk 内 token 间交互,用因果下三角掩码 $A_{[t]} = \operatorname{Tril}(\cdots)$ 保证只读已见 token;
- 结果修正:key 按累计衰减的倒数 $1/\Gamma^{1\to C}$ 重标定,把"逐 token 衰减"折算成"相对当前 chunk 头的衰减"。

这个修正正是 §2.3 的入口:累计衰减 $\Gamma$ 是 $(0,1)$ 内保留因子的连乘,其倒数会无界增大。

### 2.3 lower-bounded decay:为什么需要下界

Kimi Linear 对每步 log-decay 用无界的 negative-Softplus 映射 $g = -e^{A}\operatorname{Softplus}(z) \in (-\infty, 0)$;K3 改成 scaled sigmoid,把 log-decay 压到固定下界 $g_{\min} = -5$(报告 §2.1.1):

$$g_t^h = g_{\min}\,\operatorname{Sigmoid}\!\left(e^{A_h} z_t^h\right) \in (g_{\min},\,0)^{d_k}, \qquad \alpha_t^h = \exp(g_t^h) \in (e^{g_{\min}},\,1)^{d_k}$$

下界要解决的正是 §2.2 末尾的数值问题:key 除以累计衰减倒数 $1/\Gamma$,而 $\Gamma$ 是 $(0,1)$ 保留因子连乘,倒数可无界增大,在有限精度下溢出(报告 §2.1.1)。

$g_{\min} = -5$ 时每个保留因子 $\alpha > e^{-5} \approx 6.7\times 10^{-3}$,16-token tile 的累计 log-decay 落在 $(-80, 0)$,倒数 $< e^{80}$,仍在 BF16 动态范围内(报告 §2.1.1)。

下界带来的不只是数值稳定,而是一个 kernel 结构变化:Kimi Linear 里对角线 tile 因因果约束需显式 position-pair 计算、是 chunk 内瓶颈;K3 的有界范围让全部因果 tile 都能走密集 Tensor Core 矩阵乘,position-pair 路径整个被消除(报告 §2.1.1)。这是 09 篇 KDA kernel 的前提。

![KDA lower-bounded decay 及其对 chunkwise 计算的影响:(a) log-decay 参数化——Kimi Linear 无界 negative-Softplus 对比 K3 有界 scaled sigmoid(g_min = −5);(b) 对角线 tile 计算——K3 的有界范围使全部因果 tile 可用密集 Tensor Core 矩阵乘(源:报告 Fig.3)](./images/k3-fig3-kda-chunkwise.png)

*来源:Kimi K3 技术报告 Figure 3*

Fig.3 两幅并列:(a) 两种 log-decay 参数化的曲线,Kimi Linear 无界下降、K3 被 $g_{\min} = -5$ 限制后趋平不触零;(b) 对角线 tile 的两种计算方式(报告 Fig.3 图注)。

### 2.4 full-rank gate 与 NoPE 定位

K3 把 KDA 的输出门从 Kimi Linear 的低秩参数化改成输入依赖的 full-rank 投影(报告 §2.1.1):

$$y_t = W_o\left[\operatorname{Sigmoid}(W_g x_t) \odot \operatorname{RMSNorm}(\tilde{o}_t)\right]$$

先对循环输出 $\tilde{o}_t$ 做 head-wise RMSNorm,再用 $\operatorname{Sigmoid}(W_g x_t)$ 逐通道门控调制。低秩门表达能力受限,full-rank 让每个 token 独立决定从状态里读出哪些通道(报告 §2.1.1)。

位置信息由 KDA 的 channel-wise decay 天然编码:越近的 token 衰减越少,近因信息内嵌在状态里。这正是 §3 里 MLA 可以省掉 RoPE 的原因——KDA 层承担位置/近因敏感,MLA 层只做无约束的全局内容交互(报告 §2.1.2)。

### 2.5 部署含义

KDA 的三个部署性质直接决定后面的账本与 kernel:

1. **KV 近似 O(1)**。状态固定、不随序列增长(报告 §5.1)。
2. **每请求一份**。状态是整条序列一份,不是 per-token 条目,长前缀保留与复用便宜(报告 §5.4.1)。
3. **串行递推需要专用 decode kernel**。decode 时每步就地更新状态,与 GPU 的宽并行偏好相悖;且投机解码验证拒绝时,已就地更新的循环状态无法像 per-token KV 那样直接截断回退。报告 §5.4.2 的 ReplaySSM 式设计因此只缓存远小于状态的投影输入(每 token 的 $k_t$、$v_t$ 与衰减/写强度系数),验证时在片上回放重建已接受 token 的状态,配合 MTP 投机解码,细节在 09 篇。

## 3. Gated MLA:逐 token 的 latent KV 压缩

MLA 保留全局 token-to-token 注意力,但把每 token 的 key/value 压成一个低维 latent 向量 $c_t$ 再缓存,缓存随序列增长但被压缩;K3 的 MLA 用 NoPE + full-rank 门控,位置信息交给 KDA 层承担(报告 §2.1.2)。

### 3.1 latent 压缩与压缩比

MLA 不缓存每头的 key/value,而是缓存一个共享的压缩向量,注意力计算时用学到的上投影重建内容 key/value(报告 §2.1.2):

$$c_t = W_c x_t$$

- **缓存对象**:$c_t$ 一个低维向量,而非每头各一份 key + value;
- **重建时机**:计算注意力时把 $c_t$ 上投影回各头的 key/value;
- **压缩比含义**:缓存量从"每头 key 维 + value 维"降到"latent 维"。K3 的 latent KV 维度报告表 1 未给出,压缩比无法量化,见"待确认"。

MLA 由 DeepSeek-V2 提出,K2/K2.5 沿用,K3 保留在周期性全局注意力层里作为"精确回溯锚点"(报告 §2.1.2)。

### 3.2 NoPE

K3 对全部 MLA 层用 NoPE,query 与 key 不加显式位置编码(报告 §2.1.2)。位置与近因信息由穿插的 KDA 层提供,MLA 层专注无约束的全局内容交互;扩上下文时也不用重调 RoPE frequency base 或 YaRN(报告 §2.1.2)。这与 K2/K2.5 的做法不同(报告 §2.1.2)。

### 3.3 full-rank 门控

K3 给 MLA 加输入依赖、channel-wise 的 full-rank 输出门(报告 §2.1.2):

$$y_t = W_o\left[\operatorname{Sigmoid}(W_g x_t) \odot \tilde{o}_t\right]$$

参数化与 KDA 的门控一致,让每个 token 调制从全局注意力读到的通道(报告 §2.1.2)。

### 3.4 部署含义

1. **缓存随序列增长但被压缩**。1M 上下文下显存大头集中在 24 个 MLA 层(报告 §5.4.1),05 篇据此展开。
2. **每 token 一个条目,可 per-token 分页**。这是前缀缓存能到 512-token 细粒度的基础(报告 §5.4.1)。
3. **与 KDA 固定状态需联合分页**。两套 cache 性质不同,不能分别维护独立管理器,§4 展开。

## 4. 两种 KV 形态对比与部署后果

KDA 状态与 MLA latent KV 在大小、生命周期、复用粒度上根本不同,同一前缀只有把两者同时恢复到同一边界才能复用;K3 因此把两套 cache 装进统一分页池,而非各自建一个管理器(报告 §5.4.1)。

| 对比维度 | KDA 循环状态 | MLA latent KV |
| --- | --- | --- |
| 状态大小 | 固定 $d_k \times d_v$(per head) | 每 token 一个 latent 向量 |
| 随序列增长 | 否 | 是(被 latent 压缩) |
| 每请求份数 | 一份 | 每 token 一份 |
| 每步读带宽(decode) | 读固定状态,近似 O(1) | 读全部历史 KV,随序列 O(N) |
| 生命周期与复用粒度 | 整条序列一份,只在稀疏边界存 checkpoint | per-token 条目,可细粒度分页复用 |

这一差异落到部署端是三条结论:

1. **KV 显存**。KDA 层近似 O(1) 不随序列涨,MLA 层随序列涨但被 latent 压缩——1M 上下文下显存大头在 24 个 MLA 层(报告 §5.4.1)。
2. **前缀缓存要同时恢复两态到同一边界**。KDA 状态固定、只在稀疏边界存 checkpoint;MLA KV 是 per-token 条目。复用前缀时,命中边界必须同时有 MLA 块与 KDA checkpoint 才有效(报告 §5.4.1)。
3. **prefill/decode 分离时的传输重排**。两节点 TP 度不同时,re-layout 在传输路径上完成,零 GPU 侧 shuffle;页内状态按头连续存放,每头字节流自包含,是最小跨节点传输单元(报告 §5.4.1)。

统一分页池的具体布局(6144-token 物理块内嵌 12 个 512-token 哈希块、KDA checkpoint 稀疏落点)与两阶段查找,在 05 篇展开(报告 §5.4.1)。

## 待确认

- **MLA 的 latent KV 维度**:报告表 1 未给出,K3 的 KV 压缩比无法量化。
- **KDA 每头维度 $d_k / d_v$**:报告表 1 未给出;Kimi Linear 论文设 $d_k = d_v = 128$(该文所有实验),但这是 Kimi Linear 的配置,K3 是否沿用报告未声明。
- **头划分**:hidden 7168 / 96 头 ≈ 74.67 非整数,"96 头"不直接给出 head_dim。若 KDA 取 $d_k = 128$ 且 96 头,key 侧需 $96 \times 128 = 12288$ 维,超过 hidden 7168——KDA 与 MLA 头数可能不同,报告表 1 未给出分头方式。

## 下一篇

下一篇 [03 Stable LatentMoE](./03-moe-stable-latentmoe.md) 沿 width 轴展开:896 个路由专家工作在 3584 维 latent 空间,以及 Quantile Balancing 如何在极端稀疏下稳住负载均衡。
