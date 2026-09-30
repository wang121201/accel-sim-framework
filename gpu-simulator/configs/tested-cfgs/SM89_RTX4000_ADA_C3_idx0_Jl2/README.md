# SM89_RTX4000_ADA_C3_idx0_Jl2 —— 校准配置

RTX 4000 Ada (SM89) 的**校准配置**，由 12 例受控消融实验选出。

## 与官方 shipped 配置的差异（3 项）

```
-gpgpu_memory_partition_indexing   2 -> 0
-gpgpu_l2_rop_latency            187 -> 237
-dram_latency                    254 -> 324
```

## 文件哈希

| 文件 | SHA256 |
|---|---|
| `gpgpusim.config` | `b3618131724451f84fc839b4752eef34f64e70d660303c6532941e100d7ceca4` |
| `trace.config` | `9f57b875d0786e13590fa977afe3432f238ca3a1ce788efc315d40989d8d5fe1` |
| `config_ampere_islip.icnt` | `f1c6b2f7340605bbc500e8fc8bed457aeeffaa5774f4cb2d572feb8df4a619ca` |

## 准确度（12 例，8 train + 4 held-out，NCU 对照）

| 指标 | 值 |
|---|---:|
| TRAIN MAPE | 7.28% |
| HELD-OUT MAPE | 8.36% |
| ALL MAPE | 7.64% |
| MAX APE | 20.37% |
| train/held-out 差距 | 1.07pp |

**对比官方 shipped 配置**：ALL MAPE 20.62% → 7.64%（**2.7 倍改善**）。

## 逐指标表现（全量 Qwen1.5B P32D2，1030 kernel）

| NCU 指标 | 误差 |
|---|---:|
| DRAM 读字节 | **+0.32%** ✅ |
| L1 store 扇区 | **-0.000%** ✅ |
| L1 load 命中 | **-1.66%** ✅ |
| L2 读 miss | -74.91% 🔴 |
| DRAM 写字节 | +150.05% 🔴 |

## ⚠️ 已知限制（必须随配置发布）

1. **`indexing=0` 是补偿性近似**，不代表 Ada 硬件使用线性 partition 映射。
   真实 Ada 使用哈希；模拟器的 IPoly 实现配合当前地址位布局会过度 partition
   camping 并高估周期。选 `idx0` 是消除该模拟器侧 artefact，**物理映射仍未知**。

2. **237/324 不是端到端最优**。敏感性扫描显示：
   - L2 谷底在 250 附近（7.03% < 7.64%）
   - DRAM 在扫描范围内单调下降（400 时 6.48%），未见谷底
   - 差异在 n=12 下不显著，且 `dram=400` 的 held-out 已劣化到 10.00%

3. **L2 替换/淘汰策略与真实 Ada 不一致**（当前最大精度瓶颈）：
   全量对比显示 L2 读 miss 低估 75%、DRAM 写高估 150%。

## 证据

- 消融矩阵：`docs/ada/ablation_matrix_20260927.json`
- 校准报告：`docs/ada/CALIBRATION_REPORT_zh.md`
- 准确度报告：`docs/ada/ACCURACY_REPORT_zh.md`
- 指标详情：`docs/ada/METRIC_DETAIL_REPORT_zh.md`
- 硬件对比：`docs/ada/HW_COMPARISON_qwen_p32d2_zh.md`
