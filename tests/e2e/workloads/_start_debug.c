#include <stdint.h>
extern int main(int argc, char **argv);

// Syscall helpers
static long sys_write(int fd, const char *buf, long len) {
    register long x8 __asm__("x8") = 64;  // SYS_WRITE
    register long x0 __asm__("x0") = fd;
    register long x1 __asm__("x1") = (long)buf;
    register long x2 __asm__("x2") = len;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8), "r"(x1), "r"(x2) : "memory");
    return x0;
}
static long sys_exit(long code) {
    register long x8 __asm__("x8") = 93;
    register long x0 __asm__("x0") = code;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8) : "memory");
    __builtin_unreachable();
}

static void puthex(unsigned long v) {
    char buf[19];  // "0x" + 16 hex + \n + null
    buf[0] = 0x30; buf[1] = 0x78; // "0x"
    for (int i = 17; i >= 2; i--) {
        int nib = v & 0xf;
        buf[i] = nib < 10 ? 0x30 + nib : 0x61 + nib - 10;
        v >>= 4;
    }
    buf[18] = 0x0a; // newline
    sys_write(1, buf, 19);
}

void _start(void)
{
    register uint64_t sp __asm__("sp");
    
    // Dump first 8 words on stack
    for (int i = 0; i < 8; i++) {
        puthex(((uint64_t *)sp)[i]);
    }
    puthex(sp);
    
    uint64_t argc = *(uint64_t *)sp;
    char **argv = (char **)(sp + 8);
    
    // Print argc
    char msg[64];
    char *mp = msg;
    char *tmp = (char *)"argc=";
    while (*tmp) *mp++ = *tmp++;
    if (argc >= 10) { *mp++ = 0x30 + (argc/10); }
    *mp++ = 0x30 + (argc % 10);
    *mp++ = 0x0a;
    sys_write(1, msg, mp - msg);
    
    // Print argv[0]
    if (argc > 0) {
        char *s = argv[0];
        int len = 0;
        while (s[len]) len++;
        sys_write(1, s, len);
        sys_write(1, (char *)"\n", 1);
    }
    if (argc > 1) {
        char *s = argv[1];
        int len = 0;
        while (s[len]) len++;
        sys_write(1, s, len);
        sys_write(1, (char *)"\n", 1);
    }
    
    int ret = main((int)argc, argv);
    sys_exit(ret);
}
