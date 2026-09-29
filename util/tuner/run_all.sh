#!/bin/bash

set -eo pipefail
THIS_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
SCRIPT_DIR="$THIS_DIR/gpu-app-collection-partial/src/cuda/GPU_Microbenchmark"
# This suite needs nvcc and CUDA samples headers, not the obsolete CUDA 4.2 SDK.
command -v nvcc >/dev/null || { echo "nvcc not found in PATH" >&2; exit 1; }
export GPUAPPS_ROOT="$THIS_DIR/gpu-app-collection-partial"
if grep -q '^#include "ada_RTX4000_hw_def.h"' "$SCRIPT_DIR/hw_def/hw_def.h"; then
  export CUDA_CPPFLAGS="${CUDA_CPPFLAGS:--std=c++17 -gencode=arch=compute_89,code=sm_89 -gencode=arch=compute_89,code=compute_89}"
else
  export CUDA_CPPFLAGS="${CUDA_CPPFLAGS:--std=c++17 -arch=native}"
fi
echo "Running make in $SCRIPT_DIR"
make -C "$SCRIPT_DIR" -j2
cd "$SCRIPT_DIR/bin"
run_benchmark() {
  local rc
  if "$@"; then rc=0; else rc=$?; fi
  printf 'BENCHMARK_EXIT name=%s exit_code=%s\n' "$1" "$rc" >&2
  return "$rc"
}

# List of configuration benchmarks that output lines starting with "-"
# These are used by tuner.py to generate Accel-Sim configuration

# System config
echo "running system_config"
run_benchmark ./system_config
echo "/////////////////////////////////"

# Core config
echo "running core_config"
run_benchmark ./core_config
echo "/////////////////////////////////"

echo "running config_dpu"
run_benchmark ./config_dpu --blocks 1
echo "/////////////////////////////////"

echo "running config_fpu"
run_benchmark ./config_fpu --blocks 1
echo "/////////////////////////////////"

echo "running config_int"
run_benchmark ./config_int --blocks 1
echo "/////////////////////////////////"

echo "running config_sfu"
run_benchmark ./config_sfu --blocks 1
echo "/////////////////////////////////"

echo "running config_tensor"
run_benchmark ./config_tensor --blocks 1
echo "/////////////////////////////////"

echo "running config_udp"
run_benchmark ./config_udp --blocks 1
echo "/////////////////////////////////"

echo "running regfile_bw"
run_benchmark ./regfile_bw
echo "/////////////////////////////////"

# L1 cache config
echo "running l1_config"
run_benchmark ./l1_config
echo "/////////////////////////////////"

echo "running l1_lat with args: --blocks 1"
run_benchmark ./l1_lat --blocks 1
echo "/////////////////////////////////"

# L2 cache config
echo "running l2_config"
run_benchmark ./l2_config
echo "/////////////////////////////////"

echo "running l2_copy_engine"
run_benchmark ./l2_copy_engine
echo "/////////////////////////////////"

echo "running l2_lat"
run_benchmark ./l2_lat
echo "/////////////////////////////////"

# Memory config
echo "running mem_config"
run_benchmark ./mem_config
echo "/////////////////////////////////"

echo "running mem_lat"
run_benchmark ./mem_lat
echo "/////////////////////////////////"

# Shared memory config
echo "running shd_config"
run_benchmark ./shd_config
echo "/////////////////////////////////"

echo "running shared_lat with args: --blocks 1"
run_benchmark ./shared_lat --blocks 1
echo "/////////////////////////////////"

# Kernel latency
echo "running kernel_lat"
run_benchmark ./kernel_lat
echo "/////////////////////////////////"
