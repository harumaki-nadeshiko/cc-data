#ifndef E2E_DSM_ACCESS_H
#define E2E_DSM_ACCESS_H

#include <stdint.h>

#define SEG_SIZE  0x8000000ULL
#define DSM_VA_BASE  ((0xFFFFFFFFFFFFULL + 1) - 4 * SEG_SIZE)

static inline volatile uint32_t* dsm_addr(int home_node, uint32_t offset)
{
    uint64_t va = DSM_VA_BASE + (uint64_t)home_node * SEG_SIZE + offset;
    return (volatile uint32_t*)va;
}

/* NO-OP flush: just dmb osh, no dc civac */
static inline void flush_dsm_line(volatile void *addr)
{
    (void)addr;
    __asm__ volatile("dmb osh" ::: "memory");
}

static inline uint32_t dsm_load(int home_node, uint32_t offset)
{
    volatile uint32_t *p = dsm_addr(home_node, offset);
    flush_dsm_line((void *)p);
    uint32_t val;
    __asm__ volatile("ldr %w0, [%1]" : "=r"(val) : "r"(p));
    return val;
}

static inline void dsm_store(int home_node, uint32_t offset, uint32_t val)
{
    volatile uint32_t *p = dsm_addr(home_node, offset);
    __asm__ volatile("str %w0, [%1]" : : "r"(val), "r"(p));
    flush_dsm_line((void *)p);
}

static inline void flush_dsm_line_dmb_only(volatile void *addr)
{
    (void)addr;
    __asm__ volatile("dmb osh" ::: "memory");
}

static inline uint64_t dsm_load64(int home_node, uint32_t offset)
{
    uint64_t val;
    __asm__ volatile("ldr %0, [%1]" : "=r"(val) : "r"(dsm_addr(home_node, offset)));
    return val;
}

static inline void dsm_store64(int home_node, uint32_t offset, uint64_t val)
{
    __asm__ volatile("str %0, [%1]" : : "r"(val), "r"(dsm_addr(home_node, offset)));
}

#endif
