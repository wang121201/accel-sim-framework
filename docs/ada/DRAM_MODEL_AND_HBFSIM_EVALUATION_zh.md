# DRAM 建模归属与 HBFSim 接入评估

**日期**：2026-09-30
**问题**：当前收集到的数据，接入的 DRAM 是 accelsim2.0 自己的建模吗？能否接入 HBFSim
（`~/nvidiagds/simulators/HBFSim`）测试？两个 DRAM 建模有什么区别？

---

## 一、结论速览

| 问题 | 结论 |
|---|---|
| accelsim2.0 用的 DRAM 是谁的？ | **accelsim2.0 自己的**，即 GPGPU-Sim 原生 `dram_t`。源码里 `hbfsim` 命中 **0** 次 |
| HBFSim 能不能接进来？ | **桥接能接，但模型不对口。** 桥接源码可从 `9e1e598` 恢复，接入点（`l2cache.cc:94` + `dram.h` 6 个方法）已逐字核对一致；但 **HBFSim 只有 HBM/HBF，没有 GDDR6**，而 Ada 是 GDDR6 → 现在做等于让 Ada 跑在 HBM 上 |
| 所以能"开始 full simulation with HBFSim"吗？ | **不能用于验证 Ada GDDR6**（见 §6 开头）。对 **HBF / 分级内存**研究可以做，但那是另一个问题 |
| 那 GDDR6 该用什么第三方参照？ | **Ramulator2**（原生支持 GDDR6；HBFSim 里已有一份对齐 Accel-Sim 语义的 `GDDR6_RTX3070_SM86.yaml`，但后端未接入 HBFSim 主干） |
| 两个 DRAM 建模区别？ | 层次、刷新、tFAW、时序分级、地址映射、容量语义、介质扩展**全部不同**（见 §4） |
| 换 DRAM 能修当前的精度问题吗？ | **不能**。当前剩下的唯一大偏差是 **DRAM 写流量**，根因在 **L2 写回路径**（见另一份报告），不在 DRAM 模型 |

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

### 🔴 先要回答的问题：HBFSim 根本不能建模 Ada 的 GDDR6

**这是最关键的发现，它决定了"能不能开始 full simulation with HBFSim"的答案。**

HBFSim 自己的内存模型里**没有任何 GDDR6 支持**：

```bash
$ grep -rln "GDDR6\|gddr" src/ include/
(空)
$ find src/physical -maxdepth 1 -type d
src/physical/hbm     # JEDEC JESD270-4 (HBM3/HBM4)
src/physical/hbf     # flash (FTL/GC/磨损/温度)
src/physical/external # LPDDR / host DRAM / CXL / NVMe / CXL-SSD
```

而 RTX 4000 Ada 用的是 **GDDR6**（本项目配置里的 `nbk=16 / buswidth 2 / BL 16 /
4500.5 MHz` 是 GDDR6 语义，不是 HBM 的 stack/pseudo-channel 语义）。

HBFSim 确实带了 `configs/ramulator2/GDDR6_RTX3070_SM86.yaml`，而且这个文件
**就是照着 Accel-Sim 的参数写的**（注释里逐条列出 `gpgpu_n_mem 16`、`buswidth 2`、
`burst_length 16`、`freq_ratio 4`，以及与本项目 Ada 配置**完全相同**的时序
`nbk=16:CCD=4:RRD=12:RCD=24:RAS=55:RP=24:RC=78:CL=24:WL=8:CDLR=10:WR=24:nbkgrp=4:CCDL=6:RTPL=4`）。
它还点明了本项目推导出的那条语义：

> GPGPU-Sim models BL/data_command_freq_ratio = 16/4 = 4 DRAM command cycles of
> data-bus occupancy per 32B atom.

**但这个 GDDR6 配置指向 Ramulator2，而 HBFSim 只在"外部证据"里用了 Ramulator2**
（`evidence/external/assets/ramulator2_driver.cpp` + 一个 **DDR4-3200W** 参考配置，
且 `src/`、`ext/` 里都没有 Ramulator2 后端）。也就是说：

| 想要的东西 | HBFSim 现状 |
|---|---|
| Accel-Sim ↔ HBFSim 桥接 | ✅ 有（源码可从 `9e1e598` 恢复） |
| 桥接接进去的 DRAM 模型 | ❌ 是 **HBM/HBF**，不是 GDDR6 |
| 用 Ramulator2 跑 GDDR6 | 🟡 只有 `GDDR6_RTX3070_SM86.yaml` 配置，"后端"未接入 HBFSim 主干 |
| 建模 Ada 的 DRAM 行为 | ❌ 目前做不到 |

→ **结论：现在做 "full simulation with HBFSim" 得到的是 Ada 跑在 HBM+HBF 上的结果，
不是 Ada 的结果。** 它有意义，但属于**另一个研究问题**（分级内存 / HBF 介质），
不是"验证 Ada 的 GDDR6 建模"。

### ⚠️ 第二个障碍

HBFSim 自带的 accel-sim checkout（`ext/accel-sim`）是 **`3016c65`（2026-05）**，
**不含**本项目的任何 Ada 工作：

- Ada (SM89) trace-driven 支持 + generic 地址分类修复
- 死锁修复（per-set 脏行分配失败）
- L2 写路径修复
- `SM89_RTX4000_ADA*` 配置与校准
- `ada_opcode.h`、`trace_generic_memory.h`

→ 直接在 HBFSim 的 checkout 上跑，以上工作**全部丢失**。

### ✅ 好消息：接入点在 accelsim2.0 侧是现成的（已实测核对）

```
$ grep -n "new dram_t" gpu-simulator/gpgpu-sim/src/gpgpu-sim/l2cache.cc
94:  m_dram = new dram_t(m_id, m_config, m_stats, this, gpu);

$ grep -nE "bool full\(bool|void cycle\(\)|void push\(|return_queue_pop" dram.h
118:  bool full(bool is_write) const;
127:  class mem_fetch *return_queue_pop();
130:  void push(class mem_fetch *data);
131:  void cycle();
```

signatures 与 `0001-dram_t-virtuals.patch` / `0002-l2cache-dispatch.patch`
的预期**逐字一致** → 两个补丁都是**机械可应用**的（合计约 12 行）。
桥接本体 733 行 `.cc` + 129 行 `.hh`，只依赖 `gpu-sim.h` / `mem_fetch.h` / `dram.h`
与 `return_queue_top/pop()` 契约，这几处没被 Ada 工作改过。

### 路径 A：把桥接移到 accelsim2.0

| 步骤 | 内容 | 工作量/风险 |
|---|---|---|
| 1 | `git -C ~/nvidiagds/simulators/HBFSim checkout 9e1e598 -- integration/accelsim/` 取回源码 | 极低（已核实 22 个文件可恢复） |
| 2 | `dram.h` 加 6 个 `virtual` + `virtual ~dram_t() {}` | 低（signature 已核对一致） |
| 3 | `l2cache.cc:94` 改 `hbfsim_make_dram(...)` + include | 极低 |
| 4 | 放 `hbfsim_dram_factory.h` + `_default.cc` 进 `src/gpgpu-sim/` | 极低 |
| 5 | 用 accelsim2.0 的头文件路径构建 `libhbfsim_accelsim.a` | 中：桥接用的是 HBFSim 的 `src/...` 相对 include，需对齐 include dir |
| 6 | **选一个能建模 GDDR6 的设备** | **🔴 阻塞**：见上。HBFSim 只能给 HBM/HBF |
| 7 | 建立 stock vs hbfsim 的 A/B 与验收 | 中 |

**步骤 1–5 是纯工程，可以做；步骤 6 是当前真正的拦路虎。**
所以路径 A 在"要验证 Ada GDDR6"这个目标下**不该现在做**。

### 路径 B：只用 HBFSim 做 DRAM 侧独立校验

用 HBFSim 的 trace 前端吃 accelsim2.0 导出的 DRAM 请求流，对比同一条流的完成时间。
**但同样受 §六开头那条限制**：接进去的是 HBM，不是 GDDR6。

### 路径 C：Ramulator2（真正对口 GDDR6 的路）

既然目标是 GDDR6，**最直接的第三方参照是 Ramulator2 本身**（它原生支持 GDDR6，
HBFSim 里那份 `GDDR6_RTX3070_SM86.yaml` 已经把参数对齐到 Accel-Sim 语义）。
两条做法：

1. **把 Ramulator2 作为 accelsim2.0 的 `dram_t` 后端**（用 §六 的同一套工厂钩子，
   写一个 `ramulator2_dram_t` 而不是 `hbfsim_dram_t`）→ 直接回答
   "accelsim2.0 的 GDDR6 模型选型/参数对不对"
2. 或用 Ramulator2 独立吃 accelsim2.0 导出的 DRAM 请求流（不改源码）

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
