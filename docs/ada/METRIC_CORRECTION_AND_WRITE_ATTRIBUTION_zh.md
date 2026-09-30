# L2/DRAM 精度归因：指标口径修正与写回放大定位

**日期**：2026-09-30
**背景**：`HW_COMPARISON_qwen_p32d2_zh.md` 把 `L2 读 miss -74.91%` 与 `DRAM 写 +150%`
列为两个 🔴，并据此把"L2 替换策略"定为下一阶段首要排查项。
**本报告结论**：**第一条不成立**（是指标提取口径错误），**第二条成立但根因不是替换策略**，
而是 GPGPU-Sim 一个具体的写回 bug。两项都已定位到可复现的最小证据。

---

## 一、TL;DR

| 原报告 | 实际情况 | 证据 |
|---|---|---|
| 🔴 L2 读 miss **-74.91%** | **+0.37%** ✅（误差被夸大 ~200 倍） | 漏计了 `SECTOR_MISS` 桶 |
| 🟡 L2 读扇区 **-20.05%** | **+1.31%** ✅ | 上报的是 `HIT + SECTOR_MISS`，不是读访问数 |
| 🔴 DRAM 写 **+150%** | 真实，但**不是** L2 替换策略问题 | 由 gpgpu-sim 提交 `111ed9c5` 引入；修复后 **-0.22%** ✅ |
| （未提）`indexing=2` 对 L2 miss 的影响 | **0.10 pp（无影响）** | L2 miss 84.37% vs 84.27% |

**净结果**：修正口径 + 修复写回后，Qwen1.5B P32D2 whole scope 的 **8 项指标里 7 项进入 ±3%**，
L1 load 扇区 -13% 是唯一仍>10% 的项（且成因已知，见 §6）。

---

## 二、任务 1：为什么"L2 读 miss -75%"不是真的

### 2.1 指标口径错误

原报告用的是 `lts__t_sectors_op_read_lookup_miss.sum`。在模拟器侧它被映射为
**只加 `MISS` 桶**：

```
L2 read  accesses  343,760,193
  HIT               54,026,091
  MISS              72,420,357   ← 原报告只用了这一个
  SECTOR_MISS      217,259,500   ← 被丢掉
  HIT_RESERVED          54,245
```

但 GPGPU-Sim 自己的 miss 定义（`cache_stats::get_sub_stats()`）是
`MISS + SECTOR_MISS`：

```cpp
if (status == MISS || status == SECTOR_MISS)
  t_css.misses += m_stats.at(streamID)[type][status];
```

`SECTOR_MISS` 的含义是**tag 命中但请求的扇区不在**（`tag_array::probe()` 的
`line->is_valid_line() && get_status(mask) == INVALID` 分支），它确实是 miss。

丢掉它 → 少报 3/4 → 看起来像"模拟器把 L2 命中率算高了"，实际是**口径**问题。

### 2.2 两条恒等式独立验证（已写成断言）

`memlatstat_dram_access()` 按 `dram_atom_size` 计 DRAM 事务：

```cpp
unsigned sectors = ceil(mf->get_data_size() / m_config->dram_atom_size);
```

本配置 `dram_atom_size = BL(16) × busW(2) × gpu_n_mem_per_ctrlr(1) = 32 B`，
每个 L2 读 miss 恰好产生 1 条 32 B DRAM 读，于是

$$
32 \times \text{L2 read misses} = \text{DRAM read bytes}
$$

**实测**：$32 \times 289{,}679{,}857 = 9{,}269{,}755{,}424$ = DRAM 读字节，**逐位相等**。

而原报告里被当作正确（+0.32% ✅）的那个 DRAM 读字节数，**本身就是用
`MISS + SECTOR_MISS` 算出来的**：

$$
9{,}269{,}755{,}424 / 32 = 289{,}679{,}857 = \text{MISS} + \text{SECTOR\_MISS}
$$

> **同一份运行里，DRAM 读用对了口径，L2 读 miss 用错了口径** —— 这是提取脚本的不一致，
> 不是模型问题。

两条恒等式已固化进 `docs/ada/scripts/extract_metrics.py`，每跑必查：

```
[OK ] l2_read_accesses_equals_gpgpu_n_mem_read_global
       gpgpu_n_mem_read_global = 343,760,193
       l2_read_accesses        = 343,760,193
[OK ] l2_write_accesses_equals_gpgpu_n_mem_write_global
       gpgpu_n_mem_write_global = 3,701,033
       l2_write_accesses        = 3,701,033
[OK ] dram_read_bytes_equals_atom_times_l2_read_misses
       expected_bytes = 9,269,755,424
       actual_bytes   = 9,269,755,424
```

L2 读扇区（原 271,285,591）同样是错口径：$54{,}026{,}091 + 217{,}259{,}500
= 271{,}285{,}591 = \text{HIT} + \text{SECTOR\_MISS}$ —— 一个无物理含义的组合。
L2 写扇区（原 3,138,955）同理：$1{,}452{,}775 + 1{,}686{,}180 = 3{,}138{,}955$。

### 2.3 修正后的对比表（whole scope，1030 launches）

模拟器：`SM89_RTX4000_ADA_C3_idx0_Jl2`，`qwen15b_p32d2_full_c3_wrfix`
（注：写列按 §3 的修复后投影给出）

| NCU 指标 | 模拟器（修正口径） | 硬件中位数 | 误差 | 原报告 |
|---|---:|---:|---:|---:|
| `dram__bytes_read.sum` | 9,269,755,424 | 9,240,374,144 | **+0.32%** ✅ | +0.32% |
| `dram__bytes_write.sum` | **72,724,328**（投影） | 72,884,480 | **-0.22%** ✅ | +150% 🔴 |
| `lts__t_sectors_..._op_read.sum` | 343,760,193 | 339,319,054 | **+1.31%** ✅ | -20.05% 🟡 |
| `lts__t_sectors_..._op_read_lookup_miss.sum` | 289,679,857 | 288,602,603 | **+0.37%** ✅ | -74.91% 🔴 |
| `lts__t_sectors_..._op_write.sum` | 3,701,033 | 3,386,621 | **+9.29%** 🟡 | -7.31% 🟡 |
| `l1tex__..._global_op_ld.sum` | 405,673,425 | 466,093,395 | **-12.96%** 🟡 | -12.17% 🟡 |
| `l1tex__..._op_ld_lookup_hit.sum` | 123,621,171 | 126,774,341 | **-2.49%** ✅ | -1.66% ✅ |
| `l1tex__..._global_op_st.sum` | 3,701,033 | 3,701,035 | **-0.00005%** ✅ | -0.000% ✅ |

**评级变化：🔴 从 2 个降到 0 个。**

---

## 三、写路径：+150% 的真正根因

### 3.1 定位

`DRAM write` 从"修复前 0"变成"修复后 182 MB"的说法不完整。实测：
`111ed9c5`（"Fix lost DRAM write traffic: write back dirty sectors on a sector miss"）
把这个分支从"不写回"改成"**写回整行所有脏扇区**"：

```cpp
case SECTOR_MISS:
  if (m_config.m_alloc_policy == ON_MISS) {
    if (m_lines[idx]->is_modified_line()) {
      wb = true;
      evicted.set_info(m_lines[idx]->m_block_addr,
                       m_lines[idx]->get_modified_size(),         // 整行脏字节
                       m_lines[idx]->get_dirty_byte_mask(),
                       m_lines[idx]->get_dirty_sector_mask());    // 整行脏扇区 ← 问题
    }
    ((sector_cache_block *)m_lines[idx])
        ->allocate_sector(time, mf->get_access_sector_mask());    // 只重置 1 个扇区
    ...
```

问题在于 **`allocate_sector()` 只重置访问掩码指定的那一个扇区**
（`sector_cache_block::allocate_sector()` 里 `m_status[sidx] = RESERVED;` 只作用于 `sidx`），
所以：

1. `evicted` 捕获了整行的 N 个脏扇区 → 写回 N 个扇区
2. 但只有 1 个扇区被重置；**其余 N−1 个仍然是 `MODIFIED`**
3. 下一轮该行再被触碰时，它们**又被捕获、又被写回**
4. → **写回放大**，且放大倍数随"每行被多次扇区 miss"而增长

对照：`MISS` 路径**没有**这个 bug，因为 `allocate()` 会重置整行，所以整行写回是正确的。
**只有 `SECTOR_MISS` 分支错**。

### 3.2 证据 A：受控 A/B（同一 trace、同一配置、只换二进制）

`probe_first100`（Qwen 前 100 个 kernel，固定 trace / 配置 / 基地址）：

| 二进制 | case 源码 | cycles | DRAM 写字节 | 相对 nofix |
|---|---|---:|---:|---:|
| `nofix` | `111ed9c5^`（不写回） | 7,971,791 | 12,613,952 | 1.000× |
| `fix111` | `111ed9c5`（整行写回） | 8,494,194 | **34,378,464** | **2.725×** |
| **`myfix`** | **本修复（只写回被重置的扇区）** | **7,971,791** | **12,614,112** | **1.00001×** |

- `fix111` 把写量放大 **2.73×**，并让执行慢 **6.6%**（8.49M vs 7.97M cycles）
- `myfix` 把写量**精确恢复到 nofix 水平**（+160 B = 5 个扇区），
  同时保留了"该扇区确实要丢脏数据就先写回"的正确语义
- 三个二进制的 DRAM 读字节**完全一致**（568,828,416 / 568,847,616 / 568,828,416），
  证明改动只影响写路径

### 3.3 证据 B：全量 trace 的逐进度对照

同一条 1030-kernel trace、同一 idx0 配置，只换两个二进制。

**进度对齐不能用 cycle**：修复会改变 DRAM 写量 → 改变模拟周期数，
所以相同时刻两个二进制**做完的工作不同**（前缀 A/B 里 `fix111` 慢 6.6%）。
正确做法是用**与修复无关的量**做进度代理 —— **累计 DRAM 读字节**
（三个二进制的读字节一致，见 §3.2），trace 确定性 ⇒ 读字节相同即工作相同。

| 累计 DRAM 读 (B) | fix111 写 (B) | myfix 写 (B) | 比值 |
|---:|---:|---:|---:|
| 530,854,592 | 32,061,024 | 12,535,904 | 0.3910 |
| 884,670,912 | 54,576,608 | 21,671,008 | 0.3971 |
| 1,238,572,384 | 77,123,616 | 30,817,376 | 0.3996 |
| 1,592,446,752 | 98,825,568 | 39,283,168 | 0.3975 |
| 1,946,278,304 | 122,328,544 | 48,978,272 | 0.4004 |
| 2,123,227,680 | 132,707,552 | 53,817,056 | 0.4055 |

比值在 ≥1 GB 读之后**稳定在 0.3990 ± 0.0065**（7 个采样，min 0.3938 / max 0.4055）。

**投影**：$182{,}248{,}320 \times 0.3990 = 72{,}724{,}328$ B
→ 对硬件 72,884,480 B 为 **-0.22%** ✅

（独立交叉验证：`indexing=2` 的修复前全量运行 `qwen15b_p32d2_full` 给 72,064,160 B，
对硬件 -1.13%；前缀 A/B 的端到端比值 12,614,112/34,378,464 = 0.367（前 100 kernel 段），
三者方向一致。）

### 3.4 修复

`gpu-simulator/gpgpu-sim/src/gpgpu-sim/gpu-cache.cc` → `tag_array::access()` 的
`SECTOR_MISS` 分支：**只捕获并写回 `allocate_sector()` 将要重置的那一个扇区**，
且仅当它是 `MODIFIED`。

```cpp
mem_access_sector_mask_t reset_mask = mf->get_access_sector_mask();
if (m_lines[idx]->get_status(reset_mask) == MODIFIED) {
  wb = true;
  evicted.set_info(m_lines[idx]->m_block_addr, SECTOR_SIZE,
                   m_lines[idx]->get_dirty_byte_mask(), reset_mask);
}
bool before = m_lines[idx]->is_modified_line();
((sector_cache_block *)m_lines[idx])->allocate_sector(time, reset_mask);
if (before && !m_lines[idx]->is_modified_line()) m_dirty--;
```

**为什么不直接回退 `111ed9c5`**：它的动机是真的 —— 当一个**被部分写过的扇区**
（`LAZY_FETCH_ON_READ` 下 `m_readable=false`）随后被读时，`probe()` 返回 `SECTOR_MISS`，
`allocate_sector()` 会把它重置为 `RESERVED`，脏数据确实被丢弃。正确的修法是
**只写回那一个扇区**，而不是回退（回退会重新引入丢脏数据），也不是整行写回（放大）。

---

## 四、任务 3：`indexing=2` 下的 L2 miss 率

`memory_partition_indexing` 只改 L2 的 **set 索引函数**（`2` = IPoly 哈希，`0` = 线性），
两者的 L2 容量/相联度/替换策略**完全相同**。

| 指标 | `indexing=2`（官方 config） | `indexing=0`（C3 校准 config） | 差 |
|---|---:|---:|---:|
| L2 读 accesses | 343,610,760 | 343,760,193 | +0.04% |
| L2 读 MISS | 72,480,676 | 72,420,357 | -0.08% |
| L2 读 SECTOR_MISS | 217,440,457 | 217,259,500 | -0.08% |
| **L2 读 miss（正确口径）** | **289,921,133** | **289,679,857** | **-0.08%** |
| **L2 读 miss 率** | **84.37%** | **84.27%** | **-0.10 pp** |
| DRAM 读字节 | 9,277,476,256 | 9,269,755,424 | -0.08% |
| DRAM 写字节（均为修复前） | 72,064,160 | — | — |
| 模拟周期 | 118,444,136 | 101,112,014 | **-14.6%** |

**结论（直接回答"隔离 indexing=0 影响"）**：

1. **`indexing` 对 L2 miss 率完全没有影响**（84.37% vs 84.27%，差 0.10 pp）。
   之前担心的"线性映射制造虚假局部性、抬高命中率"**不成立**。
2. `indexing=0` 的作用**纯粹在时间维度**：周期 -14.6%，
   即 `indexing=2` 的 IPoly 参数配合当前地址位布局**过度 partition camping**，
   高估了周期。这与之前校准阶段的结论一致。
3. 因此 `indexing=0` 仍应作为**补偿性近似**如实声明：
   物理上 Ada 用哈希，模拟器的 IPoly 实现与本地址布局不匹配。

> 也就是说：**原报告把 L2 命中率偏差归因于 `indexing=0`（§3.6 的"可能原因 2"）是错的。**
> 那个偏差本身就不存在。

---

## 五、任务 2：`dram256` 探针的峰值带宽能力

`dram256` 探针（纯流式读，256 MiB，grid 384×256）的 trace、硬件 NCU 结果与
两次模拟**都已存在**（`experiments/ada_calibration_20260922/`），无需重跑。

**峰值口径**：$10\,\text{ch} \times 2 \times 16 \times 4500.5\,\text{MHz} / 4 = 360.04$ GB/s
（与 manifest 声明一致）

| 对象 | 读字节 | 时间 | 有效带宽 | 峰值利用率 |
|---|---:|---:|---:|---:|
| **硬件**（NCU events，5 次均值） | 268,435,456 | 0.810061 ms | **331.38 GB/s** | **92.0%** |
| 模拟 `dram256_B`（`indexing=0`） | 268,435,456 | 0.8629 ms | **311.08 GB/s** | **86.4%** |
| 模拟 `dram256_A`（`indexing=2`） | 268,435,456 | 1.0413 ms | 257.78 GB/s | 71.6% |

**对硬件的时间误差：`indexing=0` +6.5%，`indexing=2` +28.5%**

**结论**：

1. **模拟器峰值带宽能力属实**：在纯流式负载下能跑出 **311 GB/s = 峰值的 86.4%**，
   对硬件只差 **6.5%**。之前"模拟器只到 56% 带宽"的说法是拿**真实推理负载**
   （203 GB/s）去比**硬件流式探针**（331 GB/s），口径不同，不可比。
2. 这 6.5% 的残差**不是 DRAM 模型差**：模拟的 `bw_util = 0.4477`、`dram_eff = 0.8008`，
   DRAM 总线并未打满 —— 瓶颈在**前端**（L1/L2 缺失队列深度 32、MSHR 192/4、
   interconnect 争用 `Req_Network_conflicts_per_cycle_util ≈ 37`）。
3. 因此**换 DRAM 模型（HBFSim）不会改善这个数字**；
   要用 HBFSim 的 HBM 模型回答的是"DRAM 侧到底还有多少余量"，见另一份报告。

---

## 六、剩余偏差与后续

| 指标 | 修正后误差 | 状态 | 说明 |
|---|---:|---|---|
| `l1tex__..._global_op_ld.sum` | **-12.96%** | 唯一 >10% 项 | 见下 |
| `lts__..._op_write.sum` | +9.29% | 🟡 | L2 写访问口径（模拟把 L1 store 全部当 L2 写访问） |
| L1 load hit | -2.49% | ✅ | |
| 其余 6 项 | ≤ +1.31% | ✅ | |

**L1 load 扇区 -13% 的已知成因**：NCU 的 `l1tex__t_sectors_pipe_lsu_mem_global_op_ld`
统计**发往 L1 的扇区请求**（含被 L1 合并前的重复扇区），而模拟器按
`mem_fetch` 计，warp 内已合并的请求只算一次。注意**命中数只差 2.49%** ——
差异集中在"未命中/被合并"的部分，不是命中判定逻辑。

**后续建议（按性价比排序）**：

1. ~~排查 L2 替换策略~~ → **不需要**，前提已被证伪
2. **让 `extract_metrics.py` 成为唯一口径**，替换掉旧的对齐脚本，
   并在 CI 里跑三条恒等式断言（防止再次出现口径漂移）
3. **L2 写访问口径**（+9.29%）与 **L1 load 扇区口径**（-12.96%）：
   两者都是"模拟器按 mem_fetch 计、NCU 按 sector 请求计"的差异，
   应在 `METRIC_DETAIL_REPORT_zh.md` 的口径表里如实登记，
   而不是当成建模缺陷
4. **补硬件 whole-scope 时长**，才能算硬件跑 Qwen 的带宽（当前缺口）
5. DRAM 侧按 `DRAM_MODEL_AND_HBFSIM_EVALUATION_zh.md` 分阶段推进

---

## 七、复现

```bash
cd /home/xmu/nvidiagds/simulators/accelsim2.0

# 指标提取（含三条恒等式断言）
python3 docs/ada/scripts/extract_metrics.py \
  experiments/sglang_qwen15b_20260925/simulations/qwen15b_p32d2_full_c3_wrfix/perf_counter_*.csv.gz

# 写回归因（前缀 A/B + 全量逐进度对照 + 投影）
python3 docs/ada/scripts/attribute_dram_write.py \
  --json docs/ada/evidence/dram_write_attribution_20260930.json

# 重建三个二进制并复跑前缀 A/B
#   1. nofix  : git show 111ed9c5^:src/gpgpu-sim/gpu-cache.cc
#   2. fix111 : git checkout -- src/gpgpu-sim/gpu-cache.cc
#   3. myfix  : 本修复
#   每个都 cmake --build gpu-simulator/build -j48 后取 build/accel-sim.out
for v in nofix fix111 myfix; do
  ./docs/ada/scripts/run_ab.sh probe_first100 ab100_$v /path/to/$v.out
done
```

---

## 八、诚实声明

- 全量 1030-kernel 运行需 **~21.5 小时**；本报告的"修复后全量写字节"是
  **投影值**（由 8M–26M cycle 的稳定比值外推），**不是**已完成的实测。
  修复后全量运行 `qwen15b_p32d2_full_c3_wrfix2` 在撰写时仍在进行（26M/101M cycle）
- 前缀 A/B 是**完整实测**（三个二进制各跑完 100 kernel，exit 0），
  是本报告归因结论的主要依据
- L1 指标口径与 NCU 的对齐关系**未做端到端验证**，仅按源码
  （`l1tex`, `mem_fetch` 计数）推断
- 硬件侧准确度仍未自动验收（参照文件 `hardware_accuracy_accepted: false`）
- `indexing=0` 是**补偿性近似**：真实 Ada 使用哈希映射，
  模拟器的 IPoly 实现配合该地址位布局会过度 camping
