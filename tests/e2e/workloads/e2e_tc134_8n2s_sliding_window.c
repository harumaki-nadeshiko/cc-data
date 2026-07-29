/* TC134: 8n2s streaming analytics. Eight socket0 writers pressure Home0 with
 * 64K lines; socket1 planes cache and later reuse a 4K shared window. */
#include "dsm_access.h"
#include "e2e_common.h"
#define NODES 8
#define SOCKETS 2
#define ALL_PLANES 0xFFFFu
#define HOT 4096
#define WRITER_LINES 8192
#define WINDOW_BASE 0x000000u
#define STREAM_BASE 0x1000000u
#define VALUE 0x13400000u
static inline uint32_t hot_off(int i) { return WINDOW_BASE + (uint32_t)i * 64u; }
static inline uint32_t stream_off(int node, int i) { return STREAM_BASE + (uint32_t)(node * WRITER_LINES + i) * 64u; }
int main(int argc,char **argv) {
 int n=0,c=0;if(argc>=2)n=parse_int(argv[1]);if(argc>=3)c=parse_int(argv[2]);int sock=c%4;if(sock>=SOCKETS){_exit_program(0);return 0;}emit_e2e_meta(n,"TC134");emit_timer_selftest(n*SOCKETS+sock);
 if(n==0&&sock==0){for(int i=0;i<HOT;i++)dsm_store(0,hot_off(i),VALUE|(uint32_t)i);emit_phase_done(0,"window_seed");} sync_wait(ALL_PLANES,SOCKETS);
 if(sock==1){for(int i=0;i<HOT;i++)(void)dsm_load(0,hot_off(i));emit_phase_done(n*SOCKETS+sock,"window_share");} sync_wait(ALL_PLANES,SOCKETS);
  if(sock==0){for(int i=0;i<WRITER_LINES;i++){dsm_store(0,stream_off(n,i),0x13480000u|((uint32_t)n<<16)|(uint32_t)i);if((i+1)%1024==0)emit_progress(n*SOCKETS+sock,"window_pressure",i+1);}if(n==0)emit_phase_done(0,"window_pressure");} sync_wait(ALL_PLANES,SOCKETS);
  if(sock==1){uint64_t t0=read_cntvct_el0();for(int i=0;i<HOT;i++){uint32_t v=dsm_load(0,hot_off(i));if((i+1)%512==0)emit_progress(n*SOCKETS+sock,"window_reuse",i+1);if(n>0&&i==n*512)emit_read_val(n,0,VALUE|(uint32_t)i,v,v==(VALUE|(uint32_t)i));}emit_guest_timer(n*SOCKETS+sock,"post_pressure_window_reuse",HOT,read_cntvct_el0()-t0);if(n==0)emit_phase_done(1,"window_reuse");} sync_wait(ALL_PLANES,SOCKETS);_exit_program(0);return 0;
}
