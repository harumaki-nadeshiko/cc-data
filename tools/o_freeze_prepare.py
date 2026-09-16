"""Deployment only: snapshot HOME once, build in Docker, export immutable O."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import time

assert Path('/.dockerenv').exists(), 'Docker only'
HOME = Path('/home-source')
ROOT = Path('/o')

def sha(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda: f.read(1048576), b''): h.update(b)
    return h.hexdigest()

def inventory(root):
    return {str(p.relative_to(root)): sha(p) for p in sorted(root.rglob('*')) if p.is_file()}

def save(name, value):
    p = ROOT / name
    q = p.with_suffix('.new')
    q.write_text(json.dumps(value, indent=2)+'\n'); q.replace(p)

try:
    save('prepare-state.json', dict(state='BUILD_O', started_at=time.time()))
    work = ROOT/'build-source'
    work.mkdir()
    dirs = ('framework','modules','protocol','scripts','tests','configs','tools','thirdparty')
    for name in dirs:
        shutil.copytree(HOME/name, work/name, symlinks=False,
                        ignore=shutil.ignore_patterns('__pycache__', '*.swp'))
    shutil.copytree(HOME/'gem5', work/'gem5', symlinks=False,
                    ignore=shutil.ignore_patterns('.git','build','__pycache__','shared_ipc','m5out'))
    shutil.copytree(HOME/'build/framework', work/'build/framework', symlinks=False)
    if (HOME/'build/lib').exists():
        shutil.copytree(HOME/'build/lib', work/'build/lib', symlinks=False)
    else:
        (work/'build/lib').mkdir()
    shutil.copytree(HOME/'gem5/build/ARM', work/'gem5/build/ARM', symlinks=False)
    q = work/'boundary-evidence/remote-launch'; q.mkdir(parents=True)
    shutil.copy2(HOME/'boundary-evidence/remote-launch/queue.json', q/'queue.json')
    # Fingerprint complete source before any compiler is allowed to run.
    source_files = {k:v for k,v in inventory(work).items()
                    if not k.startswith(('build/','gem5/build/'))}
    save('prepared-source.json', dict(files=source_files, captured_at=time.time()))
    env = dict(os.environ, CCACHE_DISABLE='1', TMPDIR=str(ROOT/'tmp'))
    (ROOT/'tmp').mkdir(exist_ok=True)
    with (ROOT/'build.log').open('w') as log:
        for cmd, cwd in [(['bash','scripts/build_framework.sh'], work),
                         (['bash','scripts/build_all.sh'], work),
                         (['scons','build/ARM/gem5.opt','-j8'], work/'gem5')]:
            log.write('COMMAND '+repr(cmd)+'\n'); log.flush()
            subprocess.run(cmd, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
        subprocess.run(['ldd',str(work/'build/bin/ubio')], env=env,
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    after = {k:sha(work/k) for k in source_files}
    assert after == source_files, 'compiler changed captured source'
    source = ROOT/'source'; source.mkdir()
    for name in dirs + ('gem5','boundary-evidence'):
        shutil.copytree(work/name, source/name, symlinks=False,
                        ignore=shutil.ignore_patterns('build','__pycache__'))
    for name in ('build/bin','build/lib','build/framework'):
        shutil.copytree(work/name, source/name, symlinks=False)
    (source/'build/runs').mkdir()
    (source/'gem5/build/ARM').mkdir(parents=True)
    shutil.copy2(work/'gem5/build/ARM/gem5.opt',source/'gem5/build/ARM/gem5.opt')
    files = inventory(source)
    manifest = dict(files=files, source_version=hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest(),
                    state='O_BUILD_PASS_NOT_RELEASE', build_verified=True,
                    prepared_source_sha256=sha(ROOT/'prepared-source.json'), built_at=time.time())
    save('candidate-manifest.json', manifest)
    with tarfile.open(ROOT/'runtime.tar','w') as tar:
        tar.add(source,arcname='source')
        for name in ('candidate-manifest.json','prepared-source.json','build.log'):
            tar.add(ROOT/name,arcname=name)
    save('prepare-state.json',dict(state='READY_O',source_version=manifest['source_version'],
                                  archive_sha256=sha(ROOT/'runtime.tar'), completed_at=time.time()))
except Exception as e:
    save('prepare-state.json',dict(state='FAILED',phase='BUILD_O',error=repr(e),time=time.time()))
    raise
