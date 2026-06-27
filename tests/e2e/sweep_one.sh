#!/bin/bash
# TC2 parameter sweep — run inside Docker
# Usage: EVICT_SIZE_KB=256 EVICT_NOPS=0 bash sweep_one.sh

set -e
ROOT_DIR=/workspace/gem5
SRC="$ROOT_DIR/tests/e2e/workloads/e2e_tc2_remote_read.c"
ELF="$ROOT_DIR/tests/e2e/workloads/e2e_tc2_remote_read.elf"
WDIR="$ROOT_DIR/tests/e2e/workloads"

# Get original source from git (remove any eviction mods)
git -C "$ROOT_DIR" show HEAD:tests/e2e/workloads/e2e_tc2_remote_read.c > "$SRC.orig"

# Insert eviction code right before sync_wait
python3 -c "
import sys
src = open('$SRC.orig').read()
# Insert after 'if (primary) emit_after_wr(node_id, 1, val);' and before '    }' (closing if node_id==0)
old = '''        if (primary) emit_after_wr(node_id, 1, val);
    }'''
new = '''        if (primary) emit_after_wr(node_id, 1, val);

        /* Cache eviction sweep — parameters from sed-replace */
        {
            static uint8_t buf[EVICT_BUF_SZ] __attribute__((aligned(64)));
            uint8_t tmp = 0;
            int nlines = EVICT_BUF_SZ / 64;
            for (int i = 0; i < nlines; i++) {
                uint32_t off = i * 64;
                buf[off] ^= 0xA5;
                tmp ^= buf[off];
                if (EVICT_NOPS_VAL > 0 && (i + 1) % 256 == 0) {
                    asm volatile(\"dsb sy\" ::: \"memory\");
                    for (int n = 0; n < EVICT_NOPS_VAL; n++)
                        asm volatile(\"nop\");
                }
            }
            if (EVICT_NOPS_VAL > 0) {
                asm volatile(\"dsb sy\" ::: \"memory\");
                for (int n = 0; n < EVICT_NOPS_VAL; n++)
                    asm volatile(\"nop\");
            }
            asm volatile(\"\" :: \"r\"(tmp) : \"memory\");
        }
    }'''
src = src.replace(old, new)
open('$SRC', 'w').write(src)
"

# Replace EVICT_BUF_SZ and EVICT_NOPS_VAL with actual values
sed -i "s/EVICT_BUF_SZ/${EVICT_SIZE_KB} * 1024/g" "$SRC"
sed -i "s/EVICT_NOPS_VAL/${EVICT_NOPS}/g" "$SRC"

# Compile
rm -f "$ELF"
cd "$WDIR"
aarch64-linux-gnu-gcc -static -O0 -g -I. -o "$ELF" "$SRC" 2>&1
if [ ! -f "$ELF" ]; then
    echo "COMPILE_FAILED"
    exit 1
fi

# Run
bash "$ROOT_DIR/tests/e2e/run_multi.sh" 2 2>&1 | grep -E "TC2|PASSED|FAILED|TIMEOUT|CRASHED"
