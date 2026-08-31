# 虚拟化的内核侧:KVM on RISC-V 源码走读

[H 扩展与 KVM](./06-virtualization-h-extension.md)讲的是硬件给了什么 CSR,[H 扩展两阶段 MMU 实验](./43-lab-h-extension-two-stage-mmu.md)用 QEMU TCG 练过概念;本篇换到**真实的 host 内核**:`arch/riscv/kvm` 在 v6.16 里共约 **11.9k 行 C + 汇编**,是 H 扩展目前最大的规模化消费者。读懂它的四条主线——模块加载的能力谈判、二十行的世界开关、G-stage 页表搭建、exit 分发——你在 [IOMMU 与虚拟化验证](./25-iommu-virtualization-validation.md)里设计的每一条两阶段翻译用例,都能说出它落在内核的哪一列代码上。到这里,"H 扩展三层视图"(硬件 CSR → KVM 内核源码 → 用户态 KVM API)就闭环了。基线:v6.16(038d61f),sparse 克隆于 `riscv/src/linux`。

## 1. 文件地图:11.9k 行各管什么

| 文件(arch/riscv/kvm/) | 行数 | 职责 |
| --- | --- | --- |
| vcpu_onereg.c | 1300 | ONE_REG ioctl:所有寄存器 CSR/GPR 的统一读写接口 |
| aia_imsic / aplic / device / aia.c | 1141+645+642+671 | AIA 三件套在内核侧的全套管理([07](./07-aia-advanced-interrupt-architecture.md) 的运行时消费者) |
| vcpu.c | 1006 | VCPU 创建/生命周期/run 循环外层 |
| vcpu_pmu.c | 850 | PMU 半虚拟化(SBI PMU 合同在 Guest 侧的重放) |
| vcpu_insn.c | 782 | 指令模拟(VIRTUAL_INST_FAULT 的补充弹药库) |
| mmu.c | 772 | G-stage:gstage_map_page/fault 处理/hgatp 维护 |
| vcpu_sbi*.c 四件 | ~1100 | Guest 发的 ecall → host 内核直接应答或转底层 SBI |
| vcpu_switch.S | 441 | 世界开关汇編(§3),含 FP/V 寄存器保存例程 |
| tlb.c | 428 | hfence.gvma/vvma 封装与广播原语 |
| vcpu_exit.c | 258 | exit 分类分发与 PMU 固件事件计数(§5) |
| vmid.c | 124 | G-stage VMID 分配与回绕(§6) |

对照表就一个结论:**除 mmu 外,行数最多的一坨代码都在"替 Guest 模拟它以为自己在管的东西"**(寄存器、中断、PMU、SBI)——两阶段翻译本身反而是最薄的,因为硬件把活干了。

## 2. 模块加载 = 一次硬件能力谈判

`insmod kvm` 时内核做的事,恰好是一份现成的 bring-up 验收清单:

```c src="./src/linux/arch/riscv/kvm/main.c" lines="69-96" anchor="riscv_kvm_init"
```

谈判顺序值得记:h 扩展 → SBI ≥0.2 → **SBI RFENCE**([11 号](./11-opensbi-source-walkthrough.md)§5 扩展表里它排第三,原因在此——KVM 自己不碰别的核,hfence.gvma 全靠 SBI 代发)→ NACL 探测(SBI 嵌套加速合同,给 L0-hypervisor 提供影子 CSR 快捷路径)→ `gstage_mode_detect()` 问 hardware hgatp 支持哪种模式 → `vmid_detect()` 问 VMID 位宽 → AIA 初始化。dmesg 里 `hypervisor extension available ... sync_csr, autoswap_csr` 那一行就是这份清单的结果打印。你的 DUT 若 RFENCE 有 bug,在这第一步就会被内核直接拒载——失败面反而最小。

## 3. 世界开关只有二十行

vCPU 进入/退出 Guest 的全部魔法:

```asm src="./src/linux/arch/riscv/kvm/vcpu_switch.S" lines="198-220" anchor="kvm_switch_to"
```

结构对称得可以背下来:host GPRS 入栈 → [`SAVE_HOST_AND_RESTORE_GUEST_CSRS`](#kvm_switch_to)(hstatus/vsatp/vsip/vstvec 等**整组 VS 态 CSRRW**,宏展开见同文件头部注释)→ guest GPRS 出栈 → `sret`;返回路径完全镜像。两个验证观察点:(1) CSR 切换是"成组原子"假设,**hstatus 各位是否被硬件按预期覆盖**属于易漏检项;(2) FP 与 V 向量另有独立保存例程(vcpu_switch.S 后半),只在 Guest 用过后才执行——lazy save 的正确性依赖 mstatus.FS/VS 状态机。NACL 可用时走 `__kvm_riscv_nacl_switch_to`(同文件:227),区别是用 `CSR_NCSRS` 影子区减少 CSR 访问次数——影子寄存器的可见性时序本身就是 NaCL 合同的新验证面。

## 4. G-stage 怎么搭:mmu.c 的三个默认值

```c src="./src/linux/arch/riscv/kvm/mmu.c" lines="21-32" anchor="gstage_mode_defaults"
```

注意两点:其一,RV64 默认 Sv39×4——PGD 索引额外吃掉 GPA 最高两位,寻址域是普通 Sv39 的 4 倍(PGD 本体因此 16KB);这是为内存热插拔和 PCI 大 BAR 留的口,KVM 用 `gstage_mode_detect()` 按 DUT 实际支持从高到低选(Sv48×4 等),直至 Bare 兜底。其二,**VS-stage 完全归 Guest 自管**:内核只维护 G-stage 这一张页表,不存在 x86 EPT 语境里的 shadow page table——guest 改自己页表不产生任何 VM exit。这决定了 [两阶段翻译](./06-virtualization-h-extension.md#2-h-扩展硬件辅助虚拟化)的验证焦点应该在 G-side:缺页注入、权限位组合、大页拆分(`gstage_level_to_page_size`)、以及 mmu 里 `gstage_remote_tlb_flush` 触发的跨核失效链。

## 5. 出 Guest 的一刻:exit 分类里藏着 PMU

```c src="./src/linux/arch/riscv/kvm/vcpu_exit.c" lines="186-237" anchor="kvm_riscv_vcpu_exit_dispatch"
```

每个 exit case 都先给 PMU 加一笔固件事件(`SBI_PMU_FW_ILLEGAL_INSN` 等)再分流——意味着 **23 号 [PMU 方法论](./23-performance-benchmark-pmu.md)可以直接从 host 读到"Guest 多少性能被模拟开销吃掉"**,不用插桩。三条去路:vcpu_redirect(非法指令/非对齐/访问错→包成异常塞回 Guest,由 Guest 自己处理);kvm_riscv_vcpu_virtual_insn(真虚拟指令缺失→软件模拟);`EXC_SUPERVISOR_SYSCALL → kvm_riscv_vcpu_sbi_ecall`(**Guest 的一切 SBI 请求被 host 内核拦截**——TIME/IPI 类 host 直接应答,HSM 类转发底层 SBI,vcpu_sbi*.c 五个文件就是这层白名单)。host kernel 是不是 SBI 合同的第零号用户,这里见到实锤。

## 6. TLB 与 VMID:一份十二行代码的原型

hfence 的单条封装(tlb.c:21 起)有个实用模式:目标区间超过该层级 PTE 数就干脆 `_all`,并且有 svinval 特性探测——支持时用 `sfence.w.inval + hinval.gvma + sfence.inval.ir` 三段式批量逐条,不支持则退回 HFENCE 逐发。真正的 gems 在 vmid.c(124 行整个文件):每个 VM 一个 `struct kvm_vmid`,带版本号分配器:

```c src="./src/linux/arch/riscv/kvm/vmid.c" lines="88-118" anchor="kvm_vmid_update_wraparound"
```

这就是 [25 号](./25-iommu-virtualization-validation.md)“VMID/TLB 翻转”探针要复刻的全部语义:VMID 用尽回绕瞬间,**版本号递增 + IPI 广播全核 hfence.gvma_all + KVM_REQ_UPDATE_HGATP 唤醒所有 vCPU 重进 run loop**。RTL 若对 hgatp.VMID 翻转瞬间的 TLB 内容(旧 VMID 残留项)处理不干净,对应内核行为就是在负载波动时的偶发 Guest 内存串页——极难归因,所以要在裸机用例里先复刻这段逻辑钉死它。

## 7. 中断虚拟化:哪里看内核版的 AIA

aia_device.c 定义 `/dev/kvm` 上的 device-level ioctl(KVM_DEV_RISCV_AIA_*:配置 mode=emul/direct、nr_sources、group offset);aia_aplic.c/aia_imsic.c 分别实现两种控制器在 HS 态的管理;Guest 的 MSI 写经 §5 的 access-exit 路径进入 imsic 注入函数。寄存器级细节已在 [AIA 专篇](./07-aia-advanced-interrupt-architecture.md) 展开,本篇只需记住分工:**direct 模式几乎全程旁路内核**(IMSIC 直连 hart,内核只在配置时介入),emul 模式每一次注入都过一遍 exit——用 24 号的分段方法分别压这两种模式,行为差异比想象中大得多。

## 8. 这些路径怎么变成验证用例

| 内核路径(上四节) | 对应 RTL 风险 | 用例形态(25 号矩阵挂点) |
| --- | --- | --- |
| gstage fault → map/upgrade 大页 | hgatp 模式切换、PTE 权限位传递 | 缺页后立刻读同一 GPA 的波形级检查 |
| exit PMU 固件计数 | mhpmevent 映射(CSR 语义) | Guest 全用户态循环 vs exit 密集负载对比 |
| SBI ecall dispatch 白名单 | 各类 ecall 在 HS 态的 trap 归属 | 每个扩展一条冒烟,覆盖 host 内核的应答与转发两类分支 |
| VMID 回绕 IPI 广播 | TLB tag 位宽、fence 广播到达性 | 跑满 vmid_bits 个 Guest 再加一轮 |
| VS instr redirect | hstatus.SPV 干预位语义 | 让 Guest 故意跑需要模拟的指令序列 |

## 9. 工程实践清单

- **加载即自检**:`dmesg | grep kvm_info` 把 §2 的 gates 打印留档,作为每个 RTL 版本回归的环境指纹。
- **量化模拟开销**:per-vCPU 统计(exits/exits_instruction 等)导出自 debugfs kvm 目录,配合 [23 号](./23-performance-benchmark-pmu.md)建立"exit 成本基线";新 RTL 流片前先在旧版上固化数字。
- **别混淆两层页表调试**:host oops 里的地址全是 HVA/GPA 层,Guest panic 的才是 VA——排查两阶段问题时先声明你站在哪一层。
- **Guest 内核尽量贴近 host 同代版本**:RISC-V 的 SBI/H 扩展特性在两端同步演进快,老 Guest 配新 host 或反之都会走进各自动力不足的兼容分支,vcpu_insn.c 的模拟路径数量就是这种复杂度的直接度量。
- **AIA 模式当编译期变量测**:直接 vs emulated 不是调参,是两条不同的硬件使用曲线——纳入 [25 号](./25-iommu-virtualization-validation.md)矩阵的维度而不是环境随机项。

## 10. 下一步

到这里,"H 扩展三层视图"闭环后的自然延伸是把 [26 号 LTP](./26-linux-test-project.md) 跑进 Guest 做双阶段翻译下的功能回归(LTP 在 VM 内的行为差异本身就是 G-stage 正确性的免费探针)。更深的 RISC-V 机密计算(CoVE/TSM)当前仍在 draft,暂不建议投入工程化跟踪。
