# 标准扩展 M / A / F / D / C

> RISC-V 的模块化设计意味着你可以按需添加功能。这些标准扩展覆盖了乘除法、原子操作、浮点和代码密度。

---

## 1. M 扩展：整数乘除法

M 扩展添加了 8 条乘除法指令，分为有符号和无符号两类。

### 1.1 乘法指令

| 指令 | 功能 | 结果位宽 | 说明 |
|------|------|----------|------|
| `MUL rd, rs1, rs2` | 乘法（低半部分） | XLEN | rd = (rs1 × rs2)[XLEN-1:0] |
| `MULH rd, rs1, rs2` | 有符号×有符号（高半部分） | XLEN | rd = (rs1 × rs2)[2*XLEN-1:XLEN] |
| `MULHU rd, rs1, rs2` | 无符号×无符号（高半部分） | XLEN | 同上，无符号 |
| `MULHSU rd, rs1, rs2` | 有符号×无符号（高半部分） | XLEN | rs1 有符号，rs2 无符号 |

> **为什么需要 MULH 系列？** 两个 32 位数相乘结果是 64 位，`MUL` 只返回低 32 位。要获取完整结果，需要 `MUL` + `MULH` 配合使用。

```asm
# 64 位乘法结果（RV32I + M）
# a0:a1 = a2 * a3
mul   a0, a2, a3       # 低 32 位
mulh  a1, a2, a3       # 高 32 位

# 只需要低 32 位时，一条 MUL 即可
mul   t0, t1, t2       # t0 = t1 * t2（低 32 位）
```

### 1.2 除法指令

| 指令 | 功能 | 特殊情况处理 |
|------|------|-------------|
| `DIV rd, rs1, rs2` | 有符号除法 | 除以 0 → -1；溢出 → -2³¹ |
| `DIVU rd, rs1, rs2` | 无符号除法 | 除以 0 → 2^XLEN - 1 |
| `REM rd, rs1, rs2` | 有符号取余 | 除以 0 → rs1；溢出 → 0 |
| `REMU rd, rs1, rs2` | 无符号取余 | 除以 0 → rs1 |

> **除以 0 不触发异常！** 这是 RISC-V 的设计选择——除以 0 返回特殊值而非触发异常，简化了控制逻辑。软件可以自行检查除数是否为 0。

| 除以 0 的返回值 | 有符号 | 无符号 |
|-----------------|--------|--------|
| DIV | -1 | 2^XLEN - 1 |
| REM | rs1 | rs1 |

---

## 2. A 扩展：原子操作

A 扩展提供两种原子操作机制：**LR/SC**（保留加载/条件存储）和 **AMO**（原子内存操作）。

### 2.1 LR/SC：锁的实现基础

```mermaid
sequenceDiagram
    participant CPU1 as CPU 1
    participant MEM as 内存
    participant CPU2 as CPU 2

    CPU1->>MEM: LR rd, (rs1) — 加载并"标记"该地址
    Note over CPU1,MEM: 内存记录：地址 A 被 CPU1 监视
    CPU2->>MEM: 修改地址 A 的值
    Note over CPU1,MEM: 内存检测到冲突，清除 CPU1 的标记
    CPU1->>MEM: SC rd, rs2, (rs1) — 尝试写入
    MEM-->>CPU1: 失败！rd = 非零值
    CPU1->>CPU1: 重试
```

| 指令 | 功能 |
|------|------|
| `LR.W rd, (rs1)` | 加载 32 位值，并在该地址设置保留标记 |
| `SC.W rd, rs2, (rs1)` | 若保留标记仍有效则写入并返回 0，否则返回非零 |
| `LR.D rd, (rs1)` | 64 位版本（RV64） |
| `SC.D rd, rs2, (rs1)` | 64 位版本（RV64） |

```asm
# 自旋锁实现
# a0 指向锁变量（0=未锁，1=已锁）

spin_lock:
    li    t0, 1
1:
    lr.w  t1, (a0)        # 原子读取锁
    bnez  t1, 1b          # 如果锁已被持有，重试
    sc.w  t1, t0, (a0)    # 尝试获取锁
    bnez  t1, 1b          # 如果 SC 失败，重试
    ret                    # 成功获取锁
```

### 2.2 AMO：原子内存操作

AMO 指令在一条指令中完成"读取-修改-写回"，天然原子：

| 指令 | 功能 | 等价伪代码 |
|------|------|-----------|
| `AMOSWAP.W rd, rs2, (rs1)` | 原子交换 | temp = *addr; *addr = rs2; rd = temp |
| `AMOADD.W rd, rs2, (rs1)` | 原子加 | temp = *addr; *addr += rs2; rd = temp |
| `AMOAND.W rd, rs2, (rs1)` | 原子与 | temp = *addr; *addr &= rs2; rd = temp |
| `AMOOR.W rd, rs2, (rs1)` | 原子或 | temp = *addr; *addr \|= rs2; rd = temp |
| `AMOXOR.W rd, rs2, (rs1)` | 原子异或 | temp = *addr; *addr ^= rs2; rd = temp |
| `AMOMIN.W rd, rs2, (rs1)` | 原子最小值（有符号） | temp = *addr; *addr = min(*addr, rs2); rd = temp |
| `AMOMAX.W rd, rs2, (rs1)` | 原子最大值（有符号） | temp = *addr; *addr = max(*addr, rs2); rd = temp |
| `AMOMINU.W rd, rs2, (rs1)` | 原子最小值（无符号） | 同上，无符号比较 |
| `AMOMAXU.W rd, rs2, (rs1)` | 原子最大值（无符号） | 同上，无符号比较 |

> 所有 AMO 指令都有 `.W`（32 位）和 `.D`（64 位，RV64）两个版本。

```asm
# 原子计数器递增
# a0 指向计数器
amoadd.w t0, t1, (a0)    # *counter += t1, t0 = 旧值

# 原子设置标志位
li      t1, 0x01
amoand.w t0, t1, (a0)    # *flags &= 0x01, t0 = 旧值
```

### 2.3 内存序（Fence）

AMO 和 LR/SC 可以附加 `.AQ`（Acquire）和 `.RL`（Release）后缀来控制内存序：

| 后缀 | 含义 | 典型用途 |
|------|------|----------|
| （无） | 无排序保证 | 单核或已知无竞争场景 |
| `.AQ` | 后续读写不能重排到此指令之前 | 获取锁 |
| `.RL` | 前序读写不能重排到此指令之后 | 释放锁 |
| `.AQRL` | 同时具有 AQ 和 RL 语义 | 全屏障 |

```asm
# 使用 Acquire-Release 语义的自旋锁
spin_lock:
    li    t0, 1
1:
    lr.w.aq  t1, (a0)     # Acquire: 后续操作不能提前
    bnez  t1, 1b
    sc.w.rl  t1, t0, (a0) # Release: 之前的操作不能延后
    bnez  t1, 1b
    ret
```

---

## 3. F/D 扩展：浮点运算

### 3.1 浮点寄存器

F 扩展添加 32 个浮点寄存器 **f0-f31**（每个 32-bit），D 扩展将宽度扩展到 64-bit。

| 浮点寄存器 | ABI 名称 | 用途 | 调用约定 |
|-----------|----------|------|----------|
| f0-f7 | ft0-ft7 | 临时寄存器 | Caller 保存 |
| f8-f9 | fs0-fs1 | 保存寄存器 | Callee 保存 |
| f10-f11 | fa0-fa1 | 参数/返回值 | Caller 保存 |
| f12-f17 | fa2-fa7 | 参数 | Caller 保存 |
| f18-f27 | fs2-fs11 | 保存寄存器 | Callee 保存 |
| f28-f31 | ft8-ft11 | 临时寄存器 | Caller 保存 |

### 3.2 F 扩展指令（单精度）

| 类别 | 指令 | 功能 |
|------|------|------|
| **算术** | `FADD.S` `FSUB.S` `FMUL.S` `FDIV.S` | 加减乘除 |
| | `FSQRT.S` | 平方根 |
| | `FMIN.S` `FMAX.S` | 最小/最大值 |
| **转换** | `FCVT.W.S` `FCVT.S.W` | 整数 ↔ 单精度 |
| | `FCVT.L.S` `FCVT.S.L` | 长整数 ↔ 单精度（RV64） |
| | `FMV.X.W` `FMV.W.X` | 位模式搬移（不转换） |
| **比较** | `FEQ.S` `FLT.S` `FLE.S` | 相等/小于/小于等于 |
| **其他** | `FSGNJ.S` `FSGNJN.S` `FSGNJX.S` | 符号注入 |
| | `FCLASS.S` | 分类（NaN/Inf/零/正规数等） |

### 3.3 D 扩展指令（双精度）

D 扩展的指令与 F 扩展完全对称，只需将 `.S` 替换为 `.D`：

| F 扩展 | D 扩展 | 说明 |
|--------|--------|------|
| `FADD.S` | `FADD.D` | 双精度加减乘除 |
| `FCVT.W.S` | `FCVT.W.D` | 双精度与整数互转 |
| `FMV.X.W` | `FMV.X.D` | 64 位位模式搬移（RV64） |
| — | `FCVT.S.D` / `FCVT.D.S` | 单双精度互转 |

### 3.4 浮点控制状态寄存器（fcsr）

```
fcsr (32-bit):
┌─────────────────────────────────┬───────┬───────────────┐
│              reserved           │  FRM  │     FFLAGS     │
│                                 │ [7:5] │    [4:0]      │
└─────────────────────────────────┴───────┴───────────────┘

FFLAGS (浮点异常标志):
  bit 4: NV - 无效操作 (Invalid)
  bit 3: DZ - 除以零 (Divide by Zero)
  bit 2: OF - 上溢 (Overflow)
  bit 1: UF - 下溢 (Underflow)
  bit 0: NX - 不精确 (Inexact)

FRM (舍入模式):
  000: RNE - 向最近偶数舍入 (默认)
  001: RTZ - 向零舍入
  010: RDN - 向负无穷舍入
  011: RUP - 向正无穷舍入
  100: RMM - 向最近值舍入，远离零
  111: DYN - 动态舍入模式（由 fcsr.FRM 决定）
```

---

## 4. C 扩展：压缩指令

C 扩展将常用指令编码为 16-bit，可减少代码体积 25%-30%。

### 4.1 设计原理

```
32-bit 指令的低 2 位始终为 11
16-bit 指令的低 2 位为 00, 01, 10

→ 硬件可以通过低 2 位快速判断指令长度
→ 16-bit 和 32-bit 指令可以自由混合，无需对齐
```

### 4.2 常用压缩指令

| 压缩指令 | 展开后的 32-bit 指令 | 说明 |
|----------|---------------------|------|
| `C.LI rd, imm6` | `ADDI rd, x0, imm6` | 加载小立即数 |
| `C.LUI rd, imm6` | `LUI rd, imm6` | 加载上位立即数 |
| `C.ADDI rd, imm6` | `ADDI rd, rd, imm6` | 加小立即数 |
| `C.ADDI4SPN rd', imm` | `ADDI rd', sp, imm` | 基于 SP 的地址计算 |
| `C.ADDI16SP imm` | `ADDI sp, sp, imm` | 调整栈指针 |
| `C.MV rd, rs` | `ADD rd, x0, rs` | 寄存器复制 |
| `C.ADD rd, rs` | `ADD rd, rd, rs` | 寄存器加法 |
| `C.LW rd', offset(rs1')` | `LW rd, offset(rs1)` | 加载字 |
| `C.SW rs2', offset(rs1')` | `SW rs2, offset(rs1)` | 存储字 |
| `C.BEQZ rs', offset` | `BEQ rs, x0, offset` | 等于零则跳转 |
| `C.BNEZ rs', offset` | `BNE rs, x0, offset` | 不等于零则跳转 |
| `C.J offset` | `JAL x0, offset` | 无条件跳转 |
| `C.JAL offset` | `JAL x1, offset` | 调用（RV32） |
| `C.JR rs` | `JALR x0, 0(rs)` | 寄存器跳转 |
| `C.JALR rs` | `JALR x1, 0(rs)` | 寄存器调用 |
| `C.NOP` | `ADDI x0, x0, 0` | 空操作 |
| `C.EBREAK` | `EBREAK` | 断点 |

> **C 扩展的限制：** 压缩指令只能访问部分寄存器（x8-x15，又称 s0-s1, a0-a7），立即数范围也有限。这是 16-bit 编码空间有限的妥协。

### 4.3 代码密度对比

```asm
# 不使用 C 扩展（每条 4 字节，共 20 字节）
addi  sp, sp, -16
sw    ra, 12(sp)
sw    s0, 8(sp)
addi  s0, sp, 16
li    a5, 0

# 使用 C 扩展（混合 16/32-bit，共 14 字节，节省 30%）
c.addi sp, -16       # 2 字节
c.sw   ra, 12(sp)    # 2 字节
c.sw   s0, 8(sp)     # 2 字节
c.addi s0, sp, 16    # 2 字节
c.li   a5, 0         # 2 字节
```

---

## 5. B 扩展：位操作

B 扩展（Bitmanip）提供高效的位操作指令，对密码学、网络包处理、数据压缩等场景有显著加速。B 扩展由多个 Zb* 子扩展组成。

### 5.1 子扩展概览

| 子扩展 | 名称 | 说明 | 状态 |
|--------|------|------|------|
| **Zba** | 地址生成加速 | 加速数组索引计算 | 已冻结 |
| **Zbb** | 基本位操作 | 位反转、计数、最值等 | 已冻结 |
| **Zbs** | 单位操作 | 位设置/清除/取反/提取 | 已冻结 |
| **Zbc** | 进位乘法 | 大数乘法加速 | 已冻结 |
| **Zbkb** | 密码学位操作 | 密码学专用位操作 | 已冻结 |
| **Zbkc** | 密码学进位乘法 | 模乘加速 | 已冻结 |
| **Zbkx** | 交叉乘 | AES 等分组密码加速 | 已冻结 |

> **RVA22 Profile 要求：** 服务器场景的 RVA22 Profile 强制要求 Zba + Zbb + Zbs，是 RV64 服务器的事实标准。

### 5.2 Zba：地址生成加速

Zba 提供了加速地址计算的指令——将"移位 + 加法"合并为一条指令：

| 指令 | 功能 | 等价操作 | 典型用途 |
|------|------|----------|----------|
| `SH1ADD rd, rs1, rs2` | rs1 << 1 + rs2 | rd = (rs1 << 1) + rs2 | 数组索引（2 字节元素） |
| `SH2ADD rd, rs1, rs2` | rs1 << 2 + rs2 | rd = (rs1 << 2) + rs2 | 数组索引（4 字节元素） |
| `SH3ADD rd, rs1, rs2` | rs1 << 3 + rs2 | rd = (rs1 << 3) + rs2 | 数组索引（8 字节元素） |

```asm
# 传统方式：访问 int 数组
slli  t0, a1, 2        # t0 = index * 4
add   t0, a0, t0       # t0 = base + index * 4
lw    t1, 0(t0)        # t1 = array[index]

# Zba 方式：节省一条指令
sh2add t0, a1, a0      # t0 = base + index * 4（一条搞定）
lw     t1, 0(t0)       # t1 = array[index]
```

### 5.3 Zbb：基本位操作

| 指令 | 功能 | 用途 |
|------|------|------|
| `CLZ rd, rs` | 前导零计数 | 快速求 log2，优先级编码 |
| `CTZ rd, rs` | 后导零计数 | 求最低有效位位置 |
| `CPOP rd, rs` | 位计数（popcount） | 海明距离、位图统计 |
| `MIN rd, rs1, rs2` | 有符号最小值 | 排序、限幅 |
| `MAX rd, rs1, rs2` | 有符号最大值 | 排序、限幅 |
| `MINU rd, rs1, rs2` | 无符号最小值 | 无符号比较 |
| `MAXU rd, rs1, rs2` | 无符号最大值 | 无符号比较 |
| `SEXT.B rd, rs` | 符号扩展字节 | 数据类型转换 |
| `SEXT.H rd, rs` | 符号扩展半字 | 数据类型转换 |
| `ZEXT.H rd, rs` | 零扩展半字 | 数据类型转换 |
| `REV8 rd, rs` | 字节反转 | 大小端转换 |
| `ORC.B rd, rs` | 字节级或合并 | 字符串处理 |
| `ROL rd, rs1, rs2` | 循环左移 | 密码学 |
| `ROR rd, rs1, rs2` | 循环右移 | 密码学 |
| `RORI rd, rs1, shamt` | 立即数循环右移 | 密码学 |

```asm
# 前导零计数 → 快速求 log2
clz   t0, a0           # t0 = 前导零个数
li    t1, 63
sub   t0, t1, t0       # t0 = 63 - clz = floor(log2(a0))

# popcount → 统计位图中有多少位被设置
cpop  t0, a0           # t0 = a0 中 1 的个数

# 循环移位 → 密码学中的常见操作
ror   t0, a0, a1       # t0 = a0 循环右移 a1 位
```

### 5.4 Zbs：单位操作

| 指令 | 功能 | 等价操作 |
|------|------|----------|
| `BSET rd, rs1, rs2` | 设置指定位 | rd = rs1 \| (1 << rs2) |
| `BCLR rd, rs1, rs2` | 清除指定位 | rd = rs1 & ~(1 << rs2) |
| `BINV rd, rs1, rs2` | 取反指定位 | rd = rs1 ^ (1 << rs2) |
| `BEXT rd, rs1, rs2` | 提取指定位 | rd = (rs1 >> rs2) & 1 |

```asm
# 传统方式：设置第 5 位
li    t0, 0x20         # 1 << 5
or    t1, a0, t0       # t1 = a0 | (1 << 5)

# Zbs 方式：一条指令
bseti t1, a0, 5        # t1 = a0 | (1 << 5)，更直观
```

---

## 6. V 扩展：向量扩展

V 扩展是 RISC-V 最重要的扩展之一，提供可变长度向量（Vector）处理能力，对 AI 推理、信号处理、多媒体等场景至关重要。

### 6.1 设计哲学：可变长度向量

与 ARM SVE/NEON 的固定宽度向量不同，RISC-V V 扩展采用**可变长度向量**设计：

```mermaid
graph LR
    subgraph "固定宽度向量 (ARM NEON)"
        F1["128-bit 固定<br/>软件需要针对不同宽度<br/>写不同版本"]
    end

    subgraph "可变长度向量 (RISC-V V)"
        V1["VLEN 可变<br/>同一份代码<br/>适配不同硬件"]
    end

    style V1 fill:#4ecdc4,color:#fff
    style F1 fill:#a4b0be,color:#333
```

| 特性 | ARM NEON | ARM SVE | RISC-V V |
|------|----------|---------|----------|
| **向量宽度** | 固定 128-bit | 可变 128~2048-bit | 可变（VLEN 由实现决定） |
| **编程模型** | 需要知道宽度 | 通过谓词（Predicate） | 通过 vsetvli 动态设置 |
| **前向兼容** | ❌ 新硬件需重新编译 | ✅ 同一二进制可扩展 | ✅ 同一二进制可扩展 |
| **掩码操作** | 有限 | 谓词寄存器 | 掩码寄存器 v0 |

### 6.2 向量寄存器

V 扩展添加 32 个向量寄存器 v0-v31，每个宽度为 VLEN（实现决定，常见 128/256/512-bit）。

| 寄存器 | 特殊用途 |
|--------|----------|
| **v0** | 掩码寄存器（1 bit per element） |
| **v1-v31** | 通用向量数据寄存器 |

> **向量分组（LMUL）：** 可以将多个向量寄存器组合使用，形成更宽的向量。LMUL=1 使用 1 个寄存器，LMUL=2 使用 2 个，最大 LMUL=8（使用 8 个连续寄存器）。

### 6.3 vsetvli：动态配置向量长度

`vsetvli` 是 V 扩展最核心的指令，用于设置向量操作的元素宽度和分组：

```asm
# vsetvli rd, rs1, vtypei
#   rd    = 实际设置的向量长度 (VL)
#   rs1   = 期望的向量长度（0 = 使用最大值）
#   vtypei = 向量类型配置

# 示例：配置为 32-bit 整数，LMUL=1
vsetvli t0, a0, e32, m1, ta, ma
# e32 = 元素宽度 32-bit
# m1  = LMUL=1（1 个寄存器一组）
# ta  = tail agnostic（尾部元素不关心）
# ma  = mask agnostic（掩码元素不关心）
# t0  = 实际处理的元素个数
```

```
vtype 寄存器关键字段:

 31  30  29  28  27  26  25  24  23  22  21  20  19  18  7   6   5   4   3   2   1   0
┌───┬───┬───┬───┬───┬───┬───┬───┬───┬───┬───┬───┬───┬─────┬───┬───┬───┬───┬───┬───┬───┬───┐
│   │   │   │   │   │   │   │   │   │   │   │   │   │ ... │NV │NM │MA │TA │SEW│ VMA│ VSEW│
└───┴───┴───┴───┴───┴───┴───┴───┴───┴───┴───┴───┴───┴─────┴───┴───┴───┴───┴───┴───┴───┴───┘

VSEW (Selected Element Width):
  000 = 8-bit    001 = 16-bit    010 = 32-bit    011 = 64-bit

VLMUL (Vector Length Multiplier):
  000 = LMUL=1   001 = LMUL=2   010 = LMUL=4   011 = LMUL=8
  111 = fractional LMUL (f8, f4, f2)
```

### 6.4 向量指令分类

| 类别 | 指令示例 | 说明 |
|------|----------|------|
| **配置** | `vsetvli`, `vsetvl` | 设置向量参数 |
| **加载/存储** | `vle32.v`, `vse32.v`, `vlse32.v` | 连续/步进/索引访存 |
| **算术** | `vadd.vv`, `vsub.vv`, `vmul.vv` | 向量-向量运算 |
| | `vadd.vx`, `vsub.vx` | 向量-标量运算 |
| | `vadd.vi` | 向量-立即数运算 |
| **比较** | `vmseq.vv`, `vmslt.vv` | 产生掩码结果 |
| **归约** | `vredsum.vs`, `vredmax.vs` | 向量归约为标量 |
| **掩码** | `vmand.mm`, `vmor.mm` | 掩码逻辑操作 |
| **排列** | `vrgather.vv`, `vcompress.vm` | 向量元素重排 |
| **转换** | `vzext.vf2`, `vsext.vf2` | 元素宽度转换 |
| **浮点** | `vfadd.vv`, `vfmul.vv`, `vfsgnj.vv` | 浮点向量运算 |

### 6.5 向量编程示例

```asm
# 向量加法：C[i] = A[i] + B[i]
# a0 = A 的地址, a1 = B 的地址, a2 = C 的地址, a3 = 元素个数

vadd_loop:
    vsetvli  t0, a3, e32, m1, ta, ma   # 设置 32-bit 元素
    vle32.v  v1, (a0)                    # 加载 A
    vle32.v  v2, (a1)                    # 加载 B
    vadd.vv  v3, v1, v2                  # 向量加法
    vse32.v  v3, (a2)                    # 存储 C
    sub      a3, a3, t0                  # 剩余元素数
    slli     t0, t0, 2                   # 字节偏移
    add      a0, a0, t0                  # A 指针前进
    add      a1, a1, t0                  # B 指针前进
    add      a2, a2, t0                  # C 指针前进
    bnez     a3, vadd_loop               # 循环
```

```asm
# 条件加法：仅对偶数索引元素加 1
# 使用掩码操作

masked_add:
    vsetvli  t0, a1, e32, m1, ta, ma
    vid.v    v1                          # v1[i] = i（索引向量）
    vand.vi  v2, v1, 1                   # v2[i] = i & 1
    vmseq.vi v0, v2, 0                   # v0 = 掩码（偶数索引为 1）
    vle32.v  v3, (a0)                    # 加载数据
    vadd.vi  v3, v3, 1, v0.t             # 掩码加法：仅偶数元素 +1
    vse32.v  v3, (a0)                    # 存储结果
    sub      a1, a1, t0
    slli     t0, t0, 2
    add      a0, a0, t0
    bnez     a1, masked_add
```

### 6.6 VLEN 对性能的影响

| VLEN | 每周期处理 32-bit 元素数 | 典型硬件 |
|------|--------------------------|----------|
| 128 | 4 | 低功耗核心、嵌入式 |
| 256 | 8 | 中端核心 |
| 512 | 16 | 高性能服务器核心 |
| 1024+ | 32+ | AI 加速器 |

> **服务器场景：** RVA22 Profile 不强制要求 V 扩展，但 RVA23 Profile 强制要求。对于 AI 推理和 HPC 场景，V 扩展是必选项。

---

## 7. 服务器关键子扩展

除了上述主要扩展，RV64 服务器场景还有几个重要的子扩展：

### 7.1 Zicbom / Zicboz：缓存管理

| 子扩展 | 功能 | 关键指令 |
|--------|------|----------|
| **Zicbom** | 缓存块维护（无效化、清空） | `CBO.INVAL`, `CBO.CLEAN`, `CBO.FLUSH` |
| **Zicboz** | 缓存块零初始化 | `CBO.ZERO` |

```asm
# 缓存管理指令
cbo.clean  (a0)     # 将脏数据写回内存，保留缓存副本
cbo.inval  (a0)     # 使缓存行无效（不写回脏数据）
cbo.flush  (a0)     # 清空：写回 + 无效化（最安全）
cbo.zero   (a0)     # 将缓存行清零（用于内存分配优化）
```

> **服务器场景：** 缓存管理对 DMA 一致性、自修改代码和多核同步至关重要。RVA22 强制要求 Zicbom + Zicboz。

### 7.2 Zicntr / Zihpm：性能计数器

| 子扩展 | 功能 |
|--------|------|
| **Zicntr** | 基本性能计数器（cycle、time、instret） |
| **Zihpm** | 硬件性能监控计数器（可编程事件计数器） |

```asm
# 读取基本计数器
csrr  t0, cycle       # 时钟周期数
csrr  t1, instret     # 已完成指令数
csrr  t2, time        # 当前时间

# 使用 HPM 计数器
csrw  mhpmcounter3, x0     # 清零计数器 3
csrw  mhpmevent3, 0x01     # 设置事件：L1 I-cache miss
# ... 运行代码 ...
csrr  t0, mhpmcounter3     # 读取 L1 I-cache miss 次数
```

### 7.3 Zifencei：指令缓存刷新

```asm
fence.i              # 保证指令缓存与数据缓存的一致性
                     # 用于自修改代码、JIT 等
```

> **注意：** Zifencei 在 RVA22 中不是强制要求，Linux 通过 SBI 调用 `sbi_remote_fence_i()` 替代。但在裸机场景仍然有用。

---

## 8. 扩展组合速查

| 配置名称 | 包含扩展 | 典型应用 |
|----------|----------|----------|
| RV32I | 基础整数 | 最小实现，教学 |
| RV32IMC | + 乘除法 + 压缩 | 嵌入式 MCU |
| RV32IMAC | + 原子 | 多核嵌入式 |
| RV32IMAFDC | + 浮点 | 全功能 32 位 |
| RV64IMAC | 64 位 + 乘除法 + 原子 + 压缩 | Linux-capable |
| RV64IMAFDC | 全功能 64 位 | 服务器/桌面 |
| RV64G | = RV64IMAFDZicsr_Zifencei | 旧称"GC"的替代 |
| **RV64GC+V** | + 向量扩展 | AI/HPC 服务器 |
| **RVA22** | RV64IMAFDC + Zba+Zbb+Zbs+Zicbom+Zicboz+Zihpm+... | 服务器 Profile |
| **RVA23** | RVA22 + V + Zicond+Zimop+Zcmop+... | 服务器 Profile（含向量） |

---

## 小结

| 扩展 | 核心价值 | 关键指令 |
|------|----------|----------|
| **M** | 硬件乘除法，避免软件模拟 | MUL, DIV, REM |
| **A** | 多核同步、无锁编程 | LR/SC, AMOADD, AMOSWAP |
| **F/D** | IEEE 754 浮点运算 | FADD.S/D, FCVT |
| **C** | 代码密度提升 25-30% | C.LI, C.MV, C.LW, C.SW |
| **B** | 位操作加速、密码学 | SH2ADD, CLZ, CPOP, BSET |
| **V** | 可变长度向量、AI/HPC | vsetvli, vle32.v, vadd.vv |

→ 下一节：[特权模式与 CSR](../03-privileged/privileged-modes-and-csr.md)
