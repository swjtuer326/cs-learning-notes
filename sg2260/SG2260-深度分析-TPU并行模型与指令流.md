# SG2260 TPU 并行模型与指令流深度分析

> 回答核心问题：64 个 Lane 是否并行？Vector 和 Cube 如何协同？TIU 如何分发指令？
> GDMA 和 TPU 指令流是独立还是统一？数据搬运和计算如何重叠？
>
> 基于 SG2260_TPU_SPEC v1.1 与 TPU V7.1 指令集设计文档 v0.26。

---

## 0. 先给出直接结论

| 问题 | 答案 |
|---|---|
| 64 个 Lane 是否并行工作？ | **是**。SIMD 模式，TIU 下发一条指令，64 个 Lane 同时执行同一条指令，各自访问自己的 LMEM 数据。lane_mask 可关闭部分 Lane。 |
| Vector 和 Cube 是否并行？ | **不并行**。它们在同一条指令流水线内，一条指令要么用 Vector 要么用 Cube，不能同时使用。但它们在**空间上是独立硬件**，只是分时复用。 |
| TIU 如何下发指令？ | TIU 从指令 Buffer 取一条 1024-bit 描述符，经 dpc_dec 译码、dpc_coord_gen 生成坐标、dpc_r0/r1/w0_eng 生成 LMEM 地址、dpc_arb 仲裁 bank 冲突，然后将控制信号通过内部总线**广播**到所有 64 Lane。每个 Lane 用相同的地址在自己的 LMEM 中读写不同数据。 |
| 数据和计算是并行还是串行？ | **并行**。GDMA 有自己的独立指令流和指令 Buffer，可与 TIU 控制的 TPU 计算**同时进行**。GDMA 搬运下一批数据的同时，TIU 在执行当前计算。通过 SyncID 保证数据就绪后才发射相关 TPU 指令。 |
| TIU 和 BDC 什么关系？ | **TIU 是硬件控制器，BDC 是指令类别**。TIU 的指令流来自其内部的指令 Buffer (32KB)。BDC 指令 (TIU/TPU 指令) 是 TIU 取出并分发给 Lane 执行的指令，包括 Vector 类 (AR/binary/pool) 和 Cube 类 (Conv/MM2)。 |
| GDMA 和 TPU 指令是否都发送给 TIU？ | **不**。GDMA 有自己独立的指令 Buffer 和取指逻辑，不经过 TIU。SDMA、HAU 也各自独立。四个引擎 (TIU/GDMA/SDMA/HAU) 各自拥有独立的指令流，DES 模式下各自从 DDR 取指，PIO 模式下由 Scalar Engine 分别推送。 |

---

## 1. 硬件层的并行性全景

### 1.1 每 TPU Core 内部：四个独立引擎

```
TPU Core #N 内部
┌──────────────────────────────────────────────────────────────────┐
│                                                                    │
│  ┌─────────────────┐  ┌──────────────┐  ┌────────┐  ┌─────────┐ │
│  │    TIU + 64     │  │     GDMA     │  │  SDMA  │  │   HAU   │ │
│  │    Lane (SIMD)  │  │  (独立引擎)   │  │(独立引擎)│  │(独立引擎)│ │
│  │                  │  │              │  │        │  │         │ │
│  │ 取指: DES/PIO    │  │ 取指: DES/PIO│  │ DES/PIO│  │ DES/PIO │ │
│  │ 指令Buffer: 32KB│  │ 指令Buffer   │  │ 2指令流 │  │ 1指令流 │ │
│  │ 执行: LMEM数据   │  │ 执行: LMEM↔  │  │ L2M↔  │  │ G/L2M   │ │
│  │      计算       │  │  GMEM/L2M等  │  │ GMEM   │  │ 排序/TopK│ │
│  └────────┬────────┘  └──────┬───────┘  └───┬────┘  └────┬────┘ │
│           │                  │               │            │       │
│           └──────── SyncID ──┘── MSG ────────┴── MSG ────┘       │
│                        (GDMA↔TIU)  (跨引擎同步)                    │
└──────────────────────────────────────────────────────────────────┘

四个引擎的指令流完全独立:
• TIU:  从自己的指令Buffer取TPU指令 → 控制64 Lane执行
• GDMA: 从自己的指令Buffer取GDMA指令 → 控制GDMA engine做数据搬运
• SDMA: 从自己的指令Buffer取SDMA指令 → 控制SDMA engine (双指令流)
• HAU:  从自己的指令Buffer取HAU指令 → 控制HAU engine

DES (Descriptor) 模式下: 每个引擎各自从DDR取指，完全自治。
PIO 模式下:       Scalar Engine (RISC-V CPU) 分别向各引擎推送指令。
```

### 1.2 跨 Core 并行：8 个 Core 各自独立

```
SG2260 芯片
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
│ Core 0   │ │ Core 1   │ │ Core 2   │ │ Core 7   │
│ 64 Lane  │ │ 64 Lane  │ │ 64 Lane  │ │ 64 Lane  │
│ GDMA×1   │ │ GDMA×1   │ │ GDMA×1   │ │ GDMA×1   │
│ SDMA×1   │ │ SDMA×1   │ │ SDMA×1   │ │ SDMA×1   │
│ HAU×1    │ │ HAU×1    │ │ HAU×1    │ │ HAU×1    │
│ Scalar   │ │ Scalar   │ │ Scalar   │ │ Scalar   │
└─────┬────┘ └─────┬────┘ └─────┬────┘ └─────┬────┘
      │            │            │            │
      └────────────┴──── NoC ───┴────────────┘
                     │
              共享 L2M (128MB)
```

每个 Core 独立运行自己的程序。编程模型中，**用户程序针对单个 Core 编写，运行时复制到多个 Core 并行执行**。用户通过 `CoreNum` 和 `CoreID` 对数据做多 Core 划分：

```
// 多 Core 编程伪代码 (来自指令集文档)
CoreNum = ncores  // 参与计算的 Core 数量
CoreID  = cid     // 当前 Core 在参与组中的序号 (0 ~ CoreNum-1)

// 数据划分: 每个 Core 处理自己的数据分片
my_data = global_data[CoreID * chunk_size : (CoreID+1) * chunk_size]
// ... 在 my_data 上执行 TPU/GDMA/SDMA/HAU 指令 ...
```

---

## 2. TIU 指令分发：一条指令如何到 64 个 Lane

### 2.1 指令获取 (DES vs PIO)

```
PIO 模式:
  Scalar Engine ──AXI write──► dpc_des 寄存器 ──► 指令Buffer (128×1024bit)
  延迟最低，适合动态生成的短指令序列

DES 模式:
  TIU ──AXI read──► DDR (cfg_des_addr) ──► 指令Buffer
  TIU 自行从 DDR 取指，Scalar Engine 只需配置起始地址
  适合大批量静态编译的指令序列
```

无论哪种模式，指令最终都进入 TIU 的指令 Buffer。

### 2.2 指令发射：sync_id 门控

指令不是说发射就能发射的。指令描述符中的 `des_cmd_id_en[0]` 位控制发射行为：

```
des_cmd_id_en[0] == 0:
  TIU 直接发射 → dpc_dec 译码 → 执行
  (不依赖 GDMA 的指令，如纯计算链)

des_cmd_id_en[0] == 1:
  TIU 等待 sync_id_gdma ≥ 指令cmd_id → 然后发射
  (依赖 GDMA 先把数据搬进 LMEM 的指令)
```

这个硬件门控是实现**数据搬运与计算并行**的关键机制：

```
GDMA 指令流 (独立):               TIU 指令流 (独立):
┌────────────────────┐            ┌────────────────────┐
│ GDMA0: input→LMEM  │ ──sync_id=1──► cmd_id=1 Conv0 等待
│ GDMA1: weight→LMEM │ ──sync_id=2──► cmd_id=2 Conv1 等待
│ GDMA2: LMEM→GMEM   │               cmd_id=3 Pool  等待
└────────────────────┘            └────────────────────┘

GDMA 和 TIU 同时工作: GDMA 搬运 GDMA1 的同时, TIU 可能正在执行 Conv0
                      (因为 Conv0 只依赖 GDMA0 完成)
```

### 2.3 指令译码到执行：dpc_ls_pipe 流水线

一条 TPU 指令从进入 dpc_ls_pipe 到执行完成的完整路径：

```
指令 Buffer ──► dpc_dec ──► dpc_coord_gen ──┐
                  │             │              │
                  │ 译码参数     │ 输出tensor坐标 │
                  ▼             ▼              ▼
              dpc_r0_eng    dpc_r1_eng    dpc_w0_eng
              (opd0地址)    (opd1地址)    (结果地址+延迟写)
                  │             │              │
                  └──────┬──────┴──────┬───────┘
                         │             │
                         ▼             ▼
                      dpc_arb (总线仲裁+Bank冲突处理+时序控制)
                         │
              ┌──────────┼──────────┐
              ▼          ▼          ▼
           R0读命令    R1读命令    W0写命令 (延迟)
              │          │          │
              └──────────┼──────────┘
                         │
                   内部总线 (scs_r0, scs_r1, scs_w0, srs)
                         │
         ┌───────────────┼───────────────┐
         ▼               ▼               ▼
      Lane 0          Lane 1    ...   Lane 63

dpc_coord_gen 的输出并行度:
  INT8 精度: 每cycle 输出 16 个坐标点 → 16个点同时计算
  FP16 精度: 每cycle 输出 8 个坐标点
  FP32 精度: 每cycle 输出 4 个坐标点
  精度越低, 并行度越高(因为256-bit读口一次能读更多元素)
```

**关键点**: TIU 取出一条指令，广播到所有 64 Lane。每个 Lane 用 dpc_r0_eng 计算的地址去自己的 LMEM 中读数据（不同 Lane 地址相同但指向不同 Channel 的数据）。这就是 SIMD 的本质。

### 2.4 dpc_arb: 三总线冲突仲裁

TIU 在同一个 cycle 可能产生 R0(读opd0), R1(读opd1), W0(写结果) 三个 LMEM 访问请求。但每个 Lane 的 LMEM 只有 2 个读口 + 2 个写口，且 16 个 Bank 不能同时服务于两个访问者。

dpc_arb 的职责：
1. **Bank 冲突**: R0/R1/W0 命中同一 Bank → 按优先级串行化
2. **时序控制**: 卷积等需要跨 Lane 收集的指令，dpc_arb 精确定时，让 opd0 从其他 Lane 收集回来时 opd1 也正好读回，同时送达计算单元
3. **延迟写**: W0 命令先缓存，等 ready 再真正写入

---

## 3. Vector 和 Cube：每个 Lane 内的两个计算单元

### 3.1 位置与关系

```
每个 Lane
┌───────────────────────────────────────┐
│  LMEM (256KB, 16 bank × 2R2W × 256b) │
└───────────────┬───────────────────────┘
                │ 内部总线
    ┌───────────┼───────────┐
    ▼           ▼           ▼
┌────────┐ ┌────────┐ ┌──────────┐
│ Vector │ │  Cube  │ │  TIU控制 │
│ Unit   │ │  Unit  │ │  (lane外)│
│        │ │        │ │          │
│ INT8:  │ │ INT8:  │ │ 接收TIU  │
│  64 EU │ │ 64×4   │ │ 广播的   │
│ FP16:  │ │  MAC   │ │ 控制信号 │
│  32 EU │ │ FP16:  │ │          │
│ FP32:  │ │ 32×4   │ │          │
│  16 EU │ │  MAC   │ │          │
└────────┘ └────────┘ └──────────┘
```

### 3.2 它们如何工作：分时复用，不是并行

TIU 一次只发一条指令 → 所有 Lane 执行同一条指令 → 这条指令要么用 Vector 要么用 Cube，**不能同时使用**。

```
指令流:
  Conv0 (用Cube)  ──►  64 Lane 的 64 个 Cube 同时计算
  Add   (用Vector)──►  64 Lane 的 64 个 Vector 同时计算
  Conv1 (用Cube)  ──►  64 Lane 的 64 个 Cube 同时计算
  MaxPool(Vector) ──►  64 Lane 的 64 个 Vector 同时计算
```

**并行度计算 (INT8)**:

```
Cube 指令 (如 Conv):
  每 Lane 1 个 Cube × 每 Cube INT8: 64×4 MAC = 256 MAC/cycle
  64 Lane × 256 = 16384 MAC/cycle (全 Core)

Vector 指令 (如 Add):
  每 Lane 64 EU × 64 Lane = 4096 INT8 Operations/cycle
```

### 3.3 Cube 和 Vector 的物理独立性意味着什么？

虽然 Cube 和 Vector 不同时执行，但它们是独立的硬件单元——这意味着：

1. **流水线可以部分重叠**: Conv 的结果写入 LMEM 时 (W0 阶段)，下一条 Vector 指令可能已经进入译码阶段 (dp_dec)。这是 TIU 的 pipeline 内部并行，指令级流水 (instruction-level pipelining)。

2. **无上下文切换开销**: 从 Cube 指令切换到 Vector 指令 (或反过来) 只是 TIU 译码出不同的控制信号，无需保存/恢复寄存器。两套硬件一直在那里等着。

---

## 4. Lane 之间的数据流动：何时需要 Cross-Lane

### 4.1 不需要 Cross-Lane 的指令 (本地操作)

```
AR (逐元素算术), Pooling, Binary, Unary, Active (ReLU等):
  每个 Lane 只用自己的 LMEM 数据
  opd0 ∈ 本 Lane LMEM
  opd1 ∈ 本 Lane LMEM (如果有)
  res  ∈ 本 Lane LMEM
  → 无需跨 Lane 通信, 64 Lane 完全并行, 零开销
```

### 4.2 需要 Cross-Lane 的指令 (收集+广播)

```
Conv, MM2 (矩阵乘):
  卷积: 一个输出 Channel 需要所有输入 Channel 的部分和
         → opd1 (weight) 可以本地, opd0 (input) 需要从其他 Lane 收集
  MM2:  矩阵乘的跨 Lane 数据也需要收集

  流程:
  1. dpc_r0_eng 发出 srs 收集命令 (scs_srs 总线)
  2. 每个 Lane 将自己 LMEM 中对应地址的数据送上 dp_shf 总线
  3. dpc_cube_b_dat_eng 在 TIU 侧整理收集到的数据
     (8 种模式: 1024bit/128bit/32bit, NT转换, CW转换等)
  4. 整理后的数据广播回所有需要的 Lane
  5. 与本地读出的 opd1 同时送达 Cube → 开始计算
```

**Cross-Lane 的代价**: 收集操作引入额外延迟，且 dpc_arb 需要精确控制时序。这就是为什么有专门的 8 种收集模式——不同指令的数据分布不同，需要不同的整理策略。

---

## 5. GDMA 与 TPU 指令流：完全独立，SyncID 同步

### 5.1 两套独立的指令流

```
┌──────────────────────────────┐  ┌──────────────────────────────┐
│     GDMA 指令流 (独立)        │  │     TIU/TPU 指令流 (独立)    │
│                              │  │                              │
│  指令Buffer (独立分配)        │  │  指令Buffer (TIU 32KB)       │
│  取指: DES模式下从DDR自己取   │  │  取指: DES模式下从DDR自己取   │
│  发射: 顺序发射               │  │  发射: sync_id_gdma 门控     │
│  执行: GDMA Engine            │  │  执行: TIU → 64 Lane        │
│                              │  │                              │
│  GDMA 指令示例:              │  │  TPU 指令示例:               │
│    tensor_copy(input→LMEM)   │  │    Conv.Cube(IC=3,OC=16)    │
│    tensor_copy(weight→LMEM)  │  │    Add.Vector(opd0, opd1)   │
│    matrix_copy(LMEM→GMEM)    │  │    Pool.Vector(k=2×2)       │
│    cw_transpose(NHWC→NCHW)   │  │                              │
└──────────────────────────────┘  └──────────────────────────────┘
           │                               │
           └─────── SyncID 同步 ───────────┘
```

### 5.2 SyncID：硬件级别的生产者-消费者同步

```c
// GDMA 指令完成后,硬件自动更新 sync_id_gdma 寄存器
GDMA0: GMEM→LMEM input  // 完成后 sync_id_gdma = 1
GDMA1: GMEM→LMEM weight // 完成后 sync_id_gdma = 2

// TPU 指令设置依赖
Conv0: des_cmd_id_en=1, cmd_id=1
       → 发射前检查: sync_id_gdma ≥ 1 ?
         是 → 发射 (input 和 weight 都已就绪在 LMEM 中)
         否 → 等待 (等待 GDMA0+GDMA1 完成)

// 这就是 compile-time 知道的静态依赖, hardware-level 的自动同步
// 零软件开销: 不需要 Scalar Engine 轮询寄存器
```

### 5.3 MSG：跨引擎的通用同步

SyncID 只支持 GDMA↔TIU。当需要同步 TIU↔SDMA 或 TIU↔HAU 时，用 MSG：

```
TIU 需要等 SDMA 完成数据搬运:
  SDMA:   SEND msg_id=10, wait_cnt=1  // 发送消息
  TIU:    WAIT msg_id=10, send_cnt=1  // 等待消息

TIU 需要等 HAU 完成排序:
  HAU:    SEND msg_id=20, wait_cnt=1
  TIU:    WAIT msg_id=20, send_cnt=1
```

### 5.4 并行执行的时间线示例

```
Time ──────────────────────────────────────────────────────►

GDMA流: [GDMA0:input→LMEM][GDMA1:weight→LMEM]        [GDMA3:output→GMEM]
        sync_id=1         sync_id=2                   ...
                           │
TIU流:               [等待] [Conv0(Cube)] [Add(Vec)] [MaxPool(Vec)]
                           cmd_id=1                     cmd_id=4
                           ▲                  ▲
                           sync_id=2 到达     GDMA3 同步

并行区间: ░░░░░░░░░░░░░░░░██████████████████████████████████
          GDMA独立搬运                        TIU独立计算
          重叠区域
```

**关键**: GDMA 搬运 GDMA1(weight) 的同时，TIU 在等待也可以用——但更典型的场景是，前一轮的 TI 计算和后一轮的 GDMA 搬运重叠。在大 batch 或流水线模式下，这种重叠可以隐藏大部分 GDMA 延迟。

---

## 6. Scalar Engine 的角色：不只发指令

Scalar Engine (RISC-V C920 CPU) 除了 PIO 模式下给各引擎推送指令，还可以：

1. **动态指令生成**: 根据运行时 shape 动态构造 TPU 描述符 → PIO 推送（DES 模式只能执行静态编译的指令）
2. **访问 LMEM/SMEM**: "TPU对外没有读取和写入数据的能力" — Scalar Engine 可以充当 LMEM 和外部之间的桥
3. **SMEM 数据暂存**: "当 Scalar Engine 需要访问 LMEM 且数据量不少时，可以将 LMEM 数据搬到 SMEM 然后 Scalar Engine 再读"
4. **API Message Buffer**: SMEM 作为 Scalar Engine 与 TPU Core 间消息传递缓冲区
5. **中断/轮询同步**: Scalar Engine 通过中断或轮询方式与其他 Engine 同步

---

## 7. 数据搬运与计算的精确重叠机制

### 7.1 LMEM 的竞争者

```
LMEM  (每个 Lane 独立)
  │
  ├── TIU 控制的读写 (2读口 R0/R1 + 1写口 W0) — 计算用
  │     R0: opd0 读, R1: opd1 读, W0: 结果写
  │
  └── GDMA 控制的读写 (1写口 DMA + 共享 2读口) — 搬运用
        GDMA 仅在有空闲读口时才进行读操作
        GDMA 的读写可以在与 EU 读写不同 Bank 时并行
```

**GDMA 的读写可以 Bank 冲突**: GDMA 读写和 EU 读写之间、GDMA 读和 GDMA 写之间如果访问同一 Bank，会产生性能损失。

### 7.2 重叠的边界条件

GDMA 和 TIU 可以同时访问 LMEM 的条件：
- 访问不同 Bank → 完全并行
- 访问相同 Bank → 硬件仲裁，其中一个等待

这就是为何 Bank-aware 编程对性能至关重要——编译器在分配 tensor 地址时需要确保 GDMA 目标区域和 TIU 操作区域落在不同 Bank 上。

---

## 8. 多 TPU Core 编程模型

来自指令集文档的关键描述：

"编程者能看到的是多个 TPU Core System，每个 TPU Core System 包含 1 个由 64 Lanes 的 Vector、Cube 和 TIU 组成的 TPU 计算引擎、1 个 GDMA、1 块 LMEM、1 块 SMEM、1 个 Scalar Engine、1 个 HAU、1 个 SDMA"

"编程者所写程序是针对一个 TPU Core System 的，执行时程序将被复制到多个 TPU Core System 中并行执行"

编程者能获取 `CoreNum` 和 `CoreID` 变量，据此做数据划分。L2M (128MB) 和 GMEM (4GB) 可按核数比例在参与任务的 Core 之间划分使用。

---

## 9. 总结：并行性层次一览

```
层次1 — 芯片级别:  8 个 TPU Core 独立并行 (SPMD模型, 程序复制执行)
                   └── Core 间通过 CDMA 和 MSG 做数据交换和同步

层次2 — Core 级别:  TIU(TPU) ∥ GDMA ∥ SDMA ∥ HAU (4引擎并行)
                   └── 不同引擎的指令流完全独立
                   └── SyncID / MSG 硬件同步

层次3 — Lane 级别:  64 Lane SIMD 并行 (同一指令, 不同数据)
                   └── lane_mask 可选择性关闭 Lane

层次4 — 单元级别:  Vector或Cube内部 SIMD (Vector: EU_NUM个EU并行;
                   Cube: 64×4或32×4 MAC阵列并行)

层次5 — 流水线:    TIU内部 dpc_dec→coord_gen→r0/r1/w0_eng→arb 流水化;
                   GDMA搬运与TPU计算通过SyncID重叠
```
