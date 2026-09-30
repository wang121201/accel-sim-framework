#!/usr/bin/env bash
# Fast, GPU-free accuracy checks for the Ada development.
#
# Everything here runs off frozen fixtures under docs/ada/evidence/, so it needs
# no experiments tree, no simulator build and no GPU, and finishes in about a
# second. That is deliberate: both checks exist because a wrong number reached a
# report and then a code change, and neither was caught by anything automatic.
#
#   metric extraction   The identity checks catch a structurally wrong
#                       aggregation. The golden values catch a numerically
#                       different one. The mis-derived "L2 read miss -74.91%"
#                       in the 2026-09-30 comparison was a dropped SECTOR_MISS
#                       bucket; the check below fails on exactly that.
#
#   microbenchmark      Six of the twelve refine26 cases have a hardware DRAM
#   DRAM writes         write count of exactly zero, and the probe's trace can
#                       reproduce that. The selftest asserts the judgement
#                       separates the pre-fix binary (which invented 282-34313
#                       write bytes) from the fixed one (all zero).
#
# Wire into any harness:
#   make ci-checks                 -> docs/ada/scripts/run_ci_checks.sh
#   ctest                          -> add_test(NAME ada_accuracy COMMAND
#                                     ${CMAKE_SOURCE_DIR}/docs/ada/scripts/run_ci_checks.sh)
#   .github/workflows              -> run: docs/ada/scripts/run_ci_checks.sh
#
# Exit code: 0 if every check passes, 1 otherwise.
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
cd "$ROOT"

FIXTURES=docs/ada/evidence/fixtures
GOLDEN=$FIXTURES/golden_metrics.json
PY=${PYTHON:-python3}

pass=0
fail=0
declare -a failed

report() { # name ok?
  if [[ $2 -eq 0 ]]; then
    echo "  PASS  $1"
    pass=$((pass + 1))
  else
    echo "  FAIL  $1"
    fail=$((fail + 1))
    failed+=("$1")
  fi
}

echo "== Ada accuracy checks (frozen fixtures, no GPU) =="
echo

echo "-- perf_counter extraction口径"
shopt -s nullglob
fixtures=("$FIXTURES"/*.final_rows.csv.gz)
shopt -u nullglob
if [[ ${#fixtures[@]} -eq 0 ]]; then
  echo "  FAIL  no fixtures under $FIXTURES"
  fail=$((fail + 1))
  failed+=("no fixtures")
fi
for f in "${fixtures[@]}"; do
  name=$(basename "$f")
  out=$("$PY" docs/ada/scripts/extract_metrics.py "$f" --golden "$GOLDEN" 2>&1)
  report "$name golden values" $?
  echo "$out" | grep -q '^FAIL' && echo "$out" | sed 's/^/        /'
  out=$("$PY" docs/ada/scripts/extract_metrics.py "$f" --assert-checks 2>&1)
  report "$name identity checks" $?
  echo "$out" | grep -q '^FAIL' && echo "$out" | sed 's/^/        /'
done

echo
echo "-- refine26 DRAM-write judgement"
out=$("$PY" docs/ada/scripts/check_l2_write_zero.py --selftest 2>&1)
rc=$?
report "writeback over-count is detected (selftest)" $rc
if [[ $rc -ne 0 ]]; then
  echo "$out" | tail -6 | sed 's/^/        /'
fi

echo
echo "== $pass passed, $fail failed =="
if [[ $fail -gt 0 ]]; then
  printf 'failed: %s\n' "${failed[@]}"
  exit 1
fi
exit 0
