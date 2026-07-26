#ifndef HA_NATIVE_ADAPTER_CONTRACT_H
#define HA_NATIVE_ADAPTER_CONTRACT_H

/*
 * Customer integration contract. Implement these hooks with the platform's
 * HA placement, affinity, monotonic timer, and barrier APIs. The portable
 * workload core emits the same JSONL schema as the CC adapter.
 */
struct ha_native_adapter {
    int (*pin_primary_thread)(int node);
    int (*place_home_memory)(int home_node, void **addr, unsigned long bytes);
    unsigned long long (*monotonic_ns)(void);
    int (*barrier_wait)(unsigned long participant_mask);
};

#endif
