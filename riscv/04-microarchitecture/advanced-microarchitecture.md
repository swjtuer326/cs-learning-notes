# 高级微架构

> 从标量到超标量，从顺序到乱序，现代高性能处理器通过复杂的微架构技术追求极致性能。

---

## 1. 从 5 级流水线到高性能

```mermaid
graph LR
    subgraph 性能提升路径
        A["5 级标量<br/>CPI ≈ 1"] --> B["超标量<br/>IPC > 1"]
        B --> C["乱序执行<br/>挖掘 ILP"]
        C --> D["多核<br/>挖掘 TLP"]
        D --> E["多芯片/集群<br/>横向扩展"]
    end
```

| 技术 | 核心思想 | 性能提升 |
|------|----------|----------|
| **超标量** | 每周期发射多条指令 | IPC > 1 |
| **乱序执行** | 动态调整指令执行顺序 | 挖掘指令级并行（ILP） |
| **多核** | 多个核心并行 | 挖掘线程级并行（TLP） |
| **SMT** | 单核心多线程 | 提高资源利用率 |

---

## 2. 超标量处理器

### 2.1 基本结构

```mermaid
graph TB
    FETCH["取指单元<br/>每周期取 N 条指令"]
    DECODE["译码单元<br/>每周期译码 N 条指令"]
    RENAME["寄存器重命名<br/>消除假依赖"]
    ISSUE["发射队列<br/>等待操作数就绪"]
    EX1["ALU 1"]
    EX2["ALU 2"]
    EX3["Load/Store 单元"]
    EX4["分支单元"]
    CDB["公共数据总线 CDB"]
    COMMIT["重排序缓冲 ROB<br/>顺序提交"]

    FETCH --> DECODE --> RENAME --> ISSUE
    ISSUE --> EX1 --> CDB
    ISSUE --> EX2 --> CDB
    ISSUE --> EX3 --> CDB
    ISSUE --> EX4 --> CDB
    CDB --> COMMIT
```

### 2.2 关键组件

| 组件 | 功能 | 类比 |
|------|------|------|
| **取指单元** | 每周期取多条指令，需要分支预测支持 | 图书馆一次借多本书 |
| **寄存器重命名** | 消除 WAW 和 WAR 假依赖 | 给每个人发工号，避免同名混淆 |
| **发射队列** | 缓存已译码指令，等待操作数就绪 | 厨房备菜区，食材齐了再下锅 |
| **ROB** | 保证指令按序提交，支持精确异常 | 银行叫号，按顺序办理 |
| **CDB** | 广播执行结果，唤醒等待的指令 | 大喇叭通知"XX 号好了" |

---

## 3. 乱序执行（Out-of-Order Execution）

### 3.1 为什么需要乱序执行？

```asm
# 顺序执行的问题
lw   t0, 0(a0)       # Cache miss，需要 100 周期
add  t1, t2, t3      # 不依赖 t0，但被阻塞
sub  t4, t5, t6      # 不依赖 t0，但被阻塞
mul  t7, t0, t8      # 依赖 t0，必须等待

# 乱序执行
lw   t0, 0(a0)       # 发射，等待 Cache
add  t1, t2, t3      # 操作数就绪，先执行！
sub  t4, t5, t6      # 操作数就绪，先执行！
mul  t7, t0, t8      # 等 t0 到达后执行
```

### 3.2 乱序执行的核心流程

```mermaid
graph TD
    IN_ORDER1["顺序取指/译码"] --> RENAME["寄存器重命名"]
    RENAME --> IN_ORDER2["顺序写入发射队列和 ROB"]
    IN_ORDER2 --> OOO["乱序执行<br/>操作数就绪就发射"]
    OOO --> CDB2["结果广播到 CDB"]
    CDB2 --> IN_ORDER3["顺序提交（ROB）"]

    style OOO fill:#ff6b6b,color:#fff
    style IN_ORDER1 fill:#4ecdc4,color:#fff
    style IN_ORDER2 fill:#4ecdc4,color:#fff
    style IN_ORDER3 fill:#4ecdc4,color:#fff
```

> **关键原则：** 乱序执行，顺序提交。指令可以乱序执行，但结果必须按程序顺序提交（写回寄存器/内存），这样才能保证精确异常。

### 3.3 寄存器重命名

消除假依赖（名称依赖），只保留真依赖（数据依赖）：

```
假依赖类型:
  WAR (Write After Read):  后续指令写，前序指令读 → 重命名解决
  WAW (Write After Write): 两条指令写同一寄存器 → 重命名解决

真依赖:
  RAW (Read After Write):  后续指令读前序指令的结果 → 必须等待

示例:
  add t0, t1, t2    # t0 = t1 + t2       → t0 → p1 (物理寄存器)
  sub t3, t0, t4    # t3 = t0 + t4 (RAW) → 必须等 p1
  add t0, t5, t6    # t0 = t5 + t6 (WAW) → t0 → p2 (新物理寄存器)
  or  t7, t0, t8    # t7 = t0 | t8 (RAW) → 等 p2

  重命名后 WAW 消除：t0 先映射 p1，后映射 p2
  后续读 t0 的指令会自动读到 p2
```

---

## 4. 加载/存储队列（LSQ）

内存操作需要特殊处理，因为它们之间可能存在地址依赖：

```mermaid
graph TB
    subgraph LSQ
        LD1["Load 0x1000<br/>地址已知<br/>✅ 可以执行"]
        ST1["Store 0x2000<br/>地址已知<br/>等待数据"]
        LD2["Load 0x2000<br/>地址已知<br/>⚠️ 等待 ST1（地址重叠？）"]
        ST2["Store 0x1000<br/>地址未知<br/>❌ 不能执行"]
    end

    LD1 --> |"地址不重叠"| OK["可以执行"]
    LD2 --> |"地址与 ST1 相同<br/>需要转发"| FORWARD["Store-to-Load 转发"]
```

| LSQ 功能 | 说明 |
|----------|------|
| **地址检查** | 检查 Load 是否与之前的 Store 地址重叠 |
| **Store-to-Load 转发** | 如果 Load 的地址与前面 Store 相同，直接从 Store 队列获取数据 |
| **保序** | Store 必须按程序顺序执行（保证内存一致性） |
| **推测执行** | Load 可以在地址确认前推测执行，如果冲突则回滚 |

---

## 5. 缓存一致性

多核系统中，每个核心有自己的 Cache，需要保持数据一致性：

### 5.1 MESI 协议

```mermaid
stateDiagram-v2
    M: Modified（已修改）<br/>只有本 Cache 有此行<br/>内存数据过期
    E: Exclusive（独占）<br/>只有本 Cache 有此行<br/>与内存一致
    S: Shared（共享）<br/>多个 Cache 有此行<br/>与内存一致
    I: Invalid（无效）<br/>本 Cache 无此行

    M --> S: 其他核读取
    M --> I: 其他核写入
    E --> S: 其他核读取
    E --> I: 其他核写入
    S --> I: 其他核写入
    S --> E: 其他核 Invalidate
    I --> E: 本核读取（无其他副本）
    I --> S: 本核读取（有其他副本）
    I --> M: 本核写入
    E --> M: 本核写入
```

| 状态 | 含义 | 可读 | 可写 | 内存是否最新 |
|------|------|------|------|-------------|
| **M** Modified | 数据已修改，仅本 Cache 有 | ✅ | ✅ | ❌ |
| **E** Exclusive | 数据干净，仅本 Cache 有 | ✅ | ✅ | ✅ |
| **S** Shared | 数据干净，多个 Cache 有 | ✅ | ❌ | ✅ |
| **I** Invalid | 无效 | ❌ | ❌ | — |

### 5.2 MOESI 扩展

在 MESI 基础上增加 **O (Owner)** 状态：

| 状态 | 说明 |
|------|------|
| **O** Owner | 数据已修改或干净，本 Cache 负责向其他 Cache 提供数据，内存可能过期 |

> MOESI 的优势：允许从 Owner 状态的 Cache 直接转发数据给请求者，避免每次都要写回内存。

---

## 6. 多核架构

### 6.1 典型多核 SoC 结构

```mermaid
graph TB
    subgraph Core0
        C0_L1I["L1 I-Cache"]
        C0_L1D["L1 D-Cache"]
        C0_CORE["Core 0"]
    end

    subgraph Core1
        C1_L1I["L1 I-Cache"]
        C1_L1D["L1 D-Cache"]
        C1_CORE["Core 1"]
    end

    subgraph 共享资源
        L2["L2 Cache<br/>（共享或分 Bank）"]
        BUS["互连总线<br/>Crossbar / NoC"]
        DRAM["DRAM 控制器"]
    end

    C0_CORE --> C0_L1I
    C0_CORE --> C0_L1D
    C1_CORE --> C1_L1I
    C1_CORE --> C1_L1D
    C0_L1D --> L2
    C1_L1D --> L2
    L2 --> BUS
    BUS --> DRAM
```

### 6.2 多核互连

| 互连方式 | 延迟 | 带宽 | 扩展性 | 适用规模 |
|----------|------|------|--------|----------|
| **共享总线** | 低 | 低 | 差（4-8 核） | 小型 SoC |
| **Crossbar** | 低 | 高 | 中等（8-16 核） | 中型 SoC |
| **NoC（片上网络）** | 较高 | 高 | 好（64+ 核） | 大型芯片 |

---

## 7. SMT（同时多线程）

SMT（Intel 称 Hyper-Threading）让一个物理核心同时执行多个线程：

```mermaid
graph LR
    subgraph "单线程核心"
        ST1["线程 A<br/>ALU 30% 利用率<br/>Cache 等待时空闲"]
    end

    subgraph "SMT 核心（2 线程）"
        MT1["线程 A<br/>使用 ALU 和 Cache"]
        MT2["线程 B<br/>使用 ALU 和 Cache<br/>填补 A 的空闲"]
    end
```

| 特性 | 说明 |
|------|------|
| **共享资源** | ALU、Cache、分支预测器 |
| **独立资源** | 寄存器文件、PC、指令指针 |
| **性能提升** | 通常 20-40%（不是 2 倍！） |
| **RISC-V 支持** | 一些核心实现了类 SMT 机制 |

---

## 小结

| 技术 | 解决的问题 | 代价 |
|------|-----------|------|
| 超标量 | IPC > 1 | 硬件复杂度、功耗 |
| 乱序执行 | 挖掘 ILP | ROB、重命名表、CDB |
| 缓存一致性 | 多核数据一致性 | MESI/MOESI 协议开销 |
| 多核 | 挖掘 TLP | 互连、一致性流量 |
| SMT | 提高单核利用率 | 资源竞争、安全侧信道 |

→ 下一节：[开源 RISC-V 核心](./opensource-cores.md)
