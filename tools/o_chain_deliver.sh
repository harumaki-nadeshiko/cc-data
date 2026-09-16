#!/bin/bash
# Host orchestration only. All preparation, validation and execution use Docker.
set -euo pipefail
LOCAL=/mnt/data2/cgc/cc-ep-boundary-serial-20260913/boundary-evidence/o-frozen-20260916
REMOTE=/mnt/data-xfs/cgc/protocol-retirement-fix-20260916-o
SSH=(ssh -i /mnt/data2/cgc/.ssh/id_rsa_np -o BatchMode=yes -o ConnectTimeout=15 cgc@10.129.163.38)
while [ "$(docker inspect o-freeze-build-20260916 --format '{{.State.Running}}')" = true ]; do sleep 30; done
if [ "$(docker inspect o-freeze-build-20260916 --format '{{.State.ExitCode}}')" != 0 ]; then
  scp -i /mnt/data2/cgc/.ssh/id_rsa_np -o BatchMode=yes "$LOCAL/prepare-state.json" "cgc@10.129.163.38:$REMOTE/BUILD_FAILED.json"
  exit 1
fi
scp -i /mnt/data2/cgc/.ssh/id_rsa_np -o BatchMode=yes "$LOCAL/runtime.tar" "$LOCAL/prepare-state.json" "cgc@10.129.163.38:$REMOTE/"
"${SSH[@]}" "docker run --rm --user 0 --network none -v $REMOTE:$REMOTE -w $REMOTE ubcc-dev:ubuntu20.04 python3 -c 'import hashlib,json,pathlib,tarfile; r=pathlib.Path(\".\"); ready=json.loads((r/\"prepare-state.json\").read_text()); h=hashlib.sha256(); f=(r/\"runtime.tar\").open(\"rb\"); [h.update(b) for b in iter(lambda:f.read(1048576),b\"\")]; assert h.hexdigest()==ready[\"archive_sha256\"]; tarfile.open(r/\"runtime.tar\").extractall(r); (r/\"BUILD_READY.json\").write_text(json.dumps(ready))'"
