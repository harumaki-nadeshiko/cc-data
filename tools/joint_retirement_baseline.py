"""Build the same production-bank regression against its pre-fix API body."""
from pathlib import Path
import subprocess
assert Path('/.dockerenv').exists()
root = Path('/work')
out = root / 'boundary-evidence/joint-baseline'
out.mkdir(exist_ok=True)
header = root / 'gem5/src/mem/ruby/protocol/chi/ep/BoundaryAuthorityTable.hh'
text = header.read_text()
start = text.index('    BoundaryResult retireByControl(')
end = text.index('    BoundaryResult setFlags(', start)
# Baseline has no retirement operation: its control completion only changes
# access to R_I and leaves epoch live. This adapter makes that absent API
# executable by the identical production-bank test, not a replacement model.
text = text[:start] + '''    BoundaryResult retireByControl(Token t, uint64_t epoch) {
        auto *e = edit(t);
        if (!e || !epoch || e->epoch != epoch) return BoundaryResult::Stale;
        return BoundaryResult::NotReady;
    }
''' + text[end:]
(out / header.name).write_text(text)
cmd = ['g++', '-std=c++17', '-I'+str(out), '-I.',
       '-Igem5/src/mem/ruby/protocol/chi/ep', '-Iprotocol',
       'tools/ubcc_control_retirement_test.cc', 'protocol/NodeAddressMap.cc',
       '-o', str(out / 'test')]
subprocess.run(cmd, check=True)
result = subprocess.run([str(out / 'test')], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
(out / 'result.log').write_bytes(result.stdout)
assert result.returncode != 0, 'baseline unexpectedly passed'
print('EXPECTED_BASELINE_FAILURE', result.returncode, result.stdout.decode())
