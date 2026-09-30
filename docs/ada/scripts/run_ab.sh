#!/usr/bin/env bash
# A/B a Qwen kernel prefix across simulator binaries.
#
# Why a prefix and not the full trace: the full 1030-kernel run takes ~2.5 h,
# and the DRAM-write discrepancy this is used to attribute is already visible
# within the first ~70 kernels. The prefix, config, trace and trace base
# address are held fixed, so the only variable left is the binary.
#
# Usage: ./run_ab.sh <case-id> <sim-name> <binary>
set -euo pipefail

ROOT=/home/xmu/nvidiagds/simulators/accelsim2.0
EXP=$ROOT/experiments/sglang_qwen15b_20260925
case_id=${1:?usage: run_ab.sh <case-id> <sim-name> <binary>}
sim_name=${2:?usage: run_ab.sh <case-id> <sim-name> <binary>}
binary=${3:?usage: run_ab.sh <case-id> <sim-name> <binary>}
CFG=${CFG:-SM89_RTX4000_ADA_C3_idx0_Jl2}

trace_dir=$EXP/cases/$case_id/traces
sim_dir=$EXP/simulations/$sim_name

[[ -x "$binary" ]] || { echo "not executable: $binary" >&2; exit 2; }
[[ -f "$trace_dir/kernelslist.g" ]] || { echo "missing $trace_dir/kernelslist.g" >&2; exit 2; }

mkdir -p "$sim_dir"
log=$sim_dir/sim.log
if [[ -f "$log" ]]; then echo "Refusing to overwrite $log" >&2; exit 2; fi

cd "$ROOT"
export CUDA_INSTALL_PATH=/usr/local/cuda-12.8
set +u
source gpu-simulator/setup_environment.sh > "$sim_dir/environment.log" 2>&1
set -u

{
  echo "binary $binary"
  sha256sum "$binary"
  sha256sum "$GPGPUSIM_ROOT/configs/tested-cfgs/$CFG/gpgpusim.config"
  sha256sum "$trace_dir/kernelslist.g"
} > "$sim_dir/config_hashes.txt" 2>&1 || true

cd "$sim_dir"
set +e
"$binary" \
  -trace "$trace_dir/kernelslist.g" \
  -config "$GPGPUSIM_ROOT/configs/tested-cfgs/$CFG/gpgpusim.config" \
  -config "$ROOT/gpu-simulator/configs/tested-cfgs/$CFG/trace.config" \
  -gpgpu_max_concurrent_kernel 1 > "$log" 2>&1
rc=$?
printf '%s\n' "$rc" > "$sim_dir/exit_code.txt"
exit "$rc"
