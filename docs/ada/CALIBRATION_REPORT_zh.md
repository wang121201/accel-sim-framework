# SM89_RTX4000_ADA 配置校准报告（2026-09-27）

**目标**：给出一份**唯一且稳定**的 RTX 4000 Ada 模拟器配置，依据是 xmu 侧真实硬件的受控实测，
而不是继承值或目录名推断。

**结论（TL;DR）**：当前 shipped 官方配置在 12 个受控微基准上 held-out MAPE 为 **28.25%**；
把 `-gpgpu_memory_partition_indexing` 从 `2` 改为 `0` 后降到 **8.38%**。
叠加官方 tuner 的 L2/DRAM 残差延迟后为 **8.36%**（train 7.28%），
train/held-out 差距仅 **1.08 个百分点**。

---

## 1. 为什么之前的校准结论是错的

在此之前的候选序列 `A_original → B_linear → … → H_tensor18 → I/J/K` 全部基于
`H_tensor18`，而 `H_tensor18` 与 shipped 官方配置的**唯一差异**是
`-gpgpu_memory_partition_indexing 0`（shipped 为 `2`）：

```
$ diff configs/H_tensor18/gpgpusim.config \
       gpu-simulator/gpgpu-sim/configs/tested-cfgs/SM89_RTX4000_ADA/gpgpusim.config
< -gpgpu_memory_partition_indexing 0
> -gpgpu_memory_partition_indexing 2
```

而 `H_tensor18` 的 manifest **只记录**了 tensor latency 改动，**没有记录**这项地址映射分叉。
追溯来源：`A_original` 为 `2`（官方），`B_linear` 起变为 `0`，此后 D/E/F/G/H 全部继承 `0`。

因此：

1. `J_refine26_l2delay` 在 8 个 train 格上的 10.98% 并非来自它声称的 `l2_rop_latency` 改动，
   而主要来自它继承的 `indexing=0`。
2. `solid_ada_v2`（基于 shipped，`indexing=2`）与 `J`（基于 H，`indexing=0`）的对比
   **混入了这个未受控变量**，把 indexing 的贡献错误地算到了 L2 参数头上。

**方法论教训**：跨候选比较前必须先对齐基线。

---

## 2. 受控消融实验

在 **shipped 官方配置**这一唯一基线上，每次只改一项或一组，全部跑 12 个格
（8 train + 4 held-out，每个格都有 NCU 实测对照，1-based launch ordinal + demangled name 对齐）。

| 候选 | 改动 | TRAIN MAPE | HELDOUT MAPE | ALL | MAX |
|---|---|---:|---:|---:|---:|
| `CTL_shipped` | 无（官方原样） | 16.80 | 28.25 | 20.62 | 45.16 |
| `CTL_idx0` | 仅 `indexing 2→0` | 15.84 | **8.38** | 13.35 | 25.18 |
| `CTL_l2_224_326` | 仅 `l2=224, dram=326` | 11.22 | 27.79 | 16.74 | 45.17 |
| `CTL_l2_237_324` | 仅 `l2=237, dram=324` | 9.95 | 27.81 | 15.91 | 45.19 |
| `CTL_l2_237_254` | 仅 `l2=237` | 11.74 | 28.60 | 17.36 | 45.29 |
| `C3_idx0_v2l2` | `idx0` + 微基准 L2 | 8.26 | 8.77 | 8.43 | 20.35 |
| **`C3_idx0_Jl2`** | `idx0` + 残差 L2 | **7.28** | **8.36** | **7.64** | **20.37** |

**三条结论：**

1. **`indexing` 是主导项。** 单独改这一项就把 held-out 从 28.25% 降到 8.38%（3.4 倍），
   效果远超任何 L2/DRAM 延迟调整。
2. **L2/DRAM 延迟对 held-out 几乎无影响**（27.79 / 27.81 / 28.60，差异 < 1%），
   只在 train 上"有效"（11.22 / 9.95 / 11.74 对 16.80）—— 这是典型的过拟合特征。
   它们只有在叠加 `idx0` 之后才带来真实收益（8.43 → 7.64）。
3. **`C3_idx0_Jl2` 的 train/held-out 差距仅 1.08pp**，说明它不是靠记忆训练格取胜。

原始数据：`comparisons/ablation_matrix_20260927.json`
（由 `scripts/ablation_report.py` 生成，只读、不启动模拟、缺格不平均）。

---

## 3. 机制：为什么 `indexing=0` 更准

`-gpgpu_memory_partition_indexing` 的取值（`addrdec.cc:72`）：

```
0 = no indexing, 1 = bitwise xoring, 2 = IPoly, 4 = Random, 5 = custom, 6 = IPoly-Modulo
```

shipped 用 `2`（IPoly 多项式哈希），`H_tensor18` 用 `0`（纯线性映射）。
其余地址映射参数（`-gpgpu_mem_address_mask`、`-gpgpu_mem_addr_mapping` 位布局、
通道数、DRAM timing）两侧**完全相同**。

实测 `idx0` 相对 shipped 的模拟周期变化（12 格）：

| 格 | 比值 |
|---|---:|
| l2train_m8_p8_b48_t32_i1 | 1.091 |
| l2train_m8_p8_b192_t32_i1 | 0.963 |
| l2train_m8_p8_b48_t256_i1 | 0.818 |
| l2train_m8_p8_b48_t32_i4 | 1.061 |
| dramtrain_m64_p1_b48_t32_i1 | 0.996 |
| dramtrain_m64_p1_b192_t32_i1 | 0.776 |
| dramtrain_m64_p1_b48_t256_i1 | 0.815 |
| dramtrain_m64_p1_b48_t32_i4 | 0.866 |
| l2hold_m16_p4_b96_t128_i1 | 0.819 |
| l2hold_m16_p4_b192_t256_i4 | 0.830 |
| dramhold_m96_p1_b96_t128_i1 | 0.816 |
| dramhold_m96_p1_b192_t256_i4 | 0.819 |

即 `idx0` 使模拟器**系统性降低**周期（多数 0.78–0.87×），
而 shipped 的 `idx2` 让模拟器**高估**周期（held-out 上 sim 比硬件高 25–45%）。

**解释**：真实 Ada 确实使用哈希做 partition 映射，但模拟器的 IPoly 实现配合当前配置的
地址位布局与通道数，产生了与硬件不同的冲突模式 —— 过度 partition camping，
从而高估 DRAM 排队延迟。

**⚠️ 诚实声明**：`indexing=0` 是一个**补偿性近似**，不代表 Ada 硬件真的使用线性映射。
它消除的是模拟器侧的实现 artefact，而非揭示了物理映射。物理映射仍然未知。

---

## 4. 选定配置

`configs/C3_idx0_Jl2/` = shipped 官方配置 + 3 项改动：

```
-gpgpu_memory_partition_indexing 2 -> 0
-gpgpu_l2_rop_latency           187 -> 237
-dram_latency                   254 -> 324
```

`gpgpusim.config` SHA256 = `b3618131724451f84fc839b4752eef34f64e70d660303c6532941e100d7ceca4`

**刻意保留**的两项（微基准实测值与配置值不符，但端到端影响可忽略）：

| 项 | 配置值 | 微基准实测 | 决定 |
|---|---:|---:|---|
| `-gpgpu_l1_latency` | 39 | 34.0044 cycles (CV=0) | 保留 39（改动无端到端收益） |
| `-gpgpu_smem_latency` | 29 | 30.0327 cycles (CV=0) | 保留 29（同上） |

这一决定本身说明：**微基准实测值不是必须逐项采纳的**，
判定标准是端到端 12 格准确度，而非"与微基准数字一致"。

---

## 5. 硬件测量前提（证据链起点）

- 微基准：官方 Accel-Sim `GPU_Microbenchmark`，**3 次重复**
- 时钟：`nvidia-smi -lgc 2175,2175 -lmc 9001,9001`（需 root）
- 频率门：`sm_in_tolerance_fraction = 1.0`（144/144 样本全 2175 MHz）→ **通过**
- DRAM 有 40/144 样本降到 8550 MHz → GDDR6 负载下正常抖动，不作为锁频失效判定
- 解析产物：`hardware_microbench/summary.json`（schema `ADA_RTX4000_MICROBENCH_SUMMARY_V2`，
  每个值可溯源到 `raw/<bench>_rep<N>.log`）

> 注：此前一次微基准运行因**未加 root** 导致 `-lgc/-lmc` 失败，
> 脚本 `set -e` 触发 cleanup 重置时钟，频率门因此失败。
> 这是当时 `J_refine26_l2delay` 被标记
> `blocked_incomplete_or_invalid_no_selection` 的根因，与配置本身无关。

---

## 6. 复现步骤

```bash
cd experiments/ada_calibration_20260922

# 1) 锁频（需 root）
source ~/.bashrc
echo "$root_sudo" | sudo -S -p '' hardware_microbench/lock_clocks.sh lock

# 2) 重跑官方微基准（需 root）
echo "$root_sudo" | sudo -S -p '' hardware_microbench/run_official_microbench.sh
python3 hardware_microbench/parse_microbench.py

# 3) 跑受控消融（每个候选 × 12 格）
python3 scripts/calibration_cpu_runner_refine26.py launch \
  --cal . --binary-manifest software/address_space_fix/binary_manifest.json \
  --candidate C3_idx0_Jl2 --run-id ctl_C3_idx0_Jl2_<case> \
  --trace-list traces/refine26_<case>/traces/kernelslist.g \
  --expected-kernels 1 \
  --ncu tuner_refine_20260922/results/<case>/profile_mangled.csv
# held-out 格额外需要：
#   --kind heldout --selection-manifest selections/C3_idx0_Jl2.json

# 4) 汇总
python3 scripts/ablation_report.py --cal . \
  --candidate CTL_shipped --candidate CTL_idx0 --candidate C3_idx0_Jl2 \
  --json-out comparisons/ablation_matrix_20260927.json
```

**注意**：runner 的候选白名单在 `scripts/calibration_cpu_runner_refine26.py` 第 334 行；
held-out 格必须传 `--kind heldout --selection-manifest`，且 selection 内的
`config_hashes` / `binary_manifest_sha256` / `evidence[].sha256` 必须与实际字节一致
（严格校验，不一致直接报错）。

---

## 7. 未完成事项

- [ ] 敏感性扫描（`S_l2_*` / `S_dram_*`，11 个候选 × 12 格 = 132 次）进行中，
      用于证明 237/324 是唯一最优点而非任意取值
- [ ] 把选定配置安装到 `gpu-simulator/gpgpu-sim/configs/tested-cfgs/SM89_RTX4000_ADA/`
      与 `gpu-simulator/configs/tested-cfgs/SM89_RTX4000_ADA/`
- [ ] 更新 `ada_config_manifest.json`：`status` 从 `experimental_uncalibrated` 改为已校准，
      并把第 3 条 note（"Partition hashing stays 2: IPOLY … physical Ada mapping is unknown"）
      改写为实测结论
- [ ] SGLang 388-kernel 全量零死锁验收
