#!/usr/bin/env bash
# Run Accel-Sim on the SGLang Qwen2.5-1.5B p32/d2 full trace with the CALIBRATED
# SM89_RTX4000_ADA_C3_idx0_Jl2 config.
#
# Same trace, same binary, same -gpgpu_max_concurrent_kernel 1 as run_sim.sh.
# The only difference is the config pair: the calibrated variant changes three
# options relative to the shipped config
#   -gpgpu_memory_partition_indexing 2 -> 0
#   -gpgpu_l2_rop_latency           187 -> 237
#   -dram_latency                   254 -> 324
# gpgpusim.config SHA256 = b3618131724451f84fc839b4752eef34f64e70d660303c6532941e100d7ceca4
set -euo pipefail

ROOT=/home/xmu/nvidiagds/simulators/accelsim2.0
EXP=$ROOT/experiments/sglang_qwen15b_20260925
CFG=SM89_RTX4000_ADA_C3_idx0_Jl2
case_id=${1:-qwen15b_p32d2_full_c3}

trace_dir=$EXP/cases/qwen15b_p32d2_full/traces
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

# Record which config was actually used, by hash, so the run is self-describing.
sha256sum "$GPGPUSIM_ROOT/configs/tested-cfgs/$CFG/gpgpusim.config" \
         "$ROOT/gpu-simulator/configs/tested-cfgs/$CFG/trace.config" \
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
