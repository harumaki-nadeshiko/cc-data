"""Copy only tested runtime/build/test sources into remote-tip worktrees."""
import hashlib
import json
from pathlib import Path
import shutil

root = Path('/candidate')
records = {}
groups = {
    'publication-gem5': ('gem5', ('src', 'configs')),
    'publication-main': ('', ('framework', 'modules', 'protocol', 'configs', 'config', 'tests/e2e')),
}
extensions = {'.cc', '.hh', '.h', '.c', '.cpp', '.py', '.sm', '.slicc', '.sh', '.json'}
for dest, (prefix, directories) in groups.items():
    source = root / prefix
    target = root / dest
    for directory in directories:
        for path in (source / directory).rglob('*'):
            if not path.is_file() or path.is_symlink():
                continue
            if '.git' in path.parts or '__pycache__' in path.parts:
                continue
            if path.suffix not in extensions and path.name not in {'SConscript', 'SConstruct'}:
                continue
            relative = path.relative_to(source)
            output = target / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, output)
            records[dest + '/' + str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    # Exact source deletion semantics for tracked runtime files absent from the
    # candidate (do not restore retired protocol entry points).
    for directory in directories:
        for path in (target / directory).rglob('*'):
            if path.is_file() and not (source / path.relative_to(target)).exists():
                if path.suffix in extensions or path.name in {'SConscript', 'SConstruct'}:
                    path.unlink()

build_scripts = ['build_all.sh', 'build_framework.sh', 'build_ubio.sh',
                 'build_networksim.sh', 'build_barrier.sh', 'compile_workload.sh']
for name in build_scripts:
    relative = Path('scripts') / name
    shutil.copy2(root / relative, root / 'publication-main' / relative)
    records['publication-main/' + str(relative)] = hashlib.sha256((root / relative).read_bytes()).hexdigest()
for pattern in ('boundary*test.cc', 'control_credits_test.cc', 'compact_boundary_frames_test.cc',
                'legacy_page_window_test.cc', 'bounded_authority_test.cc', 'park_validation*.py',
                'park_control_reservation_test.cc', 'ep_boundary_lifecycle*.hh',
                'ep_boundary_lifecycle_test.cc', 'ha_conditional_release_test.cc'):
    for path in (root / 'tools').glob(pattern):
        output = root / 'publication-main/tools' / path.name
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, output)
        records['publication-main/tools/' + path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
for relative, expected in records.items():
    assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == expected
(root / 'boundary-evidence/publication-source-overlay.json').write_text(json.dumps(records, indent=2) + '\n')
print('matched', len(records), 'source files; protected docs excluded')
