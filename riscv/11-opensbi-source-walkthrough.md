# OpenSBI 源码走读:M-mode 固件的骨架与定制点

[启动链总览](./10-boot-chain-overview.md)给过 OpenSBI 的位置感,[实验二](./41-lab-minimal-sbi.md)手写过最小 SBI——本篇把视线换成 OpenSBI 本体的源码,回答两个工程问题:**从复位向量到把控制权交给内核,这条路径上每一步在哪个文件里发生**;以及在 Palladium/FPGA 上跑自己的板子时,**该改哪里、不该改哪里**。基线为 submodule 固定的 **v1.9**(`riscv/src/opensbi`,commit cbf9f673),行号引用均构建期同步。

## 1. 源码树与构建形态

| 目录 | 内容 | 走读重点 |
| --- | --- | --- |
| `firmware/` | fw_base.S 等启动汇编、三种固件形态 | §2 |
| `lib/sbi/` | 核心库:init/domain/ecall/hsm/pmu… | §3、§5、§6 |
| `include/sbi/` | fw_dynamic.h、scratch、平台接口 | §2 |
| `platform/` | 各板级支撑;`generic/` 是 FDT 化通用平台 | §4 |

构建产物按形态分三档:`FW_PIC`(重定位后运行)、`FW_JUMP`(假定下一阶段链接地址已知)、`FW_DYNAMIC`(运行时由上一阶段传参决定去向——QEMU 默认形态)。仿真环境上最常用的是最后一种:

```bash
make -C opensbi PLATFORM=generic FW_DYNAMIC=y CROSS_COMPILE=riscv64-unknown-linux-gnu-
# 产物:build/platform/generic/firmware/fw_dynamic.bin
qemu-system-riscv64 -M virt -bios build/platform/generic/firmware/fw_dynamic.bin \
    -kernel Image -append "console=ttyS0"
```

## 2. 从复位到 C:fw_base.S 与加载器契约

### 2.1 _start:原子指令的第一次实战

HART 复位后所有核同时进入 `_start`,谁干活由两道闸门决定:

```asm src="./src/opensbi/firmware/fw_base.S" lines="48-76" anchor="fw_start"
```

值得停下来的三点:

- **boot HART 选择**:`fw_boot_hart` 先问平台有没有偏好(比如 loader 通过 dynamic info 指定 `boot_hart`),没有就进 [`_try_lottery`](#fw_start);
- **relocation lottery**:fw 可能被多核同时拷贝到最终地址,"彩票"即一个先到先得的标志字。注意实现是双分支——有 A 扩展走 `amoswap`,只有 ZaLRSC 走 `lr/sc` 自旋。对核 IP 验证工程师这是个提示:**你的 LL/SC 若支持得不完整,OpenSBI 是复位的第一个用户**,卡死在这一步比崩溃更常见;
- 输了彩票的核跳去 `_wait_for_boot_hart` 自旋等 `_boot_status` 变化,warm boot 路径由此开始([`_start_warm`](#fw_start), firmware/fw_base.S:317)。

### 2.2 FW_DYNAMIC 契约:loader 和固件的握手协议

`fw_dynamic.bin` 之所以不用重新编译就能换内核跳转地址,靠的是上一阶段(mostly QEMU `-kernel` / U-Boot)在内存里放好的这个结构体:

```c src="./src/opensbi/include/sbi/fw_dynamic.h" lines="49-76" anchor="fw_dynamic_info"
```

四个字段构成完整语义:[`next_addr/next_mode`](#fw_dynamic_info) 决定 OpenSBI 最后 `mret` 到哪、进什么特权级(S-mode 给内核,U-mode 给裸机测试);`options` 位开关如 `FW_DYNAMIC_INFO_OPTIONS_BIT_FDT_COPY`;`boot_hart` 补充上面的彩票规则(填 −1UL 则纯靠 lottery)。结构体末尾一排 `assert_member_offset` 把 ABI 偏移钉死——跨语言、跨工具链的握手契约就该这样防漂移。

## 3. 冷启动主干:init_coldboot

汇编准备完 scratch 区域后进入 C 世界的主函数。v1.9 里它的前几步顺序是**写死且带注释强调**的,阅读价值极高:

```c src="./src/opensbi/lib/sbi/sbi_init.c" lines="231-261" anchor="init_coldboot_head"
```

为什么必须是这个顺序:`sbi_scratch_init` 划定每hart私有布局区,后续所有模块从这里拿 offset;`sbi_heap_init` 提供唯一堆;`sbi_domain_init` 要在任何人分配内存之前建好默认 domain(内存属性的源头)。之后的调用序列依次完成:HSM 初始化(其余 hart 转入等待态)、[`wake_coldboot_harts`](#init_coldboot_head)(广播 coldboot_done,别的核不再傻等)、hart 特性探测([`sbi_hart_init`](#init_coldboot_head)),随后 timer、platform early_init、PMU、IRQCHIP,最后 重定位 scratch 指针→per-hart init→交棒。

一个容易忽略的 v1.9 新细节:若设备树报告实现了 Zkr 扩展,会用 `csr_swap(CSR_SEED)` 从熵源收集 stack guard 种子——对 `CSR_SEED` 的 opst 状态机轮询那段(lib/sbi/sbi_init.c,Zkr 分支内)是很好的 CSR 编程示例。

## 4. 平台抽象:generic 平台如何接新板

v1.9 的 platform 层已经完全 FDT 化:中断控制器走 `fdt_irqchip_init`、timer 走 `fdt_timer_init`,绝大多数新板子**不需要 fork 整个 platform 目录**,只要 DTS 正确 + 少量 Kconfig:

```c src="./src/opensbi/platform/generic/platform.c" lines="337-365" anchor="generic_platform_ops"
```

定制点收敛到三个动作:(1) `CONFIG_PLATFORM_GENERIC_NAME/VERSION` 命名;(2) 需要额外初始化时覆写对应的 op 槽位(`early_init/final_init/extensions_init/domains_init`);(3) `hart_index2id` 数组描述物理 hart 编号映射——Palladium 上 RTL 的 hart 编号经常不连续,这里就是消化不连续性的地方。反过来,**不要**在 platform 层加业务逻辑:domain/PMP 这种内存属性问题属于 lib 层语义(见 [实验二](./41-lab-minimal-sbi.md)踩过的坑)。

## 5. SBI 调用分发:a7/a6 协议与扩展全景

内核侧 `ecall` 后,a7 放 extension id、a6 放 func id,M 态 trap 进入统一分发器:

```c src="./src/opensbi/lib/sbi/sbi_ecall.c" lines="120-168" anchor="sbi_ecall_handler"
```

三个设计要点:查找即一次链表遍历(`sbi_ecall_find_extension`),参数寄存器原样透传给扩展自己的 handle;**错误值卫生检查**——handle 返回了超出合法区间的值会被降级为 `SBI_ERR_FAILED` 并打印告警,防止内核拿到垃圾负数;mepc 统一 `+= 4`,扩展不必关心 trap 返回地址。

v1.9 的扩展文件清单,对照 SBI 规范即一张能力表:

| 文件(lib/sbi/) | 扩展 id | 状态 |
| --- | --- | --- |
| sbi_ecall_base.c | 0x10 BASE | SBI 0.2+,必实现 |
| sbi_ecall_time.c | 0x54494D45 TIME | ratified,set_timer 新写法 |
| sbi_ecall_rfence.c | 0x52464E43 RFNC | ratified,sfence.vma 远程发射 |
| sbi_ecall_ipi.c | 0x735049 IPI | ratified |
| sbi_ecall_hsm.c | 0x504D53 HSM | ratified,hart start/stop/suspend |
| sbi_ecall_srst.c | 0x53525354 SRST | ratified,系统重启/关机 |
| sbi_ecall_pmu.c | 0x504D55 PMU | ratified,事件↔mhpmevent 映射 |
| sbi_ecall_dbcn.c | 0x4442434E DBCN | ratified(debug console) |
| sbi_ecall_susp.c | 0x53555350 SUSP | ratified,系统挂起 |
| sbi_ecall_cppc.c | 0x43505043 CPPC | ratified |
| sbi_ecall_fwft.c | 0x46574654 FWFT | v1.9 新增 draft→ratified 进程中 |
| sbi_ecall_dbtr.c | 0x44425452 DBTR | draft(trigger) |
| sbi_ecall_sse.c | 0x535345 SSE | draft(softwar的异常注入) |
| sbi_ecall_mpxy.c | 0x4D505859 MPXY | draft(rpmi 消息代理) |
| sbi_ecall_legacy.c | 0x0–0x08 legacy | 兼容旧内核,可裁剪 |

> **如何读这张表**:每个"ratified"一行对应一类你在 [20 号](./20-presilicon-validation-environment.md)bring-up 时迟早要验收的行为——TIME 不走则 smpboot 卡 `calibrate_delay`,RFNC 异常则各核 TLB 一致性全乱,HSM 不回 error code 则 CPU hotplug 假活。裁剪启动用 Kconfig 把不需要的整文件关掉而非改代码。

## 6. 多核唤醒:HSM 状态机

冷启动只让一颗核跑到交棒,其余 hart 在 HSM 里以 STOPPED 状态待命,内核后续通过 `sbi_ecall(HSM, HART_START)` 逐颗拉起。状态迁移集中在 lib/sbi/sbi_hsm.c(约 570 行):START 让目标 hart 从 `_start_warm` 入口醒来读到 start_addr;STOP 保存上下文后进 WFI 待命;SUSPEND 支持 Retentive / Non-Retentive 两档(是否保留 CSR 状态是两者唯一的验证差异点)。在 FPGA 上做 hotplug 回归时,若目标 hart 偶发起不来,先用 OpenSBI 侧日志确认它是否真的进入了事件循环,再怀疑内核侧——这层边界清晰,[中断验证](./24-interrupt-validation.md)的"看不到中断"分锅树同样适用于"看不到 hart 起来"。

## 7. 在 Palladium/FPGA 上跑 OpenSBI 的定制清单

| 场景 | 动作 | 位置 |
| --- | --- | --- |
| 板上 hart 编号不连续 | 提供 `hart_index2id` 映射数组 | platform(generic)/platform.c §4 |
| 早期串口波特率异常(输出乱码) | 核对 `fdt` 里 UART clock-rate;或临时走 DBCN | DTS + lib/sbi/dbcn |
| 内核 Image 加载地址改动 | 只改 loader 侧 `next_addr`,固件不动 | fw_dynamic.h 契约 |
| 需要 PMP 保护一段 MMIO | 在 `domains_init` 定义新 domain 而非散落改 PMP 寄存器代码 | generic domains_init |
| 仅跑裸机性能测试(23 号场景) | FW_DYNAMIC `next_mode`=U-mode 直接进用户程序,绕过内核 | fw_dynamic_info 字段 |
| 排查"第二颗核没起来" | 先看 OpenSBI 冷启动日志的 hart mask,再 `sbi_hsm` 状态机 | sbi_hsm.c §6 |

共同原则:配置驱动的改动(Kconfig/DTS/dynamic info)优先于源码改动,前者升级 OpenSBI 版本时不产生合并冲突。

## 8. 工程实践清单

- **日志是第一调试器**:`FW_OPTIONS=0x2` 打开 all-harts debug 打印;冷启动卡住时,先确认卡在 fw_base 汇编窗口还是 C 序列——只有第一行 banner 之前卡住才是汇编/原子问题(§2.1)。
- **给固件留版本戳**:把 SoC 的 git 描述符注进 `CONFIG_PLATFORM_*_VER`,让每次 emulation 回归的 sbi banner 能对上 RTL 快照。
- **裁剪要成套**:关 legacy 时确认你的 bootloader 不再发 0.x 老调用;关 PMU 会连累 [23 号](./23-performance-benchmark-pmu.md)依赖的 mhpmevent 配置通路。
- **验证观察点**:sbi_scratch 结构是 per-hart 私有内存的模板,HSM 迁移时它是否被正确保留(SUSP retentive 路径)值得 DV 盯着看。
- **升级姿势**:v1.9 对新扩展(fwft/mpxy)仍在快速演进;固定 tag + 订阅 CHANGELOG,而不是无脑追 master。

## 9. 下一步

走到这里,从复位向量到 SBI 合同的 M 态栈已在眼前。向上是 [Linux bring-up](./13-linux-bringup.md) 如何消费这条链(a0/a1/设备树);向下,FPGA 场景的 uart 与 PLIC bring-up 实操都在 [硅前验证环境](./20-presilicon-validation-environment.md) 有对应坑位记录。
