#!/usr/bin/env bash
# Dump the full per-kernel counter stream for a Qwen prefix case.
#
# `run_sim_c3.sh` (the canonical full run) writes a gzip perf_counter; this
# wrapper reuses it against a *prefix* case directory so a fix can be A/B'd in
# minutes instead of hours. Everything else -- binary, config, trace format --
# is identical, which is what makes the comparison meaningful.
#
# Usage: ./run_prefix.sh <case-id> <sim-name> [config-name]
set -euo pipefail

ROOT=/home/xmu/nvidiagds/simulators/accelsim2.0
EXP=$ROOT/experiments/sglang_qwen15b_20260925
case_id=${1:?usage: run_prefix.sh <case-id> <sim-name> [config]}
sim_name=${2:?usage: run_prefix.sh <case-id> <sim-name> [config]}
CFG=${3:-SM89_RTX4000_ADA_C3_idx0_Jl2}

trace_dir=$EXP/cases/$case_id/traces
sim_dir=$EXP/simulations/$sim_name

if [[ ! -f "$trace_dir/kernelslist.g" ]]; then
  echo "missing $trace_dir/kernelslist.g" >&2
  exit 2
fi

mkdir -p "$sim_dir"
log=$sim_dir/sim.log
if [[ -f "$log" ]]; then echo "Refusing to overwrite $log" >&2; exit 2; fi

cd "$ROOT"
export CUDA_INSTALL_PATH=/usr/local/cuda-12.8
set +u
source gpu-simulator/setup_environment.sh > "$sim_dir/environment.log" 2>&1
set -u

sha256sum "$GPGPUSIM_ROOT/configs/tested-cfgs/$CFG/gpgpusim.config" \
         "$ROOT/gpu-simulator/configs/tested-cfgs/$CFG/trace.config" \
         "$ROOT/gpu-simulator/bin/release/accel-sim.out" \
         "$trace_dir/kernelslist.g" \
         > "$sim_dir/config_hashes.txt" 2>&1 || true

cd "$sim_dir"
set +e
/usr/bin/time -v "$ROOT/gpu-simulator/bin/release/accel-sim.out" \
  -trace "$trace_dir/kernelslist.g" \
  -config "$GPGPUSIM_ROOT/configs/tested-cfgs/$CFG/gpgpusim.config" \
  -config "$ROOT/gpu-simulator/configs/tested-cfgs/$CFG/trace.config" \
  -gpgpu_max_concurrent_kernel 1 > "$log" 2>&1
rc=$?
printf '%s\n' "$rc" > "$sim_dir/exit_code.txt"
exit "$rc"
