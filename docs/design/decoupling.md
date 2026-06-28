# Gem5 Decoupling Design

## Architecture

Each node = 1 Gem5 process. UBIOModule (routing/ports) + UBCCController (directory/arbitration) both in modules/ubiomodule/. Gem5 side: zero UBCC/UBIOModule code. All cross-node communication via Port messages only.

## Version A (Recommended)

### Gem5 Changes
- `EPBackend`: remove local UBCC dependency; retain txn-level message bridge only
- `UBAdapter`: message codec + pending completion + Port polling only; delete router/local UBCC
- `EPRNFController`: recovery snoop/recall to real ReadShared/ReadUnique paths
- `CHI-cache-*.sm`: EP-RNF excluded from snoop priority, non-Fwd fallback
- `CHI_ubcc_framework.py`: no UBIOModule instantiation
- Delete forward-includes: `#include "UBCCController.hh"` from EPBackend.cc, UBAdapter.cc

### modules/ubiomodule/
- `UBIOModule`: ports, routing, transit forwarding, timed queue
- `UBCCController`: directory state (with embedded ResidentDir), arbitration, single-flight BUSY(-1), fanout
- `ResidentDir`: cache-line granularity, unchanged semantics
- `CoherenceMessage`: txn-level envelope (txnId, src/dst node+port, addr, reqType, data)

### Message Flow
```
EPSNF → EPBackend → UBAdapter → Port → ubio(UBIOModule) → UBCCController
```
All gem5↔ubio via Port messages. UBCC internal: per-address single outstanding, BUSY on conflict, first-writer-wins arbitration.

## Version B (Full)
- A + physical deletion of all UBCC/UBIOModule files from gem5 tree
- Backstore also message-based (read/write round-trips via Port)
- Standalone ubio process with full protocol
- Cross-socket transit rewriting in UBIOModule
