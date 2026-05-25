/* TC-T0-3: Multi-Thread Same Node Count
 *
 * Node0 starts 2 threads but only 1 calls the barrier.
 * Node1 and Node2 each have 1 thread that calls the barrier.
 * Total: 3 threads call the barrier (mask 0b111, popcount=3).
 * The 4th thread on Node0 that does NOT call the barrier
 * should not block or affect barrier convergence.
 *
 * Compile:
 *   aarch64-linux-gnu-gcc -static -o tc_t0_3_caller tests/sync_wait/tc_t0_3.c -DCALLER=1
 *   aarch64-linux-gnu-gcc -static -o tc_t0_3_noncaller tests/sync_wait/tc_t0_3.c -DCALLER=0
 *
 * Run:
 *   ./tc_t0_3_caller <node_id>     (for nodes 0-2, caller threads)
 *   ./tc_t0_3_noncaller <node_id>  (for node 0 extra non-caller thread)
 */

#include <stdint.h>

#define SYS_WRITE      64
#define SYS_SYNC_WAIT  436
#define SYS_EXIT       93

#ifndef CALLER
#define CALLER 1
#endif

static long syscall3(long num, long arg0, long arg1, long arg2) {
    register long x8 __asm__("x8") = num;
    register long x0 __asm__("x0") = arg0;
    register long x1 __asm__("x1") = arg1;
    register long x2 __asm__("x2") = arg2;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8), "r"(x1), "r"(x2) : "memory");
    return x0;
}

static long syscall1(long num, long arg0) {
    register long x8 __asm__("x8") = num;
    register long x0 __asm__("x0") = arg0;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8) : "memory");
    return x0;
}

static void print_str(int fd, const char *s) {
    int len = 0; while (s[len]) len++;
    syscall3(SYS_WRITE, fd, (long)s, (long)len);
}

static void print_int(int fd, int val) {
    char buf[16]; int pos=0;
    if (val==0) { buf[pos++]='0'; }
    else {
        char tmp[16]; int tp=0;
        unsigned u=(unsigned)(val<0?-val:val);
        while(u){tmp[tp++]='0'+(u%10);u/=10;}
        if(val<0)buf[pos++]='-';
        while(tp)buf[pos++]=tmp[--tp];
    }
    buf[pos++]='\n';
    syscall3(SYS_WRITE,fd,(long)buf,(long)pos);
}

static int parse_int(const char *s) {
    int v=0; while(*s>='0'&&*s<='9'){v=v*10+(*s-'0');s++;} return v;
}

int main(int argc, char **argv) {
    int node_id = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);

    if (CALLER) {
        print_str(1, "BEFORE_BARRIER CALLER node=");
        print_int(1, node_id);

        /* mask 0b111 = popcount 3, expecting 3 caller threads */
        syscall1(SYS_SYNC_WAIT, 0b111);

        print_str(1, "AFTER_BARRIER CALLER node=");
        print_int(1, node_id);
    } else {
        print_str(1, "NON_CALLER node=");
        print_int(1, node_id);
        /* This thread does NOT call Sync_Wait.
         * It must not block the barrier convergence. */
        print_str(1, "NON_CALLER_DONE node=");
        print_int(1, node_id);
    }

    syscall1(SYS_EXIT, 0);
    return 0;
}
