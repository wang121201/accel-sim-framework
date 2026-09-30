#!/usr/bin/env bash
# Regression: run all 12 refine26 cases with the DRAM write-path fix and report
# DRAM read/write traffic plus completion status for each.
set -uo pipefail

ROOT=/home/xmu/nvidiagds/simulators/accelsim2.0
EXP=$ROOT/experiments/ada_calibration_20260922
CFG=SM89_RTX4000_ADA_C3_idx0_Jl2
OUT=${1:-/tmp/wr_regress}

mkdir -p "$OUT"
cd "$ROOT"
set +u
source gpu-simulator/setup_environment.sh > /dev/null 2>&1
set -u

for case in \
  l2train_m8_p8_b48_t32_i1 l2train_m8_p8_b192_t32_i1 \
  l2train_m8_p8_b48_t256_i1 l2train_m8_p8_b48_t32_i4 \
  dramtrain_m64_p1_b48_t32_i1 dramtrain_m64_p1_b192_t32_i1 \
  dramtrain_m64_p1_b48_t256_i1 dramtrain_m64_p1_b48_t32_i4 \
  l2hold_m16_p4_b96_t128_i1 l2hold_m16_p4_b192_t256_i4 \
  dramhold_m96_p1_b96_t128_i1 dramhold_m96_p1_b192_t256_i4 ; do
  d="$OUT/$case"
  mkdir -p "$d"
  if [[ -f "$d/sim.log" ]]; then echo "SKIP $case (done)"; continue; fi
  ( cd "$d"
    timeout 3600 "$ROOT/gpu-simulator/bin/release/accel-sim.out" \
      -trace "$EXP/traces/refine26_$case/traces/kernelslist.g" \
      -config "$GPGPUSIM_ROOT/configs/tested-cfgs/$CFG/gpgpusim.config" \
      -config "$ROOT/gpu-simulator/configs/tested-cfgs/$CFG/trace.config" \
      -gpgpu_max_concurrent_kernel 1 > sim.log 2>&1
    echo $? > exit_code.txt )
  wr=$(grep -oE 'total dram writes = [0-9]+' "$d/sim.log" | tail -1)
  rd=$(grep -oE 'total dram reads = [0-9]+' "$d/sim.log" | tail -1)
  ec=$(cat "$d/exit_code.txt")
  dl=$(grep -c 'DEADLOCK' "$d/sim.log")
  echo "$case exit=$ec deadlock=$dl $rd $wr"
done
