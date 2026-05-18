# RISC-V CPU 系统学习笔记

> 面向系统软件工程师，由浅入深全面掌握 RISC-V CPU 架构。

---

## 学习路径

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#f8fafc", "primaryTextColor": "#1e293b", "primaryBorderColor": "#475569", "lineColor": "#64748b", "secondaryColor": "#f1f5f9", "secondaryBorderColor": "#94a3b8", "tertiaryColor": "#f8fafc", "fontFamily": "\"trebuchet ms\", verdana, arial, sans-serif"}}}%%
graph LR
    A[第一阶段<br/>基础入门] --> B[第二阶段<br/>指令集架构]
    B --> C[第三阶段<br/>特权架构 ⭐]
    C --> D[第四阶段<br/>微架构实现]
    D --> E[第五阶段<br/>系统软件]
    E --> F[第六阶段<br/>工具链生态]
    F --> G[第七阶段<br/>项目实践]

    style C fill:#ff6b6b,color:#fff
    style A fill:#4ecdc4,color:#fff
    style B fill:#45b7d1,color:#fff
    style D fill:#96ceb4,color:#fff
    style E fill:#ffeaa7,color:#333
    style F fill:#dfe6e9,color:#333
    style G fill:#fd79a8,color:#fff
```

---

## 文档目录

### 第一阶段：基础入门

| 文档 | 说明 |
|------|------|
| [计算机体系结构基础](./01-basics/computer-architecture-fundamentals.md) | CPU 组成、流水线、内存层次、中断机制 |
| [RISC-V 概览](./01-basics/riscv-overview.md) | 起源、设计哲学、模块化扩展、生态对比 |

### 第二阶段：指令集架构

| 文档 | 说明 |
|------|------|
| [基础整数指令集 RV32I/RV64I](./02-isa/rv32i-rv64i-instructions.md) | 寄存器、指令格式、指令分类、编码规则 |
| [标准扩展 M/A/F/D/C/B/V](./02-isa/standard-extensions.md) | 乘除法、原子操作、浮点、压缩、位操作、向量扩展 |

### 第三阶段：特权架构 ⭐核心重点

| 文档 | 说明 |
|------|------|
| [特权模式与 CSR](./03-privileged/privileged-modes-and-csr.md) | M/S/U/HS/VS/VU 模式、CSR 寄存器详解、模式切换、委托 |
| [中断与异常处理](./03-privileged/interrupts-and-exceptions.md) | 中断分类、异常处理流程、委托机制、PLIC/CLINT |
| [内存管理](./03-privileged/memory-management.md) | PMP、Sv39/Sv48 页表、Sv39x4 虚拟化翻译、TLB 管理 |
| [启动流程](./03-privileged/boot-process.md) | 复位向量、OpenSBI、UEFI+ACPI 服务器启动 |
| [虚拟化：H 扩展与 KVM](./03-privileged/virtualization.md) | 两阶段翻译、H 扩展 CSR、KVM、IOMMU、AIA 虚拟化 |

### 第四阶段：微架构与硬件实现

| 文档 | 说明 |
|------|------|
| [流水线基础](./04-microarchitecture/pipeline-basics.md) | 5 级流水线、冒险处理、分支预测 |
| [高级微架构](./04-microarchitecture/advanced-microarchitecture.md) | 超标量、乱序执行、缓存一致性、多核 |
| [开源 RISC-V 核心](./04-microarchitecture/opensource-cores.md) | Rocket、BOOM、香山、蜂鸟 E203、CVA6 |
| [SoC 与系统设计](./04-microarchitecture/soc-design.md) | 总线协议、中断控制器、调试接口 |

### 第五阶段：系统软件

| 文档 | 说明 |
|------|------|
| [汇编与底层编程](./05-system-software/assembly-and-abi.md) | 汇编语法、调用约定、内联汇编、裸机编程 |
| [操作系统移植](./05-system-software/os-porting.md) | Linux 启动、上下文切换、驱动开发、设备树 |

### 第六阶段：工具链与生态

| 文档 | 说明 |
|------|------|
| [工具链与模拟器](./06-tools/toolchain-and-simulator.md) | GCC/LLVM 交叉编译、QEMU、Spike、gem5、调试 |

### 第七阶段：项目实践

| 文档 | 说明 |
|------|------|
| [硬件平台与前沿方向](./07-practice/hardware-platforms.md) | 开发板选型、RTOS 移植、AI 加速器、服务器 |

### 实战 Lab（贯穿各阶段）

> 以下 Lab 案例将理论知识转化为可运行代码，建议在阅读对应章节后动手实践。

| Lab | 主题 | 涉及知识点 | 对应章节 |
|-----|------|-----------|----------|
| [Lab 1](./08-labs/lab01-baremetal-trap-handler.md) | 裸机中断框架 + 上下文切换 | CSR、trap 处理、sscratch 技巧 | 03-privileged, 05-system-software |
| [Lab 2](./08-labs/lab02-minimal-sbi.md) | 最小 SBI 实现 + 跨模式调用 | M/S 模式切换、PMP、SBI 调用约定 | 03-privileged, 05-system-software |
| [Lab 3](./08-labs/lab03-sv39-page-table.md) | 页表遍历与缺页处理 | Sv39、MMU 启用、页故障处理 | 03-privileged |
| [Lab 4](./08-labs/lab04-h-extension-two-stage-mmu.md) | 虚拟化两阶段翻译 | H 扩展、vsatp/hgatp、KVM API | 03-privileged |

### 专题文档

| 文档 | 说明 |
|------|------|
| [RISC-V AIA 完全指南](./aia/riscv-aia-notes.md) | 高级中断架构详解 |

---

## 文档特色

| 特色 | 说明 |
|------|------|
| **双重视角** | 每章兼顾"初学者理解"和"工程师实战"两个层面 |
| **Lab 驱动** | 4 个完整实战案例，从裸机到虚拟化，覆盖固件/内核开发核心技能 |
| **交叉引用** | 理论章节与 Lab 案例相互引用，形成知识网络 |
| **调试导向** | 包含大量 GDB、QEMU、objdump 的实战调试技巧 |
| **中文优先** | 核心概念中文解释，保留英文术语便于对照官方 Spec |

## 推荐学习资源

| 类型 | 资源 |
|------|------|
| 官方规范 | [RISC-V Unprivileged ISA Spec](https://riscv.org/technical/specifications/) |
| 官方规范 | [RISC-V Privileged Architecture Spec](https://riscv.org/technical/specifications/) |
| 教材 | 《计算机组成与设计：硬件/软件接口（RISC-V 版）》Patterson & Hennessy |
| 教材 | 《RISC-V 指令集手册》 |
| 开源项目 | [Rocket Chip](https://github.com/chipsalliance/rocket-chip) / [XiangShan](https://github.com/OpenXiangShan/XiangShan) / [OpenSBI](https://github.com/riscv-software-src/opensbi) |
| 在线课程 | Berkeley CS61C / MIT 6.004 |
