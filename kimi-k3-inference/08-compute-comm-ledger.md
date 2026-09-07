# Kimi K3 计算、显存与通信账本

承接 [07 专家并行通信](./07-expert-parallel-comm.md) 的 all-to-all 账本,本篇把 01-07 定义过的尺寸、并行切分、KV 形态折算成三本可复算的账:一次 K3 推理到底吃多少算力、多少显存、多少带宽。

结论先行:**算力账由激活参数 104.2B 主导、不被 2.78T 总参数拖住;显存账的大头在权重桶,量化后落到 KV 与 AttnRes 块表示上;通信账里 EP 的 all-to-all 与 KCP 的固定 all-gather 分别对应高频与长上下文的两种取舍**。

三本账共用表 1 的同一组超参,读者可逐项复算。

数字纪律沿用全专题统一口径:报告表 1 可查的标"报告表 1";推算类标"按表 1 推算"并给公式;查不到的标 `> **待确认**`。报告未给出任何绝对 FLOPs、显存与带宽数值,本篇全部数值为公式推算,来源统一声明于每节表头,不再逐表单列出处栏。

## 1. 算力账:FLOPs 由激活参数而非总参数决定

本节回答"一次前向算多少乘加"。口径用标准"每参数每 token 一次乘加 = 2 FLOPs";计算量分两笔:权重绑定 FLOPs(每 token 触碰的权重做一次乘加)与注意力交互 FLOPs(token 与 token 之间的点积,随上下文长度 $L$ 增长)。稀疏化的结论落在第一笔:它与激活参数 104.2B 成正比,与总参数 2.78T 无关。

### 1.1 口径与稀疏化主结论

每 token 权重绑定 FLOPs 只数"被激活的那部分权重":

$$F_{\text{weight}} = 2 \times \text{激活参数} = 2 \times 104.2\times10^9 \approx 2.08\times10^{11}\ \text{FLOPs/token} \approx 208\ \text{GFLOPs}$$

104.2B 是报告表 1 的每 token 激活参数,即单 token 实际经过的权重(16 个路由专家、2 个共享专家、注意力投影等)之和。

若把 2.78T 全部激活(dense 化),这笔账放大到 $2\times2.78\times10^{12}\approx5.56\times10^{12}$,约 27 倍。

**稀疏度 56 把单 token 计算量钉在 104.2B 一侧,总参数盘子 2.78T 只决定显存、不决定 FLOPs**——这是"总 FLOPs 由 104.2B 激活主导、不被 2.78T 拖住"的严格含义。

### 1.2 权重绑定 FLOPs:MoE 子账

104.2B 里可被表 1 直接折算的是 MoE 路径,它是大头。每 token 每层的四项(按表 1 推算,$\ell=3584$、$h=3072$、$d=7168$、$K=16$、$N_s=2$、$E=896$):

| 组件 | 公式 | 数值(FLOPs/token/层) |
| --- | --- | --- |
| 路由专家 FFN | $K\times 6\ell h$ | $16\times6\times3584\times3072\approx1.06$ G |
| 共享专家 FFN | $N_s\times 6dh$ | $2\times6\times7168\times3072\approx0.26$ G |
| latent 投影 $W_\downarrow/W_\uparrow$ | $2\times2d\ell$ | $4\times7168\times3584\approx0.10$ G |
| 路由器 | $2dE$ | $2\times7168\times896\approx0.013$ G |
| **MoE 路径合计(每层)** | 上四项之和 | $\approx1.44$ G |

每专家 FFN 三个矩阵(gate/up/down)共 $6\ell h$ FLOPs、共享专家共 $6dh$,推导与 07 §6 一致。MoE 路径每层 1.44 G,乘 93 层约 134 GFLOPs/token,占权重绑定总账 208 G 的约 64%(按表 1 推算)。

其余约 74 G(约 36%)落在注意力投影、embedding、输出头、门控与归一化上。注意力的投影 FLOPs 由 $d$ 与 head 维度决定,而 head 维与 MLA latent 维报告表 1 未给,精确拆分不可闭合:

- **MLA 层**(24 层):投影量级约 $4d^2\approx0.21$ G/token/层(标准 q/k/v/o 口径,latent 压缩 KV 后实际略低)。
- **KDA 层**(69 层):投影量级约 $n_h\times2d(d_k+3d_v)$;取 Kimi Linear 的 $d_k=d_v=128$ 时约 0.70 G/token/层,输出投影 $W_o$ 与门控 $W_g$ 占其中大半。

两者量级之和约 53 G,加上 embedding 与输出头约 2-3 G,与 74 G 的缺口由 short conv、RMSNorm、MTP、AttnRes pseudo-query 等补足。**注意力的 head 维、latent 维、embedding 是否共享权重报告均未给出,精确组件拆分待确认;可复算的硬结论是 MoE 路径的 134 G(64%)。**

### 1.3 注意力交互 FLOPs:MLA 的二次项与 KDA 的常数项

第二笔 FLOPs 来自 token 间点积,它不随激活参数、而随上下文长度 $L$ 增长,是 prefill 与 decode 差异的来源。KDA 与 MLA 在这里分道:

- **MLA 全局 softmax 注意力**:decode 每个新 token 与全部 $L$ 个历史 key 点积,$4dL$ 每层;prefill 全体 $L$ 个 query 与 $L$ 个 key 点积,因果掩码下约 $2dL^2$ 每层。
- **KDA 线性注意力**:每 token 只读写固定状态 $S\in\mathbb{R}^{d_k\times d_v}$,交互项约 $O(d_k d_v)$ 每头,与 $L$ 无关。

$L=1\text{M}$ 时的量级(按表 1 与标准注意力公式推算):

| 阶段 | 公式(每 MLA 层) | 24 层合计($L=1\text{M}$) |
| --- | --- | --- |
| decode | $4dL$ | $24\times4\times7168\times10^6\approx6.9\times10^{11}\approx0.69$ TFLOPs/token |
| prefill | $2dL^2$(因果) | $24\times2\times7168\times10^{12}\approx3.4\times10^{17}$ FLOPs |

对照权重绑定项:1M prefill 的权重 FLOPs 为 $208\text{G}\times10^6\approx2.1\times10^{17}$。

**24 层 MLA 的二次注意力($3.4\times10^{17}$)是权重绑定项的约 1.6 倍,是 1M prefill 的真正大头。**

这正是 02 篇"全 MLA 在 1M 上下文下不可承受"在计算侧的兑现——MLA 的 latent 压缩只省 KV 显存、不省 O($L^2$) 注意力计算,长上下文的线性混合只能交给 69 层 KDA。

decode 侧 $L=1\text{M}$ 时 0.69 TFLOPs/token 的注意力读也超过 208 G 的权重项,成为 decode 的又一访存压力。

### 1.4 prefill 与 decode 汇总

| 分量 | decode(每 token) | prefill(全序列,1M) | 随 $L$ |
| --- | --- | --- | --- |
| 权重绑定 | $\approx208$ GFLOPs | $\approx2.1\times10^{17}$ | 否(×token 数) |
| MLA 注意力交互 | $\approx0.69$ TFLOPs(1M) | $\approx3.4\times10^{17}$ | decode O($L$)/prefill O($L^2$) |
| KDA 交互 | $\approx$ 常数,可忽略 | $\approx$ 常数×$L$ | 否 |

两阶段的边界与 05 篇一致:prefill 计算密集(权重项摊到全序列、再叠加 MLA 二次注意力),decode 访存密集(每 token 重读激活权重 52 GB 量级,见 §2.1)。05 篇给的 208 PFLOPs 是权重绑定项,本节补上注意力交互项后,1M prefill 的完整算力约 $5.5\times10^{17}$ FLOPs。

## 2. 显存账:权重/激活/KV/块表示四桶

显存按生命周期分四桶:权重(全模型共享、常驻)、激活(前向中间量、逐层)、KV(KDA 状态 + MLA latent KV,随请求)、块表示(AttnRes 常驻,承接 04)。量化把权重桶压掉 4×,之后显存压力向 KV 与块表示迁移。

### 2.1 权重桶:两条位宽口径

2.78T 参数几乎全在 896 个路由专家的 FFN 里。逐层路由专家 FFN $\approx3\times3584\times3072\times896\approx29.6$B(03 篇已算),乘 93 层约 2.75T,与总量 2.78T 吻合——即路由专家占约 99%。

非专家组件(注意力投影、latent 投影、共享专家、路由器、embedding、输出头等)约 30B、占约 1%(按表 1 推算)。

| 口径 | 组成 | 字节 |
| --- | --- | --- |
| 全 BF16(2 B/参数) | 2.78T 全量 | $\approx5.56$ TB |
| MXFP4 专家 + BF16 非专家 | 路由专家 2.75T @ 0.5 B + 非专家 $\approx30$B @ 2 B | $\approx1.38$ TB + 60 GB $\approx1.44$ TB |

削减 $\approx3.9$×。量化范围出自报告 §4.1.4:只有路由专家权重压到 MXFP4(0.5 B/参数),非专家组件(含共享专家)保持更高精度 BF16。

这就是 decode 带宽下限的出处:单 token 需流过 $104.2$B 激活权重,$52$ GB 是"专家全 MXFP4、非专家 0 字节"的理想下限,实际因非专家 BF16 更高(05 篇 §1 同口径)。

### 2.2 EP 分片后的每 rank 权重

权重桶要跨机分片才能装下。EP16 下的每 rank 权重按全模型 93 层口径计(每层口径见 07 §6;专家权重 MXFP4、共享专家与 dense 非专家 BF16):

| 项 | 每 rank 参数(全模型) | 每 rank 字节(全模型) | 分片方式 |
| --- | --- | --- | --- |
| 路由专家 home($56/层\times93$) | $93\times56\times33.0$M $\approx172$B | $\approx86$ GB | EP 分片 |
| 路由专家 冗余(上界) | 同上 $\approx172$B | $\approx86$ GB | EP 冗余复制 |
| 共享专家($2/层\times93$) | $93\times132$M $\approx12.3$B | $\approx24.5$ GB | EP rank 间复制 |
| dense 非专家(注意力/latent/路由/embedding) | $\approx30$B | $\approx60$ GB(TP8 后 $\approx7.5$ GB) | TP 域内分片 |

路由专家 home 每卡约 86 GB、冗余上界再翻倍到约 172 GB,与 10 篇 §2 的全模型口径一致。dense 非专家约 30B 参数、BF16 下约 60 GB,由 8-GPU NVLink 域内的 TP 分片(注意力按头、latent 投影按秩分片,报告 §5.4.2),TP8 时每卡约 7.5 GB。

**MXFP4 把路由专家(home)从 BF16 的约 344 GB 压到约 86 GB(4×),而 dense 非专家 30B 虽只占参数的 1%,却以 BF16 保持约 60 GB 未压缩**——量化只落在专家权重上,这部分非专家字节一分没省,与量化后的路由专家 home 同量级,是"只量化专家"这一取舍的代价(报告 §4.1.4)。

### 2.3 KV 桶:KDA 固定状态与 MLA 逐 token KV

两套缓存大小、生命周期都不同,但共用统一分页池(报告 §5.4.1,05 篇 §2)。字节账:

- **KDA 循环状态**:每请求一份、固定大小,不随 $L$ 增长。总量 $=69$ 层 $\times 96$ 头 $\times d_k d_v$ 元素。取 Kimi Linear 的 $d_k=d_v=128$ 代入:$\approx69\times96\times16384\approx108$M 元素,BF16 约 217 MB/请求。
- **MLA latent KV**:逐 token 增长。总量 $=24$ 层 $\times L\times\ell_{kv}\times b$($\ell_{kv}$ 为 latent KV 维,$b$ 为每元素字节)。$\ell_{kv}$ 报告表 1 未给;若取 512、FP8 1 字节,1M 下 $\approx24\times10^6\times512\approx12.3$ GB/请求。

**1M 上下文的 KV 大头集中在 24 个 MLA 层**(报告 §5.4.1),KDA 状态约 217 MB 相对可忽略。KDA 的"固定大小"红利在显存侧兑现为:长前缀每加一个 token,KDA 层零增长、只有 MLA 层按 $\ell_{kv}$ 增长。

### 2.4 激活桶与 AttnRes 块表示

- **激活桶(前向中间量)**:prefill 期间逐层物化的 FFN hidden(3072)、注意力中间量等,峰值与 batch × 序列长相关。训练侧用块级 FP8 量化 + offload/remote-offload 管理(报告 §5.2.2);推理侧 prefill 是瞬时峰值,decode 几乎不物化长序列激活。
- **块表示桶(AttnRes 常驻)**:承接 04 篇 §2.1。块表示在边界层算一次、常驻 GPU(报告 §5.2.2),量级 $N\cdot T\cdot d$(N=8 块 + embedding 源,随 token 数 $T$ 增长)。BF16、1M 下约 $8\times10^6\times7168\times2\approx115$ GB,经序列并行(SP)按 rank 分片、FP8 再减半(报告 §5.4.2)。

### 2.5 四桶总表

1M 上下文、单请求口径(权重为全模型共享项,其余为每请求项):

| 桶 | 内容 | 量级(1M) | 随 $L$ | 主要手段 |
| --- | --- | --- | --- | --- |
| 权重 | 2.78T 参数 | 1.44 TB(MXFP4)/ 5.56 TB(BF16) | 否 | EP 分片 + MXFP4 量化 |
| 激活 | 前向中间量 | prefill 逐层瞬时峰值 | 否 | FP8 + offload/重算 |
| KV | KDA 状态 + MLA latent KV | KDA $\approx217$ MB(固定)+ MLA $\approx12$ GB 量级 | KDA 否 / MLA 是 | 统一分页池 + latent 压缩 |
| 块表示 | AttnRes 常驻 | $N\cdot T\cdot d\approx115$ GB(BF16) | 是(随 $T$) | SP 分片 + FP8 |

四桶里"KV"专指两套注意力缓存,"块表示"专指 AttnRes 的跨层常驻表示(承接 04),二者生命周期不同,分桶以显存归属为准。

## 3. 通信账:五路数据量与量级

并行切分换来五路集合通信,各自的数据量与频率不同:TP all-reduce 高频小量,EP all-to-all 每层中量,KCP all-gather 固定小量,PP 激活传递与 softmax CP 各占一端。量级统一按元素数 × 每元素字节 $b$ 计。

### 3.1 TP all-reduce

dense 算子(注意力、共享专家、latent 投影)在 NVLink 域内 TP 分片,每层对部分结果做一次 all-reduce。数据量正比于该层激活张量,约 $2d$ 元素/层/token,BF16 下约 28.7 KB/token/层,93 层约 2.7 MB/token。高频但每步量小,是 TP 只能落在节点内高带宽域的原因(承接 06 §2)。

### 3.2 EP 两段 all-to-all

直接引用 07 §2 的公式与数值。一层前向的 dispatch + combine 两段,单向数据量:

$$D_{\text{一层,单向}} = 2\,S\,K\,\ell\,b = 2\times S\times16\times3584\times b = 114{,}688\times S\times b\ \text{字节}$$

| 场景 | $S$(每 rank token) | 一层单向(FP8,$b=1$) |
| --- | --- | --- |
| decode 单步 | 1 | $\approx112$ KiB |
| prefill chunk | 1024 | $\approx112$ MiB |

数据量与专家总数 $E$、EP 规模 $R$ 无关——每个 token 只路由 16 个专家,这是 896 专家规模下 all-to-all 仍可负担的前提。$R$ 只影响远端占比($1-1/R$)与消息粒度。

### 3.3 PP 激活传递

PP 切层后,激活逐 stage 点对点传递,数据量 $d\times b\approx14$ KB/token/层边界,量级远小于 all-to-all,且可与计算错相位重叠(06 §5、报告 §5.2,Fig.11)。

### 3.4 KCP 固定 all-gather vs softmax CP

KCP 每 rank 每 KDA 层交换两个固定张量:累计转移矩阵 $M\in\mathbb{R}^{d_k\times d_k}$ 与从零生成的状态 $\tilde{S}\in\mathbb{R}^{d_k\times d_v}$(报告 §5.1.2,06 §4)。数据量与 $L$ 无关;softmax CP 则交换随序列增长的 KV 块(报告 §5.1.2):

| CP 方案 | 交换内容 | 随 $L$ | 每层每 rank 量级($d_k=d_v=128$、96 头、$P=8$ 或 $L=1\text{M}$) |
| --- | --- | --- | --- |
| KCP | $M$($d_k\times d_k$)+ $\tilde{S}$($d_k\times d_v$) | O(1) | $\approx96\times(128^2+128^2)\times8\times2\approx50$ MB/层 |
| softmax CP | KV 块($L\times d_{kv}$) | O($L$) | $\approx10^6\times(2\times7168)\times2\approx28.7$ GB/层 |

1M 下单层差约 500 倍,softmax CP 的 KV 交换随序列线性增长到不可承受,KCP 固定 $d_k\times d_k+d_k\times d_v$ 两个张量、不随 $L$ 增长。这是 KDA"固定大小循环状态"在通信侧的直接兑现——不是 KCP 的实现技巧,而是 KDA 取消逐 token KV 之后,CP 通信自然与序列长度解耦(报告 §5.1.2)。

## 4. MXFP4/MXFP8:量化范围与代价

量化是显存账和带宽账里最大的一笔削减(权重桶 5.56 TB → 1.44 TB,约 3.9×),但它只落在路由专家权重上,且要付 QAT 与反量化的代价(报告 §4.1.4)。

### 4.1 为什么只量化专家权重

报告 §4.1.4 的量化范围是:路由专家权重 MXFP4、专家输入激活 MXFP8、非专家组件(注意力投影、latent 投影、共享专家、路由器)保持更高精度。落点的选择由三件事决定:

1. **专家权重占参数 99%**。2.75T / 2.78T 是参数显存的大头,量化收益集中在它身上;非专家约 30B 只占 1%,量化省不出多少。
2. **非专家是 dense 精度敏感路径**。注意力投影管全局 token 交互、latent 投影管 routed 分支的压缩表示、路由器管 Top-k 选择,这些的精度损失会直接放大到下游;共享专家吃全宽表示、恒激活,也是 dense 主路。
3. **激活用 MXFP8 而非更低**。激活有离群点与动态范围问题(03 篇 SiTU-GLU 有界激活正是为低精度铺路),8 bit 是精度与带宽的折中;专家输入激活 8 bit 与权重 4 bit 组成 MX 格式的混合精度 GEMM。

### 4.2 削减比例

| 项 | BF16 | MXFP4/MXFP8 | 削减 |
| --- | --- | --- | --- |
| 权重总量 | 5.56 TB | 1.44 TB | $\approx3.9$× |
| 路由专家权重 | 2.75T×2 B = 5.5 TB | 2.75T×0.5 B = 1.38 TB | 4× |
| decode 权重流(激活权重) | $\approx208$ GB/token | $\approx52$ GB 下限 | 4×(下限) |

权重读取是 decode 访存密集瓶颈的主成分,4× 的权重字节削减直接对应 decode 吞吐的带宽红利上限。

### 4.3 代价:反量化、QAT、精度损失

量化换来的削减有三笔代价:

1. **反量化开销**。MXFP4 权重进 GEMM 前要反量化,落在 kernel 侧。报告 §5.4.2 用离线权重重排把运行时反量化开销大幅降下来:权重布局一次性预处理置换,使反量化与访存融合(09 篇展开)。
2. **QAT 负担**。整个后训练阶段(SFT + RL)全程感知量化训练,让模型适应量化精度损失;RL 时 rollout 与训练同量化方案,消除 train–inference mismatch(报告 §4.1.4)。代价是训练流程被量化约束绑住、每次改精度都要重训。
3. **精度损失落在专家权重表示上**。路由专家权重只有 4 bit,表示精度有损,靠 QAT 让模型"学会在 4 bit 下工作";非专家保持 BF16 是兜底,保证注意力与路由不被量化拖累。MX 格式带 block-wise scale(microscaling,报告 §4.1.4 引 [104]),具体 block size 报告未给出,> **待确认**。

## 5. 配置→账:选型观

三本账不是固定值,而是随 EP 规模、量化位宽、CP 方案、TP 度四个旋钮变。下表把主要旋钮对三本账的影响列成对照,是选型的索引:

| 旋钮 | 算力账 | 显存账 | 通信账 |
| --- | --- | --- | --- |
| EP8 → EP16 | 每 token FLOPs 不变 | 每 rank home/冗余专家各减半(冗余显存每层 1.85→0.92 GB),共享专家复制翻倍 | all-to-all 每 rank 量不变,远端占比 87.5%→93.75% |
| BF16 → MXFP4 专家 | FLOPs 不变(反量化另算) | 权重桶 5.56 TB → 1.44 TB(约 3.9×) | decode 权重流 4×(带宽下限) |
| softmax CP → KCP | 无直接影响 | 无直接影响 | 1M 下单层 28.7 GB → 50 MB(约 500×) |
| TP1 → TP8 | dense 算力分摊到 8 卡 | dense 权重每卡 /8 | 新增每层 all-reduce(28.7 KB/token/层) |

选型落点:

1. **显存受限(小 HBM 卡)**:EP16 + MXFP4,每卡路由专家 1.85 GB、dense TP 分片,权重桶压到每卡 GB 级;代价是共享专家复制总量翻倍、远端占比略升。
2. **decode 吞吐受限(访存密集)**:MXFP4 专家权重把 decode 权重流从 BF16 的约 208 GB 压到约 52 GB 下限,是带宽红利最大的单项;配 EAGLE-3 投机解码进一步摊薄权重读取(05 篇 §5)。
3. **1M 长上下文 prefill**:KCP 是硬约束,softmax CP 的线性 KV 交换不可承受;同时 MLA 二次注意力是 prefill 算力大头,靠 TP8 分摊 dense、靠 3:1 KDA 插值把全局注意力限制在 24 层。
4. **算力密集 prefill**:TP8 加速 dense 算子(注意力、共享专家、latent 投影),换每层 all-reduce 的 NVLink 域内开销。

四个旋钮不独立:EP 决定权重分多细、量化决定权重存多省、CP 决定长上下文通多省、TP 决定 dense 算多快,组合由部署目标的瓶颈(显存 / 带宽 / 长上下文 / 算力)倒推。

## 优化点清单

| 优化点 | 优化对象 | 机制 | 收益(量级) | 代价/适用 |
| --- | --- | --- | --- | --- |
| MXFP4 专家权重 | 权重桶显存 + decode 权重流 | 路由专家权重 0.5 B/参数 | 权重桶 5.56→1.44 TB(≈3.9×);decode 权重流 208→52 GB(4×) | 反量化开销 + 全程 QAT(报告 §4.1.4) |
| KCP 固定 all-gather | 长上下文 CP 通信 | 拆齐次/特解 + prefix scan | 1M 单层 28.7 GB→50 MB(≈500×) | 每头二次维度 $d_k^2$;依赖 delta-rule 仿射性质 |
| MLA latent KV 压缩 | KV 显存 | KV 压到 latent 维 | 1M 下 KV 大头集中 24 层、量级约 12.3 GB | 不省 O($L^2$) 注意力计算 |
| dense 非专家保持高精度 | 量化粒度取舍 | 只量化路由专家,非专家 BF16 | 保注意力/路由/共享专家精度 | 非专家约 30B/60 GB 未压缩、与量化后路由专家 home 同量级 |

## 待确认

- **KDA 每头维度 $d_k/d_v$ 与 MLA latent KV 维 $\ell_{kv}$**:报告表 1 未给。KV 字节账、注意力投影 FLOPs、KCP 通信量的精确值都以这两个维度为乘数;正文 $d_k=d_v=128$(Kimi Linear 论文值)与 $\ell_{kv}=512$(DeepSeek-V2 风格)均为代入示例,非 K3 实测。
- **EP×TP 组合**:路由专家 FFN 是否在节点内再叠 TP、dense 非专家 30B 的精确分片方式,报告 §5.4 未展开;§2.2 的每卡 dense 权重按 TP8 域内分片推算。
- **MXFP4 block size / microscaling 参数**:报告 §4.1.4 只引 [104] 未给 block size,按 MX 格式典型值记、具体待确认。
- **组件级参数分解**:2.75T 路由专家 / 30B 非专家为按表 1 推算,报告未给逐组件(注意力各投影、embedding 是否 tied、MTP)细分;104.2B 激活参数的组件拆分因此不可闭合。
- **prefill 绝对 FLOPs 与带宽**:报告未给;208 GFLOPs/token、1M prefill 完整约 $5.5\times10^{17}$ FLOPs、52 GB decode 权重流下限,均为公式推算。

## 下一篇

下一篇 [09 Kernel 与系统协同](./09-kernel-system-codesign.md) 落到 kernel 级:MXFP4 反量化与稀疏 MoE GEMM 的访存优化、KDA decode 的循环状态 kernel、AttnRes 的两阶段合并,以及它们如何与本节的三本账对账。
