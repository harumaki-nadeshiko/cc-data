#!/usr/bin/env python3
"""Inject debug fprintf into generated Cache_Controller.cc"""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "build/ARM/mem/ruby/protocol/CHI/Cache_Controller.cc"
with open(path) as f:
    content = f.read()

counts = []

# 1. RegisterEPRNF_OnSharedHint
old1 = '    if ((((*in_msg_ptr)).m_m_shared_hint && (m_epRnfMachineVersion >= (0)))) {'
debug1 = '\n    std::fprintf(stderr, "[EPRNF-REG] shared_hint=%d epRnfVer=%d\\n", (int)(*in_msg_ptr).m_m_shared_hint, m_epRnfMachineVersion);\n'
c = content.count(old1)
content = content.replace(old1, debug1 + old1, 1)
counts.append(f"EPRNF-REG: {c} found, 1 replaced")

# 2. Initiate_CleanUnique — willSnoop check
old2 = '    if ((((((*m_tbe_ptr).m_dir_sharers).count()) > (1)) || (((((*m_tbe_ptr).m_dir_sharers).count()) > (0)) && (! (((*m_tbe_ptr).m_dir_sharers).isElement((*m_tbe_ptr).m_requestor)))))) {'
debug2 = '\n    std::fprintf(stderr, "[CU-INIT] willSnoop=1 count=%d isReq=%d\\n", (int)(((*m_tbe_ptr).m_dir_sharers).count()), (int)(((*m_tbe_ptr).m_dir_sharers).isElement((*m_tbe_ptr).m_requestor)));\n'
c = content.count(old2)
content = content.replace(old2, debug2 + old2, 1)
counts.append(f"CU-INIT: {c} found, 1 replaced")

# 3. HNF-CU-START 
old3 = '    if (((*m_tbe_ptr).m_reqType == CHIRequestType_CleanUnique)) {'
debug3 = '\n        std::fprintf(stderr, "[CU-DEBUG] addr=%#lx sharers=%d\\n", (unsigned long)addr, (int)(((*m_tbe_ptr).m_dir_sharers).count()));\n'
c = content.count(old3)
content = content.replace(old3, debug3 + old3, 1)
counts.append(f"CU-DEBUG: {c} found, 1 replaced")

with open(path, "w") as f:
    f.write(content)
for s in counts:
    print(s, file=sys.stderr)
print("Done", file=sys.stderr)
