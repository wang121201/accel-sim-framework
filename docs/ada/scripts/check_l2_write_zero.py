#!/usr/bin/env python3
"""Judge the refine26 DRAM-write column against hardware, per case.

Why this exists
---------------
The refine26 probes were captured with the NVBit ROI pinned to the probe kernel
alone (`memory_concurrency_probe`), and the probe source *requires* that:

    "trace requires CUDA_INJECTION64_PATH and no DYNAMIC_KERNEL_RANGE"
    (memory_concurrency_probe.cu, Nvbit())

so the kernels that dirty L2 before the ROI -- initialize_pattern,
flush_cache_cg -- can never appear in a trace. The probe also declares the
property outright: `flush_and_validation_outside_roi = true`.

That splits the 12 cases cleanly, and the hardware agrees with the split:

  l2train_*, l2hold_*   hardware dram__bytes_write.sum == 0 (3/3 reps)
        A read-only probe that ends with a clean L2 must write nothing to DRAM.
        A single-kernel trace reproduces this exactly, so the column is VALID
        and is the sharpest available test for the `111ed9c5` class of bug:
        any writeback the simulator invents here is provably wrong.

  dramtrain_*, dramhold_*  hardware ~1.34-1.5 MB
        Those bytes can only be residual dirty lines evicted by the probe's own
        streaming reads -- state that a single-kernel trace cannot inherit. The
        simulator's 0 is the structurally expected answer, so the column is NOT
        COMPARABLE. Reporting it as an error is what produced 111ed9c5.

Freezing the hardware side here keeps the reason, the provenance and the
expected value in one place instead of in prose.

Usage:
    # freeze hardware expectations from the NCU exports
    ./check_l2_write_zero.py --freeze

    # judge a regression run
    ./check_l2_write_zero.py --regression /tmp/wr_regress_wbfix
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RESULTS = (
    ROOT
    / "experiments/ada_calibration_20260922/tuner_refine_20260922/results"
)
DEFAULT_EXPECTATIONS = (
    ROOT / "docs/ada/evidence/refine26_dram_write_expectations.json"
)

# Case groups and whether the DRAM-write column may be judged.
COMPARABLE_PREFIXES = ("l2train_", "l2hold_")
NOT_COMPARABLE_PREFIXES = ("dramtrain_", "dramhold_")


def classify(case: str) -> str:
    if case.startswith(COMPARABLE_PREFIXES):
        return "comparable"
    if case.startswith(NOT_COMPARABLE_PREFIXES):
        return "not_comparable"
    raise SystemExit(f"unclassified case: {case}")


def _mangled_rows(path: Path) -> dict[str, str]:
    """NCU 'mangled' export: row 0 = names, row 1 = units, row 2 = values."""
    rows = list(csv.reader(path.open()))
    if len(rows) < 3:
        return {}
    return dict(zip([n.strip() for n in rows[0]], [v.strip() for v in rows[2]]))


def freeze() -> int:
    cases = sorted(
        d.name
        for d in RESULTS.iterdir()
        if d.is_dir() and (d.name.startswith(COMPARABLE_PREFIXES + NOT_COMPARABLE_PREFIXES))
    )
    out = {
        "schema": "REFINE26_DRAM_WRITE_EXPECTATIONS_V1",
        "why": (
            "The probe traces contain only the ROI kernel, so DRAM writes are "
            "judgeable only where hardware is exactly 0. See the module docstring "
            "in check_l2_write_zero.py."
        ),
        "source": "NCU profile exports, tuner_refine_20260922/results/<case>/profile*_mangled.csv",
        "cases": {},
    }
    for case in cases:
        reps = []
        reads = []
        used = []
        for f in sorted((RESULTS / case).glob("profile*mangled.csv")):
            d = _mangled_rows(f)
            if "dram__bytes_write.sum" not in d:
                continue
            reps.append(int(d["dram__bytes_write.sum"]))
            if "dram__bytes_read.sum" in d:
                reads.append(int(d["dram__bytes_read.sum"]))
            used.append(str(f.relative_to(ROOT)))
        if not reps:
            raise SystemExit(f"no hardware write data for {case}")
        group = classify(case)
        out["cases"][case] = {
            "group": group,
            "hardware_write_bytes": reps,
            "hardware_read_bytes": reads,
            "hardware_write_exactly_zero": all(v == 0 for v in reps),
            "judgeable": group == "comparable",
            "evidence": used,
        }
        # A comparable case must actually be the zero case, otherwise the
        # classification is wrong and the checker would silently pass anything.
        if group == "comparable" and not out["cases"][case]["hardware_write_exactly_zero"]:
            raise SystemExit(
                f"{case} is classified comparable but hardware writes are {reps}"
            )
        if group == "not_comparable" and out["cases"][case]["hardware_write_exactly_zero"]:
            raise SystemExit(
                f"{case} is classified not_comparable but hardware writes are 0"
            )
    DEFAULT_EXPECTATIONS.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_EXPECTATIONS.write_text(json.dumps(out, indent=2) + "\n")
    print(f"wrote {DEFAULT_EXPECTATIONS}")
    print(f"  comparable (must be 0):     {sum(1 for c in out['cases'].values() if c['judgeable'])}")
    print(f"  not comparable:             {sum(1 for c in out['cases'].values() if not c['judgeable'])}")
    return 0


_WRITES = re.compile(r"total dram writes\s*=\s*(\d+)")
_READS = re.compile(r"total dram reads\s*=\s*(\d+)")


def _sim_totals(sim_log: Path) -> tuple[int, int] | None:
    text = sim_log.read_text(errors="replace")
    w = _WRITES.findall(text)
    r = _READS.findall(text)
    if not w or not r:
        return None
    return int(r[-1]), int(w[-1])


def summarize(regression: Path) -> dict:
    """Snapshot per-case DRAM totals so the judgement can run without a rerun."""
    out: dict = {"cases": {}}
    for case_dir in sorted(p for p in regression.iterdir() if p.is_dir()):
        log = case_dir / "sim.log"
        if not log.is_file():
            continue
        totals = _sim_totals(log)
        if totals is None:
            continue
        out["cases"][case_dir.name] = {"read_bytes": totals[0], "write_bytes": totals[1]}
    return out


def _load_summaries(target: Path) -> dict:
    if target.is_dir():
        return summarize(target)
    return json.loads(target.read_text())


def check(target: Path, expectations: Path) -> int:
    exp = json.loads(expectations.read_text())
    sim = _load_summaries(target)
    rows = []
    failures = []
    for case, spec in sorted(exp["cases"].items()):
        if case not in sim["cases"]:
            rows.append((case, spec["group"], "missing", ""))
            continue
        sim_write = sim["cases"][case]["write_bytes"]
        if not spec["judgeable"]:
            rows.append((case, "not_comparable", "excluded", ""))
            continue
        ok = sim_write == 0
        rows.append(
            (case, "comparable", "PASS" if ok else "FAIL",
             f"sim_write={sim_write} hardware=0")
        )
        if not ok:
            failures.append(
                f"{case}: simulator emitted {sim_write} DRAM write bytes where "
                f"hardware measures 0"
            )

    width = max(len(r[0]) for r in rows) if rows else 10
    print(f"{'case':{width}s}  {'group':15s} {'verdict':9s} detail")
    for case, group, verdict, detail in rows:
        print(f"{case:{width}s}  {group:15s} {verdict:9s} {detail}")
    print()
    judged = sum(1 for r in rows if r[2] in ("PASS", "FAIL"))
    print(f"judged {judged} comparable case(s) on the DRAM-write column")
    if failures:
        print()
        for f in failures:
            print(f"  FAIL {f}")
        return 1
    return 0


SELFTEST_BASELINE = ROOT / "docs/ada/evidence/refine26_summary_fix111.json"
SELFTEST_FIXED = ROOT / "docs/ada/evidence/refine26_summary_wbfix.json"


def selftest(expectations: Path) -> int:
    """Assert the judgement separates the two binaries it was built for.

    Runs in well under a second and needs no GPU, so it can live in CI. The two
    summaries are the actual 12-case regression runs from 2026-09-29 and
    2026-10-01; if a future change makes the checker stop discriminating them,
    this fails.
    """
    missing = [p for p in (SELFTEST_BASELINE, SELFTEST_FIXED) if not p.is_file()]
    if missing:
        print("selftest needs frozen summaries; missing:")
        for p in missing:
            print(f"  {p}")
        print("\nrecreate them with:")
        print(f"  {sys.argv[0]} --summarize /tmp/wr_regress       --out {SELFTEST_BASELINE}")
        print(f"  {sys.argv[0]} --summarize /tmp/wr_regress_wbfix --out {SELFTEST_FIXED}")
        return 2

    print("== 111ed9c5 baseline (expected: FAIL) ==")
    baseline_rc = check(SELFTEST_BASELINE, expectations)
    print()
    print("== this fix (expected: PASS) ==")
    fixed_rc = check(SELFTEST_FIXED, expectations)
    print()
    if baseline_rc != 1:
        print("SELFTEST FAIL: the baseline did not fail; the checker no longer "
              "catches the writeback over-count")
        return 1
    if fixed_rc != 0:
        print("SELFTEST FAIL: the fixed run did not pass")
        return 1
    print("SELFTEST PASS: baseline fails, fixed run passes")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--freeze", action="store_true")
    ap.add_argument("--summarize", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--judge", type=Path)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--expectations", type=Path, default=DEFAULT_EXPECTATIONS)
    args = ap.parse_args()

    if args.freeze:
        return freeze()
    if args.selftest:
        return selftest(args.expectations)
    if args.summarize:
        if not args.out:
            ap.error("--summarize needs --out")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(summarize(args.summarize), indent=2) + "\n")
        print(f"wrote {args.out}")
        return 0
    if args.judge:
        return check(args.judge, args.expectations)
    ap.error("pass --freeze, --summarize, --judge or --selftest")



if __name__ == "__main__":
    sys.exit(main())
