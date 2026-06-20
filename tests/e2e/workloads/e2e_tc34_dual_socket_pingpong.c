/* TC34: dual-socket pingpong — Node0 writes DSM(node=0, socket=0),
 * Node1 writes DSM(node=0, socket=1), Node2 reads both after sync.
 * With NUM_SOCKETS=2, home=0 and home=1 map to different socket planes
 * of the same home node, exercising per-socket UBCC isolation.
 * Compile: aarch64-linux-gnu-gcc -static -O0 -g -DNUM_SOCKETS=2 -DNUM_NODES=3 */
#include "e2e_common.h"
#include "dsm_access.h"

#define VAL0 0xCAFE0000u
#define VAL1 0xBEEF0000u

int main(int argc, char **argv)
{
    int n = 0, c = 0;
    if (argc >= 2) n = parse_int(argv[1]);
    if (argc >= 3) c = parse_int(argv[2]);
    if (c % 4 != 0) _exit_program(0);

    if (n == 0)      dsm_store(0, 0, VAL0);   /* DSM(node=0,socket=0) on socket-0 plane */
    else if (n == 1) dsm_store(1, 0, VAL1);   /* DSM(node=0,socket=1) on socket-1 plane */

    sync_wait(0b111);

    if (n == 2) {
        uint32_t a = dsm_load(0, 0);          /* DSM(node=0,socket=0) */
        uint32_t b = dsm_load(1, 0);          /* DSM(node=0,socket=1) */
        emit_read_val(2, 0, VAL0, a, a == VAL0);
        emit_read_val(2, 1, VAL1, b, b == VAL1);
    }

    sync_wait(0b111);
    _exit_program(0);
    return 0;
}
