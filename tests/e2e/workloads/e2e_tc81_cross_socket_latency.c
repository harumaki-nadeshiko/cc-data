/* TC81: Cross-socket DSM write/read — same node, 2 sockets.
 * Node 0 writes to both DSM(s0) and DSM(s1), barrier, reads both back.
 */
#include "e2e_common.h"
#define NUM_NODES 3
#define SEG_SIZE 0x8000000ULL
#define DSM_VA_BASE 0xFFFFC8000000ULL
static inline volatile uint32_t *dsm2(int h,int s,uint32_t o){
    return (volatile uint32_t*)(DSM_VA_BASE+((uint64_t)h*2+s)*SEG_SIZE+o);
}
int main(int argc,char**argv){
    int nid=0; if(argc>=2)nid=parse_int(argv[1]);
    if(nid!=0){_exit_program(0);return 0;}
    __asm__("str %w0,[%1]"::"r"(0x810000AAu),"r"(dsm2(0,0,0x6100)));
    __asm__("str %w0,[%1]"::"r"(0x810000BBu),"r"(dsm2(0,1,0x6100)));
    uint32_t g0,g1;
    uint64_t t0 = read_cntvct_el0();
    __asm__("ldr %w0,[%1]":"=r"(g0):"r"(dsm2(0,0,0x6100)));
    __asm__("ldr %w0,[%1]":"=r"(g1):"r"(dsm2(0,1,0x6100)));
    emit_guest_timer(nid, "cross_socket_read", 2,
                     read_cntvct_el0() - t0);
    int ok=(g0==0x810000AAu&&g1==0x810000BBu);
    emit_read_val(nid,0,0x810000AAu,g0,ok);
    _exit_program(ok?0:1);return 0;
}
