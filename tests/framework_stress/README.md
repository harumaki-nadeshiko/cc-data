# Framework public-interface stress test

This test links only the installed `framework/iface` API and one selected
backend archive. It creates two single-threaded processes/roles (`gem5` and
`ubio`) on canonical `coherence` Port gid 0. To avoid symmetric blocking-send
deadlock, gem5 bursts phase A while ubio receives, then ubio bursts phase B
while gem5 receives. Payload and ACK ordering makes the reversal explicit.

It checks every sequence exactly once and in order, exact timestamps (including
link latency), payload size/pattern/checksum, source/target/request IDs, sync
throttling, `PendingFuture`, and `SafeTimestamp`. Output consists of one result
object per role and a final concise JSON result from the orchestrator.

## Docker-only build and run

Compilation, linking, and executable tests **must run only in Docker image
`ubcc-dev:ubuntu20.04`**. The scripts enforce this and never fall back to the
host. The default exercises the local backend with 100,000 messages each way:

```bash
bash scripts/run_framework_stress.sh
```

Useful test options are forwarded to both roles:

```bash
bash scripts/run_framework_stress.sh \
  --messages 250000 --payload-bytes 1024 --start-timestamp 5000 \
  --timestamp-step 7 --link-latency 2500 --sync-interval 1000000 \
  --timeout-ms 180000
```

Backend/build selection is environment based:

```bash
FRAMEWORK_BACKEND_LIB=build/framework/lib/libframework_real.a \
FRAMEWORK_INCLUDE_DIR=build/framework/include \
FRAMEWORK_BACKEND_CPPFLAGS='-DREAL_BACKEND=1' \
FRAMEWORK_BACKEND_LDFLAGS='-Lvendor/lib -lbackend_dependency' \
FRAMEWORK_RUNTIME_LIBRARY_PATH='vendor/lib' \
FRAMEWORK_LINK_LIBZMQ=0 \
  bash scripts/run_framework_stress.sh --messages 100000
```

Optional `LIBZMQ_INCLUDE_DIR` and `LIBZMQ_LIB_DIR` select non-default ZeroMQ
paths. `FRAMEWORK_LINK_LIBZMQ=auto` (default) links ZeroMQ for an archive named
`libframework_local.a`; set it to `1` or `0` explicitly for another backend.
`FRAMEWORK_RUNTIME_LIBRARY_PATH` adds shared backend dependency directories at
run time.
Paths supplied to these variables must be visible under the mounted workspace
(`/workspace`) in the container; workspace-relative paths are recommended.
If the host library directory is outside the checkout, use
`LIBZMQ_HOST_LIB_DIR=/host/path/to/lib`; the runner mounts it read-only and
sets the corresponding in-container `LIBZMQ_LIB_DIR` automatically.

For a compile-only operation use `scripts/build_framework_stress.sh` with the
same variables. This is still Docker-only.
