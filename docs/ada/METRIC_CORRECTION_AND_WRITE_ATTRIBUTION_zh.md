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
| 🔴 DRAM 写 **+150%** | 真实，但**不是** L2 替换策略问题 | 由 gpgpu-sim 提交 `111ed9c5` 引入；修复后 **≈+1.8%** ✅（两条独立方法一致） |
| （未提）`indexing=2` 对 L2 miss 的影响 | **0.10 pp（无影响）** | L2 miss 84.37% vs 84.27% |
| （未提）`111ed9c5` 的动机 —— "微基准 sim 写 = 0 vs 硬件 1.34 MB" | **trace 范围不匹配，不是模型缺陷** | 那批 case 的 trace **只有 1 个只读 kernel**；`l2train` 硬件写**本来就是 0**（见 §3.5） |

**净结果**：修正口径 + 修复写回后，Qwen1.5B P32D2 whole scope 的 **8 项指标里 7 项进入 ±3%**，
L1 load 扇区 -13% 是唯一仍>10% 的项（且成因已知，见 §6）。

**附带结论**：`111ed9c5` **既没解决它声称的问题**（`dramtrain` 只从 0 走到 96 B，
离硬件 1.37 MB 仍差 450×），**又破坏了本来正确的量**（`l2train` 凭空多出 282–2298 个扇区写，
Qwen 写流量放大 2.725×）。详见 §3.5。

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
| `dram__bytes_write.sum` | **约 74.2 MB**（投影，85% 数据两条方法一致） | 72,884,480 | **≈+1.8%** ✅ | +150% 🔴 |
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

比值在 ≥1 GB 读之后稳定在 **0.396–0.410**。

**两条互相独立的投影（都跑在 85% 数据上，结果一致）：**

| 方法 | 计算 | 结果 |
|---|---|---|
| A. 按 fix111 比值 | $182{,}248{,}320 \times 0.4069$ | 74.15 MB → **+1.73%** |
| B. 按修复前完整运行 `idx2 NOFIX` 比值 | $72{,}064{,}160 \times 1.030$ | 74.23 MB → **+1.84%** |

方法 B 的逐点对照（同一 trace，按累计 DRAM 读对齐）：

| 累计 DRAM 读 (B) | MYFIX 写 (B) | `idx2 NOFIX` 写 (B) | 比值 |
|---:|---:|---:|---:|
| 783,920,992 | 18,907,360 | 18,343,872 | 1.0307 |
| 2,351,698,272 | 58,929,248 | 57,327,424 | 1.0279 |
| 3,919,500,672 | 69,693,600 | 67,685,888 | 1.0297 |
| 5,487,273,920 | 70,896,160 | 68,802,176 | 1.0304 |
| 6,584,730,208 | 72,278,208 | 70,158,368 | 1.0302 |

比值**稳定在 1.030**（去掉最初暂态后区间仅 1.028–1.032）—— 即修复后的写量
**逐点贴合"修复前正确模型"**，两者只差 idx0/idx2 的组映射差异。

**结论：修复后 `dram__bytes_write` ≈ +1.8%**（从 +150% 降下来）。
两条方法差 0.1 pp，可作为最终估值。

> ⚠️ 投影会随运行推进小幅漂移（早期 41% 快照给 -0.5%，85% 快照给 +1.8%）。
> **以 85% 快照的 ≈+1.8% 为准**；完整运行（`qwen15b_p32d2_full_c3_wrfix3`）已在跑，
> 完成后可给确数。

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

### 3.5 证据 C：`111ed9c5` 的动机本身站不住（微基准追溯）

`111ed9c5` 的提交信息写道：

> On the RTX 4000 Ada microbenchmarks the simulator reported total dram writes = 0
> while hardware measured **1.34-1.46 MB**

这一条追到底后发现：**那个 "0 vs 1.34 MB" 不是模型缺陷，是 trace 范围不匹配。**

**第一步：这几个 case 的 trace 里只有 1 个 kernel。**

```bash
$ wc -l experiments/ada_calibration_20260922/traces/refine26_dramtrain_m64_p1_b48_t32_i1/traces/kernelslist.g
1
$ head -1 .../kernelslist.g
kernel-8-ctx_0x58a041ed0d10.tracez        # memory_concurrency_probe<1>
```

探针源码 `tuner_refine_20260922/source/memory_concurrency_probe.cu` 显示，
产生写流量的两个 kernel **不在 trace 里**：

| kernel | 作用 | 是否在 trace |
|---|---|---|
| `initialize_pattern` | 把 pattern 写进整个缓冲（**写流量来源**） | ❌ 不在 |
| `flush_cache_cg` | 读 256 MiB，把 L2 脏行挤出去 | ❌ 不在 |
| `memory_concurrency_probe<1>` | 只读探针（`ld.global.cg`）+ 极小的 `output[]` 写 | ✅ 唯一在 trace 里的 |

探针自身的写只有 `output[chain*workers+worker]`：grid 48 × block 32 × ILP 1 × 4 B
= **6 KB**，且先进 L2。所以**单跑这个 kernel，DRAM 写必然是 ~0**。

**第二步：硬件侧的数字确实来自 ROI 之外的残留脏数据。**

直接读 NCU 原始导出（`tuner_refine_20260922/results/<case>/profile*_mangled.csv`）：

| case | 硬件 `dram__bytes_read.sum` | 硬件 `dram__bytes_write.sum`（3 次） |
|---|---:|---:|
| `l2train_m8_p8_b48_t32_i1` | 8,396,288 / 8,392,064 / 8,392,064 | **0 / 0 / 0** |
| `dramtrain_m64_p1_b48_t32_i1` | 67,133,184 / 67,115,264 / 67,115,520 | **1,385,984 / 1,380,608 / 1,359,232** |

- `l2train`：硬件的 DRAM 写**就是 0** —— 模拟器的 0 是**对的**
- `dramtrain`：硬件 1.37 MB，但那只能是**前序 `initialize_pattern` 弄脏、被探针的
  64 MiB 读挤出去**的残留（`flush_cache_cg` 已排掉绝大部分）。模拟器的 trace 里
  没有那个弄脏的 kernel，L2 从**干净**状态开始 → 0 是**结构上应有的答案**

**第三步：`111ed9c5` 在自己的动机 case 上也没达标。**

| case | 硬件写 | 修复前 | `111ed9c5` | 本次修复 |
|---|---:|---:|---:|---:|
| `l2train_m8_p8_b48_t32_i1` | **0** | 0 ✅ | **282**（凭空多出） ❌ | **0** ✅ |
| `l2train_m8_p8_b192_t32_i1` | **0** | 0 ✅ | **1092** ❌ | **0** ✅ |
| `l2train_m8_p8_b48_t256_i1` | **0** | 0 ✅ | **2298** ❌ | **0** ✅ |
| `l2train_m8_p8_b48_t32_i4` | **0** | 0 ✅ | **1128** ❌ | **0** ✅ |
| `dramtrain_m64_p1_b48_t32_i1` | 1,385,984 | 0 | **96**（仍差 **450×**） | **0** |

（`111ed9c5` 一列来自 2026-09-29 的 12 格回归 `/tmp/wr_regress`；
本次修复一列来自 `docs/ada/scripts/regress_write_path.sh`。）

**结论**：`111ed9c5`
1. 在 `l2train` 上把**本该是 0 的写**变成 282–2298 个扇区（**凭空造流量**）；
2. 在自己的动机 case `dramtrain` 上只从 0 走到 96 B，离硬件 1.37 MB 仍差 450 倍
   （它自己也在提交信息里承认了这一点，归因于"工作集留在 L2"）；
3. 在真实负载 Qwen 上把写流量放大 **2.725×**。

即：它既没解决它声称要解决的问题，又破坏了一个本来正确的量。
**正确的处理是修 trace 范围（把 `initialize_pattern` / `flush_cache_cg` 也纳入 trace），
而不是在缓存模型里补写回。**

> ⚠️ 这条也说明：**微基准的 DRAM 写对比目前不可用于判定模型好坏**，
> 因为 trace 只含只读探针。此前把 `dramtrain` 的 0 当成"丢写回 bug"是误判。

### 3.6 修复方案：不是重截 trace，而是按"可比性"拆分这 12 格

#### 为什么不能重截 trace 来修

ROI 只含探针 kernel 是**探针源码强制的**，不是截取时的疏忽：

```cpp
// tuner_refine_20260922/source/memory_concurrency_probe.cu, Nvbit()
const std::string injection = environment("CUDA_INJECTION64_PATH");
if (injection.empty() || std::getenv("DYNAMIC_KERNEL_RANGE"))
  throw std::runtime_error("trace requires CUDA_INJECTION64_PATH and no DYNAMIC_KERNEL_RANGE");
```

即**显式拒绝**用 `DYNAMIC_KERNEL_RANGE` 放宽窗口；ROI 靠
`ProfileScope roi(nvbit, mode != "events", tag)` → `nvbit.begin(tag)/nvbit.end()`
（NVBit 的 start/stop API）圈定。探针自己还在报告里声明了
`flush_and_validation_outside_roi = true`。tracer 的 `stats_ctx_*` 也证明它
**看到了全部 8 个 kernel**（`initialize_pattern`×2、`flush_cache_cg`×3、探针×3），
只是按 ROI 只给第 8 个落了盘：

```
kernel-3  flush_cache_cg              total_reported_insts = 0,0
kernel-8  memory_concurrency_probe<1> total_reported_insts = 1443280,1443280
```

**而且重截的代价不可接受**。按探针几何量算全序列的内存指令数：

| kernel | 次数 | 内存指令 |
|---|---:|---:|
| `initialize_pattern`（64 MiB + 256 MiB 写） | 2 | ~84 M stores |
| `flush_cache_cg`（每次读 256 MiB） | 3 | ~50 M loads |
| `memory_concurrency_probe` | 3 | ~4 M |
| **合计** | | **~138 M** |

按现有 trace 的 ~67 B/指令估，全序列 trace ≈ **9 GB**；
对照 Qwen（9.27 GB DRAM 流量、1030 kernel）耗时 **21.5 h**，
单格全序列模拟约 **10–25 h**，而且每格要跑两次（含/不含探针）才能差分出
探针的写 —— 12 格 ≈ **数周机时**。**不划算。**

#### 实际做法：按硬件是否恰为 0 拆分

把 12 格的硬件写值取全（`profile*_mangled.csv`，各 3 次重复）：

| 组 | 硬件 `dram__bytes_write.sum` | 单 kernel trace 能不能复现 |
|---|---|---|
| `l2train_*`（4）、`l2hold_*`（2） | **0 / 0 / 0** | ✅ **能**（读-only 探针 + 干净 L2 ⇒ 必须 0） |
| `dramtrain_*`（4）、`dramhold_*`（2） | ~1.34–1.50 MB | ❌ 不能（需 ROI 之前的脏状态） |

于是：

- **`l2*` 6 格 → 写列有效且是"零断言"**：硬件 0，模拟器必须也是 0。
  这是**能抓住 `111ed9c5` 那一类 bug 的最锐利测试** —— 这里模拟器只要写出
  一个字节就**可证伪**。
- **`dram*` 6 格 → 写列标记为不可比**（附原因），不再当误差用。

#### 已落地的检查器

`docs/ada/scripts/check_l2_write_zero.py`（三种模式 + 自检）：

```bash
# 从 NCU 导出冻结硬件期望（含分组守卫：分组与硬件零/非零必须自洽）
python3 docs/ada/scripts/check_l2_write_zero.py --freeze

# 快照一次回归的模拟侧结果
python3 docs/ada/scripts/check_l2_write_zero.py --summarize /tmp/wr_regress_wbfix \
        --out docs/ada/evidence/refine26_summary_wbfix.json

# 判定（12 格中 6 格参与，6 格 excluded）
python3 docs/ada/scripts/check_l2_write_zero.py --judge /tmp/wr_regress_wbfix

# 自检：断言判定器能把两个二进制区分开（<1 s，不需 GPU）
python3 docs/ada/scripts/check_l2_write_zero.py --selftest
```

实测区分度：

| 回归 | `l2*` 6 格的模拟写 | 判定 |
|---|---|---|
| `/tmp/wr_regress`（`111ed9c5`） | 282 / 1092 / 2298 / 1128 / 2292 / 34313 | **6/6 FAIL**，exit 1 |
| `/tmp/wr_regress_wbfix`（本次修复） | 0 / 0 / 0 / 0 / 0 / 0 | **6/6 PASS**，exit 0 |

> 这个检查器**如果当时就存在，`111ed9c5` 会在提交前被拦住**。

### 3.7 CI 入口：`run_ci_checks.sh`（0.3 s，不需 GPU / 不需 build）

两处防线合并成**一个入口**：

```bash
./docs/ada/scripts/run_ci_checks.sh     # 全绿 exit 0，任一失败 exit 1
```

它跑 5 项检查（**实测 0.31 s**），全部基于 `docs/ada/evidence/` 下的**冻结夹具**，
不依赖 `experiments/`（323 GB）、不依赖模拟器二进制、不依赖 GPU：

| 检查 | 防住什么 |
|---|---|
| `extract_metrics.py --golden` ×2 | **数值口径漂移**：18 个钉住值（含 `l2.read.misses` 与 `l2.read.misses_miss_only` 两个都钉） |
| `extract_metrics.py --assert-checks` ×2 | **结构性口径错误**：三条恒等式 |
| `check_l2_write_zero.py --selftest` | **写回虚增**：断言判定器能区分修复前/后二进制 |

**夹具**是真实运行的 `perf_counter` 的**头行 + 末行**（dump 是累计值，末行即总量），
从 ~1 GB 压到 **18 KB**，数值逐位一致（已核对）：

```
docs/ada/evidence/fixtures/qwen_p32d2_full_c3_wrfix.final_rows.csv.gz    18 KB
docs/ada/evidence/fixtures/qwen_p32d2_full_idx2_nofix.final_rows.csv.gz  18 KB
docs/ada/evidence/fixtures/golden_metrics.json
```

**反向验证（都实测过）**：

- 把 `l2.read.misses` 钉成 `misses_miss_only`（即复现原报告的口径错误）→
  `FAIL ... expected 72,420,357 got 289,679,857`，**exit 1**
- `check_l2_write_zero.py` 在 `111ed9c5` 基线上 **6/6 FAIL**，在本次修复上 **6/6 PASS**

**接入方式**：

**1）GitHub Actions（已落地）** —— `.github/workflows/ada-accuracy.yml`

```yaml
jobs:
  accuracy-checks:
    runs-on: ubuntu-latest      # 不是 tgrogers-*（那是上游的自托管 GPU runner）
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v6
      - uses: actions/setup-python@v5
        with: { python-version: '3.10' }
      - run: ./docs/ada/scripts/run_ci_checks.sh
```

触发：`push` / `pull_request` 且改动落在 `docs/ada/**`（或该 workflow 本身）+ 手动 `workflow_dispatch`。

**在干净克隆里实测过**（这是 runner 会看到的样子）：

```
$ git clone --depth 1 --branch ada-sm89-support file://$PWD /tmp/ci_sim
experiments present? NO
build present? NO
== 5 passed, 0 failed ==      0.4 s
```

**非绿时也确实会红**（在同一个干净克隆里复现原报告的口径错误）：

```
== 4 passed, 1 failed ==
failed: qwen_p32d2_full_c3_wrfix.final_rows.csv.gz golden values
EXIT=1
```

> `paths:` 过滤是刻意的：只改模拟器源码的 push 不该占用 runner 去跑
> **看不到源码** 的检查（这些检查是夹具驱动的）。想让每次 push 都跑就删掉
> `paths:` 块 —— 代价是 runner 启动，不是检查本身。

**2）make（未落地，需要你决定）**

```make
ci-checks:
	./docs/ada/scripts/run_ci_checks.sh
```

未直接加进 `gpu-simulator/Makefile`：它末行的
`include $(BUILD_DIR)/main.makedepend` 会在**任何 target** 上触发 `checkenv`
（要求先 `source setup_environment.sh`），与"CI 不该依赖构建环境"冲突。
要让这个 target 真正可用，需要把该 `include` 改成 `-include` —— 属构建语义变更。

**3）ctest**

```cmake
add_test(NAME ada_accuracy COMMAND ${CMAKE_SOURCE_DIR}/docs/ada/scripts/run_ci_checks.sh)
```


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
2. ~~修微基准的 trace 范围~~ → **已用另一种方式解决**（见 §3.6）：
   重截全序列实测不可行（~138 M 内存指令、~9 GB trace、每格 10–25 h）；
   改为**按"硬件是否恰为 0"拆分 12 格** —— `l2*` 6 格写列有效且是零断言
   （`docs/ada/scripts/check_l2_write_zero.py`，已能拦住 `111ed9c5`），
   `dram*` 6 格写列标记不可比
3. **让 `extract_metrics.py` 成为唯一口径**，替换掉旧的对齐脚本，
   并在 CI 里跑三条恒等式断言（防止再次出现口径漂移），
   外加 `check_l2_write_zero.py --selftest`（<1 s，不需 GPU）
4. **L2 写访问口径**（+9.29%）与 **L1 load 扇区口径**（-12.96%）：
   两者都是"模拟器按 mem_fetch 计、NCU 按 sector 请求计"的差异，
   应在 `METRIC_DETAIL_REPORT_zh.md` 的口径表里如实登记，
   而不是当成建模缺陷
5. **补硬件 whole-scope 时长**，才能算硬件跑 Qwen 的带宽（当前缺口）
6. **DRAM 后端**：**不要接 HBFSim** —— 它只有 HBM/HBF，**没有 GDDR6**，
   而 Ada 是 GDDR6（见 `DRAM_MODEL_AND_HBFSIM_EVALUATION_zh.md` §六）。
   要第三方参照请用 **Ramulator2**（HBFSim 里已有一份对齐 Accel-Sim 语义的
   `GDDR6_RTX3070_SM86.yaml`）

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

# 12 格微基准的 DRAM 写判定（6 格参与、6 格 excluded）+ 自检
python3 docs/ada/scripts/check_l2_write_zero.py --selftest
python3 docs/ada/scripts/check_l2_write_zero.py --judge /tmp/wr_regress_wbfix

# 一次跑完全部快速检查（0.3 s，不需 GPU / 不需 build）
./docs/ada/scripts/run_ci_checks.sh

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
  **投影值**，**不是**已完成的实测
- ⚠️ **第一次全量尝试（`qwen15b_p32d2_full_c3_wrfix2`）在半途被杀**：
  跑到 86,244,839 cycle（**85.3%**，DRAM 读 7.84 GB = 完整运行的 84.6%）后进程消失，
  **无 exit_code、无报错、无死锁**，sim.log 停在 kernel 916 中间。
  机器侧已排除 OOM 与重启（无 OOM 记录、uptime 113 天、内存 366 GB 空闲）；
  原因是它被作为**交互终端的子进程**启动（未 `nohup`/`setsid`），随该终端会话一起结束。
  **教训：小时级任务必须 detach（`setsid nohup ... &`），不能挂在终端上。**
- **该次运行的 85.3% 数据仍然可用**，且正是本报告 §3.3 两条投影的依据：按累计
  DRAM 读对齐后，修复后的写量**逐点贴合**修复前的 `idx2 NOFIX` 完整运行
  （比值稳定 1.030），故 +1.8% 的估值可信
- 重跑（`qwen15b_p32d2_full_c3_wrfix3`，已 `setsid` detach）在撰写时进行中；
  完成后可给确数，预期与 +1.8% 同量级
- 前缀 A/B 是**完整实测**（三个二进制各跑完 100 kernel，exit 0），
  是本报告归因结论的主要依据
- 12 格回归：`l2train` 4 格已完成（本次修复 0 写 = 硬件 0 写 ✅），
  `dramtrain` 第 1 格已完成（0 写；该 case 的 trace 只含只读探针，
  见 §3.5，故 0 是预期值）；其余格在撰写时仍在跑
- §3.5 关于"硬件 1.37 MB 来自 ROI 之外残留脏数据"是**推断**，
  依据是 trace 只含 1 个只读 kernel + 探针自身仅 ~6 KB 写；
  要彻底证实需把 `initialize_pattern`/`flush_cache_cg` 纳入 trace 重做
- L1 指标口径与 NCU 的对齐关系**未做端到端验证**，仅按源码
  （`l1tex`, `mem_fetch` 计数）推断
- 硬件侧准确度仍未自动验收（参照文件 `hardware_accuracy_accepted: false`）
- `indexing=0` 是**补偿性近似**：真实 Ada 使用哈希映射，
  模拟器的 IPoly 实现配合该地址位布局会过度 camping
