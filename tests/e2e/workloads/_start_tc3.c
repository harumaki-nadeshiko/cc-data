// Minimal _start for gem5 SE mode: reads argc from stack, calls main
#include <stdint.h>

extern int main(int argc, char **argv);

void _start(void)
{
    register uint64_t sp __asm__("sp");
    // When gem5 SE mode initializes, the stack has:
    //   sp+0: argc (as uint64_t)
    //   sp+8: argv[0]
    //   sp+16: argv[1] ...
    // We read argc from [sp] and set up x0/x1 for main.
    uint64_t argc = *(uint64_t *)sp;
    char **argv = (char **)(sp + 8);
    int ret = main((int)argc, argv);
    // exit syscall
    __asm__ volatile("mov x0, %0\n" "mov x8, #93\n" "svc #0\n"
                     : : "r"((long)ret) : "x0", "x8");
    __builtin_unreachable();
}
