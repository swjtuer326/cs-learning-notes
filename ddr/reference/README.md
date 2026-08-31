# DDR reference 目录清单

本目录存放 DDR 专题的一手 **JEDEC 标准**,作为正文的事实来源。**不编号、不进 HTML**(构建只收 `README.md` + `NN-*.md`),仅做本地查证。正文引用时标注「JEDEC JESDxx-xx §章节」。

## 已就位的标准

| 文件 | 标准 | 覆盖 |
|------|------|------|
| `JESD79-4D-DDR4.pdf` | JESD79-4D | DDR4 SDRAM |
| `JESD79-5C.01-DDR5.pdf` | JESD79-5C.01 | DDR5 SDRAM |
| `JESD209-5C-LPDDR5-5X.pdf` | JESD209-5C | LPDDR5 / LPDDR5X |
| `JESD209-6-LPDDR6.pdf` | JESD209-6 | LPDDR6 |
| `JESD235D-HBM1-2.pdf` | JESD235D | HBM / HBM2 |
| `JESD238B.01-HBM3.pdf` | JESD238B | HBM3 |
| `JESD270-4-HBM4.pdf` | JESD270-4 | HBM4 |
| `JESD239C-GDDR7.pdf` | JESD239C | GDDR7 |
| `JESD250D-GDDR6.pdf` | JESD250D | GDDR6 |
| `viking-ddr4-sodimm-datasheet.pdf` | —(模块级,复述 JEDEC 表) | DDR4 ECC SO-DIMM |

## 关键 DDR4 事实速查(已与 JESD79-4D 交叉核对)

**DDR4-3200 速度分级(x8 颗粒)**:tCK=0.625ns,CL=22,tRCD=13.75ns(22 nCK),tRP=13.75ns(22 nCK),tRAS=32ns,tRC=45.75ns,时序串 22-22-22。

**模式寄存器位域**(A12..A0):

| MR | 关键位域 |
|----|---------|
| MR0 | A[11:9]=WR/RTP、**A8=DLL Reset**(1=复位,自清)、A7=TM、A[6:4]+A2=CL、A3=突发类型、A[1:0]=BL |
| MR1 | A12=Qoff、A11=TDQS、A[10:8]=Rtt_NOM、**A7=Write Leveling 使能**、A[4:3]=AL、A[2:1]=Ron(DIC)、**A0=DLL 使能** |
| MR2 | A12=Write CRC、A[10:9]=Rtt_WR、A[7:6]=Auto Self-Refresh、A[5:3]=CWL |
| MR3 | A[12:11]=MPR Read Format、A[8:6]=Fine Granularity Refresh、A5=温度传感器读出、A4=Per-DRAM 寻址、A3=Geardown、A2=MPR 操作、A[1:0]=MPR Page |
| MR4 | A12=写前导码、A11=读前导码、A9=Self-Refresh Abort、A[8:6]=CAL、A4=内部 Vref Monitor、**A3=温度控制刷新使能、A2=温度区间(0=0–85°C,1=85–95°C)**、A1=Max Power Down |
| MR5 | Rtt_PARK、CRC/Parity 相关、Read/Write DBI、Data Mask |
| MR6 | A[12:11]=tCCD_L/tDLLK Timing、A7=VrefDQ Training 使能、A[5:0]=VrefDQ Training 值 |

**关键结论(用于纠错)**:
- DLL Reset 是 **MR0[8]**(不是 MR0[11]);WR 是 **MR0[11:9]**;Write Leveling 使能是 **MR1[7]**;DLL 使能是 **MR1[0]**。
- **MR4 不读实际温度**,只有温度控制刷新的「使能(A3)+区间(A2)」两位标志;温度传感器读出在 **MR3[5]**。
- 刷新:tREFI=7.8μs(0–85°C)、3.9μs(85–95°C);tRFC(min)=350ns(8Gb)、550ns(16Gb);tZQinit=1024 nCK;tDLLK=1024 nCK(@DDR4-3200);tMRD=8 nCK;tMOD=max(24 nCK, …)。
