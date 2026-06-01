#include <stdint.h>
extern int main(int argc, char **argv);
static long sys_write(int fd, const char *buf, long len) {
    register long x8 __asm__("x8") = 64;
    register long x0 __asm__("x0") = fd;
    register long x1 __asm__("x1") = (long)buf;
    register long x2 __asm__("x2") = len;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8), "r"(x1), "r"(x2) : "memory");
    return x0;
}
static void puthex(unsigned long v) {
    char buf[19];
    buf[0]=0x30;buf[1]=0x78;
    for(int i=17;i>=2;i--){ int nib=v&0xf; buf[i]=nib<10?0x30+nib:0x61+nib-10; v>>=4; }
    buf[18]=0x0a;
    sys_write(1,buf,19);
}
void _start(void)
{
    register uint64_t x0_reg __asm__("x0");
    register uint64_t x1_reg __asm__("x1");
    register uint64_t sp_reg __asm__("sp");
    
    puthex(sp_reg);
    puthex(x0_reg);
    puthex(x1_reg);
    
    // Check if x1 points to a string (argv[0])
    if (x1_reg > 0x1000) {
        char c = *(char*)x1_reg;
        if (c) { sys_write(1, (char*)"x1=", 3); sys_write(1, (char*)x1_reg, 40); sys_write(1, (char*)"\n", 1); }
    }
    
    // Search for the binary name string near sp
    // Look 256 bytes below and above sp for non-zero data
    uint64_t base = sp_reg & ~0xffULL;
    for (int off = 0; off < 512; off += 8) {
        uint64_t v = *(uint64_t*)(base + off);
        if (v != 0) {
            puthex(base + off); puthex(v);
        }
    }
    
    // Now try to find argc/argv differently: search for "e2e_tc3" string
    // Just use x1 as argv if it looks like a pointer
    char **argv = (char**)x1_reg;
    if (x1_reg > 0x1000) {
        // Try reading argv[0]
        char *s0 = argv[0];
        if ((uint64_t)s0 > 0x1000) {
            puthex((uint64_t)s0);
        }
    }
}
