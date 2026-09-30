# DRAM 建模归属与 HBFSim 接入评估

**日期**：2026-09-30
**问题**：当前收集到的数据，接入的 DRAM 是 accelsim2.0 自己的建模吗？能否接入 HBFSim
（`~/nvidiagds/simulators/HBFSim`）测试？两个 DRAM 建模有什么区别？

---

## 一、结论速览

| 问题 | 结论 |
|---|---|
| accelsim2.0 用的 DRAM 是谁的？ | **accelsim2.0 自己的**，即 GPGPU-Sim 原生 `dram_t`。源码里 `hbfsim` 命中 **0** 次 |
| HBFSim 能不能接进来？ | **能**，而且 HBFSim 里**已经写好**了 Accel-Sim↔HBFSim 桥接。但要前移到 accelsim2.0 才能用 |
| 两个 DRAM 建模区别？ | 层次、刷新、tFAW、时序分级、地址映射、容量语义、介质扩展**全部不同**（见 §4） |
| 换 DRAM 能修当前的精度问题吗？ | **不能**。当前剩下的唯一大偏差是 **DRAM 写流量**，根因在 **L2 写回路径**（见另一份报告），不在 DRAM 模型。但做**带宽/延迟饱和实验**时 HBFSim 的 HBM 模型更可信 |

---

## 二、accelsim2.0 的 DRAM 建模（当前数据来自这里）

**代码位置**

| 文件 | 作用 |
|---|---|
| `gpu-simulator/gpgpu-sim/src/gpgpu-sim/dram.h` / `dram.cc` | `dram_t`：bank 状态机、FR-FCFS 调度、时序约束、返回队列 |
| `gpu-simulator/gpgpu-sim/src/gpgpu-sim/dram_sched.h` / `.cc` | FR-FCFS 调度器 |
| `gpu-simulator/gpgpu-sim/src/gpgpu-sim/gpu-sim.h` (~296) | `dram_atom_size = BL × busW × gpu_n_mem_per_ctrlr` |
| `gpu-simulator/gpgpu-sim/src/gpgpu-sim/addrdec.cc` | 地址 → (chip, bank, row, col) 映射；`gpgpu_memory_partition_indexing` |
| `gpu-simulator/gpgpu-sim/src/gpgpu-sim/mem_latency_stat.cc` (~285) | `dram_{reads,writes}_per_mc` 计数（按 **sector** 计） |

**建模方式**：传统 GPGPU-Sim 模型 —— bank/bank-group 状态机 + 数据总线时序约束。
**没有** stack / pseudo-channel / 刷新 / tFAW 概念。

**本次全量运行的实测参数**（`SM89_RTX4000_ADA_C3_idx0_Jl2`）

```text
-gpgpu_clock_domains 2175:2175:2175:4500.5     # core:icnt:L2:DRAM (MHz)
-gpgpu_n_mem 10                                 # 10 个 DRAM channel
-gpgpu_n_sub_partition_per_mchannel 2           # 每 channel 2 个 sub-partition
-gpgpu_dram_buswidth 2
-gpgpu_dram_burst_length 16
-dram_data_command_freq_ratio 4
-gpgpu_n_mem_per_ctrlr 1
=> dram_atom_size = 16 × 2 × 1 = 32 B (= SECTOR_SIZE)

-gpgpu_dram_timing_opt nbk=16:CCD=4:RRD=12:RCD=24:RAS=55:RP=24:RC=78:CL=24:WL=8:CDLR=10:WR=24:nbkgrp=4:CCDL=6:RTPL=4
-dram_latency 324
```

**峰值带宽**（manifest 值，可复现）：
$10 \times 2 \times 16 \times 4500.5\,\text{MHz} / 4 = 360.08$ GB/s ≈ **360.04 GB/s** ✅

**关键计数口径**：`memlatstat_dram_access()` 里
```cpp
unsigned sectors = ceil(mf->get_data_size() / m_config->dram_atom_size);   // =32B
```
- 读：每个 L2 读 miss（含 SECTOR_MISS）产生 1 条 32 B DRAM 读
  → 这就是能反推 L2 读 miss 的恒等式 $\text{DRAM\_read\_bytes} = 32 \times \text{L2\_read\_misses}$（实测成立）
- 写：写回的 `data_size = get_modified_size()`（= 脏扇区数 × 32）→ **按扇区计**

---

## 三、HBFSim 的 DRAM 建模

**代码位置**

| 文件 | 行数 | 作用 |
|---|---|---|
| `src/physical/hbm/hbm_device.hpp` | 475 | HBM 设备/地址/统计 |
| `src/physical/hbm/hbm_device.cpp` | 2253 | 控制器、调度、刷新、时序 |
| `src/physical/hbm/hbm_config.hpp` | 81 | 组织与时序默认值 |

**标准与组织**（`JEDEC-JESD270-4-2025-04`）

```text
capacity 48 GiB, stacks 1, channels/stack 32, pseudo_channels/channel 2,
bank_groups/pseudo_channel 16, banks/group 4, row 2048 B,
channel_width 64 bit, burst_length 8, pin_rate 8.0 Gbps,
data_rate_per_command_clock 4
```

**时序**（显式，含刷新与四激活窗口）

```text
tRCD 14 / tCL 14 / tCWL 10 / tRP 14 / tRAS 32 / tRC 46 / tWR 15 / tRTP 7.5 ns
tCCD_S 2 / tCCD_L 4 cycles
tRRD_S 4 / tRRD_L 6 ns,  tFAW 20 ns,  tWTR_S 4 / tWTR_L 8 ns, tRTW 8 ns
tREFI 3900 / tRFC 350 / tRFCsb 160 / tRREFD 10 ns      ← 刷新，GPGPU-Sim 完全没有
```

**地址映射**：`pch-interleave-bg-rotate-v2`，`interleave_bytes` 默认 256 B
（必须整除 burst 与 row）。**不是** GPGPU-Sim 的 bit 布局字符串。

**控制器**：FR-FCFS，`queue_depth=32`，`frfcfs_cap_ns=5000`，
外加 **anti-starvation bypass 计数**（GPGPU-Sim 无）；`replicate_symmetric_pseudo_channels`
对称伪通道复制加速（结果与逐个服务逐位一致）。

**介质扩展**：HBF（flash）层 —— FTL、mapping cache、write buffer、GC/WL、
每负载磨损图、温度节流；外部层 —— LPDDR / host DRAM / CXL / NVMe / CXL-SSD。

**证据纪律**：`configs/parameter-provenance.json` 给每个物理默认值分级；
DANA A100 实测 overlay 覆盖 host-DRAM 与 NVMe 路径。

---

## 四、两个模型逐项对比

| 维度 | accelsim2.0 `dram_t` | HBFSim HBM |
|---|---|---|
| 层次 | bank / bank-group | **stack / channel / pseudo-channel / bank-group / bank / row** |
| 伪通道 | 无 | 有（2 pch/ch） |
| 刷新 | **无** | tREFI / tRFC / tRFCsb / tRREFD / same-bank refresh |
| 四激活窗口 | **无** | **tFAW = 20 ns** |
| 时序分级 | 单值（CCD/RRD/WTR） | **S/L 分级**（tCCD_S/L、tRRD_S/L、tWTR_S/L）+ tRTW |
| 地址映射 | bit 布局字符串 + linear/xor/IPoly | pch-interleave-bg-rotate-v2 + interleave_bytes |
| 反饥饿 | 无 | bypass 计数 + `frfcfs_cap_ns` |
| 容量语义 | **无**（纯时序，不建模容量/地址空间） | 48 GiB 默认，容量是配置的一部分 |
| 介质扩展 | 无 | HBF(flash) + 5 类外部层 |
| 计数口径 | 按 `dram_atom_size=32B` sector | 按 burst（`burst_bytes`）+ 命令计数器 |
| 校准依据 | 手工/微基准 + 本项目调参 | JEDEC 公开参数 + DANA 实测 overlay + provenance 分级 |
| 时钟域 | 单一 DRAM clock（4500.5 MHz，÷4 得命令率） | 命令时钟与 DQ 速率显式分开（`data_rate_per_command_clock`） |

**一句话**：accelsim2.0 的 `dram_t` 是**事务级 bank 时序模型**（够用、快、但对刷新/FAW/伪通道不敏感）；
HBFSim 的 HBM 是**具备 JEDEC 级时序与介质行为的设备模型**（更真、更慢、且带容量与介质语义）。

---

## 五、HBFSim 已存在的 Accel-Sim 桥接

**机制：弱符号工厂覆写**

```text
hbfsim_dram_factory.h            声明 dram_t *hbfsim_make_dram(partition_id, config, stats, mp, gpu)
hbfsim_dram_factory_default.cc   __attribute__((weak)) 默认实现 = new dram_t(...)
l2cache.cc:86                    m_dram = hbfsim_make_dram(m_id, m_config, m_stats, this, gpu);
```

链接时若 `libhbfsim_accelsim.a` 排在前面 → 强符号胜出 → 全部 memory partition 走桥接。
**不需要改 Makefile**（GPGPU-Sim 的 Makefile 会 glob 该目录下的 `*.cc`）。

**补丁**：`patches/0001-dram_t-virtuals.patch`（把 `dram_t` 虚化）+
`patches/0002-l2cache-dispatch.patch`（改成工厂调用）。

**桥接类**：`hbfsim_accelsim::hbfsim_dram_t : public dram_t`，内部持有
`HBFController / LogicDie / NANDStack / HBMInterface / StagedHbmBaseFabric /
MemorySystem / DirectPeerFabric`。

**输出**：`hbfsim_accelsim.json` —— 含
`hbf_aggregate:host_writes / gc_writes / waf`、`max_temperature_c`、
`fabric.direct_peer:*_busy_frac`、`hbf_controller[...]`。

**现成用法**（HBFSim 侧）

```bash
cd ~/nvidiagds/simulators/HBFSim
ACCELSIM_MODE=hbfsim \
ACCELSIM_GPU_PROFILE=SM89_RTX4000Ada \
integration/accelsim/run_rodinia_batch.sh
# 对照：ACCELSIM_MODE=stock
```

**源码位置问题**：桥接源码在工作树里**已丢失**（`git status` 显示 `?? integration/`），
但**可从 git 恢复**：

```bash
git -C ~/nvidiagds/simulators/HBFSim checkout 9e1e598 -- integration/accelsim/
# 22 个文件：hbfsim_dram_bridge.cc/.hh, hbfsim_accelsim_main.cc, CMakeLists.txt,
#           setup_env.sh, apply_gpgpusim_bridge_patch.py, patches/*, ...
```

编译产物也还在：`build-accelsim/libhbfsim_accelsim.a`、`build-accelsim/hbfsim-accelsim`。

---

## 六、能不能接入 accelsim2.0？（三条路径与工作量）

### ⚠️ 关键障碍

HBFSim 自带的 accel-sim checkout（`ext/accel-sim`）是 **`3016c65`（2026-05）**，
**不含**本项目的任何 Ada 工作：

- Ada (SM89) trace-driven 支持 + generic 地址分类修复
- 死锁修复（per-set 脏行分配失败）
- L2 写路径修复
- `SM89_RTX4000_ADA*` 配置与校准
- `ada_opcode.h`、`trace_generic_memory.h`

→ 直接在 HBFSim 的 checkout 上跑，以上工作**全部丢失**。桥接必须**前移**到 accelsim2.0。

### 路径 A：把桥接移到 accelsim2.0（推荐，工作量中等）

| 步骤 | 内容 | 风险 |
|---|---|---|
| 1 | `git checkout 9e1e598 -- integration/accelsim/` 取回桥接源码 | 低 |
| 2 | 把 `0001-dram_t-virtuals.patch` 应用到 accelsim2.0 的 `dram.h/.cc` | **中**：`dram_t` 虚化会改动基类布局，需确认不影响 `dram_req_t` 与 AccelWattch 的 `set_dram_power_stats` |
| 3 | 把 `0002-l2cache-dispatch.patch` 应用到 accelsim2.0 的 `l2cache.cc`（`m_dram = hbfsim_make_dram(...)`） | 低 |
| 4 | 放 `hbfsim_dram_factory.h` + `_default.cc` 进 `src/gpgpu-sim/` | 低 |
| 5 | 用 accelsim2.0 的 `SM89_RTX4000_ADA_C3_idx0_Jl2` 对齐 HBFSim 的 HBM 参数 | **高**：两套参数模型不同（见 §7），无法逐字段映射 |
| 6 | 建立 stock vs hbfsim 的 A/B 与验收 | 中 |

### 路径 B：只用 HBFSim 做 DRAM 侧独立校验（推荐先做，工作量小）

用 HBFSim 的 trace 前端（`ext/hyfiss/`）吃 accelsim2.0 导出的 DRAM 请求流，
只把 DRAM 层换成 HBM，对比**同一条请求流的完成时间与带宽**。
好处：不动 accelsim2.0 源码；坏处：失去 L2↔DRAM 的耦合反馈。

### 路径 C：Ramulator2 作为第三种后端（补充选项）

HBFSim 已带 `configs/ramulator2/GDDR6_RTX3070_SM86.yaml`。
Ramulator2 是社区标准 DRAM 模型，可作为 `dram_t` 与 HBFSim-HBM 之间的**第三方参照**，
用于判断差异来自"模型实现"还是"参数选择"。对 RTX 4000 Ada 的 GDDR6 更对口。

---

## 七、参数对齐的真实困难（务必诚实记录）

两个模型**不是同一套参数的不同表示**，无法逐字段搬运：

| accelsim2.0 | HBFSim | 对齐问题 |
|---|---|---|
| `nbk=16`（每 channel bank 数） | `bank_groups 16 × banks/group 4 = 64` | 数量级差 4× |
| `nbkgrp=4` | 16 | 不同 |
| `CCD=4, RRD=12, RCD=24, RAS=55, RC=78, CL=24, WL=8, CDLR=10, WR=24, RTPL=4`（**单位=cycle，DRAM clock 4500.5 MHz**） | `tRCD 14, tCL 14, tRAS 32, tRC 46, tWR 15, tRTP 7.5 ns`（**单位=ns**） | 需换算：cycle→ns 用 4500.5 MHz，则 `RCD=24 cyc=5.33 ns` vs HBFSim 14 ns —— **差 2.6×**，不是简单换算问题，是**建模假设不同** |
| 无刷新 | `tREFI=3900 ns` | 无法对齐（一侧没有） |
| `buswidth 2`、`BL 16` | `channel_width 64 bit`、`burst_length 8` | 派生方式不同 |
| 无 pseudo-channel | 2 pch/ch，interleave 256 B | 无法对齐 |

**结论**：接入可以做，但**"对齐参数"是一个独立的研究任务**，不能当成配置搬运。
直接把 accelsim2.0 的 cycle 值填进 HBFSim 的 ns 字段会产生无意义的模型。

---

## 八、对当前精度问题的影响判断（重要）

当前硬件对比里**唯一的红色项**是 `dram__bytes_write.sum` +150%，
以及修正口径后的 L2 读（见 `L2_METRIC_CORRECTION_zh.md`）。

- **读侧**：`dram__bytes_read.sum` 误差 **+0.32%** —— 已经很好
- **写侧**：根因在 **L2 写回路径**（`111ed9c5` 的整行脏扇区过度捕获），**不在 DRAM 模型**
  - 实测：同一条 trace、同一 cycle 点，`dram_writes_per_mc` 从 12.55 MB → 32.60 MB（**2.6×**），
    纯粹由该提交引起；`indexing=2→0` 对写量**无影响**（12.37 vs 12.55 MB）

→ **换 DRAM 模型不会修这个偏差**。DRAM 侧工作的价值在别处：

1. **带宽饱和实验**：`dram256` 探针模拟已显示 sim 峰值能力 311 GB/s（linear）/ 258 GB/s（IPoly）
   vs 硬件 331 GB/s。HBFSim 的 HBM 模型有真实刷新/tFAW/pch 交错，能判断这 6% 差距
   是"DRAM 模型过于乐观"还是"前端（L1/L2/icnt）限流"
2. **容量/介质研究**：HBFSim 独有的 HBF 层与 CXL/SSD 外部层，accelsim2.0 完全没有
3. **方法论交叉验证**：用 Ramulator2（路径 C）对 GDDR6 做第三方校验

---

## 九、建议

1. **本阶段不要动 DRAM 模型**。先把 L2 写回路径修完（已实施，待全量验证），
   让 `dram__bytes_write` 回到硬件水平，再谈换模型。
2. **恢复 HBFSim 桥接源码**（`git checkout 9e1e598 -- integration/accelsim/`），
   保证这套能力不被再次丢失。
3. **先做路径 B**（HBFSim 独立吃 accelsim2.0 的 DRAM 请求流），成本低、信息量够，
   用来回答"DRAM 模型差多少"。
4. **路径 A 作为下一阶段立项**，并把"参数对齐"显式列为一个交付物，
   不要隐含在"接入"里。
5. **加入 Ramulator2 作为第三方参照**（路径 C），避免只在两个自研模型之间做结论。

---

## 十、诚实声明

- HBFSim 的桥接在本仓库**从未运行过**（`build-accelsim/` 产物是 2026-05-20 的，
  早于本项目所有 Ada 工作）；§5 的机制描述来自源码与构建产物，**不是实测**
- §7 的时序换算为按 `4500.5 MHz` 粗算，**未做过正式的单位对齐验证**
- HBFSim 自述 "not a vendor product model ... or a substitute for hardware calibration"；
  其 HBM 绝对时序大部分是 **provenance 分级的产品假设**，不是恢复的 JEDEC 表
- 本报告未改变 accelsim2.0 的任何 DRAM 代码
