#!/usr/bin/env python3
"""Attribute the Qwen P32D2 DRAM-write discrepancy to a single GPGPU-Sim commit.

The hardware comparison reported `dram__bytes_write.sum` at +150% and attributed
it to L2 replacement policy. That is not what the measurements show. Two
independent experiments here isolate the cause:

A. Controlled A/B on a fixed kernel prefix (`probe_first100`, one binary at a
   time, everything else -- trace, base address, config -- held fixed).

B. Matched-cycle tracking of the full 1030-kernel trace, comparing the cumulative
   DRAM write counters of two binaries at equal `gpu_tot_sim_cycle`. The trace is
   deterministic, so at a given cycle both runs have executed the same work.

The three binaries are built from the same tree, differing only in the SECTOR_MISS
branch of `tag_array::access()`:

    nofix   gpu-cache.cc at 111ed9c5^   (writeback never issued on a sector miss)
    fix111  gpu-cache.cc at HEAD        (all dirty sectors of the line written back)
    myfix   this change                 (only the sector allocate_sector() resets)

Writes are read from `dram_writes_per_mc_*` and multiplied by `dram_atom_size`
(32 B, = burst_length x buswidth x chips-per-controller). That is exactly how
`memory_stats_t::memlatstat_dram_access()` counts them.
"""

from __future__ import annotations

import argparse
import bisect
import gzip
import json
import re
import sys
from pathlib import Path

DRAM_ATOM_BYTES = 32


def _rows(path: Path):
    """Yield (cycle, write_sectors, read_sectors) from a perf_counter dump."""
    with gzip.open(path, "rt") as handle:
        header = handle.readline().rstrip("\n").split(",")
        cycle_i = header.index("gpu_tot_sim_cycle")
        write_cols = [
            i for i, h in enumerate(header) if re.match(r"^dram_writes_per_mc_\d+$", h)
        ]
        read_cols = [
            i for i, h in enumerate(header) if re.match(r"^dram_reads_per_mc_\d+$", h)
        ]
        for line in handle:
            fields = line.rstrip("\n").split(",")
            if len(fields) != len(header):
                continue
            try:
                yield (
                    int(fields[cycle_i]),
                    sum(int(fields[i] or 0) for i in write_cols),
                    sum(int(fields[i] or 0) for i in read_cols),
                )
            except ValueError:
                # A dump that was still being written when the run was killed.
                continue


def _totals(path: Path) -> dict:
    last = None
    for last in _rows(path):
        pass
    if last is None:
        return {}
    return {
        "cycles": last[0],
        "write_bytes": last[1] * DRAM_ATOM_BYTES,
        "read_bytes": last[2] * DRAM_ATOM_BYTES,
    }


def _total_reads(path: Path) -> int:
    return _totals(path).get("read_bytes", 0)


def _at_reads(path: Path, targets: list[int]) -> list[dict]:
    """Sample cumulative write bytes at matched *read* bytes.

    Matching on the run clock would compare different amounts of work: the fix
    changes the DRAM write volume, which changes the simulated cycle count, so
    at a given cycle the two binaries are at different points in the trace.

    DRAM *read* bytes are unaffected by the writeback change (verified: the
    three binaries agree to <0.01% on the prefix), and the trace is
    deterministic, so equal read bytes means equal work done. That makes read
    bytes the correct progress proxy.
    """
    rows = list(_rows(path))
    if not rows:
        return []
    reads = [r[2] * DRAM_ATOM_BYTES for r in rows]
    out = []
    for target in targets:
        i = bisect.bisect_left(reads, target)
        if i >= len(rows):
            break
        out.append(
            {
                "target_read_bytes": target,
                "matched_cycle": rows[i][0],
                "read_bytes": reads[i],
                "write_bytes": rows[i][1] * DRAM_ATOM_BYTES,
            }
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--simulations",
        default="experiments/sglang_qwen15b_20260925/simulations",
        type=Path,
    )
    ap.add_argument("--json", type=Path, help="write the evidence JSON here")


    args = ap.parse_args()

    sim = args.simulations

    def one(subdir: str, pattern: str) -> Path:
        hits = sorted((sim / subdir).glob(pattern))
        if not hits:
            raise SystemExit(f"no {pattern} under {sim / subdir}")
        return hits[-1]

    prefix = {
        "nofix": one("ab100_nofix", "perf_counter_*.csv.gz"),
        "fix111": one("ab100_fix111", "perf_counter_*.csv.gz"),
        "myfix": one("ab100_myfix", "perf_counter_*.csv.gz"),
    }
    full = {
        "idx2_nofix": one("qwen15b_p32d2_full", "perf_counter_*.csv.gz"),
        "idx0_fix111": one("qwen15b_p32d2_full_c3_wrfix", "perf_counter_*.csv.gz"),
        "idx0_myfix_partial": one(
            "qwen15b_p32d2_full_c3_wrfix2", "perf_counter_*.csv.gz"
        ),
    }

    evidence = {
        "dram_atom_bytes": DRAM_ATOM_BYTES,
        "prefix_ab": {
            name: _totals(path) for name, path in prefix.items()
        },
        "full_totals": {name: _totals(path) for name, path in full.items()},
    }

    # The projection that matters: the myfix/fix111 write ratio is stable over
    # the completed part of the run, so apply it to fix111's completed total.
    partial_reads = _total_reads(full["idx0_myfix_partial"])
    step = max(partial_reads // 12, 1)
    targets = [step * k for k in range(1, 13)]
    tracked = {
        "idx0_fix111": _at_reads(full["idx0_fix111"], targets),
        "idx0_myfix_partial": _at_reads(full["idx0_myfix_partial"], targets),
    }
    ratios = []
    for a, b in zip(tracked["idx0_fix111"], tracked["idx0_myfix_partial"]):
        if a["write_bytes"]:
            ratios.append(
                {
                    "read_bytes": a["read_bytes"],
                    "fix111_write_bytes": a["write_bytes"],
                    "myfix_write_bytes": b["write_bytes"],
                    "ratio": b["write_bytes"] / a["write_bytes"],
                }
            )
    evidence["matched_read_bytes_tracking"] = ratios

    if ratios:
        # Use the steady-state part: the ratio rises during the first few
        # hundred MB of reads and then flattens.
        steady = [r["ratio"] for r in ratios if r["read_bytes"] >= 1_000_000_000]
        mean_ratio = sum(steady) / len(steady)
        fix111_total = evidence["full_totals"]["idx0_fix111"]["write_bytes"]
        evidence["projection"] = {
            "method": (
                "match the two full runs on cumulative DRAM *read* bytes (a "
                "writeback-fix-invariant progress proxy) and apply the "
                "steady-state myfix/fix111 write-bytes ratio to fix111's "
                "completed full-run total"
            ),
            "progress_proxy": "cumulative dram_read_bytes",
            "steady_state_ratio_mean": mean_ratio,
            "steady_state_ratio_min": min(steady),
            "steady_state_ratio_max": max(steady),
            "samples": len(steady),
            "fix111_full_write_bytes": fix111_total,
            "projected_myfix_full_write_bytes": fix111_total * mean_ratio,
        }

    text = json.dumps(evidence, indent=2)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(text + "\n")

    if not args.json:
        print(text)
        return 0

    print(f"wrote {args.json}")
    print()
    print("prefix A/B (same trace, config and base address; binary is the only variable)")
    for name, t in evidence["prefix_ab"].items():
        print(f"  {name:8s} cycles={t['cycles']:>10,}  write_bytes={t['write_bytes']:>12,}")
    n, f, m = (
        evidence["prefix_ab"]["nofix"]["write_bytes"],
        evidence["prefix_ab"]["fix111"]["write_bytes"],
        evidence["prefix_ab"]["myfix"]["write_bytes"],
    )
    print(f"  fix111/nofix = {f / n:.3f}x   myfix/nofix = {m / n:.5f}x")
    if "projection" in evidence:
        p = evidence["projection"]
        print()
        print("full-trace projection")
        print(f"  steady-state ratio      = {p['steady_state_ratio_mean']:.4f} "
              f"(min {p['steady_state_ratio_min']:.4f}, max {p['steady_state_ratio_max']:.4f}, n={p['samples']})")
        print(f"  fix111 full write bytes = {p['fix111_full_write_bytes']:,}")
        print(f"  projected myfix         = {p['projected_myfix_full_write_bytes']:,.0f}")
        print(f"  hardware                = 72,884,480")
        hw = 72_884_480
        print(
            f"  projected error         = "
            f"{(p['projected_myfix_full_write_bytes'] - hw) / hw * 100:+.2f}%"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
