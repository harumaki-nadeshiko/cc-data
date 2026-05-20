/*
 * UBCC M2 Domain Isolation Test
 *
 * Node 0 cores (cpu0, cpu1): run full workload exercising cache hierarchy
 * Node 1 cores (cpu2, cpu3): idle (only startup)
 * Node 2 cores (cpu4, cpu5): idle (only startup)
 *
 * Verifies: Node0 workload does not generate Node1/Node2 CHI messages.
 * The Ruby stats should show HN-F 0 active, HN-F 1/2 idle.
 *
 * Usage: m2_isolation <node_id> <core_within_node>
 */

#include <stdlib.h>

#define ARRAY_SIZE 8192

volatile int data[ARRAY_SIZE];

int main(int argc, char *argv[]) {
    int node_id = 0;
    int core_id = 0;
    if (argc > 1) node_id = atoi(argv[1]);
    if (argc > 2) core_id = atoi(argv[2]);

    if (node_id != 0) {
        /* Non-Node-0 cores: minimal work, exit immediately */
        return 0;
    }

    /* Node 0 cores: exercise cache hierarchy */
    for (int i = 0; i < ARRAY_SIZE; i++) {
        data[i] = (core_id << 16) | (i & 0xFFFF);
    }

    int sum = 0;
    int step = 127 + core_id * 31;
    for (int i = 0; i < ARRAY_SIZE; i++) {
        int idx = (i * step) % ARRAY_SIZE;
        sum += data[idx];
    }

    for (int i = 0; i < ARRAY_SIZE; i += 256) {
        data[i] = sum + core_id + i;
    }

    return sum & 0xFF;
}
