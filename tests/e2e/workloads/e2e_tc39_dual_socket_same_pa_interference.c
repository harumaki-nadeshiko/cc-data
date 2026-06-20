/* TC39: dual-socket same-PA interference (home socket fixed to plane-1). */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOME_SOCKET1_SEG 1   /* with NUM_SOCKETS=2: node0/socket1 => seg 1 */
#define HOME_SOCKET0_SEG 0
#define X_OFF             0x3900
#define Y_OFF             0x3940

static inline void emit_tc39_route(int node_id, int home_socket, int req_socket)
{
    char buf[220]; int p = 0;
    char *s = (char *)"[TC39_ROUTE] node=";
    while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, node_id);
    s = (char *)" homeSocket="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, home_socket);
    s = (char *)" reqSocket="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, req_socket);
    buf[p++] = '\n';
    _raw_write(buf, p);
}

int main(int argc, char **argv)
{
    int node_id = 0, cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);

    int primary = (cpu_index % 4 == 0);
    if (!primary) _exit_program(0);
    emit_e2e_meta(node_id, "TC39");

    int fail = 0;
    const uint32_t v0 = 0x3900A011u;
    const uint32_t v2 = 0x3900B022u;

    if (node_id == 0) dsm_store(HOME_SOCKET1_SEG, X_OFF, v0);
    sync_wait(0b111);

    if (node_id == 1) {
        uint32_t a = dsm_load(HOME_SOCKET1_SEG, X_OFF);
        emit_read_val(node_id, HOME_SOCKET1_SEG, v0, a, a == v0);
        emit_tc39_route(node_id, 1, 0);
        if (a != v0) fail++;
    }
    sync_wait(0b111);

    if (node_id == 2) {
        dsm_store(HOME_SOCKET1_SEG, X_OFF, v2);
        emit_tc39_route(node_id, 1, 1);
    }
    if (node_id == 0) {
        dsm_store(HOME_SOCKET0_SEG, Y_OFF, 0x3900CC33u); /* unrelated cross-plane traffic */
    }
    sync_wait(0b111);

    {
        uint32_t got = dsm_load(HOME_SOCKET1_SEG, X_OFF);
        emit_read_val(node_id, HOME_SOCKET1_SEG, v2, got, got == v2);
        if (got != v2) fail++;
    }

    sync_wait(0b111);
    _exit_program(fail ? 1 : 0);
    return 0;
}
