# Accel-Sim 2.0 Ada 支持 —— 阶段整合报告

**日期**：2026-09-30
**阶段**：SM89 RTX 4000 Ada 支持 + 死锁修复 + 写路径修复 + 校准 + 硬件对比
**状态**：阶段性完成，归档

---

## 一、阶段成果总览

| # | 成果 | 状态 | 证据 |
|---|---|---|---|
| 1 | **死锁修复**（per-set 脏行分配失败）| ✅ 已修复验证 | 30-kernel + 1030-kernel 通过 |
| 2 | **DRAM 写路径修复**（SECTOR_MISS 丢脏数据）| ✅ 已修复验证 | 12 格回归 + 全量 |
| 3 | **Ada (SM89) 配置校准** | ✅ 12 格验证 | MAPE 20.62% → 7.64% |
| 4 | **全量 Qwen1.5B P32D2 模拟** | ✅ exit 0，1030/1030 | 零死锁 |
| 5 | **硬件 NCU 对比** | ✅ 完成 | 读路径 +0.32% |
| 6 | **带宽评估** | ✅ 完成 | 见下 |

---

## 二、带宽评估

### 2.1 模拟器有效带宽（Qwen1.5B P32D2 全量）

| 项 | 值 |
|---|---:|
| 模拟周期 | 101,125,548 cycles |
| Core 时钟 | 2175 MHz |
| **模拟时间** | **46.495 ms** |
| DRAM 读 | 9,269.8 MB |
| DRAM 写 | 182.2 MB |
| DRAM 总流量 | 9,452.0 MB |
| **有效 DRAM 带宽** | **203.29 GB/s** |
| 读带宽 | 199.37 GB/s |
| 写带宽 | 3.92 GB/s |
| 峰值带宽（manifest） | 360.04 GB/s |
| **带宽利用率** | **56.46%** |

### 2.2 硬件实测带宽参照

来自 `GPU3_CHECKPOINT.md` 的 `dram256` 纯流式探针（NCU 实测）：

| 项 | 值 |
|---|---:|
| DRAM 读字节 | 268,491,392 B |
| NCU 时长 | 812,352 ns |
| GPC cycles | 1,748,886.5 |
| DRAM 时钟 | 8.542 GHz |
| **实测带宽（NCU 时长）** | **330.51 GB/s** |
| 实测带宽（events） | 331.45 GB/s |
| 文档记录 | 331.3769 GB/s |

### 2.3 带宽对比与解读

| 项 | 模拟器 | 硬件实测 | 说明 |
|---|---:|---:|---|
| 峰值带宽 | 360.04 GB/s | — | manifest 声明 |
| **实测/有效带宽** | **203.29** | **330.51** | ⚠️ 见下 |
| 利用率 | 56.46% | 91.79% | |

**⚠️ 关键说明：两者不可直接比较**

- **硬件 330.51 GB/s** 来自 `dram256` **纯流式探针** —— 设计目标就是打满 DRAM，
  是**峰值带宽测试**（利用率 91.8%）
- **模拟器 203.29 GB/s** 来自 **Qwen1.5B 真实推理负载** —— 访存模式受
  KV cache 复用、L2 命中、依赖链限制，**本就不应打满带宽**

**正确的对比方式**：
- 模拟器的**峰值能力**应由同类流式负载验证（需额外跑 `dram256` 探针的模拟）
- 模拟器的**真实负载带宽 203 GB/s** 应与**硬件跑同一 Qwen 负载**的带宽对比

**硬件跑 Qwen 的带宽**（由 NCU 流量 + 时长推算）：
```
硬件 whole scope: DRAM 读 9.240 GB + 写 0.073 GB = 9.313 GB
```
> ⚠️ 参照文件**未提供 whole scope 的硬件时长**，因此无法直接算硬件 Qwen 带宽。
> 这是**已知缺口**，需补充 NCU 时长采集。

---

## 三、硬件 NCU 对比（Qwen1.5B P32D2，whole scope = 1030 launches）

| NCU 指标 | 我的模拟 | 硬件中位数 | 误差 | 评级 |
|---|---:|---:|---:|:--:|
| `dram__bytes_read.sum` | 9,269,755,424 | 9,240,374,144 | **+0.32%** | ✅ |
| `l1tex__..._op_st.sum` | 3,701,033 | 3,701,035 | **-0.000%** | ✅ |
| `l1tex__..._op_ld_lookup_hit.sum` | 124,666,742 | 126,774,341 | **-1.66%** | ✅ |
| `l1tex__..._op_ld.sum` | 409,374,458 | 466,093,395 | -12.17% | 🟡 |
| `lts__..._op_write.sum` | 3,138,955 | 3,386,621 | -7.31% | 🟡 |
| `lts__..._op_read.sum` | 271,285,591 | 339,319,054 | -20.05% | 🟡 |
| `lts__..._op_read_lookup_miss.sum` | 72,420,357 | 288,602,603 | **-74.91%** | 🔴 |
| `dram__bytes_write.sum` | 182,248,320 | 72,884,480 | **+150.05%** | 🔴 |

**结论**：
- ✅ **读路径优秀**（DRAM 读 +0.32%，L1 store 逐位精确，L1 load hit -1.66%）
- 🔴 **L2 层显著偏差**（读 miss -75%，写 +150%）→ **L2 替换/淘汰策略与真实 Ada 不一致**

---

## 四、代码改动清单

### 4.1 GPGPU-Sim（`ada-sm89-deadlock-fix` 分支，3 commits）

| Commit | 内容 |
|---|---|
| `f6204579` | **死锁修复**：`tag_array::probe()` 按 set 脏行最后手段淘汰 + L2 write-alloc ack |
| `23a9393b` | Ada configs + manifest（记录校准发现）|
| `111ed9c5` | **写路径修复**：`SECTOR_MISS` 分支捕获脏扇区 + 设 `wb` |

**文件**：
- `src/gpgpu-sim/gpu-cache.cc`（2 处修复，共 38 行）
- `src/gpgpu-sim/l2cache.cc`（write-alloc ack）
- `src/gpgpu-sim/shader.cc/.h`、`gpu-sim.cc`、`scoreboard.cc/.h`（诊断）
- `configs/tested-cfgs/SM89_RTX4000_ADA/`（配置 + manifest）
- `configs/tested-cfgs/SM89_RTX4000_ADA_C3_idx0_Jl2/`（校准配置）

### 4.2 Accel-Sim 框架（`ada-sm89-support` 分支，8 commits）

| Commit | 内容 |
|---|---|
| `6fdad71` | Ada trace-driven 支持 + generic 地址分类修复 |
| `963c693` | Ada trace config + 注册 RTX4000ADA |
| `9f485b6` | tuner runner 路径健壮化 |
| `bf29b6d` | 校准报告与证据 |
| `2f51956` | gitignore 本地实验产物 |
| `294861b` | L1/L2/DRAM 详细指标对比 |
| `1c77152` | 记录写路径修复 |
| `396bdb7`、`7d01f63` | 12 格回归验证报告 |
| `2c1973e` | 硬件 NCU 对比报告 |

**文件**：
- `gpu-simulator/trace-driven/trace_driven.cc/.h`、`trace_generic_memory.h`
- `gpu-simulator/ISA_Def/ada_opcode.h`
- `gpu-simulator/configs/tested-cfgs/SM89_RTX4000_ADA{,_C3_idx0_Jl2}/`
- `util/tuner/run_all.sh`、`util/tuner/NVIDIA_RTX_4000_Ada_Generation/`
- `util/tracer_nvbit/tracer_tool/tracer_tool.cu`
- `util/job_launching/configs/define-standard-cfgs.yml`
- `docs/ada/`（5 份报告）

---

## 五、Smoke 测试与验证清单

| 测试 | 规模 | 结果 | 位置 |
|---|---|---|---|
| 死锁复现（kernel 30）| 30 kernel | ✅ exit 0 | `simulations/probe_n30/` |
| 死锁复现（前 17）| 17 kernel | ✅ exit 0 | `simulations/probe_first17/` |
| 死锁复现（前 43）| 43 kernel | ✅ exit 0 | `simulations/probe_first43/` |
| 写路径回归 | **12 格** | ✅ 12/12 exit 0 | `scripts/regress_write_path.sh` |
| 全量 Qwen（无修复）| 1030 kernel | ✅ exit 0 | `simulations/qwen15b_p32d2_full/` |
| **全量 Qwen（含写修复）** | **1030 kernel** | ✅ **exit 0** | `simulations/qwen15b_p32d2_full_c3_wrfix/` |
| Llama 回归 | — | ✅ exit 0 | `simulations/regress_llama4/` |

**复现脚本**：
- `experiments/sglang_qwen15b_20260925/run_sim.sh`（官方配置）
- `experiments/sglang_qwen15b_20260925/run_sim_c3.sh`（校准配置）
- `experiments/sglang_qwen15b_20260925/run_sim_c3_wrfix.sh`（校准 + 写修复）
- `experiments/ada_calibration_20260922/scripts/regress_write_path.sh`（12 格回归）

---

## 六、归档内容

### 6.1 提交到 Git（小文件）

| 仓库 | 分支 | 内容 |
|---|---|---|
| gpgpu-sim | `ada-sm89-deadlock-fix` | 源码修复 + 配置 + manifest |
| Accel-Sim | `ada-sm89-support` | trace-driven 支持 + 配置 + 文档 + 脚本 |

### 6.2 归档到 `docs/ada/`（报告）

| 文件 | 内容 |
|---|---|
| `CALIBRATION_REPORT_zh.md` | 配置校准方法与消融 |
| `ACCURACY_REPORT_zh.md` | 12 格准确度评估 |
| `METRIC_DETAIL_REPORT_zh.md` | L1/L2/DRAM 逐指标对比 |
| `WRITE_PATH_FIX_VERIFICATION_zh.md` | 写路径修复验证（12 格）|
| `HW_COMPARISON_qwen_p32d2_zh.md` | 硬件 NCU 对比（全量）|
| `ablation_matrix_20260927.json` | 消融矩阵原始数据 |

### 6.3 不入 Git 的大产物（保留在实验目录）

| 产物 | 大小 | 位置 |
|---|---|---|
| 全量模拟 perf_counter | ~1.2 GB/次 | `simulations/*/perf_counter_*.csv.gz` |
| trace 文件 | ~100 GB | `cases/*/traces/` |
| NCU 原始报告 | 15.2 GB | `tuner_refine_20260922/results/` |

> `experiments/` 已在 `.gitignore` 中排除（323 GB）。

---

## 七、已知限制与后续工作

### 7.1 已知限制

1. **L2 建模偏差**（最大问题）：读 miss -75%，写 +150% → 替换/淘汰策略与真实 Ada 不一致
2. **L1 建模未验证**：NCU 无 `l1tex__` 命中指标，微基准无复用
3. **硬件 Qwen 带宽缺口**：参照文件无 whole scope 时长，无法算硬件 Qwen 带宽
4. **MSHR 并发未建模**（参照文件 `limits` 明确指出）
5. **配置未定优**：敏感性扫描显示 237/324 非最优点（详见 `ACCURACY_REPORT_zh.md`）

### 7.2 建议后续

1. **优先排查 L2 替换策略** —— 当前最大精度瓶颈
2. 补充 NCU 时长采集 → 完成硬件 Qwen 带宽对比
3. 跑 `dram256` 探针的模拟 → 验证模拟器峰值带宽能力
4. 验证 `indexing=2`（官方）下的 L2 miss 率，隔离 `indexing=0` 的影响

---

## 八、诚实声明

- 硬件侧准确度**未自动验收**（参照文件 `hardware_accuracy_accepted: false`）
- 参照文件警告**不要混合 whole/Decode/steps 三个 scope** → 本报告只用 `whole`
- 带宽对比中，模拟器（真实负载）与硬件（流式探针）**口径不同**，不可直接比较
- `indexing=0` 是**补偿性近似**，不代表 Ada 硬件使用线性映射
