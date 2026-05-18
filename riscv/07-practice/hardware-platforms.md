# 硬件平台与前沿方向

> 从开发板选型到前沿研究，RISC-V 生态正在快速演进。了解硬件平台和未来方向，有助于规划学习和职业路径。

## 为什么重要

RISC-V 最令人兴奋的地方不是"又多了一种指令集"——而是你第一次可以用 5 块钱买到一颗 RV32EC 芯片（CH32V003），用 80 美元买到一台可以跑完整 Linux 的四核 RISC-V 单板机（VisionFive 2），用一万块买到一台 64 核的 RISC-V 服务器（Milk-V Pioneer）。这种从 MCU 到服务器全覆盖的硬件生态，在 ARM 和 x86 的世界里从未以如此开放的方式存在过。

但选择多了也意味着决策成本上升：你是用 ESP32-C3 入门还是直接上 VisionFive 2？做边缘 AI 推理该选 Milk-V Duo（双核 RV64 + 小 NPU）还是等带向量扩展 V 的芯片？做 Chiplet 研究该关注 UCIe 互连标准还是 RISC-V 的 TileLink 一致性协议？本章以"按需求选板子"为主线，按入门/中端/高端/服务器四档整理当前主流 RISC-V 开发板，同时覆盖前沿方向（高性能计算、AI 加速器、Chiplet、安全可信计算）和学习路线建议。

> **工程师视角**：选择硬件平台不是"买最贵的"，而是"买最适合当前阶段的"。入门时 ESP32-C3 足够理解指令集；做 Linux 驱动开发时需要 VisionFive 2；而调试服务器级芯片的固件问题，QEMU 往往比真实硬件更高效。

## 学习目标

- 根据学习和项目需求选择合适的 RISC-V 开发板（入门/中端/高端/服务器）
- 区分 ESP32-C3（RV32IMAC MCU）与 VisionFive 2（RV64GC Linux SBC）的适用场景
- 了解 RISC-V 前沿方向：向量扩展 V、Chiplet 互连、数据中心部署
- 规划从 ISA 基础到 Linux 驱动开发的 20 周学习路线
- 利用 RISC-V 社区资源（PLCT Lab、OSPP、邮件列表）持续学习

### 前置知识

| 需要了解 | 参考文档 |
|----------|----------|
| RISC-V ISA 模块化扩展组合与 Profile | [RISC-V 概览](../01-basics/riscv-overview.md) |
| 开源核心架构 (Rocket/BOOM/CVA6/香山) | [Open-Source 核心](../04-microarchitecture/opensource-cores.md) |
| 开发工具链与模拟器 | [工具链](../06-tools/toolchain-and-simulator.md) |

---

## 1. 开发板选型

### 1.1 按需求选择

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#f8fafc", "primaryTextColor": "#1e293b", "primaryBorderColor": "#475569", "lineColor": "#64748b", "secondaryColor": "#f1f5f9", "secondaryBorderColor": "#94a3b8", "tertiaryColor": "#f8fafc", "fontFamily": "\"trebuchet ms\", verdana, arial, sans-serif"}}}%%
graph TD
    Q{你的目标？} --> |"学习 ISA/汇编"| ENTRY["入门级<br/>ESP32-C3 / CH32V003<br/>几十元"]
    Q --> |"RTOS 开发"| MID["中端<br/>VisionFive 2 / Milk-V Duo<br/>几百元"]
    Q --> |"Linux 开发"| HIGH["高端<br/>Milk-V Pioneer / BPI-F3<br/>千元级"]
    Q --> |"高性能计算"| SERVER["服务器级<br/>SG2042 / Milk-V Vega<br/>万元级"]

    style ENTRY fill:#4ecdc4,color:#fff
    style MID fill:#ffa502,color:#fff
    style HIGH fill:#ff6b6b,color:#fff
    style SERVER fill:#a4b0be,color:#333
```

### 1.2 入门级开发板

| 开发板 | 核心 | ISA | 价格 | 特点 |
|--------|------|-----|------|------|
| **ESP32-C3** | ESP32-C3 | RV32IMAC | ~20 元 | WiFi/BLE，Arduino 兼容，生态好 |
| **CH32V003** | QingKe V2A | RV32EC | ~5 元 | 极便宜，适合纯入门 |
| **CH32V307** | QingKe V4F | RV32IMAC | ~30 元 | 丰富外设，USB OTG |
| **SiFive HiFive1 Rev B** | FE310 | RV32IMAC | ~$59 | 官方开发板，Arduino 兼容 |
| **Longan Nano** | GD32VF103 | RV32IMAC | ~$5 | 类 STM32，有 LCD |

### 1.3 中高端开发板

| 开发板 | 核心 | ISA | 价格 | 特点 |
|--------|------|-----|------|------|
| **VisionFive 2** | JH7110 (4×U74) | RV64GC | ~$80 | 最流行的 RISC-V SBC |
| **Milk-V Duo** | CV1800B | RV64GC + C906 | ~$9 | 超便宜，适合边缘 AI |
| **Milk-V Duo S** | SG2000 | RV64GC | ~$15 | Duo 升级版 |
| **LicheeRV Dock** | D1 | RV64GC | ~$30 | 带屏幕，适合便携项目 |
| **BPI-F3** | K1 (8×X60) | RV64GCV | ~$120 | 8 核，向量扩展 |

### 1.4 服务器级

| 开发板 | 核心 | 特点 |
|--------|------|------|
| **Milk-V Pioneer** | SG2042 (64×C920) | 64 核，4×DDR4，PCIe |
| **Milk-V Vega** | SG2042 | 服务器主板 |
| **DCU-R1** | 4×SG2042 | 256 核服务器 |

---

## 2. 实践项目建议

### 2.1 入门项目

| 项目 | 难度 | 学到什么 |
|------|------|----------|
| **裸机 Hello World** | ⭐ | 工具链、链接脚本、QEMU |
| **UART 驱动** | ⭐⭐ | 寄存器操作、轮询/中断 |
| **定时器中断** | ⭐⭐ | CLINT、CSR、trap 处理 |
| **简单调度器** | ⭐⭐⭐ | 上下文切换、栈管理 |

### 2.2 进阶项目

| 项目 | 难度 | 学到什么 |
|------|------|----------|
| **移植 FreeRTOS** | ⭐⭐⭐ | RTOS 架构、移植层 |
| **移植 Zephyr** | ⭐⭐⭐ | 设备树、驱动模型 |
| **实现虚拟内存** | ⭐⭐⭐⭐ | Sv39 页表、TLB、缺页处理 |
| **实现文件系统** | ⭐⭐⭐⭐ | VirtIO Block、块设备驱动 |

### 2.3 高级项目

| 项目 | 难度 | 学到什么 |
|------|------|----------|
| **实现简易 OS** | ⭐⭐⭐⭐⭐ | 全栈系统开发 |
| **实现简单 RISC-V 核心** | ⭐⭐⭐⭐ | 流水线、冒险处理 |
| **贡献 Linux 内核** | ⭐⭐⭐⭐⭐ | 内核社区、代码审查 |
| **贡献 OpenSBI** | ⭐⭐⭐⭐ | 固件开发、SBI 接口 |

---

## 3. 前沿方向

### 3.1 RISC-V 高性能计算

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#f8fafc", "primaryTextColor": "#1e293b", "primaryBorderColor": "#475569", "lineColor": "#64748b", "secondaryColor": "#f1f5f9", "secondaryBorderColor": "#94a3b8", "tertiaryColor": "#f8fafc", "fontFamily": "\"trebuchet ms\", verdana, arial, sans-serif"}}}%%
graph LR
    subgraph cur ["当前"]
        C1["64 核服务器<br/>SG2042"]
        C2["向量扩展 V<br/>AI 推理加速"]
    end

    subgraph near ["近期"]
        N1["128+ 核<br/>Chiplet 架构"]
        N2["RVA22/RVA23<br/>服务器 ISA Profile"]
    end

    subgraph fut ["未来"]
        F1["RISC-V 数据中心<br/>大规模部署"]
        F2["异构计算<br/>CPU + 加速器"]
    end

    C1 --> N1 --> F1
    C2 --> N2 --> F2
```

### 3.2 AI 加速器与自定义扩展

RISC-V 的最大优势之一是允许自定义指令扩展：

| 方向 | 说明 | 代表项目 |
|------|------|----------|
| **向量扩展 (V)** | 标准化的 SIMD 指令 | 已冻结，Linux 已支持 |
| **AI 自定义指令** | 针对推理/训练的专用指令 | 芯动科技、赛昉科技 |
| **密码学扩展** | 硬件加速加密算法 | Zkn/Zks/Zkg 系列 |
| **RoCC 协处理器** | Rocket Chip 自定义协处理器 | 学术研究 |

### 3.3 RISC-V 服务器与数据中心

| 里程碑 | 时间 | 说明 |
|--------|------|------|
| 64 核 SG2042 发布 | 2023 | 首款商用 RISC-V 服务器芯片 |
| RVA22 Profile 发布 | 2023 | 服务器 ISA 标准化 |
| Android RISC-V | 2024 | Google 官方支持 |
| RVA23 Profile | 2024 | 向量扩展标准化 |
| 大规模数据中心部署 | 2025+ | 预期 |

### 3.4 Chiplet 与 RISC-V

```
传统 SoC:
  [CPU] [GPU] [NPU] [IO]  ← 单片集成，制程统一

Chiplet:
  [CPU (3nm)] ─┐
  [GPU (5nm)] ─┤── 互连 (UCIe) ── 封装
  [IO (12nm)] ─┘

  RISC-V 的优势:
  - 不同 Chiplet 可以使用不同的 RISC-V 核心配置
  - 自定义扩展 Chiplet 更容易集成
  - 开源 IP 降低 Chiplet 开发门槛
```

### 3.5 安全与可信计算

| 方向 | RISC-V 机制 | 说明 |
|------|-------------|------|
| **TEE** | PMP + M-mode | 可信执行环境 |
| **安全启动** | M-mode 验证 | Chain of Trust |
| **内存加密** | 硬件加密引擎 | 加密内存访问 |
| **侧信道防护** | 实现相关 | Flush+Reload, Spectre 防御 |

---

## 4. 学习路线建议

### 4.1 系统软件工程师路线

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#f8fafc", "primaryTextColor": "#1e293b", "primaryBorderColor": "#475569", "lineColor": "#64748b", "secondaryColor": "#f1f5f9", "secondaryBorderColor": "#94a3b8", "tertiaryColor": "#f8fafc", "fontFamily": "\"trebuchet ms\", verdana, arial, sans-serif"}}}%%
graph TD
    W1["第 1-2 周<br/>ISA 基础<br/>汇编编程"]
    W2["第 3-4 周<br/>特权架构<br/>CSR + Trap"]
    W3["第 5-6 周<br/>裸机编程<br/>UART + 定时器"]
    W4["第 7-8 周<br/>虚拟内存<br/>Sv39 页表"]
    W5["第 9-12 周<br/>RTOS 移植<br/>Zephyr/FreeRTOS"]
    W6["第 13-16 周<br/>Linux 内核<br/>驱动开发"]
    W7["第 17-20 周<br/>进阶主题<br/>虚拟化/性能优化"]

    W1 --> W2 --> W3 --> W4 --> W5 --> W6 --> W7
```

### 4.2 每个阶段的产出与对应文档

| 阶段 | 产出 | 验证方式 | 参考文档 |
|------|------|----------|----------|
| ISA 基础 | 能读懂 RISC-V 汇编 | objdump 反汇编分析 | [01-基础入门](../01-basics/) |
| 特权架构 | 能写 trap 处理程序 | QEMU 运行裸机代码 | [03-特权架构](../03-privileged/) |
| 裸机编程 | 能实现 UART 输出和定时器 | QEMU/开发板运行 | [Lab 1](../08-labs/lab01-baremetal-trap-handler.md) |
| 虚拟内存 | 能建立页表并启用 MMU | 虚拟地址访问成功 | [Lab 3](../08-labs/lab03-sv39-page-table.md) |
| RTOS 移植 | 能在 RISC-V 上运行 RTOS | 多任务调度正常 | [操作系统移植](../05-system-software/os-porting.md) |
| Linux 内核 | 能编写简单驱动 | insmod 加载运行 | [操作系统移植](../05-system-software/os-porting.md) |
| 虚拟化 | 能运行带 KVM 的 VM | 两阶段地址翻译成功 | [Lab 4](../08-labs/lab04-h-extension-two-stage-mmu.md) |

---

## 5. 社区与资源

| 资源 | 链接/说明 |
|------|-----------|
| **RISC-V International** | https://riscv.org |
| **RISC-V GitHub** | https://github.com/riscv |
| **RISC-V 邮件列表** | lists.riscv.org — 技术讨论主阵地 |
| **PLCT Lab** | https://github.com/plctlab — 中国团队贡献 |
| **RISC-V 中国社区** | 微信群、知乎专栏 |
| **香山社区** | https://github.com/OpenXiangShan |
| **OSPP 开源之夏** | 每年有 RISC-V 相关项目 |

---

## 6. 如何为这份笔记做贡献

这份笔记采用"理论 + 实战"的双轨结构。如果你发现内容有误或希望补充：

1. **理论修正**：ISA 规范以 [RISC-V 官方 Spec](https://riscv.org/technical/specifications/) 为准，特权架构以最新 Ratified 版本为准
2. **实战补充**：Lab 案例需要能在 QEMU 7.0+ 上直接运行，附 Makefile 和预期输出
3. **硬件验证**：真实硬件上的测试结果请注明开发板型号和固件版本

---

## 小结

| 要点 | 说明 |
|------|------|
| 开发板从便宜的开始 | ESP32-C3 / CH32V003 足够入门 |
| QEMU 是最佳练习平台 | 零成本，支持所有功能 |
| 自定义扩展是杀手锏 | 这是 x86/ARM 做不到的 |
| 服务器是未来方向 | RVA22/RVA23 Profile 标准化 |
| 实践比看文档更重要 | 每个阶段都要有可运行的产出 |
| 文档交叉引用 | 各章节通过 Lab 案例形成知识网络 |

---

## 参考资料

- [RISC-V International — Hardware Database](https://riscv.org/risc-v-hw/) — RISC-V 硬件平台数据库
- [RISC-V International — Exchange (RISC-V Market)](https://riscv.org/exchange/) — 商用 RISC-V 产品列表
- [SiFive HiFive Premier P550 Manual](https://www.sifive.com/documentation/chips/hifive-premier-p550-manual) — Eswin EIC7700 平台参考
- [StarFive VisionFive/VisionFive2 Documentation](https://doc-en.rvspace.org/) — JH7110 SoC 平台文档
- [Microchip PolarFire SoC Icicle Kit Docs](https://www.microchip.com/en-us/products/fpgas-and-plds/system-on-chip-fpgas/polarfire-soc-fpgas/icicle-kit) — PolarFire SoC 参考
- [Kendryte K230 Documentation](https://www.canaan.io/products/k230) — 国产端侧 AI 芯片
- [UCIe Consortium Spec v1.1](https://www.uciexpress.org/) — Chiplet 互连标准
- [RISC-V International — Ratification Process](https://wiki.riscv.org/display/HOME/Policies) — 扩展规范化流程

---

→ 实验：[Lab 1 — 裸机中断框架](../08-labs/lab01-baremetal-trap-handler.md)
