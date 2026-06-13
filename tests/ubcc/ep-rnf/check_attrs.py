import sys
sys.path.insert(0, "gem5/build/ARM")
sys.path.insert(0, "gem5/src/python")
import m5
from m5.objects import EPRNFController
attrs = [a for a in dir(EPRNFController) if any(x in a.lower() for x in ('machine', 'version', 'id', 'get'))]
print("EPRNFController attrs:", attrs)
if hasattr(EPRNFController, 'getMachineID'):
    print("getMachineID EXISTS")
else:
    print("getMachineID MISSING")
if hasattr(EPRNFController, 'getVersion'):
    print("getVersion EXISTS")
else:
    print("getVersion MISSING")    
if hasattr(EPRNFController, 'm_machineID'):
    print("m_machineID EXISTS")
else:
    print("m_machineID MISSING")
