#!/usr/bin/env python3
"""Extract L1/L2/DRAM metrics from an Accel-Sim perf_counter dump.

The perf_counter CSV is a per-interval dump of *cumulative* counters, so the
final row holds the totals for the whole run. Its header carries one name per
data field, in the same order, and every name is qualified by the location it
belongs to:

    L1D_<sm>_<TYPE>_<STATUS>          per-SM L1 data cache
    L2_bank_<bank>_<TYPE>_<STATUS>    per-bank L2 slice
    dram_{reads,writes}_per_mc_<mc>   per-memory-controller DRAM transactions

Aggregation is therefore "sum over the location dimension", and that is all
this script does. It deliberately does not guess at NVIDIA-equivalent names.

Why this script exists
----------------------
The 2026-09-30 hardware comparison reported

    L2 read sectors  = 271,285,591
    L2 read misses   =  72,420,357

Neither is the simulator's read lookups or read misses:

    reads  = HIT + HIT_RESERVED + MISS + SECTOR_MISS
    misses = MISS + SECTOR_MISS          <- SECTOR_MISS was dropped

`data_cache::access()` records a SECTOR_MISS whenever the tag matches but the
requested sector is absent, and `cache_stats::get_sub_stats()` adds SECTOR_MISS
to `misses`. Dropping it understates L2 read misses by 4x and turns an excellent
result into an apparent 75% error.

Two identities pin the correct definitions down, and this script asserts them:

    32 * (L2 read misses)  == DRAM read bytes
    L2 read accesses       == gpgpu_n_mem_read_global
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from pathlib import Path

# A DRAM transaction moves `dram_atom_size` bytes; in this configuration it is
# burst_length(16) x buswidth(2) x chips-per-controller(1) = 32 B, i.e. one
# sector. `memory_stats_t::memlatstat_dram_access()` counts one transaction per
# ceil(data_size / dram_atom_size).
DRAM_ATOM_BYTES = 32

# cache_request_status buckets emitted by gpu-cache.cc.
STATUSES = (
    "HIT",
    "HIT_RESERVED",
    "MISS",
    "RESERVATION_FAIL",
    "SECTOR_MISS",
    "MSHR_HIT",
    "WRITE_ALLOCATED",
)

READ_TYPES = ("GLOBAL_ACC_R", "CHIPLET_ACC_R")
WRITE_TYPES = ("GLOBAL_ACC_W", "CHIPLET_ACC_W")

LEVEL_PREFIX = {"l1": "L1D", "l2": "L2_bank"}


def _load_last_row(path: str) -> tuple[list[str], list[str]]:
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt") as handle:
        header = handle.readline().rstrip("\n").split(",")
        last = None
        for line in handle:
            if line.strip():
                last = line.rstrip("\n").split(",")
    if last is None:
        raise SystemExit(f"no data rows in {path}")
    if len(header) != len(last):
        raise SystemExit(
            f"header/data width mismatch: {len(header)} names vs {len(last)} fields"
        )
    return header, last


def _int(value: str) -> int:
    # `wall_clock_ms` and a few utility columns are floats; everything this
    # script sums is an integer counter, but tolerate either form.
    return int(float(value)) if value else 0


def _sum(header: list[str], row: list[str], predicate) -> int:
    return sum(_int(value) for name, value in zip(header, row) if predicate(name))


def _status_predicate(prefix: str, status: str, access_types: tuple[str, ...]):
    suffix = "_" + status
    location = re.compile(rf"^{prefix}_\d+_")

    def predicate(name: str) -> bool:
        if not name.endswith(suffix):
            return False
        if not location.match(name):
            return False
        tail = name[: -len(suffix)]
        return any(tail.endswith("_" + t) for t in access_types)

    return predicate


def _summarise(values: dict) -> None:
    values["accesses"] = (
        values["HIT"] + values["HIT_RESERVED"] + values["MISS"] + values["SECTOR_MISS"]
    )
    # Mirrors cache_stats::get_sub_stats().
    values["misses"] = values["MISS"] + values["SECTOR_MISS"]
    # What the old comparison table used, kept for contrast.
    values["misses_miss_only"] = values["MISS"]
    values["pending_hits"] = values["HIT_RESERVED"]
    values["res_fails"] = values["RESERVATION_FAIL"]
    values["mshr_hits"] = values["MSHR_HIT"]
    values["write_allocated"] = values["WRITE_ALLOCATED"]
    acc = values["accesses"]
    values["hit_rate_percent"] = (
        100.0 * (values["HIT"] + values["HIT_RESERVED"]) / acc if acc else 0.0
    )
    values["miss_rate_percent"] = 100.0 * values["misses"] / acc if acc else 0.0


def _level(header: list[str], row: list[str], prefix: str) -> dict:
    level = {"read": {}, "write": {}}
    for direction, types in (("read", READ_TYPES), ("write", WRITE_TYPES)):
        for status in STATUSES:
            level[direction][status] = _sum(
                header, row, _status_predicate(prefix, status, types)
            )
        _summarise(level[direction])
    return level


def extract(path: str) -> dict:
    header, row = _load_last_row(path)
    index = {name: i for i, name in enumerate(header)}

    def counter(name: str):
        i = index.get(name)
        return None if i is None else _int(row[i])

    out: dict = {"source": path}
    out["cycles"] = {k: counter(k) for k in ("gpu_tot_sim_cycle", "gpu_sim_cycle")}
    out["instructions"] = {
        k: counter(k)
        for k in ("gpu_tot_sim_insn", "gpgpu_n_load_insn", "gpgpu_n_store_insn")
    }

    for level, prefix in LEVEL_PREFIX.items():
        out[level] = _level(header, row, prefix)

    dram = {
        "reads": _sum(header, row, lambda n: re.match(r"^dram_reads_per_mc_\d+$", n)),
        "writes": _sum(header, row, lambda n: re.match(r"^dram_writes_per_mc_\d+$", n)),
    }
    dram["read_bytes"] = dram["reads"] * DRAM_ATOM_BYTES
    dram["write_bytes"] = dram["writes"] * DRAM_ATOM_BYTES
    dram["total_bytes"] = dram["read_bytes"] + dram["write_bytes"]
    out["dram"] = dram

    # Consistency checks. These are what proved the L2 read figure in the old
    # comparison table was mis-derived.
    checks: dict = {}
    mem_read = counter("gpgpu_n_mem_read_global")
    mem_write = counter("gpgpu_n_mem_write_global")
    if mem_read is not None:
        checks["l2_read_accesses_equals_gpgpu_n_mem_read_global"] = {
            "gpgpu_n_mem_read_global": mem_read,
            "l2_read_accesses": out["l2"]["read"]["accesses"],
            "matches": mem_read == out["l2"]["read"]["accesses"],
        }
    if mem_write is not None:
        checks["l2_write_accesses_equals_gpgpu_n_mem_write_global"] = {
            "gpgpu_n_mem_write_global": mem_write,
            "l2_write_accesses": out["l2"]["write"]["accesses"],
            "matches": mem_write == out["l2"]["write"]["accesses"],
        }
    expected = DRAM_ATOM_BYTES * out["l2"]["read"]["misses"]
    checks["dram_read_bytes_equals_atom_times_l2_read_misses"] = {
        "expected_bytes": expected,
        "actual_bytes": dram["read_bytes"],
        "matches": expected == dram["read_bytes"],
    }
    out["checks"] = checks
    return out


def _print_level(label: str, level: dict) -> None:
    for direction in ("read", "write"):
        s = level[direction]
        print(f"{label} {direction}")
        for k in (
            "accesses",
            "HIT",
            "HIT_RESERVED",
            "MISS",
            "SECTOR_MISS",
            "misses",
            "misses_miss_only",
            "MSHR_HIT",
            "RESERVATION_FAIL",
            "WRITE_ALLOCATED",
        ):
            print(f"  {k:22s} {s[k]:>16,d}")
        print(f"  {'miss_rate_percent':22s} {s['miss_rate_percent']:>16.2f}")
        print()


def freeze_fixture(perf_counter: str, out: str) -> int:
    """Write a two-row fixture: the header plus the final (cumulative) row.

    The whole run is not needed to reproduce the counters -- the dump is
    cumulative, so the last row *is* the run total. Keeping just those two rows
    turns a 1 GB dump into a ~60 KB fixture that carries the same numbers, which
    is what lets the identity checks run in CI without the experiments tree.
    """
    opener = gzip.open if perf_counter.endswith(".gz") else open
    with opener(perf_counter, "rt") as handle:
        header = handle.readline()
        last = None
        for line in handle:
            if line.strip():
                last = line
    if last is None:
        raise SystemExit(f"no data rows in {perf_counter}")

    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt") as handle:
        handle.write(header)
        handle.write(last)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("perf_counter", help="perf_counter_*.csv[.gz]")
    ap.add_argument("--json", action="store_true", help="emit JSON only")
    ap.add_argument(
        "--assert-checks",
        action="store_true",
        help=(
            "exit non-zero if any identity check fails. Use this in CI: the "
            "checks are what caught the mis-derived L2 read figures, so a "
            "failure means the extraction口径 has drifted again."
        ),
    )
    ap.add_argument(
        "--freeze-fixture",
        metavar="OUT",
        help="write header+final row to OUT as a small CI fixture and exit",
    )
    ap.add_argument(
        "--golden",
        metavar="FILE",
        help="assert the extracted counters match the frozen golden values in FILE",
    )
    ap.add_argument(
        "--freeze-golden",
        metavar="FILE",
        help="add/refresh this perf_counter's entry in the golden file and exit",
    )
    args = ap.parse_args()

    if args.freeze_fixture:
        return freeze_fixture(args.perf_counter, args.freeze_fixture)
    if args.freeze_golden:
        return freeze_golden(args.perf_counter, args.freeze_golden)
    if args.golden:
        return check_golden(args.perf_counter, args.golden)

    data = extract(args.perf_counter)
    if args.json:
        print(json.dumps(data, indent=2))
        return 0 if not args.assert_checks else _verdict(data)

    d = data["dram"]
    print(f"source: {data['source']}")
    print(f"cycles: {data['cycles']['gpu_tot_sim_cycle']:,}")
    print()
    _print_level("L1", data["l1"])
    _print_level("L2", data["l2"])
    print(f"DRAM reads  {d['reads']:>16,d} sectors {d['read_bytes']:>18,d} bytes")
    print(f"DRAM writes {d['writes']:>16,d} sectors {d['write_bytes']:>18,d} bytes")
    print()
    for name, check in data["checks"].items():
        mark = "OK " if check["matches"] else "FAIL"
        print(f"[{mark}] {name}")
        for k, v in check.items():
            if k != "matches":
                print(f"       {k} = {v:,}")
    if args.assert_checks:
        return _verdict(data)
    return 0


def _verdict(data: dict) -> int:
    failed = [n for n, c in data["checks"].items() if not c["matches"]]
    if failed:
        print(f"\nFAIL: {len(failed)} identity check(s) failed", file=sys.stderr)
        for n in failed:
            print(f"  {n}", file=sys.stderr)
        return 1
    print(f"\nOK: all {len(data['checks'])} identity checks pass")
    return 0


# The counters a golden fixture pins. Deliberately includes both the correct
# L2 read definition (`misses`) and the one the old comparison used
# (`misses_miss_only`), so a change that silently swaps them shows up as a diff
# rather than as a plausible-looking number.
GOLDEN_PATHS = (
    ("cycles", "gpu_tot_sim_cycle"),
    ("l1", "read", "accesses"),
    ("l1", "read", "misses"),
    ("l1", "write", "accesses"),
    ("l1", "write", "misses"),
    ("l2", "read", "accesses"),
    ("l2", "read", "HIT"),
    ("l2", "read", "MISS"),
    ("l2", "read", "SECTOR_MISS"),
    ("l2", "read", "misses"),
    ("l2", "read", "misses_miss_only"),
    ("l2", "write", "accesses"),
    ("l2", "write", "misses"),
    ("l2", "write", "WRITE_ALLOCATED"),
    ("dram", "reads"),
    ("dram", "read_bytes"),
    ("dram", "writes"),
    ("dram", "write_bytes"),
)


def _dig(data: dict, path: tuple):
    node = data
    for key in path:
        node = node[key]
    return node


def _golden_entry(data: dict) -> dict:
    return {".".join(p): _dig(data, p) for p in GOLDEN_PATHS}


def freeze_golden(perf_counter: str, out: str) -> int:
    """Add or refresh this fixture's entry in a golden-metrics file."""
    path = Path(out)
    doc = json.loads(path.read_text()) if path.is_file() else {
        "schema": "ADA_METRIC_GOLDEN_V1",
        "why": (
            "Pins the extracted counter values for frozen two-row perf_counter "
            "fixtures. The identity checks catch a structurally wrong "
            "aggregation; these catch a numerically different one, which is how "
            "the dropped SECTOR_MISS escaped review."
        ),
        "fixtures": {},
    }
    key = Path(perf_counter).name
    doc["fixtures"][key] = _golden_entry(extract(perf_counter))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"wrote {path} [{key}]")
    return 0


def check_golden(perf_counter: str, golden: str) -> int:
    doc = json.loads(Path(golden).read_text())
    key = Path(perf_counter).name
    if key not in doc["fixtures"]:
        print(f"FAIL: {key} is not in {golden}", file=sys.stderr)
        return 1
    expected = doc["fixtures"][key]
    actual = _golden_entry(extract(perf_counter))
    bad = [
        (k, expected[k], actual[k])
        for k in expected
        if k in actual and actual[k] != expected[k]
    ]
    missing = [k for k in expected if k not in actual]
    if missing:
        print(f"FAIL: {key} lost counters: {missing}", file=sys.stderr)
        return 1
    if bad:
        print(f"FAIL: {key} drifted from the frozen golden values:", file=sys.stderr)
        for k, e, a in bad:
            print(f"  {k}: expected {e:,} got {a:,}", file=sys.stderr)
        return 1
    print(f"OK: {key} matches all {len(expected)} frozen golden values")
    return 0


if __name__ == "__main__":
    sys.exit(main())
