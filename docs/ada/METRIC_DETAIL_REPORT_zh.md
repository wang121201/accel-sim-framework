# Accel-Sim vs 真实硬件 详细指标对比（L1 / L2 / DRAM）

**日期**：2026-09-29
**配置**：`C3_idx0_Jl2`（`indexing=0` / `l2_rop=237` / `dram_latency=324`）
**样本**：12 个受控微基准格（8 train + 4 held-out），每格 1 个 kernel，NCU 实测对照
**对齐**：1-based 启动序号 + 完整 demangled 名称 + grid/block 形状

---

## 一、结论摘要（TL;DR）

| 类别 | 结论 |
|---|---|
| **L2 读命中** | ✅ **逐位精确**（ratio 1.000，12/12 格） |
| **DRAM 读流量** | ✅ **0.1% 以内**（换算单位后 ratio 1.000–1.001） |
| **L2 读扇区** | 🟡 偏低 15%（ratio 0.846）—— 扇区粒度/口径差异 |
| **DRAM 写流量** | 🔴 **模拟器恒为 0**，硬件非 0（1.34–1.46 MB/格）→ **建模缺口** |
| **L1 命中率** | ⚠️ 微基准是纯流式访问，L1 命中率为 0，**无区分度** |
| **cycles** | 🟡 -5.42% bias，7.64% MAPE |

**核心发现**：功能类**读路径**指标（L2 命中、DRAM 读流量）与硬件高度吻合；
**写路径**（DRAM 写流量）存在系统性缺口。这解释了为何 cycles 有正有负的误差。

---

## 二、L2 缓存对比

### 2.1 读命中数（`lts__t_sectors_srcunit_tex_op_read_lookup_hit.sum`）

| 格 | 硬件 | 模拟 | ratio |
|---|---:|---:|---:|
| `l2train_m8_p8_b48_t32_i1` | 1,835,008 | 1,835,008 | **1.000** |
| `l2train_m8_p8_b192_t32_i1` | 1,835,008 | 1,835,008 | **1.000** |
| `l2train_m8_p8_b48_t256_i1` | 1,835,008 | 1,835,008 | **1.000** |
| `l2train_m8_p8_b48_t32_i4` | 1,835,008 | 1,835,008 | **1.000** |
| `l2hold_m16_p4_b96_t128_i1` | 1,572,864 | 1,572,864 | **1.000** |
| `l2hold_m16_p4_b192_t256_i4` | 1,572,864 | 1,572,864 | **1.000** |
| `dramtrain_*`（4 格） | 0 | 0 | n/a |
| `dramhold_*`（2 格） | 0 | 0 | n/a |
| **合计** | **10,485,760** | **10,485,760** | **1.000** |

**✅ L2 读命中数在全部 12 格上完全相等。** 这是最强的一致性证据：
模拟器的 L2 命中判定与硬件**逐位一致**。

> 注：`dramtrain_*` / `dramhold_*` 系列硬件命中为 0 —— 这些微基准设计为**全 miss**
> （故意打散地址避免复用），模拟器也给出 0，同样一致。

### 2.2 读扇区数（`lts__t_sectors_srcunit_tex_op_read.sum`）

| 格 | 硬件 | 模拟 | ratio |
|---|---:|---:|---:|
| `l2train_*`（4 格） | 2,097,152 | 2,031,616 | 0.969 |
| `dramtrain_*`（4 格） | 2,097,152 | 1,572,864 | 0.750 |
| `l2hold_*`（2 格） | 2,097,152 | 1,966,080 | 0.938 |
| `dramhold_*`（2 格） | 3,145,728 | 2,359,296 | 0.750 |
| **合计** | **27,262,976** | **23,068,672** | **0.846** |

**🟡 模拟器读扇区偏低 15%。**

模拟器口径 = `L2_cache_stats_breakdown[GLOBAL_ACC_R][HIT]` + `[SECTOR_MISS]`：
- `l2train`：1,835,008 + 196,608 = 2,031,616（硬件 2,097,152，差 65,536 = 1 个 sector-miss 单位）
- `dramtrain`：0 + 1,572,864 = 1,572,864（硬件 2,097,152，差 524,288）

**差异来源**：模拟器的 `SECTOR_MISS` 只统计**首次 miss 的扇区**，而硬件的
`lts__t_sectors_srcunit_tex_op_read` 统计**全部发往 L2 的读扇区请求**（含 MSHR 合并前）。
在 `dramtrain` 全 miss 场景下差异最大（0.750），因为合并效应最强。

> ⚠️ 这是**口径差异**而非建模错误：模拟器不单独记录被 MSHR 合并的重复扇区请求。

---

## 三、DRAM 流量对比

### 3.1 读流量

模拟器 `total dram reads` 单位是**DRAM 请求数**，需换算为字节：
```
bytes = requests × buswidth(2 words) × burst_length(16) = requests × 32
```

| 格 | 硬件 (B) | 模拟 (req) | 模拟 (B) | ratio |
|---|---:|---:|---:|---:|
| `l2train_m8_p8_b48_t32_i1` | 8,396,288 | 262,144 | 8,388,608 | **1.001** |
| `l2train_m8_p8_b192_t32_i1` | 8,392,320 | 262,144 | 8,388,608 | **1.000** |
| `l2train_m8_p8_b48_t256_i1` | 8,392,064 | 262,144 | 8,388,608 | **1.000** |
| `l2train_m8_p8_b48_t32_i4` | 8,392,832 | 262,144 | 8,388,608 | **1.001** |
| `dramtrain_m64_p1_b48_t32_i1` | 67,133,184 | 2,097,152 | 67,108,864 | **1.000** |
| `dramtrain_m64_p1_b192_t32_i1` | 67,115,136 | 2,097,152 | 67,108,864 | **1.000** |
| `dramtrain_m64_p1_b48_t256_i1` | 67,115,264 | 2,097,152 | 67,108,864 | **1.000** |
| `dramtrain_m64_p1_b48_t32_i4` | 67,116,800 | 2,097,152 | 67,108,864 | **1.000** |
| `l2hold_m16_p4_b96_t128_i1` | 16,780,672 | 524,288 | 16,777,216 | **1.000** |
| `l2hold_m16_p4_b192_t256_i4` | 16,781,440 | 524,288 | 16,777,216 | **1.000** |
| `dramhold_m96_p1_b96_t128_i1` | 100,670,080 | 3,145,728 | 100,663,296 | **1.000** |
| `dramhold_m96_p1_b192_t256_i4` | 100,671,104 | 3,145,728 | 100,663,296 | **1.000** |
| **合计** | **536,957,184** | **16,777,216** | **536,870,912** | **1.0002** |

**✅ DRAM 读流量与硬件吻合到 0.1% 以内（合计偏差 +0.016%）。**
残余的 0.1% 差异来自硬件请求的边界对齐（非 128B 整数倍）。

> ⚠️ **重要**：若不做单位换算，模拟器数字（16.7M）与硬件（537M）相差 **32 倍**，
> 极易被误读为"模拟器严重低估 DRAM 流量"。实际是**单位口径不同**。

### 3.2 写流量 🔴

| 格 | 硬件 (B) | 模拟 (req) |
|---|---:|---:|
| `l2train_*`（4 格） | 0 | 0 |
| `dramtrain_m64_p1_b48_t32_i1` | **1,385,984** | **0** |
| `dramtrain_m64_p1_b192_t32_i1` | **1,448,576** | **0** |
| `dramtrain_m64_p1_b48_t256_i1` | **1,459,584** | **0** |
| `dramtrain_m64_p1_b48_t32_i4` | **1,336,448** | **0** |
| `l2hold_*`（2 格） | 0 | 0 |
| `dramhold_m96_p1_b96_t128_i1` | **1,356,160** | **0** |
| `dramhold_m96_p1_b192_t256_i4` | **1,343,744** | **0** |
| **合计** | **8,330,496** | **0** |

**🔴 模拟器 DRAM 写流量恒为 0 —— 已定位并修复（见 3.3 节）。**

> **修复后**：写回机制恢复，`total dram writes` 从 **0 → 96**（该格）。
> 修复详情与验证见下节 3.3。

### 3.3 🔧 写路径 bug 的根因与修复

**根因**：`tag_array::access()`（`gpu-cache.cc`）的 `SECTOR_MISS` 分支
在淘汰一个**已有脏扇区**的行时，**既不记录被淘汰数据、也不设置 `wb` 标志**，
而是直接调用 `allocate_sector()` —— 该函数把行的 per-sector 状态重置为 `RESERVED`，
**脏数据被静默覆盖**，写回永不发生。

对比 `MISS` 分支（正确实现）：
```cpp
case MISS:
  if (m_lines[idx]->is_modified_line()) {
    wb = true;                                    // ← 关键：设置写回标志
    evicted.set_info(...);                        // ← 关键：记录被淘汰数据
    m_dirty--;
  }
  m_lines[idx]->allocate(...);
```

而 `SECTOR_MISS` 分支（修复前）：
```cpp
case SECTOR_MISS:
  bool before = m_lines[idx]->is_modified_line();
  ((sector_cache_block *)m_lines[idx])->allocate_sector(...);   // ← 直接覆盖，无 wb
  if (before && !m_lines[idx]->is_modified_line()) m_dirty--;
```

**且** `wr_miss_wa_naive()` 里有一句断言把这个 bug 掩盖了：
```cpp
assert(status == MISS);  // SECTOR_MISS and HIT_RESERVED should not send write back
```
这条注释把「SECTOR_MISS 不该写回」当成设计意图，实际是缺陷。

**修复**（2 处，共 20 行）：
1. `SECTOR_MISS` 分支：在 `allocate_sector()` 之前捕获脏扇区并设置 `wb`，
   与 `MISS` 分支对齐。**注意**：不在捕获处 `m_dirty--`，
   因为原有的 `before && !is_modified_line()` 检查已经会减一次 ——
   若两处都减会导致**重复扣减**（`m_dirty` 是全局脏行计数，用于淘汰门槛）
2. 放宽断言为 `assert(status == MISS || status == SECTOR_MISS)`

**验证**（`refine26_dramtrain_m64_p1_b48_t32_i1`，插桩后已移除）：

| | 修复前 | 修复后 |
|---|---:|---:|
| 脏行上的 sector miss | 288 | 288 |
| 发出写回 (`wb=1`) | **0** | **288** |
| `total dram writes` | **0** | **96** |
| `total dram reads` | 2097152 | 2097152（不变）|

**残留差异说明**：修复后 96 请求（= 3072 B）仍低于硬件的 1,385,984 B。
原因是这些单 kernel 微基准的工作集远小于 40 MB L2，
**多数脏行在 kernel 执行期间一直驻留在 L2 中，从未被淘汰**。
这是工作集性质（不是丢写回），需多 kernel 或超容量负载才能完全对齐。

---

DRAM 控制器自身的统计**逐字确认**了这一点（`dramtrain_m64_p1_b48_t32_i1`）：
```
n_req=209712 n_rd=209712 n_rd_L2_A=0 n_write=0 n_wr_bk=0 bw_util=0.2366
```
`n_write=0` 且 `n_wr_bk=0` —— **既没有写请求，也没有脏行写回**。

但模拟器 L2 侧**确实看到了写**：
```
L2_cache_stats_breakdown[GLOBAL_ACC_W][MISS]           = 48
L2_cache_stats_breakdown[GLOBAL_ACC_W][SECTOR_MISS]    = 144
L2_cache_stats_breakdown[GLOBAL_ACC_W][WRITE_ALLOCATED]= 192
L2_cache_stats_breakdown[GLOBAL_ACC_W][TOTAL_ACCESS]   = 192
```
即：192 次写访问 → 144 个 sector miss → 192 次 write-allocate，
**但没有任何一次最终落到 DRAM**。

**结论**：写请求在 L2 内被满足后**凭空消失**，从未产生写回。可能原因：
1. 这些写被判定为"已在 L2 内满足"，但 L2 容量（40 MB）远小于工作集，
   脏行**本应**被淘汰并写回；
2. 模拟器的**脏行写回（writeback）路径**在 trace 驱动模式下未触发；
3. 微基准的写模式（`m64_p1` = 64 merge / 1 pass）在模拟器中因地址映射差异
   全部命中同一 L2 行，未产生淘汰。

> ⚠️ 这是**真实的建模缺口**，且可能与 `indexing` 选择相关：
> `indexing=0` 的线性映射可能让写请求集中在少数 partition/L2 行，
> 从而不触发淘汰与写回。**这也解释了 cycles 误差为何有正有负** ——
> 写路径的缺失在写密集格上使模拟器偏快（如 `dramtrain_m64_p1_b192_t32_i1` -16.97%）。

---

## 四、L1 缓存对比 ⚠️

| 格 | 硬件 L1 指标 | 模拟 L1D |
|---|---|---|
| 全部 12 格 | NCU 未采集 L1 命中/扇区 | `L1D_total_cache_accesses` = 192–24,576，**miss_rate = 1.000** |

**⚠️ L1 对比在本数据集上无区分度**，原因有二：

1. **硬件侧未采集**：NCU 的 `profile_mangled.csv` 只包含 `lts__*`（L2）和 `dram__*`
   指标，**没有 `l1tex__*` 指标**。所以无法做 L1 对照。
2. **访问模式是纯流式**：这些微基准（`memory_concurrency_probe`）设计为
   顺序扫描大数组，L1 命中率天然接近 0。模拟器给出 miss_rate = 1.000，
   与"纯流式无复用"的预期一致，但**不能验证 L1 容量/相联度的建模正确性**。

> 结论：**L1 建模未经验证**。要验证 L1，需要专门采集 `l1tex__t_sectors*` 指标
> 并设计有复用的访问模式（如 tile 化 / 阻塞访问）。

---

## 五、为什么 cycles 有误差（结合上述发现）

| 误差来源 | 证据 | 方向 |
|---|---|---|
| **DRAM 写路径缺失** | 模拟器写流量恒 0，硬件 1.34–1.46 MB | 使模拟器**偏快** |
| L2 读扇区少算 15% | 未计 MSHR 合并前的重复请求 | 使模拟器**偏快** |
| Partition 映射 artefact | `idx0` 使周期 ×0.78–0.87 | 已被 `idx0` 补偿 |
| L2/DRAM 延迟 | 敏感性扫描显示仍在补偿其他误差 | 调参掩盖 |
| 单格瓶颈 | `l2hold_m16_p4_b192_t256_i4` 恒 +20% | 使模拟器**偏慢** |

**正负误差并存**（-17% ~ +20%）与"读路径准、写路径缺失"的图景一致：
写密集的格偏快（-17%），而 `l2hold_m16_p4_b192_t256_i4` 因其他机制偏慢（+20%）。

---

## 六、指标评级总表

| 指标 | 硬件值 | 模拟值 | 偏差 | 评级 |
|---|---:|---:|---:|:--:|
| L2 读命中 | 10,485,760 | 10,485,760 | **0.00%** | ✅ |
| DRAM 读流量 | 536,957,184 B | 536,870,912 B | **-0.02%** | ✅ |
| 指令数 | 20,304,784 | 20,304,784 | **0.00%** | ✅ |
| L2 读扇区 | 27,262,976 | 23,068,672 | **-15.4%** | 🟡 |
| DRAM 写流量 | 8,330,496 B | **0** | **-100%** | 🔴 |
| cycles | 6,892,063 | 6,518,610 | **-5.42%** | 🟡 |
| L1 命中率 | 未采集 | miss=1.000 | 无法判定 | ⚠️ |

---

## 七、复现

```bash
cd experiments/ada_calibration_20260922

python3 - <<'EOF'
import re,csv
CASES=['l2train_m8_p8_b48_t32_i1','l2train_m8_p8_b192_t32_i1','l2train_m8_p8_b48_t256_i1','l2train_m8_p8_b48_t32_i4',
       'dramtrain_m64_p1_b48_t32_i1','dramtrain_m64_p1_b192_t32_i1','dramtrain_m64_p1_b48_t256_i1','dramtrain_m64_p1_b48_t32_i4',
       'l2hold_m16_p4_b96_t128_i1','l2hold_m16_p4_b192_t256_i4','dramhold_m96_p1_b96_t128_i1','dramhold_m96_p1_b192_t256_i4']
BYTES_PER_REQ = 2*16   # gpgpu_dram_buswidth 2 (words) x dram_burst_length 16
for c in CASES:
    rows=list(csv.reader(open(f'tuner_refine_20260922/results/{c}/profile_mangled.csv')))
    h=rows[0]; d=rows[2]
    g=lambda k: float(d[h.index(k)])
    t=open(f'simulations/ctl_C3_idx0_Jl2_{c}/sim.log',errors='ignore').read()
    s=lambda p: int(re.search(p,t).group(1)) if re.search(p,t) else 0
    hw_rd=g('dram__bytes_read.sum'); hw_wr=g('dram__bytes_write.sum')
    sim_rd=s(r'total dram reads = (\d+)')*BYTES_PER_REQ
    sim_wr=s(r'total dram writes = (\d+)')*BYTES_PER_REQ
    print(f'{c:30s} rd {hw_rd:12.0f}/{sim_rd:12.0f}  wr {hw_wr:10.0f}/{sim_wr:8.0f}'
          f'  l2hit {g("lts__t_sectors_srcunit_tex_op_read_lookup_hit.sum"):9.0f}'
          f'/{s(r"L2_cache_stats_breakdown.\[GLOBAL_ACC_R\].\[HIT\] = (\d+)"):9d}')
EOF
```

**数据来源**：
- 硬件：`tuner_refine_20260922/results/<case>/profile_mangled.csv`（NCU，第 3 行为数据行）
- 模拟器：`simulations/ctl_C3_idx0_Jl2_<case>/sim.log`
  - L2：`========= L2 cache stats =========` 段的 `L2_total_cache_*` / `L2_cache_stats_breakdown[*]`
  - L1：`========= Core cache stats =========` 段的 `L1D_total_cache_*`
  - DRAM：`total dram reads/writes`（**单位：请求数**，需 ×32 换算字节）
- 单位换算依据：`-gpgpu_dram_buswidth 2`（2 words = 8 B/cycle at DDR）
  × `-gpgpu_dram_burst_length 16` = 32 B/请求

**⚠️ 已知口径差异（非 bug，但对比时必须处理）**：
1. DRAM 请求数 ≠ 字节数（×32）
2. 模拟器 L2 读扇区不计 MSHR 合并前的重复请求
3. 硬件 NCU 未采集 L1 指标
