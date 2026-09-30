#!/usr/bin/env bash
# Re-capture a GPU microbenchmark trace over a wider kernel window.
#
# Why: the refine26 traces were captured with the NVBit range filter pinned to
# the ROI kernel alone, so the simulator never sees the kernels that dirty L2
# before the ROI. Any DRAM-write comparison against a probe-ROI hardware number
# is therefore not like-for-like (see docs/ada/METRIC_CORRECTION_...zh.md §3.5).
#
# This re-runs the same probe binary under the same NVBit tool, changing only
# DYNAMIC_KERNEL_RANGE. Everything else (env, tool, binary) mirrors
# tuner_refine_20260922/scripts/run_stage.py so the capture stays comparable.
#
# Usage: retrace_probe.sh <probe-args...> <kernel-range>
#   e.g. retrace_probe.sh memory 8 1 48 32 1 20260922 1-8
#
# Env:
#   TRACE_OUT   output directory (default /tmp/retrace/<tag>)
#   TRACES_HOME trace folder passed to the tool (default $TRACE_OUT/traces)
#   GPU_INDEX   CUDA device index (default 3)
set -euo pipefail

ROOT=/home/xmu/nvidiagds/simulators/accelsim2.0
PROBE=$ROOT/experiments/ada_calibration_20260922/tuner_refine_20260922/source/memory_concurrency_probe
TOOL=$ROOT/util/tracer_nvbit/tracer_tool/tracer_tool.so
CUDA_ROOT=/usr/local/cuda-12.8

[[ -x "$PROBE" ]] || { echo "missing probe binary: $PROBE" >&2; exit 2; }
[[ -f "$TOOL" ]] || { echo "missing tracer tool: $TOOL" >&2; exit 2; }
[[ $# -ge 2 ]] || { echo "usage: $0 <probe-args...> <kernel-range>" >&2; exit 2; }

range=${!#}
args=("${@:1:$#-1}")

tag="retrace_m${args[1]}_p${args[2]}_b${args[3]}_t${args[4]}_i${args[5]}_k${range//[^0-9A-Za-z]/_}"
out=${TRACE_OUT:-/tmp/retrace/$tag}
traces=${TRACES_HOME:-$out/traces}
mkdir -p "$traces"

env -i \
  PATH="$CUDA_ROOT/bin:/usr/bin:/bin" \
  LD_LIBRARY_PATH="$CUDA_ROOT/lib64" \
  HOME="$HOME" \
  CUDA_DEVICE_ORDER=PCI_BUS_ID \
  CUDA_VISIBLE_DEVICES="${GPU_INDEX:-3}" \
  TRACES_FOLDER="$traces" \
  CUDA_INJECTION64_PATH="$TOOL" \
  NVBIT_INSTRUMENTATION_ENABLED=0 \
  ACTIVE_FROM_START=1 \
  ALLOW_REG_VAL_TRACING=0 \
  TRACE_FILE_COMPRESS=0 \
  SPINLOCK_HANDLING_MODE=0 \
  TRACE_LINEINFO=0 \
  TOOL_TRACE_CORE=0 \
  DYNAMIC_KERNEL_RANGE="$range" \
  "$PROBE" "${args[@]}" trace "$out/trace.json"

echo "out=$out"
ls -la "$traces" | head -20
