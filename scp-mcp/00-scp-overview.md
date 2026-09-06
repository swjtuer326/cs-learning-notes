# 设计背景:SCP 为什么存在

> SCP 不是一颗"随便找的 MCU",而是一套被标准化了的系统管理架构(PCSA)中的一环。本篇回答三个问题:超大规模 SoC 的电源管理为何要从 AP 里拆出来、Arm 用哪套标准定义了这个分工、以及 SCP-firmware 这个参考实现管到哪一层。事实来源:SCP-firmware 仓库 `readme.md`、SCMI 规范 DEN0056(DEN0056F)§1 引言、[PCSA DEN0050](https://developer.arm.com/documentation/den0050/d/)(未下载,仅作链接依据)。
>
> **本章位置**(见 [README 全景](./README.md)):全景图上所有角色(AP/TF-A/SCP/MCP)与两条接口(SCMI/PSCI)的"为什么"都在这篇定义;全景图与旅程表收在 [README](./README.md),此后各章的"本章位置"都相对它而言。

## 1. 问题:超级 SoC 的电源管理谁来管

服务器和旗舰移动 SoC 里,AP(Application Processor,应用处理器)只是整颗芯片电源管理对象中的一小部分。一颗芯片上还有 GPU、NPU、DDR、各种一致性互联,以及几十个需要独立上/下电、调频、调压的电源域。这些需求来自四面八方:

- **OS**:想进 idle、想调 CPU 频率
- **安全固件**:要复位某个外设、要通知系统掉电
- **基带/管理控制器**:要读温度、要协调系统功耗
- **硬件事件**:过热报警、电源轨跌落

让 AP 直接操作这些硬件有两个根本问题:一是 AP 休眠或断电时系统仍要能响应唤醒和复位;二是电源/时钟/复位的具体实现(哪几个寄存器、什么顺序、什么延迟)不该暴露给处于最高特权、职责边界过大的操作系统。

行业里早就有一个公认的解法:**放一个专用的、极低功耗的控制器进芯片,让它在自己的电源域里常驻,替所有组件处理这些底层管理操作**。SCMI(System Control and Management Interface,系统控制与管理接口)规范把这个趋势写进了引言:

> There is a strong trend in the industry to provide microcontrollers in systems to abstract various power, or other system management tasks, away from APs.
>
> —— SCMI DEN0056§1,Introduction

## 2. PCSA:把"系统管理"从 AP 里拆出来

Arm 用 **PCSA(Power Control System Architecture)**,DEN0050 把这套思路规范成了架构。它定义了 SCP(以及配套概念)该是什么样:

- **SCP(System Control Processor)**:一颗专门用来把电源与系统管理从 AP 身上抽象走的处理器;
- **MCP(Manageability Control Processor)**:同思路的"可管理性入口",面向服务器这类要求带外管理能力的 SoC(对应下一节)。蓝图给它的两条硬件通路是 **SPMI**(System Power Management Interface,连电源管理芯片的串行接口)与 **SMCF**(System Monitoring Control Framework,组织整芯片监控的框架)——注意这是规划,公开参考实现从未启用(见 [09](./09-mcp-management-hardware.md));详情在 MCP 主线([06](./06-mcp-manageability.md) 起)展开;
- 配套的硬件单元概念:**PPU(Power Policy Unit)** 与 **PIK(Power Integration Kit)**(见仓库 `doc/glossary.md`)。

SCMI 规范这样描述 SCP 的角色(§1):

> PCSA defines the concept of the System Control Processor (SCP), a processor that is used to abstract power and system management tasks from the APs. The SCP can take requests from APs and other system agents. It can coordinate these requests and place components in the platform into appropriate power and performance states.

三个关键词值得记住:**接收请求、协调请求、下达动作**。SCP 不是某个功能的实现细节,它是系统里一切电源/性能类诉求的汇聚点。

## 3. SCP 与 MCP:分工不同,代码同源

仓库 `readme.md` 的 Introduction 直接给了分工:

| | SCP | MCP |
| --- | --- | --- |
| 目标 | 抽象电源与系统管理,让 AP 专注运行用户负载 | 给 SoC 提供"可管理性"入口 |
| 典型服务 | 电源域/性能/时钟/复位/电压 | 服务器场景的带外管理入口 |
| 出现场景 | 几乎所有 Arm CSS | 面向服务器的 SoC |

一条经验:SCP 保障这片硅的功能运行(电源、时钟、性能),MCP 提供对它的管理与监控通道——这是规范/平台文档层面的分工,别当成代码事实:公开参考实现里 MCP 的实职远小于此,而 SCP 的调压链路本来就要直达 PMIC(两层对照见 [06](./06-mcp-manageability.md) §1)。两者都基于同一套 SCP-firmware 代码构建,差别在**选择的模块和配置**——这是理解后文框架的关键伏笔。

## 4. SCP-firmware 参考实现管到哪一层

Arm 的参考实现(`readme.md` 的 Functionality 一章)列出的运行时服务:

| 服务 | 说明 |
| --- | --- |
| 初始化系统以支持 AP 启动 | 上电/时钟就绪后才放 AP 复位 |
| 电源域管理 | 把设备/域置入各种省电状态 |
| 系统电源管理 | shutdown / suspend / reset 等整机状态 |
| 性能域管理(DVFS) | 电压频率动态调节(DVFS,Dynamic Voltage and Frequency Scaling) |
| 时钟管理 | 平台管时钟的查/设 |
| 传感器管理 | 读温度等、数值变化通知 |
| 复位域管理 | 分层复位设备/域 |
| 电压域管理 | 电压域配置 |

加上 SCMI 平台侧实现(SCMI,platform-side)和对多控制处理器平台的支持。这条服务清单与 SCMI 规范 §1 列出的接口能力几乎一一对应——**SCP-firmware 就是 SCMI 的"平台"实现**。

## 5. 为什么不能干脆让 AP 自己管

把管理职责拆给一颗独立核,换来四样东西:

| 收益 | 反面(不用会怎样) |
| --- | --- |
| **断电后仍可控**:AP 全部断电时 SCP 在自己的电源域里仍运行,能响应唤醒、执行 reset/off | AP 休眠后就无人管理系统,关机/唤醒无从谈起 |
| **单一汇聚点**:所有 agent 的请求被统一仲裁,共享资源的引用计数有了归属 | 多方各自操作寄存器,状态相互覆盖冲突 |
| **实现自由度**:寄存器时序封装在 SCP 固件内部,OS/固件只见语义 | 实现细节绑定到 OS 代码,改硬件要改软件栈 |
| **降低 OS 复杂度**:OS 只发"把域 3 放到睡眠"这种请求 | OS 要理解全部电源拓扑与时序 |

代价同样真实:多了一颗要开发、要调、要验证的固件;SCP 固件成了新的故障面(它失效则整机停止工作);AP 每次电源操作多一跳跨核通信延迟。

## 6. 为什么要用专用 MCU,而不是在上面跑个系统

SCP 的传统实现是 Cortex-M 级内核——仓库 `arch/arm/arm-m` 按 ARMv6-M/ARMv7-M/ARMv8-M 条件编译(MPU 模块也分 `armv7m_mpu`/`armv8m_mpu`/`armv8r_mpu`);近期又新增 AArch64(`arch/arm/aarch64`,见 `doc/architecture_support.md`)。它不跑 Linux,连 RTOS 都不用——`doc/framework.md` 描述运行时是"事件循环 + 各模块协同"的单体固件:

- **裸金属 + 事件驱动**:中断来了就处理、事件排队,没有进程/调度器;
- **资源受芯片 SRAM(Static Random-Access Memory,静态随机存取存储器)限制**:代码和运行态都在芯片内 SRAM(内存模式区分单/双区域,见 `doc/framework.md` §Firmware),这约束了代码形态——配置驱动、表驱动、不跑重型框架;
- **确定性优先**:电源操作要求快速、可预期,事件循环 + 有限状态机比抢占式 RTOS 更容易做到。

这一选择是**成本与确定性**权衡的结果:在芯片内划出一颗常年运行的小核的供电与面积,换来毫秒级响应。代价是固件开发舒适度(没有现成 OS 服务)。反过来看,这套事件驱动、配置表驱动的代码自洽又紧凑,没有 OS 层隔离耦合,可以完整通读——是一份难得的裸机工程样本。

## 7. 与其它技术路线的对比

从更大的范围看,"谁在管电源/系统"这条职能不只 SCP 一种解法——BMC、RISC-V 生态、移动私有实现各有一套。看它们管到哪一层、标准化到什么程度,SCP 的生态位才显出来:

| 路线 | 管什么粒度 | 典型形态 | 与 SCP 的关系 |
| --- | --- | --- | --- |
| **BMC**(服务器板卡) | 板级:整机带外管理、IPMI/REDFISH | 独立 BMC 芯片 + 专用 SoC,跑自己的 OS | SCP 在 Die 内管电源时序,BMC 在板上管整机——各管一段,服务器 SoC 两者都有 |
| **RISC-V 生态** | 无 PCSA 等价标准 | 各家自行做服务处理器;OpenSBI 只管固件层引导 | 没有统一"SCP 接口"标准,管理固件形态因厂商而异 |
| **移动 SoC 私有实现** | SoC 内电源管理 | 各家私有 PMIC(Power Management IC,电源管理芯片)/电源固件 | 思路相同(独立控制核),但没有公开规范与参考实现可供学习 |

对比的结论:SCP 的价值不只是"一颗控制核",而是 **PCSA 定义的分工 + SCMI 定义的接口 + 开源的参考实现**三者齐全——这是生态里少有的可系统性学习的闭环。BMC 那条线管的是板级,和 SCP 不是同一层,别混。

## 8. 生态现状:参考实现与国际分工

- SCP-firmware 是**参考实现**(BSD-3-Clause),官方仓库已宣布转为**只读**,不再接收外部贡献(见 `readme.md` 顶部公告)——发展靠各厂商的 fork 延续;
- 厂商基于它做自己的产品:加平台配置、私有模块、私有协议扩展(SCMI 协议 id 的 `0x80-0xFF` 段留给厂商/平台专用,见 SCMI §3.1.2 Table 2);
- SCMI 本身是活规范(本笔记基线为 DEN0056F,v4.0),协议族持续在扩。

学习主线的落点:有了这一层的背景,[下一篇](./01-system-multicore-interaction.md)回答"SCP 在系统形态里站在哪、和 AP/安全固件之间靠什么协议通信",从"为什么"进入"怎么配合"。