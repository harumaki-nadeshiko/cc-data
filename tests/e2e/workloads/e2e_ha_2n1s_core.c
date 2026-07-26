#include "dsm_access.h"
#include "e2e_common.h"

#include <stdio.h>

#ifndef HA_SCENARIO
#define HA_SCENARIO 1
#endif

static void json(const char *kind, int node, int errors)
{
    printf("{\"kind\":\"%s\",\"scenario\":\"HA%02d\",\"mode\":\"cc\",\"node\":%d,\"seed\":131,\"errors\":%d}\n",
           kind, HA_SCENARIO, node, errors);
    fflush(stdout);
}

int main(int argc, char **argv)
{
    int node = argc >= 2 ? parse_int(argv[1]) : 0;
    int cpu = argc >= 3 ? parse_int(argv[2]) : 0;
    if ((cpu % 4) != 0) { _exit_program(0); return 0; }
    const uint64_t mask = 0x3;
    const uint32_t off = 0x6000 + HA_SCENARIO * 0x100;
    int fail = 0;
    json("manifest", node, 0);
    if (HA_SCENARIO == 1) {
        if (node == 0) dsm_store(0, off, 0x101);
        sync_wait(mask);
        if (node == 0 && dsm_load(0, off) != 0x101) fail++;
    } else if (HA_SCENARIO == 2) {
        if (node == 0) dsm_store(0, off, 0x202);
        sync_wait(mask);
        if (node == 1 && dsm_load(0, off) != 0x202) fail++;
    } else if (HA_SCENARIO == 3) {
        if (node == 0) dsm_store(0, off, 0x303);
        sync_wait(mask);
        if (node == 1) dsm_store(0, off, 0x304);
        sync_wait(mask);
        if (node == 0 && dsm_load(0, off) != 0x304) fail++;
    } else if (HA_SCENARIO == 4) {
        if (node == 0) dsm_store(0, off, 0x404);
        sync_wait(mask);
        (void)dsm_load(0, off);
        sync_wait(mask);
        if (node == 1) dsm_store(0, off, 0x405);
        sync_wait(mask);
        if (node == 0 && dsm_load(0, off) != 0x405) fail++;
    } else {
        if (node == 0) dsm_store(0, off, 0x701);
        sync_wait(mask);
        if (node == 1 && dsm_load(0, off) != 0x701) fail++;
        sync_wait(mask);
    }
    json("validation", node, fail);
    _exit_program(fail ? 1 : 0);
    return 0;
}
