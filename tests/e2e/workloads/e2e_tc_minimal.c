/* E2E-TC-MINIMAL: Bare-minimum workload to verify CPU can execute instructions.
 *
 * Just writes a sentinel via syscall to prove the CPU started, then exits.
 * Compile:
 *   aarch64-linux-gnu-gcc -static -O0 -g -I. -o e2e_tc_minimal.elf e2e_tc_minimal.c
 */
#include <stdint.h>

/* Minimal syscall wrappers (no headers needed) */
static inline long _syscall3(long num, long a0, long a1, long a2)
{
    register long x8 __asm__("x8") = num;
    register long x0 __asm__("x0") = a0;
    register long x1 __asm__("x1") = a1;
    register long x2 __asm__("x2") = a2;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8), "r"(x1), "r"(x2) : "memory");
    return x0;
}

static inline long _syscall1(long num, long a0)
{
    register long x8 __asm__("x8") = num;
    register long x0 __asm__("x0") = a0;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8) : "memory");
    return x0;
}

#define SYS_WRITE  64
#define SYS_EXIT   93

static const char sentinel[] = "[SENTINEL] CPU started successfully\n";

void _start(void)
{
    _syscall3(SYS_WRITE, 1, (long)sentinel, sizeof(sentinel) - 1);
    _syscall1(SYS_EXIT, 0);

    /* Not reached */
    while (1) {}
}

/* Weak main for compiler compatibility */
__attribute__((weak)) int main(void) { _start(); return 0; }
