# SoC 设计全流程总览 —— 从一行规格到一片芯片

> 一句话概括：一片大规模 SoC（System on Chip，片上系统）从立项到量产，要走过**规格、架构、前端、后端、流片、制造、封装、测试、量产**九个阶段，跨越 Fabless 设计公司、IP 供应商、EDA 厂商、Foundry（晶圆厂）、OSAT（外包封测厂）五类角色，通常耗时 18–36 个月、耗资数千万到数亿美元。
> **工程师视角**：作为在"芯片-软件"交界处工作的工程师，你日常打交道的 SoC 手册、设备树、寄存器表，背后是一整套设计流程的产物。理解这套流程，不是为了去画版图，而是为了在调试时能判断"这是设计阶段定的、还是制造偏差、还是固件没用对"——知道一个现象该去问谁、该查哪份文档。

### 关键术语

| 缩写 | 全称 | 含义 |
|------|------|------|
| SoC | System on Chip | 片上系统，把 CPU/GPU/内存控制器/IO 等集成在一颗芯片上 |
| IP | Intellectual Property (core) | 可复用的设计模块，如 Arm CPU 核、Synopsys PCIe 控制器 |
| RTL | Register Transfer Level | 寄存器传输级，用硬件描述语言描述数字电路的数据流与控制流 |
| HDL | Hardware Description Language | 硬件描述语言，主要指 Verilog / VHDL / SystemVerilog |
| GDSII | Graphic Data Stream Information Interchange | 流片用的版图数据格式，交付给 Foundry 的最终产物 |
| EDA | Electronic Design Automation | 电子设计自动化工具，如综合、布局布线、验证工具 |
| PDK | Process Design Kit | 工艺设计套件，Foundry 提供给设计方的"工艺规则包" |
| Fabless | Fabrication-less | 无晶圆厂设计公司，自己不制造，如高通、英伟达、海思 |
| IDM | Integrated Device Manufacturer | 集成器件制造商，设计+制造+封测一体，如 Intel、三星 |
| Foundry | — | 晶圆代工厂，按设计方提供的版图代工制造，如 TSMC、三星 |
| OSAT | Outsourced Semiconductor Assembly and Test | 外包封测厂，做封装与成品测试，如日月光/安靠 |
| Tape-out | — | 流片，把版图数据交付 Foundry 制造的里程碑节点 |
| PPA | Performance / Power / Area | 性能、功耗、面积，芯片设计的核心三角权衡 |
| DFT | Design for Test | 可测性设计，在芯片内插入便于测试的硬件结构 |
| STA | Static Timing Analysis | 静态时序分析，不跑仿真即验证所有路径是否满足时序 |
| P&R | Place and Route | 布局布线，后端物理设计的核心步骤 |
| DRC/LVS | Design Rule Check / Layout Versus Schematic | 设计规则检查 / 版图与原理图一致性检查 |
| Signoff | — | 签核，流片前各维度（时序/功耗/物理）的最终验收 |
| PPA / Node | — | 工艺节点，如 7nm/5nm/3nm，代表工艺代际 |
| ESL | Electronic System Level | 电子系统级，用高级语言做架构级建模与仿真 |
| RTL/GDS/... | — | 见上 |

> **跨角色对照**：Fabless 设计方（写 RTL、做物理设计）↔ Foundry（按 GDSII 制造晶圆）↔ OSAT（切割封装测试）↔ IP 供应商（卖 CPU/PHY 等模块授权）↔ EDA 厂商（卖工具链）。这是当今半导体产业的主流分工——一颗芯片的设计与制造被切分到多家公司协同完成。

### 1.1 前置知识

| 需要了解 | 参考文档 |
|----------|----------|
| 数字电路基础（组合/时序逻辑、寄存器、时钟） | — |
| Verilog/SystemVerilog 基本语法 | — |
| 计算机体系结构（流水线、Cache、内存层级） | — |
| 片内总线协议（AXI/CHI） | [../interconnect/01-互联总线与协议全景辨析.md](../interconnect/01-互联总线与协议全景辨析.md) |
| DDR 内存基础 | [../ddr/README.md](../ddr/README.md) |

---

## 1. 概述

### 1.2 系统上下文

**项目定位**：本文是整个 SoC 流程笔记的"地图"。它不深入任何单个工具或阶段的实现细节，而是回答三个问题——**这片芯片是谁造的？经历了哪些阶段？每个阶段产出什么、交给谁？** 读完本文，你应该能在看到任何 SoC 相关名词（比如"Tape-out 了"、"这颗 5nm"、"走 OSAT 封装"）时，迅速定位它在流程的哪个环节、涉及哪类角色。

**软硬件耦合点**（本规范最强调的视角）：

- **设计方 ↔ Foundry**：设计方拿 PDK 写 RTL、做版图；Foundry 按 GDSII 制造。PDK 是两者的契约——它规定了某一工艺下能用的器件、设计规则（最小线宽/间距）、电气参数（SPICE 模型）。**PDK 不对齐，版图就是废纸。**
- **IP 供应商 ↔ 设计方**：Arm 卖 CPU 核给高通，高通把它和自研 GPU、Synopsys 的 PCIe PHY 集成。IP 的"软核/硬核"形态决定了集成方式——软核给 RTL 自己综合，硬核给已物理实现的 GDSII 直接摆放。**IP 交付质量直接决定集成难度。**
- **芯片 ↔ 固件/OS**：芯片定义的中断号、地址映射、时钟树、电源域，最终变成设备树、ACPI 表、SBI/TF-A 平台代码。你在固件里调的每一个寄存器，都是架构与前端阶段设计决策的固化。
- **制造 ↔ 良率**：设计阶段每个"过紧的时序约束"、"过于激进的密度"，都会在量产时变成良率损失。设计与制造不是两个孤立环节，而是通过 DFM（Design for Manufacturing，可制造性设计）耦合。

**跨实现/跨架构对比**：

| 对比维度 | Fabless（高通/英伟达/海思） | IDM（Intel/三星） | RISC-V 新势力（SiFive/算能/进迭时空） |
|----------|------|------|------|
| 设计与制造 | 分离，靠 Foundry 代工 | 一体，自研工艺+自设计 | 分离，依赖 Foundry + 公开 IP |
| 工艺选择 | 选 TSMC/三星节点 | 用自家工艺（Intel 18A 等） | 多用成熟节点（7nm/12nm）控制成本 |
| CPU IP | 授权 Arm | 自研或授权 Arm | 自研或用开源 RISC-V |
| 设计周期 | 18–30 个月 | 类似，但工艺迭代更激进 | 12–24 个月，强调敏捷 |
| 软件生态 | 厂商驱动 BSP | 自家软硬协同（如 Intel+软件栈） | 依赖开源 + Linux 主线 |

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#f8fafc", "primaryTextColor": "#1e293b", "primaryBorderColor": "#475569", "lineColor": "#64748b", "secondaryColor": "#f1f5f9", "secondaryBorderColor": "#94a3b8", "tertiaryColor": "#f8fafc", "fontFamily": "\"trebuchet ms\", verdana, arial, sans-serif"}}}%%
flowchart LR
    subgraph Design["设计方（Fabless / IDM 设计部门）"]
        Spec["规格与架构"]
        RTL["RTL + 验证"]
        PD["物理设计 + Signoff"]
    end
    subgraph Supply["供应商"]
        IP["IP 供应商<br/>Arm/Synopsys"]
        EDA["EDA 厂商<br/>Synopsys/Cadence/Siemens"]
    end
    subgraph Fab["制造方"]
        Foundry["Foundry<br/>TSMC/三星/Intel"]
        OSAT["OSAT<br/>日月光/安靠"]
    end
    subgraph SW["软件方"]
        Firmware["固件/OS/BSP"]
    end
    IP -->|"RTL/GDSII 授权"| Spec
    EDA -->|"工具链"| RTL
    EDA -->|"工具链"| PD
    Foundry -->|"PDK"| Spec
    PD -->|"GDSII Tape-out"| Foundry
    Foundry -->|"裸 die"| OSAT
    OSAT -->|"封测后成品"| Customer["客户/系统厂商"]
    Spec -->|"手册/寄存器表"| Firmware
    Firmware -->|"BSP"| Customer
```

> **如何读这张图**：横向是五个角色，纵向是数据/产物流向。注意三条关键耦合线——**IP 与 PDK 在规格阶段就进入设计方**（决定能用什么）、**GDSII 是设计方交付 Foundry 的唯一产物**（流片里程碑）、**固件依赖芯片手册**（架构阶段就要把软件接口定下来）。Fabless 模式的本质就是：设计方不拥有 Foundry，靠 GDSII 和 PDK 两个契约把设计与制造解耦。

> **核心要点**：现代半导体产业的主流是 Fabless + Foundry + OSAT 的分工模式。设计方掌握 RTL 与物理设计，Foundry 掌握工艺与制造，OSAT 掌握封装测试。三者的耦合点是 PDK（设计→制造）、GDSII（设计→制造产物）、手册（设计→软件）。理解这三个契约，就理解了整个产业链的接口。

---

## 2. 全流程地图：九个阶段一张图

先看一张完整的流程图，建立全局印象，再逐节展开。

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#f8fafc", "primaryTextColor": "#1e293b", "primaryBorderColor": "#475569", "lineColor": "#64748b", "secondaryColor": "#f1f5f9", "secondaryBorderColor": "#94a3b8", "tertiaryColor": "#f8fafc", "fontFamily": "\"trebuchet ms\", verdana, arial, sans-serif"}}}%%
flowchart TD
    S1([①规格定义]) --> S2([②架构设计])
    S2 --> S3([③前端 RTL 设计])
    S3 --> S4([④功能验证])
    S4 --> S5([⑤逻辑综合 + STA])
    S5 --> S6([⑥DFT 插入])
    S6 --> S7([⑦后端物理设计])
    S7 --> S8([⑧物理验证 + Signoff])
    S8 --> TO{{Tape-out 流片}}
    TO --> S9([⑨制造封装测试量产])

    subgraph FE["前端（Frontend）"]
        S3
        S4
        S5
        S6
    end
    subgraph BE["后端（Backend）"]
        S7
        S8
    end
    subgraph MFG["制造（Manufacturing）"]
        S9
    end
```

> **如何读这张图**：①②是"想清楚要做什么"的阶段，产出规格与架构文档；③④⑤⑥是前端，把架构翻译成可验证的 RTL 并转向门级网表；⑦⑧是后端，把网表变成物理版图并通过签核；⑨是制造封测。**Tape-out 是分水岭**——之前是"在电脑上画芯片"，之后是"在工厂里造芯片"。Tape-out 一旦发生，纠错成本就从"改 RTL 重跑"变成"几十万到上千万美元的掩模费打水漂"。

### 2.1 一个具体例子：一颗服务器 SoC 的诞生

光看流程图太抽象，用一颗**RISC-V 服务器 SoC**（参考 SG2046 这类产品）走一遍，让你看到每个阶段到底产出什么。

| 阶段 | 这颗 SoC 在做什么 | 典型产出 |
|------|------|------|
| ①规格 | 定位"64 核 RISC-V 服务器，支持 DDR5/PCIe5/CXL，目标 2.0GHz" | 规格书、PPA 目标表 |
| ②架构 | 选 64 个 SG2042 同系列核、CMN-700 互联、Synopsys DDR5 控制器 | 架构文档、地址映射、IP 清单 |
| ③前端 RTL | 集成 IP，写自研模块（如安全协处理器）的 SystemVerilog | RTL 代码树 |
| ④验证 | 跑 UVM 回归、启动 Linux、跑 RISC-V 架构测试 | 验证报告、覆盖率报告 |
| ⑤综合+STA | 用 Design Compiler 综合，PrimeTime 收时序到 2.0GHz | 门级网表、时序报告 |
| ⑥DFT | 插扫描链、加 memory BIST | 带 DFT 的网表、ATPG 测试向量 |
| ⑦后端 | Floorplan、布局布线、时钟树（多时钟域） | 版图（DEF/GDSII） |
| ⑧签核 | DRC/LVS、最终 STA、功耗/EM/IR 分析 | Signoff 报告 |
| Tape-out | 把 GDSII 交给 TSMC | 流片里程碑 |
| ⑨制造封测 | TSMC 造晶圆 → 日月光倒装封装 → 测试分 bin | ES（工程样片）、量产芯片 |

> **核心要点**：每个阶段的产出都是下一阶段的输入。**Tape-out 之前的所有阶段都在"可逆"区间**——发现 bug 改 RTL 重跑就行（虽然贵）。**Tape-out 之后进入"半不可逆"区间**——制造已开始，掩模费已花；如果流片回来发现严重 bug，要么改 metal 重流（Eco/金属层改版，较便宜），要么全改重流（极贵）。所以签核阶段会反复检查到近乎偏执。

### 2.2 阶段的时间与人力分布

不同阶段花的时间差异巨大。下面是一颗中等规模 SoC（如手机 SoC）的典型分布（数值为业界经验区间，具体项目差异大）：

| 阶段 | 占设计周期比例 | 典型时长 | 人力主力 |
|------|:------:|------|------|
| ①规格 + ②架构 | 15–20% | 3–6 月 | 架构师、系统工程师 |
| ③前端 RTL | 15–20% | 4–6 月 | RTL 工程师 |
| ④功能验证 | 30–40%（最大头） | 与 RTL 并行且更长 | 验证工程师（人数常多于 RTL） |
| ⑤综合 + STA | 5–10% | 1–2 月 | 前端工程师 |
| ⑥DFT | 5% | 1 月 | DFT 工程师 |
| ⑦后端物理设计 | 15–20% | 4–6 月 | 后端工程师 |
| ⑧签核 | 5–10% | 1–2 月 | 后端 + 专项工程师 |
| ⑨制造封测 | 另算 | 3–6 月（流片到拿回芯片） | Foundry/OSAT |

> **如何读这张表**：注意**验证占了 30–40%**——这是设计阶段最大的单项投入。一个常见误解是"芯片设计就是写 RTL"，实际上写 RTL 的人往往比验证的人少。后端物理设计也占近 1/5，因为先进工艺下布局布线、时序收敛、IR 压降的难度急剧上升。制造封测的时间不算在"设计周期"里，但从立项到芯片到手通常再加 3–6 个月。

---

## 3. 钱花在哪：成本构成

理解芯片流程，必须理解钱。一颗芯片的成本分两大块：**一次性投入（NRE）**和**单颗制造成本**。

### 3.1 NRE：一次性工程费用

NRE（Non-Recurring Engineering）是"不管你造多少颗都要花一次"的钱，包括设计人力、EDA 工具授权、IP 授权费、掩模费。

| NRE 项 | 典型金额（先进工艺） | 说明 |
|------|------|------|
| 掩模（Mask）费 | 3nm 约 50M$，5nm 约 20–40M$，7nm 约 10–15M$ | **最大单项**，先进工艺掩模套数多、贵到离谱 |
| EDA 工具年费 | 数百万到千万美元/年 | 三大厂（Synopsys/Cadence/Siemens）整套授权 |
| IP 授权费 | Arm CPU 核数百万到千万美元级 | 一次性 + 版税两段 |
| 设计人力 | 数千万美元/项目 | 大型 SoC 团队数百人 |
| 流片服务费 | 数百万美元 | Foundry 工程支持 |

> **待确认**：上述掩模费为业界常引用的经验区间，不同 Foundry、不同节点、不同掩模套数差异极大，实际以 Foundry 报价为准。

### 3.2 单颗成本：良率与规模

单颗芯片成本 ≈ (晶圆成本 + 封测成本) / (良率 × 每片晶圆好die数)。

举个数值例子（示意，非真实报价）：一片 12 英寸 5nm 晶圆成本约 17000 美元，一片晶圆面积约 70000 mm²，单 die 假设 100 mm²，则每片晶圆理论上切出 700 颗 die；若良率 80%，则好 die 约 560 颗，单 die 晶圆成本约 30 美元；加上封装测试约 10–30 美元，单颗成本约 40–60 美元。**注意**：这还没摊销 NRE——如果只卖 100 万颗，1 亿美元掩模费每颗就要摊 100 美元。

> **核心要点**：芯片是典型的"高固定成本 + 低边际成本"生意。NRE（尤其掩模费）是准入门槛，量大才能摊薄。这就是为什么先进工艺只有少数大厂能用得起——销量不够，单颗成本就降不下来，宁可退守成熟节点。3nm 掩模 50M$ 意味着至少要卖数千万颗才有经济性。

---

## 4. SoC 的典型组成：到底"片上"集成了什么

在深入每个阶段前，先看清楚"SoC 到底是个什么东西"。下图是一颗现代服务器/AI SoC 的典型组成：

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#f8fafc", "primaryTextColor": "#1e293b", "primaryBorderColor": "#475569", "lineColor": "#64748b", "secondaryColor": "#f1f5f9", "secondaryBorderColor": "#94a3b8", "tertiaryColor": "#f8fafc", "fontFamily": "\"trebuchet ms\", verdana, arial, sans-serif"}}}%%
flowchart TD
    subgraph SoC["SoC 内部"]
        subgraph Compute["计算核"]
            CPU["CPU 集群<br/>RISC-V/Arm/x86"]
            GPU["GPU/NPU<br/>AI 加速"]
            Accel["专用加速器<br/>加密/视频/网络"]
        end
        subgraph MemCtrl["内存与缓存"]
            L3["L3/LLC Cache"]
            DDRC["DDR 控制器"]
            HBMCtrl["HBM 控制器"]
        end
        subgraph IO["IO 子系统"]
            PCIe["PCIe RC/EP"]
            CXL["CXL"]
            USB["USB/以太网/SATA"]
        end
        subgraph Fabric["片内互联"]
            NoC["NoC / Mesh<br/>CMN-700 / 自研"]
        end
        subgraph SysCtrl["系统控制"]
            CLK["时钟/复位/电源管理"]
            Sec["安全/信任根/TEE"]
            Debug["调试/JTAG/Trace"]
        end
    end
    CPU --> NoC
    GPU --> NoC
    Accel --> NoC
    NoC --> L3
    L3 --> DDRC
    L3 --> HBMCtrl
    NoC --> PCIe
    NoC --> CXL
    NoC --> USB
    CLK -.->|"全局"| SoC
    Sec -.->|"全局"| SoC
    Debug -.->|"全局"| SoC
```

> **如何读这张图**：SoC = 计算 + 内存 + IO + 互联 + 系统控制，五类模块通过片内互联（NoC/Mesh）连成一体。**互联是骨架**（详见 [../interconnect/](../interconnect/)），**IP 是肉**。架构师的工作，本质上就是"选哪些 IP、怎么连、怎么分地址、怎么管时钟电源"。注意右下角"系统控制"——时钟树、电源域、安全根、调试设施，这些往往不出现在产品宣传里，却决定了固件能不能正常初始化、能不能调起来。

### 4.1 IP 的来源：买还是自研

SoC 里的模块不是都自己写的，大部分是买来的：

| IP 类型 | 典型来源 | 软核/硬核 | 谁在用 |
|------|------|------|------|
| CPU 核 | Arm（Cortex/Neoverse）、SiFive、T-Head | 多为软核（RTL） | 几乎所有非 x86 SoC |
| GPU | Arm Mali、Imagination、自研 | 软核 | 手机/服务器 SoC |
| DDR PHY/控制器 | Synopsys、Cadence、Rambus | 硬核（PHY）/软核（控制器） | 所有带 DDR 的 SoC |
| PCIe/CXL/USB PHY | Synopsys、Cadence | 硬核 | 所有高速 IO SoC |
| 互联 | Arm CMN、Arteris、自研 | 软核 | 大规模 SoC |
| 安全 | Arm TrustZone、RISC-V PMP/Tee | 软核 | 通用 |
| 物理库（标准单元/IO） | TSMC/Synopsys/Cadence | 硬核 | 所有 Foundry 工艺 |

**软核 vs 硬核**是 IP 集成的核心区分：

- **软核（Soft IP）**：交付 RTL，设计方自己综合、布局。灵活、可移植到不同工艺，但性能/面积由设计方综合质量决定。CPU 核、控制器逻辑通常是软核。
- **硬核（Hard IP）**：交付已物理实现的 GDSII（针对特定工艺），直接摆放。性能/面积/功耗最优，但不可移植，绑定单一工艺。PHY（DDR/PCIe/USB）、标准单元库通常是硬核。

> **核心要点**：SoC 设计不是"从零写所有模块"，而是"集成一堆 IP + 自研少量核心逻辑"。架构师 70% 的工作是 IP 选型与集成，30% 是自研差异化模块。**软核靠综合、硬核靠摆放**——这两类 IP 的集成流程在后端阶段完全不同。

---

## 5. 现代趋势：Chiplet、3D、AI SoC

经典的"单 die 单片 SoC"在 7nm 以下遇到三重墙：**良率墙**（die 越大良率越低）、**光刻墙**（掩模成本爆炸）、**功耗墙**（单片功耗密度爆表）。业界应对是三条路线。

### 5.1 Chiplet：把大 die 拆成小 die

Chiplet（小芯片）思路：把一颗大 SoC 拆成多个小 die，在封装内用高速互连（如 UCIe）连起来。代表：AMD Zen 系列的 chiplet CPU、Intel Ponte Vecchio、Intel/AMD 的多 die 设计。

| 对比维度 | 单片 SoC（Monolithic） | Chiplet |
|------|------|------|
| 良率 | die 大，良率低 | die 小，良率高 |
| 工艺 | 全用一个节点 | 可混合（计算核用 5nm，IO 用 12nm） |
| 互联 | 片内总线 | 封装内 D2D（UCIe/NVLink-C2C） |
| 成本 | 掩模一套但贵 | 掩模多套但每套小，可复用 |
| 设计 | 一次 | 多 die 协同设计复杂 |

### 5.2 先进封装：2.5D / 3D

- **2.5D**：多 die 摆在同一基板上，通过硅中介层（Silicon Interposer）高密度互连。代表：台积电 CoWoS（NVIDIA H100 用）。
- **3D**：die 垂直堆叠，用 TSV（Through-Silicon Via，硅通孔）连接。代表：HBM（高带宽内存）与计算 die 的 3D 堆叠、台积电 SoIC、Intel Foveros。

### 5.3 AI 加速器 SoC

AI 大模型催生了一类新 SoC：以张量计算为核心、HBM 为内存、NVLink/UALink 为互联。代表：NVIDIA H100/B200、AMD MI300、Google TPU、Tenstorrent。这类 SoC 的设计重心从"通用计算"转向"数据流与内存带宽"——**算力可堆，带宽才是天花板**。

> **核心要点**：先进工艺下，"单片大 SoC"越来越不经济，产业向"Chiplet + 先进封装 + 异构工艺"演进。这意味着芯片设计的边界从"单 die"扩展到"多 die 系统"，封装内互连（UCIe）和 3D 堆叠成为新的设计维度。对软件工程师而言，多 die SoC 带来了 NUMA、die 间延迟、跨 die 一致性等新问题——硬件不再是"一个平面"。

---

## 6. 全流程的角色与协作

把九个阶段、五类角色、三种产物放一张表，建立最后的全局视图：

| 阶段 | 主力角色 | 用到的关键 EDA | 产出 | 交给谁 |
|------|------|------|------|------|
| ①规格 | 架构师、产品 | Excel/Word/内部工具 | 规格书、PPA 目标 | ②架构 |
| ②架构 | 架构师 | ESL 工具（如 Gem5/Synopsys Platform Architect） | 架构文档、IP 清单、地址映射 | ③前端、固件 |
| ③前端 RTL | RTL 工程师 | 编辑器 + SystemVerilog | RTL 代码树 | ④验证 |
| ④验证 | 验证工程师 | VCS/Xcelium、UVM、JasperGold | 验证报告、覆盖率 | ⑤综合 |
| ⑤综合+STA | 前端工程师 | Design Compiler/Genus、PrimeTime | 门级网表、时序报告 | ⑥DFT、⑦后端 |
| ⑥DFT | DFT 工程师 | Tessent/TestKompress | 带 DFT 网表、ATPG 向量 | ⑦后端 |
| ⑦后端 | 后端工程师 | ICC2/Innovus、CTS | 版图 | ⑧签核 |
| ⑧签核 | 后端 + 专项 | Calibre、PT、Voltus | Signoff 报告 | Foundry |
| Tape-out | 项目经理 | — | GDSII | Foundry |
| ⑨制造封测 | Foundry/OSAT | — | 裸 die → 封装芯片 → 测试分 bin | 客户 |

> **核心要点**：整个流程是"前一段产出喂给后一段"的瀑布，但验证和签核是两个**回环点**——验证发现 bug 改 RTL，签核不过改物理设计，都会回到上游。**EDA 工具是贯穿全流程的"第二语言"**，三大厂商（Synopsys/Cadence/Siemens）几乎垄断了先进工艺的工具链，这也是为什么 EDA 授权费如此之高。

---

## 7. 本文之后：怎么读下去

本文建立了全景地图。接下来的四章按流程顺序深入每个关键环节：

- [02-规格定义与架构设计](./02-规格定义与架构设计.md)：讲清楚"想做什么"——PPA 权衡、IP 选型、软硬件协同。
- [03-前端设计RTL与验证](./03-前端设计RTL与验证.md)：讲清楚"怎么把架构变成可验证的代码"——RTL、UVM、综合、STA、DFT。
- [04-后端物理设计与签核](./04-后端物理设计与签核.md)：讲清楚"怎么把代码变成版图"——Floorplan、P&R、签核。
- [05-流片制造封装与量产测试](./05-流片制造封装与量产测试.md)：讲清楚"怎么把版图变成芯片"——Tape-out、Fab、封装、测试、良率。

如果你只读一章，读 02——它最能改变你"看芯片手册"的方式。

---

## 参考资料

- [IRDS (International Roadmap for Devices and Systems)](https://irds.ieee.org/) — 参考了工艺节点演进趋势与路线图
- [TSMC Technology Overview](https://www.tsmc.com/english/dedicatedFoundry/technology/index.htm) — 参考了 N7/N5/N3 节点信息
- [Synopsys/Cadence/Siemens EDA 产品文档](https://www.synopsys.com/) — 参考了 EDA 工具链分工
- [UCIe Specification](https://www.uciexpress.org/) — 参考了 Chiplet 片间互连
- [../interconnect/01-互联总线与协议全景辨析.md](../interconnect/01-互联总线与协议全景辨析.md) — 参考了片内/片间互连分层
