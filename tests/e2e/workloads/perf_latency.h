#ifndef PERF_LATENCY_H
#define PERF_LATENCY_H

#include <stdint.h>

#include "e2e_common.h"

static inline uint64_t read_counter_serialized(void)
{
    uint64_t value;
    __asm__ volatile("isb\n\tmrs %0, cntvct_el0" : "=r"(value) : : "memory");
    return value;
}

static inline void perf_store_complete(int home_node, uint32_t offset,
                                       uint32_t value)
{
    dsm_store(home_node, offset, value);
    __asm__ volatile("dsb sy" ::: "memory");
}

static inline void perf_store_complete_plane(int home_node, int home_socket,
                                             uint32_t offset, uint32_t value)
{
    dsm_store_plane(home_node, home_socket, offset, value);
    __asm__ volatile("dsb sy" ::: "memory");
}

static inline int perf_fmt_u64(char *buf, int p, uint64_t value)
{
    char digits[24];
    int n = 0;
    if (!value) digits[n++] = '0';
    while (value) {
        digits[n++] = (char)('0' + value % 10);
        value /= 10;
    }
    while (n) buf[p++] = digits[--n];
    return p;
}

static inline void perf_sort_samples(uint64_t *samples, uint32_t count)
{
    for (uint32_t i = 1; i < count; ++i) {
        uint64_t value = samples[i];
        uint32_t j = i;
        while (j && samples[j - 1] > value) {
            samples[j] = samples[j - 1];
            --j;
        }
        samples[j] = value;
    }
}

static inline uint64_t perf_percentile(const uint64_t *samples, uint32_t count,
                                       uint32_t percentile)
{
    uint32_t rank = (percentile * count + 99u) / 100u;
    if (!rank) rank = 1;
    if (rank > count) rank = count;
    return samples[rank - 1];
}

static inline void emit_latency_summary(int node_id, const char *phase,
                                        uint64_t *samples, uint32_t count)
{
    if (!count) return;

    perf_sort_samples(samples, count);
    uint64_t sum = 0;
    for (uint32_t i = 0; i < count; ++i) sum += samples[i];

    char buf[512];
    int p = 0;
    const char *s = "[PERF-LATENCY] node=";
    while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, node_id);
    s = " phase="; while (*s) buf[p++] = *s++;
    while (*phase) buf[p++] = *phase++;
    s = " samples="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, (int)count);
    s = " min="; while (*s) buf[p++] = *s++;
    p = perf_fmt_u64(buf, p, samples[0]);
    s = " p50="; while (*s) buf[p++] = *s++;
    p = perf_fmt_u64(buf, p, perf_percentile(samples, count, 50));
    s = " p95="; while (*s) buf[p++] = *s++;
    p = perf_fmt_u64(buf, p, perf_percentile(samples, count, 95));
    s = " p99="; while (*s) buf[p++] = *s++;
    p = perf_fmt_u64(buf, p, perf_percentile(samples, count, 99));
    s = " max="; while (*s) buf[p++] = *s++;
    p = perf_fmt_u64(buf, p, samples[count - 1]);
    s = " mean="; while (*s) buf[p++] = *s++;
    p = perf_fmt_u64(buf, p, sum / count);
    s = " counter_frequency_hz="; while (*s) buf[p++] = *s++;
    p = perf_fmt_u64(buf, p, read_cntfrq_el0());
    s = " source=arm_cntvct_el0 unit=counter_ticks\n";
    while (*s) buf[p++] = *s++;
    _raw_write(buf, p);
}

#endif /* PERF_LATENCY_H */
