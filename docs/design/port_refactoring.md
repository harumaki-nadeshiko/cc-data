# Port Interface Refactoring Design

## Architecture

Port exposes only: `init(PortParams)`, `send`, `recv`, `allocateSendBuffer`, `terminate`, `emitSync`, `safeTimestamp`. No ZMQ internals visible.

`PortParams` from `EnvLoader` via environment variables: `name`, `moduleId`, `portId`, `localRxEndpoint`, `peerRxEndpoint`.

## Version A (Recommended)

- syncWindow not in PortParams (independent config)
- terminate() sends TERMINATE then local cleanup (immediate, no queue flush)
- RAII TxHandle replaces _sendBufInUse; destructor auto-cancels if not sent
- per-port EnvLoader, fail-fast on any error (rollback all created ports)
- endpoints must be full `ipc://` URLs

## Version B (Full)

- A + bounded drain mode for terminate
- TxHandle has `armMustSend()` debug assert
- PortFactory::createAllOrRollback() two-phase creation
- Launcher sends TERMINATE before kill on error paths

## Files Changed

| File | A Change |
|------|----------|
| `framework/Port.hh` | Add PortParams/PortRuntime/PortConfig, TxHandle, terminate(), closeLocal() |
| `framework/Port.cc` | RAII send slot, future COH_MSG must go to _pending, terminate(immediate) |
| `framework/PortEnvLoader.hh/.cc` (new) | Per-port load PortConfig, validate ipc:// |
| `gem5/.../UBAdapter.hh/.cc` | Use PortEnvLoader, delete busy-poll, TransportMode fixed |
| `tools/ubio/ubio_main.cc` | PortEnvLoader, pollAllPorts(), terminate on error |
| `modules/networksim/networksim_main.cc` | Config-based ports, pollAllPorts(), re-timestamp on dequeue |
