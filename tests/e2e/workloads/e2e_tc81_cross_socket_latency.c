/* TC81: Cross-socket read — same node, 2 sockets. Core@S0 reads DSM@S1.
 * 4 reads each for same-socket and cross-socket. Requires --2s.
 */
#include "e2e_common.h"
#define NUM_NODES 3
#define SEG_SIZE 0x8000000ULL
#define DSM_VA_BASE 0xffff00000000ULL
static inline volatile uint32_t *dsm2(int h,int s,uint32_t o){
    return (volatile uint32_t*)(DSM_VA_BASE+((uint64_t)h*2+s)*SEG_SIZE+o);
}
int main(int argc,char**argv){
    int nid=0; if(argc>=2)nid=parse_int(argv[1]);
    if(nid==0){__asm__("str %w0,[%1]"::"r"(0x810000AAu),"r"(dsm2(0,0,0x6100)));__asm__("str %w0,[%1]"::"r"(0x810000BBu),"r"(dsm2(0,1,0x6100)));}
    sync_wait(0b111);
    if(nid==0){uint32_t g;for(int i=0;i<4;i++)__asm__("ldr %w0,[%1]":"=r"(g):"r"(dsm2(0,0,0x6100)));for(int i=0;i<4;i++)__asm__("ldr %w0,[%1]":"=r"(g):"r"(dsm2(0,1,0x6100)));emit_read_val(nid,0,0x810000AAu,g,1);}
    sync_wait(0b111);_exit_program(0);return 0;
}
