#!/usr/bin/env bash
# Run Accel-Sim on the SGLang Qwen2.5-1.5B p32/d2 Prefill trace.
#
# Mirrors experiments/llama_ada_20260921/scripts/run_sim.sh: same binary, same
# SM89_RTX4000_ADA config pair, same -gpgpu_max_concurrent_kernel 1, and the same
# refusal to overwrite an existing sim.log.
set -euo pipefail

ROOT=/home/xmu/nvidiagds/simulators/accelsim2.0
EXP=$ROOT/experiments/sglang_qwen15b_20260925
case_id=${1:-qwen15b_p32d2_prefill}

trace_dir=$EXP/cases/$case_id/traces
sim_dir=$EXP/simulations/$case_id

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

cd "$sim_dir"
set +e
/usr/bin/time -v "$ROOT/gpu-simulator/bin/release/accel-sim.out" \
  -trace "$trace_dir/kernelslist.g" \
  -config "$GPGPUSIM_ROOT/configs/tested-cfgs/SM89_RTX4000_ADA/gpgpusim.config" \
  -config "$ROOT/gpu-simulator/configs/tested-cfgs/SM89_RTX4000_ADA/trace.config" \
  -gpgpu_max_concurrent_kernel 1 > "$log" 2>&1
rc=$?
printf '%s\n' "$rc" > "$sim_dir/exit_code.txt"
exit "$rc"
