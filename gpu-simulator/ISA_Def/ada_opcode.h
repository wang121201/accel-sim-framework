// Experimental SM89 front-end compatibility, not an Ada timing calibration.
// NVIDIA documents Ampere and Ada in a common instruction-set table:
// https://docs.nvidia.com/cuda/archive/12.8.0/cuda-binary-utilities/index.html#nvidia-ampere-gpu-and-ada-instruction-set
// Preserve binary_version=89 in traces; unknown opcodes must remain fatal.
#ifndef ADA_OPCODE_H
#define ADA_OPCODE_H
#include "ampere_opcode.h"
#define ADA_RTX_BINART_VERSION 89
static const auto &Ada_OpcodeMap = Ampere_OpcodeMap;
#endif
