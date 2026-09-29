# Accel-Sim vs 真实硬件 准确度报告（SM89 RTX 4000 Ada）

**日期**：2026-09-29
**模拟器**：`gpu-simulator/bin/release/accel-sim.out`（Accel-Sim `d930ad6` + GPGPU-Sim `91880c53`，含死锁修复）
**硬件**：NVIDIA RTX 4000 Ada (SM89)，驱动 550.163.01，锁频 SM 2175 MHz / MEM 9001 MHz
**对照方法**：NCU 实测（`ncu --csv`）× 模拟器，按 **1-based 启动序号 + 完整 demangled 名称 + grid/block 形状** 对齐（无名称-only join）

---

## 一、结论摘要（TL;DR）

| 问题 | 结论 |
|---|---|
| 新配置是否 solid？ | 🟡 **可用，但未定优**。12 格 MAPE 7.64%，但敏感性扫描显示存在更优点（6.48–7.03%）；差异在 12 格样本下不显著 |
| 当前配置下 accelsim 表现如何？ | ✅ 功能类指标（指令数 / L2 扇区 / L2 命中）**逐位精确（0.00%）**；周期（cycles）**平均偏差 -5.42%，MAPE 7.64%** |
| 哪类指标表现好？ | 计数类（确定性）**完美**；时序类（cycles）**受内存子系统建模误差主导** |
| 最大误差来源 | 单格 `l2hold_m16_p4_b192_t256_i4` 恒 +20%，是所有候选的共同瓶颈 |

---

## 二、逐指标表现（12 个受控微基准格）

聚合口径：12 格的 `hardware_sum` / `sim_sum` 求和后计算偏差；MAPE 为逐格绝对相对误差的算术平均。

| 指标 | 覆盖率 | 硬件合计 | 模拟合计 | 偏差 (bias) | MAPE | 评级 |
|---|---:|---:|---:|---:|---:|:--:|
| `cycles` | 12/12 | 6,892,063 | 6,518,610 | **-5.42%** | **7.64%** | 🟡 |
| `warp_instructions` | 12/12 | 20,304,784 | 20,304,784 | **+0.00%** | **0.00%** | ✅ |
| `l2_read_sectors` | 12/12 | 27,262,976 | 27,262,976 | **+0.00%** | **0.00%** | ✅ |
| `l2_read_hits` | 12/12 | 10,485,760 | 10,485,760 | **+0.00%** | **0.00%** | ✅ |

**解读**：

1. **功能类指标逐位精确。** 指令数、L2 读扇区、L2 命中三项在全部 12 格上**完全相等**（偏差 0.00%）。
   这说明 trace 驱动路径的**功能建模正确**，且 L2 扇区粒度与流量口径与硬件一致。
2. **唯一有误差的是 cycles**，平均低估 5.42%。误差**不来自**功能模型，而来自
   **内存子系统时序建模**（L2/DRAM 延迟、partition 映射、排队）。

---

## 三、逐格 cycles 明细（选定配置 `C3_idx0_Jl2`）

| 格 | 类型 | 硬件 cycles | 模拟 cycles | 相对误差 |
|---|---|---:|---:|---:|
| `l2train_m8_p8_b48_t32_i1` | train | 1,000,505 | 954,127 | -4.64% |
| `l2train_m8_p8_b192_t32_i1` | train | 266,097 | 257,828 | -3.11% |
| `l2train_m8_p8_b48_t256_i1` | train | 164,144 | 173,357 | +5.61% |
| `l2train_m8_p8_b48_t32_i4` | train | 322,428 | 337,984 | +4.82% |
| `dramtrain_m64_p1_b48_t32_i1` | train | 1,851,318 | 1,713,126 | -7.46% |
| `dramtrain_m64_p1_b192_t32_i1` | train | 529,366 | 439,523 | **-16.97%** |
| `dramtrain_m64_p1_b48_t256_i1` | train | 448,005 | 423,600 | -5.45% |
| `dramtrain_m64_p1_b48_t32_i4` | train | 603,707 | 542,151 | -10.20% |
| `l2hold_m16_p4_b96_t128_i1` | held-out | 202,063 | 207,201 | +2.54% |
| `l2hold_m16_p4_b192_t256_i4` | held-out | 173,245 | 208,538 | **+20.37%** |
| `dramhold_m96_p1_b96_t128_i1` | held-out | 671,187 | 631,574 | -5.90% |
| `dramhold_m96_p1_b192_t256_i4` | held-out | 659,998 | 629,601 | -4.61% |

- **TRAIN MAPE 7.28% / HELD-OUT MAPE 8.36% / 差距 1.07pp** → 无过拟合
- **最大误差 +20.37%** 恒定出现在 `l2hold_m16_p4_b192_t256_i4`
- 误差**有正有负**（-17% ~ +20%），说明不是系统性缩放偏差，而是**特定访存模式的建模差异**

---

## 四、与基线配置的对比

| 配置 | 改动 | TRAIN | HELD-OUT | ALL | MAX |
|---|---|---:|---:|---:|---:|
| `CTL_shipped`（官方原样） | — | 16.80 | 28.25 | 20.62 | 45.16 |
| `CTL_idx0` | 仅 `indexing 2→0` | 15.84 | 8.38 | 13.35 | 25.18 |
| `CTL_l2_237_324` | 仅 L2/DRAM 延迟 | 9.95 | 27.81 | 15.91 | 45.19 |
| `C3_idx0_v2l2` | idx0 + 微基准 L2 | 8.26 | 8.77 | 8.43 | 20.35 |
| **`C3_idx0_Jl2`（选定）** | idx0 + 残差 L2 | **7.28** | **8.36** | **7.64** | **20.37** |

**关键洞察**：

1. **`memory_partition_indexing` 是主导项**：单独从 `2` 改到 `0` 就把 held-out 从
   28.25% 降到 8.38%（**3.4 倍改善**），远超任何延迟参数调整。
2. **L2/DRAM 延迟单独调整对 held-out 几乎无效**（27.79 / 27.81 / 28.60，差 <1%），
   只在 train 上"有效" → 典型过拟合。它们只有在叠加 `idx0` 后才带来真实收益。
3. **官方配置在 held-out 上高估周期 25–45%** —— 这是模拟器侧 artefact。

---

## 五、⚠️ 配置 solid 性评估（重要）

### 5.1 敏感性扫描结果：237/324 不是最优点

对选定值做单参数扫描（其余固定，全部 12 格）：

**L2 延迟扫描（DRAM 固定 324）**

| L2 值 | 187 | 210 | 224 | **237** | 250 | 270 |
|---|---:|---:|---:|---:|---:|---:|
| ALL MAPE | 11.25 | 9.47 | 8.63 | **7.64** | **7.03** | 7.73 |

**DRAM 延迟扫描（L2 固定 237）**

| DRAM 值 | 254 | 290 | **324** | 360 | 400 |
|---|---:|---:|---:|---:|---:|
| ALL MAPE | 10.16 | 9.01 | **7.64** | **6.49** | **6.48** |

### 5.2 结论

1. **L2 谷底在 250 附近，不是 237**（7.03% < 7.64%）。237 来自官方 tuner 的
   residual-stage 中位数，**不是**端到端最优。
2. **DRAM 在扫描范围内单调下降，未见谷底**（254→10.16, 400→6.48）。
   说明 DRAM 延迟仍在补偿其他建模误差，**不是物理标定值**。
3. **差异统计上不显著**：12 格样本下 7.64% vs 6.49% 的差距不足以判定优劣；
   且 `S_dram_400` 的 held-out 已劣化到 10.00%（train 4.72%）→ **gap 5.28pp，过拟合迹象**。
4. **所有候选 MAX 都在 20% 左右**，且**恒为同一格** `l2hold_m16_p4_b192_t256_i4`
   → 该格是**共同瓶颈**，调 L2/DRAM 延迟无法解决。

### 5.3 判定

> **`C3_idx0_Jl2` 是"可用且显著优于官方"的配置，但不是"已定优"的配置。**
> - ✅ 相比官方：ALL 20.62% → 7.64%（**2.7 倍改善**），无过拟合（gap 1.07pp）
> - 🟡 但 237/324 是 tuner 中位数而非端到端最优；敏感性扫描存在更优点
> - 🔴 残余的 ~7% 误差集中在单一格，需要**机制性修复**（而非继续调延迟参数）

### 5.4 诚实声明（必须随配置发布）

`indexing=0` 是**补偿性近似**，不代表 Ada 硬件使用线性 partition 映射。
真实 Ada 使用哈希；模拟器的 IPoly 实现配合当前地址位布局会**过度 partition camping**
并高估周期。选 `idx0` 是消除该模拟器侧 artefact，**物理映射仍未知**。

---

## 六、误差来源分析

| 来源 | 证据 | 影响 |
|---|---|---|
| Partition 映射 artefact | `idx0` 使 sim 周期系统性 ×0.78–0.87 | **主导**（已补偿） |
| L2/DRAM 延迟 | 单独调整对 held-out 无效 | 次要（过拟合风险） |
| 单格瓶颈 `l2hold_m16_p4_b192_t256_i4` | 所有候选恒 +20% | **未解决** |
| 时钟域口径 | sim core cycles vs NCU `gpc__cycles_elapsed.avg` | 系统性小偏差 |

**未解决问题**：`l2hold_m16_p4_b192_t256_i4` 在**所有**候选下都是 +20%，
且 train/held-out 表现一致 → 说明是**结构性建模问题**，不是参数问题。
需要单独诊断该访存模式（`m16_p4` = 16 次 merge、4 次 pass；`b192_t256` = 192 block、256 线程）。

---

## 七、复现

```bash
cd experiments/ada_calibration_20260922

# 逐指标聚合（本报告第二节）
python3 - <<'EOF'
import json,glob
from collections import defaultdict
agg=defaultdict(lambda: {'hw':0.0,'sim':0.0,'absrelerr':[],'n':0})
for f in sorted(glob.glob('comparisons/ctl_C3_idx0_Jl2_*/comparison.json')):
    d=json.load(open(f))
    if not d.get('success'): continue
    for m,v in d['summary'].items():
        if v.get('hardware_sum') is None or v.get('sim_sum') is None: continue
        agg[m]['hw']+=v['hardware_sum']; agg[m]['sim']+=v['sim_sum']
        e=v.get('sum_absolute_relative_error_percent')
        if e is not None: agg[m]['absrelerr'].append(e)
        agg[m]['n']+=1
for m in sorted(agg):
    a=agg[m]
    bias=(a['sim']-a['hw'])/a['hw']*100
    mape=sum(a['absrelerr'])/len(a['absrelerr'])
    print(f'{m:26s} n={a["n"]:2d} bias={bias:+7.2f}% MAPE={mape:6.2f}%')
EOF

# 敏感性扫描汇总（本报告第五节）
python3 - <<'EOF'
import json,glob
def mape(p):
    e=[json.load(open(f))['summary']['cycles']['sum_absolute_relative_error_percent']
       for f in sorted(glob.glob(f'comparisons/{p}_*/comparison.json'))
       if json.load(open(f)).get('success')]
    return sum(e)/len(e) if e else None
for v in [187,210,224,250,270]: print(f'S_l2_{v}', mape(f'ctl_S_l2_{v}'))
for v in [254,290,360,400]:    print(f'S_dram_{v}', mape(f'ctl_S_dram_{v}'))
EOF
```

**数据来源**：
- 消融矩阵：`comparisons/ablation_matrix_20260927.json`
- 敏感性扫描：`comparisons/ctl_S_{l2,dram}_*/comparison.json`（12 格/候选）
- 硬件微基准：`hardware_microbench/summary.json`（schema `ADA_RTX4000_MICROBENCH_SUMMARY_V2`）
- NCU 对照：`tuner_refine_20260922/results/<case>/profile_mangled.csv`

**对齐口径**：1-based 启动序号 + 完整 demangled 名称（空白归一化）+ 可用 grid/block；
**无名称-only join**，形状不符的配对会被拒绝（`rejected_pairs`）。
