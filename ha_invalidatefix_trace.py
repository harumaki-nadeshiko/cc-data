"""Stream generated CHI evidence; retain full logs and compact summary."""
import collections
import json
from pathlib import Path
import re

C=Path('/campaign')
d=C/'remote-gates/3n1s/tc229/p0/ha-vi'
counts=collections.Counter()
examples=[]
stales=[]
for p in d.rglob('*.log'):
    with p.open(errors='replace') as f:
        for line in f:
            if '[INVALIDATE-BARRIER]' in line:
                counts['barriers']+=1
                assert 'sharers=0 owner=0 valid=0 pending=0' in line
                if len(examples)<6:
                    examples.append(line.strip())
            if '[HNF-CU-COMPUC]' in line and 'stale=1' in line:
                counts['stale_comp']+=1
            if 'CleanUnique_Stale' in line:
                stales.append(line.strip())
            if 'SnpCleanInvalid' in line:
                counts['snp_clean_invalid_log_records']+=1
            if 'SnpRespData_I_PD' in line:
                counts['dirty_snoop_data_log_records']+=1
            if 'event: CleanUnique,' in line and 'hnf_' in line:
                counts['hnf_clean_unique_transitions']+=1
            if 'panic:' in line or 'fatal:' in line:
                counts['fatal']+=1
assert counts['barriers']==48,counts
assert counts['stale_comp']==counts['fatal']==0,counts
assert counts['snp_clean_invalid_log_records']>0
assert all('system.cpu0.l2:' in line and 'addr:' in line for line in stales)
(C/'trace-evidence.json').write_text(json.dumps(dict(counts=counts,examples=examples,
    cpu_stale_retained=stales,
    scope='TC229 3N1S P0 generated trace, not exhaustive protocol proof'),indent=2)+'\n')
print(json.dumps(counts),flush=True)
