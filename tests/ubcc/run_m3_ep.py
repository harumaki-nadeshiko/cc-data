"""
M3 Verification: EP Controller SimObject Instantiation
"""
import m5
from m5.objects import *

print("M3: Testing EP controller SimObject creation...")

# EPRNFController
ep1 = EPRNFController()
ep1.node_id = 0
ep1.version = 100
print(f"  EPRNFController created: type={type(ep1).__name__}, node_id={ep1.node_id}")

# EPSNFController
ep2 = EPSNFController()
ep2.node_id = 0
ep2.version = 101
print(f"  EPSNFController created: type={type(ep2).__name__}, node_id={ep2.node_id}")

print("M3: PASSED - EP controllers instantiate correctly")
