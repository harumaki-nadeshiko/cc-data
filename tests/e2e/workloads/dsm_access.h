/* dsm_access.h — DSM load/store inline macros for E2E ARM workloads.
 *
 * DSM_VA_BASE = MaxAddr - 4*SEG_SIZE  (installed by setup_dsm_va_mapping)
 * Each node k's DSM_k window = DSM_VA_BASE + k * SEG_SIZE
 * SEG_SIZE = 128 MB = 0x8000000
 *
 * All loads/stores use volatile inline asm to prevent compiler reordering
 * and ensure real memory access through the cache hierarchy.
 */
#ifndef E2E_DSM_ACCESS_H
#define E2E_DSM_ACCESS_H

#include <stdint.h>

#define SEG_SIZE  0x8000000ULL   /* 128 MB */

/* DSM_VA_BASE: (MaxAddr+1) - 4*SEG_SIZE for page-aligned mapping.
 * Must match setup_dsm_va_mapping() in CHI_ubcc_framework.py.
 * (0xFFFFFFFFFFFFULL + 1) = 0x1000000000000 for 48-bit VA space.
 * 4 * SEG_SIZE = 512MB = 0x20000000
 * Base = 0x1000000000000 - 0x20000000 = 0xFFFFFFE0000000
 */
#define DSM_VA_BASE  ((0xFFFFFFFFFFFFULL + 1) - 4 * SEG_SIZE)

static inline volatile uint32_t* dsm_addr(int home_node, uint32_t offset)
{
    uint64_t va = DSM_VA_BASE + (uint64_t)home_node * SEG_SIZE + offset;
    return (volatile uint32_t*)va;
}

/* DSM load (32-bit) — issues real ldr through cache hierarchy */
static inline uint32_t dsm_load(int home_node, uint32_t offset)
{
    uint32_t val;
    __asm__ volatile("ldr %w0, [%1]" : "=r"(val) : "r"(dsm_addr(home_node, offset)));
    return val;
}

/* DSM store (32-bit) — issues real str through cache hierarchy */
static inline void dsm_store(int home_node, uint32_t offset, uint32_t val)
{
    __asm__ volatile("str %w0, [%1]" : : "r"(val), "r"(dsm_addr(home_node, offset)));
}

/* DSM load (64-bit) */
static inline uint64_t dsm_load64(int home_node, uint32_t offset)
{
    uint64_t val;
    __asm__ volatile("ldr %0, [%1]" : "=r"(val) : "r"(dsm_addr(home_node, offset)));
    return val;
}

/* DSM store (64-bit) */
static inline void dsm_store64(int home_node, uint32_t offset, uint64_t val)
{
    __asm__ volatile("str %0, [%1]" : : "r"(val), "r"(dsm_addr(home_node, offset)));
}

#endif /* E2E_DSM_ACCESS_H */
