/* dsm_access.h — DSM load/store inline macros for E2E ARM workloads.
 *
 * DSM_VA_BASE = MaxAddr - (num_nodes * num_sockets + 1) * SEG_SIZE
 * Must match setup_dsm_va_mapping() in CHI_ubcc_framework.py.
 *
 * Compile with -DNUM_SOCKETS=2 for dual-socket tests.
 * Default: NUM_SOCKETS=1, NUM_NODES=3.
 */
#ifndef E2E_DSM_ACCESS_H
#define E2E_DSM_ACCESS_H

#include <stdint.h>

#define SEG_SIZE  0x8000000ULL   /* 128 MB */

#ifndef NUM_NODES
#define NUM_NODES 3
#endif
#ifndef NUM_SOCKETS
#define NUM_SOCKETS 1
#endif

#define TOTAL_SEGS  (NUM_NODES * NUM_SOCKETS)

/* DSM_VA_BASE = (MaxAddr+1) - (TOTAL_SEGS + 1) * SEG_SIZE */
#define DSM_VA_BASE  ((0xFFFFFFFFFFFFULL + 1) - (TOTAL_SEGS + 1) * (uint64_t)SEG_SIZE)

/* test_e2e.py pre-maps this VA window to each node's local_private PA
 * (node_id << 40). Accesses through this helper route to local HN-F/local DRAM,
 * not the DSM EP path. */
#define LOCAL_DRAM_VA_BASE 0x01000000ULL

static inline volatile uint32_t* dsm_addr_plane(int home_node, int home_socket,
                                                uint32_t offset)
{
    uint64_t segment = (uint64_t)home_node * NUM_SOCKETS + home_socket;
    uint64_t va = DSM_VA_BASE + segment * SEG_SIZE + offset;
    return (volatile uint32_t*)va;
}

static inline volatile uint32_t* dsm_addr(int home_node, uint32_t offset)
{
    return dsm_addr_plane(home_node, 0, offset);
}

static inline volatile uint32_t* local_dram_addr(uint32_t offset)
{
    return (volatile uint32_t*)(LOCAL_DRAM_VA_BASE + (uint64_t)offset);
}

static inline uint32_t local_dram_load(uint32_t offset)
{
    uint32_t val;
    __asm__ volatile("ldr %w0, [%1]" : "=r"(val) : "r"(local_dram_addr(offset)));
    return val;
}

static inline void local_dram_store(uint32_t offset, uint32_t val)
{
    __asm__ volatile("str %w0, [%1]" : : "r"(val), "r"(local_dram_addr(offset)));
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

static inline uint32_t dsm_load_plane(int home_node, int home_socket,
                                      uint32_t offset)
{
    uint32_t val;
    __asm__ volatile("ldr %w0, [%1]" : "=r"(val) :
                     "r"(dsm_addr_plane(home_node, home_socket, offset)));
    return val;
}

static inline void dsm_store_plane(int home_node, int home_socket,
                                   uint32_t offset, uint32_t val)
{
    __asm__ volatile("str %w0, [%1]" : : "r"(val),
                     "r"(dsm_addr_plane(home_node, home_socket, offset)));
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

/* v4 dsm_flush: writes to 16K cache lines to evict dirty DSM to DDR4.
 * The flush buffer is 1MB (16384 lines × 64B), enough to overflow L1+L2. */
static inline void dsm_flush(int home_node, uint32_t offset);
volatile char _v4_flush_buf[1048576] __attribute__((aligned(64)));
static inline void dsm_flush(int home_node, uint32_t offset)
{
    for (int i = 0; i < 1048576; i += 64) {
        __asm__ volatile("str %w0, [%1]" : : "r"(0), "r"(&_v4_flush_buf[i]) : "memory");
    }
    __asm__ volatile("dmb sy" ::: "memory");
}

#endif /* E2E_DSM_ACCESS_H */
