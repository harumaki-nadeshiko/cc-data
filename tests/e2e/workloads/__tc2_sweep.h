static void _tc2_sweep(void) {
    // 10 lines at 32KB stride → all map to same L2 set
    // Critical line already occupies 1 way → 10 more > 8-way → evict from L2
    static uint8_t _buf[327680] __attribute__((aligned(64)));
    uint8_t tmp = 0;
    int stride = 32768; // 512 sets * 64B = 32KB → same L2 set
    for (int i = 0; i < 10; i++) {
        _buf[i * stride] ^= 0xA5;
        tmp ^= _buf[i * stride];
    }
    asm volatile("" :: "r"(tmp) : "memory");
}
