/* TC132: dirty active-set checkpoint with a 64K dirty stream at one Home. */
#include "dsm_access.h"
#include "e2e_common.h"
#ifndef TC132_ACTIVE_LINES
#define TC132_ACTIVE_LINES 8192
#endif
#ifndef TC132_PRESSURE_LINES
#define TC132_PRESSURE_LINES 65536
#endif
#ifndef TC132_READ_STRIDE
#define TC132_READ_STRIDE 512
#endif
#define ACTIVE TC132_ACTIVE_LINES
#define PRESSURE TC132_PRESSURE_LINES
#define ACTIVE_BASE 0x000000u
#define STREAM_BASE 0x1000000u
#define VALUE 0x13200000u
static inline uint32_t active_off(int i) { return ACTIVE_BASE + (uint32_t)i * 64u; }
static inline uint32_t stream_off(int i) { return STREAM_BASE + (uint32_t)i * 64u; }
int main(int argc,char **argv) {
 int n=0,c=0;if(argc>=2)n=parse_int(argv[1]);if(argc>=3)c=parse_int(argv[2]);if(c%4){_exit_program(0);return 0;}emit_e2e_meta(n,"TC132");
 if(n==1){for(int i=0;i<ACTIVE;i++)dsm_store(0,active_off(i),VALUE|(uint32_t)i);emit_phase_done(1,"checkpoint_seed");} sync_wait(7);
 if(n==0){for(int i=0;i<PRESSURE;i++)dsm_store(0,stream_off(i),0x13280000u|(uint32_t)i);emit_phase_done(0,"dirty_stream");} sync_wait(7);
 if(n==2){for(int i=0;i<ACTIVE;i++){uint32_t v=dsm_load(0,active_off(i));if(i%TC132_READ_STRIDE==0)emit_read_val(2,0,VALUE|(uint32_t)i,v,v==(VALUE|(uint32_t)i));}emit_phase_done(2,"checkpoint_recover");} sync_wait(7);_exit_program(0);return 0;
}
