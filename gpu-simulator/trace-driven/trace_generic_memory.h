#ifndef ACCELSIM_TRACE_GENERIC_MEMORY_H
#define ACCELSIM_TRACE_GENERIC_MEMORY_H

#include <cstdint>

// NVBit 1.8 core/nvbit.h documents two independent, 16 MiB aligned
// generic-address apertures for nvbit_get_shmem_base_addr() and
// nvbit_get_local_mem_base_addr(). Their ordering is not specified: on the
// RTX 4000 Ada traces used here the shared base is HIGHER than the local base,
// so a check of the form `shared_base <= addr < local_base` can never hold.
// These are address-space windows, not the per-kernel shared allocation or
// GPGPU-Sim's per-thread LOCAL_MEM_SIZE_MAX. In particular, driver-reserved
// shared memory is not included in the trace's kernel shmem field.
enum class trace_generic_space { shared, local, global };

inline bool in_trace_generic_aperture(std::uint64_t address,
                                      std::uint64_t base) {
  const std::uint64_t aperture_bytes = std::uint64_t{16} << 20;
  // Subtraction after the lower-bound check avoids overflow at base + size.
  return address >= base && address - base < aperture_bytes;
}

// Caller retains the legacy fallback when either aperture base is missing.
// Classification is still per warp using its first active lane; mixed-space
// active lanes and normalization of local addresses are outside this fix.
inline trace_generic_space classify_trace_generic_space(
    std::uint64_t address, std::uint64_t shared_base,
    std::uint64_t local_base) {
  if (in_trace_generic_aperture(address, shared_base))
    return trace_generic_space::shared;
  if (in_trace_generic_aperture(address, local_base))
    return trace_generic_space::local;
  return trace_generic_space::global;
}

#endif
