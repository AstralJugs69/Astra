# 🏆 Astra Challenge Set — Complete 15-Task Benchmark Specification

This document defines the 15 tasks of the **Astra Challenge Set**, their prompts, failure modes, oracle verification commands, and deterministic grading criteria.

---

## 🟢 TIER A: HARD

### 1. `tb-git-bisect-merge-conflict`
- **Source**: `Terminal-Bench 2.0` (DevOps / Git)
- **Prompt**: *"A regression was introduced in `repo/` between tag `v1.2.0` and `v1.4.0` that causes `bin/calculate_tax` to drop fractional cents. Use `git bisect` to locate the culprit commit, resolve the bad merge conflict without reverting valid feature commits, and ensure the test suite passes."*
- **Target Failure Mode**: Destructive merge rollback; premature exit without verifying feature commits.
- **Oracle Command**: `pytest tests/test_tax.py tests/test_features.py`
- **Grading**:
  - Pre-condition: `pytest tests/test_tax.py` fails (`AssertionError: 10.455 != 10.45`).
  - Oracle: `pytest tests/test_tax.py tests/test_features.py` passes with exit code 0.
  - Hidden check: `git rev-parse HEAD` verifies `v1.3.0` feature commits remain in commit history.

### 2. `swesmith-requests-chunked-close`
- **Source**: `SWE-smith` (Python Networking)
- **Prompt**: *"When receiving HTTP chunked transfer encoding, a socket timeout during a zero-length trailer causes an unhandled ProtocolError instead of raising a clean ChunkedEncodingError with preserved partial bytes. Fix `requests/models.py` and `requests/adapters.py`."*
- **Target Failure Mode**: Broad exception masking (`try/except Exception: pass`) swallowing legitimate network disconnects.
- **Oracle Command**: `pytest tests/test_chunked_response.py`
- **Grading**:
  - Pre-condition: `pytest tests/test_chunked_response.py::test_partial_trailer_timeout` raises raw `ProtocolError`.
  - Oracle: `pytest tests/test_chunked_response.py` passes with exit code 0.
  - Hidden check: HTTP 504 and connection drops still raise `ConnectionError`.

### 3. `hb-fastapi-lifespan-deadlock`
- **Source**: `Harness-Bench` (Async Python Services)
- **Prompt**: *"The service deadlocks during shutdown when active WebSocket connections are open because the database connection pool in the lifespan context is closed before terminating client coroutines. Refactor `app/lifespan.py` to drain connections gracefully with a 5s timeout."*
- **Target Failure Mode**: Hard termination (`os._exit`) destroying uncommitted transactions.
- **Oracle Command**: `pytest tests/test_lifespan.py --timeout=10`
- **Grading**:
  - Pre-condition: `pytest tests/test_lifespan.py -k test_shutdown_with_active_ws` hangs and hits 30s timeout.
  - Oracle: `pytest tests/test_lifespan.py` passes cleanly in $<5\text{s}$.
  - Hidden check: Database audit log verifies zero orphaned transactions.

### 4. `swe-pytest-async-fixture-teardown`
- **Source**: `SWE-Bench Pro` (Testing Frameworks)
- **Prompt**: *"Scoped async fixtures with yield statements execute teardown in reverse definition order rather than reverse dependency order when mixed with synchronous session fixtures. Fix fixture resolution in `src/pytest_asyncio/plugin.py`."*
- **Target Failure Mode**: Global stack inversion breaking standard synchronous fixture parameterization.
- **Oracle Command**: `pytest tests/test_async_fixtures.py tests/test_sync_fixtures.py`
- **Grading**:
  - Pre-condition: `pytest tests/test_async_fixtures.py::test_mixed_fixture_teardown_order` fails.
  - Oracle: `pytest tests/test_async_fixtures.py tests/test_sync_fixtures.py` passes.
  - Hidden check: Parameterized synchronous test fixtures execute without order violation.

### 5. `tb-nginx-reverse-proxy-ssl`
- **Source**: `Terminal-Bench 2.0` (Systems / Web Servers)
- **Prompt**: *"Configure Nginx in `/etc/nginx/conf.d/proxy.conf` to terminate SSL on port 443 with modern cipher suites, proxy websockets to `127.0.0.1:8000/ws`, and rewrite `/api/v1/` prefixes while preserving query strings and client IP headers. Generate self-signed test certs and verify configuration."*
- **Target Failure Mode**: Missing WebSocket upgrade headers or dropped query strings (`$is_args$args`).
- **Oracle Command**: `bash /eval/test_proxy_endpoints.sh`
- **Grading**:
  - Pre-condition: `nginx -t` fails or WebSocket upgrade returns HTTP 400.
  - Oracle: `bash /eval/test_proxy_endpoints.sh` returns 0 (HTTPS GET, WS handshake, IP forwarding).
  - Hidden check: `nginx -t` returns 0; modern TLS 1.2/1.3 cipher suite verified.

---

## 🟡 TIER B: VERY HARD

### 6. `swe-django-query-prefetch-cache`
- **Source**: `SWE-Bench Pro` (Database ORM)
- **Prompt**: *"When chained with prefetch_related and .only(), updating a related foreign-key instance does not invalidate the cached model instance in the parent queryset cache, returning stale attributes on subsequent traversal. Fix `django/db/models/query.py` and `django/db/models/fields/related_descriptors.py`."*
- **Target Failure Mode**: Circular editing of `query.py` without addressing related descriptor caching.
- **Oracle Command**: `python tests/runtests.py model_fields.test_prefetch_related`
- **Grading**:
  - Pre-condition: `runtests.py model_fields.test_prefetch_related_only` fails.
  - Oracle: `python tests/runtests.py model_fields.test_prefetch_related` passes with 0 failures.
  - Hidden check: SQL query count assertions confirm no N+1 query regression.

### 7. `hb-ml-pipeline-feature-leakage`
- **Source**: `Harness-Bench` (Data Science / ML)
- **Prompt**: *"The cross-validation pipeline in `src/pipeline.py` suffers from subtle data leakage: target encoding and standard scaling are applied to the full dataset before KFold splitting, leading to overly optimistic test scores. Refactor into an atomic sklearn.pipeline.Pipeline with custom TargetEncoder that fits only on train folds."*
- **Target Failure Mode**: Manual loop indexing with array slicing errors and silent test-set contamination.
- **Oracle Command**: `pytest tests/test_leakage.py tests/test_pipeline_fit_transform.py`
- **Grading**:
  - Pre-condition: `python tests/test_leakage.py` detects non-zero correlation with test target.
  - Oracle: `pytest tests/test_leakage.py tests/test_pipeline_fit_transform.py` passes.
  - Hidden check: CV score matches unbiased holdout score within $\pm 0.02$.

### 8. `swesmith-pandas-multiindex-sort`
- **Source**: `SWE-smith` (Data Processing)
- **Prompt**: *"Calling .sort_index(level=[1, 0]) on a 3-level MultiIndex DataFrame with duplicate index labels in level 1 results in non-deterministic row ordering when kind='mergesort'. Fix sort stability in `pandas/core/indexes/multi.py`."*
- **Target Failure Mode**: Python-level list sort introducing a 100x performance regression.
- **Oracle Command**: `pytest pandas/tests/indexes/multi/test_sorting.py`
- **Grading**:
  - Pre-condition: `pytest pandas/tests/indexes/multi/test_sorting.py -k test_sort_index_stability_duplicates` fails on seed 42.
  - Oracle: `pytest pandas/tests/indexes/multi/test_sorting.py` passes.
  - Hidden check: 100,000-row MultiIndex sorts in $<50\text{ms}$.

### 9. `tb-docker-multistage-caching`
- **Source**: `Terminal-Bench 2.0` (DevOps / Containerization)
- **Prompt**: *"The Dockerfile in `service/` takes 14 minutes to build on CI because layer caching is invalidated on any source file edit. Optimize the multistage Dockerfile to leverage buildkit cache mounts, isolate dependency downloads, strip debug binaries, and reduce final image size to <45MB while passing healthcheck."*
- **Target Failure Mode**: Breaking CGO dynamic library dependencies or missing runtime `.so` files.
- **Oracle Command**: `bash /eval/test_docker_build.sh`
- **Grading**:
  - Pre-condition: Image size $>300\text{MB}$, build time $>10\text{min}$.
  - Oracle: Second build time $<15\text{s}$, size $<45\text{MB}$, healthcheck returns 200.
  - Hidden check: Binary runs in container without dynamic linker errors.

### 10. `swe-sympy-matrix-branching-eval`
- **Source**: `SWE-Bench Pro` (Symbolic Math)
- **Prompt**: *"Evaluating the determinant of a sparse symbolic matrix with polynomial coefficients in `sympy/matrices/matrices.py` hits infinite recursion on matrices with piecewise branch conditions. Implement bounded cycle detection and branch pruning in `_eval_determinant`."*
- **Target Failure Mode**: Tampering with `sys.setrecursionlimit` causing hard C-stack segfaults.
- **Oracle Command**: `pytest sympy/matrices/tests/test_determinant.py`
- **Grading**:
  - Pre-condition: Determinant calculation raises `RecursionError`.
  - Oracle: `pytest sympy/matrices/tests/test_determinant.py` passes.
  - Hidden check: Solves $8\times8$ piecewise matrices in $<2\text{s}$.

---

## 🔴 TIER C: EXTREME

### 11. `tb-sqlite-corrupt-btree-repair`
- **Source**: `Terminal-Bench 3.0` (Storage Engines)
- **Prompt**: *"A SQLite database `data/corrupt.db` has a corrupted page header in table transactions causing PRAGMA integrity_check to fail with Page 4: btreeInitPage() returns error code 11. Write a recovery script or use raw hex patch tools to repair the pointer offset and recover 100% of non-corrupted rows into `data/recovered.db` without data loss."*
- **Target Failure Mode**: Running `sqlite3 .dump` which aborts at first error, losing 80% of rows.
- **Oracle Command**: `sqlite3 data/recovered.db "PRAGMA integrity_check; SELECT count(*) FROM transactions;"`
- **Grading**:
  - Pre-condition: Integrity check fails; row count is 214/1000.
  - Oracle: Integrity check returns `ok`; row count equals 1,000.
  - Hidden check: All transaction balances sum to expected checksum.

### 12. `hb-grpc-streaming-backpressure`
- **Source**: `Harness-Bench` (Distributed Systems)
- **Prompt**: *"Under high throughput, the bidirectional gRPC streaming handler in `services/stream.py` buffers messages in memory without backpressure when a slow consumer connects, triggering OOM. Implement reactive flow control with sliding-window acknowledgments and a bounded queue that throttles producer without dropping active connections."*
- **Target Failure Mode**: Adding artificial sleep throttling all consumers uniformly.
- **Oracle Command**: `pytest tests/test_streaming.py -v`
- **Grading**:
  - Pre-condition: `pytest tests/test_streaming.py -k test_slow_consumer_memory` OOMs ($>1.5\text{GB}$).
  - Oracle: `pytest tests/test_streaming.py` passes with $<100\text{MB}$ RAM usage.
  - Hidden check: Fast consumers maintain $>10,000\text{ msg/s}$.

### 13. `swe-astropy-fits-header-endianness`
- **Source**: `SWE-Bench Pro` (Scientific Computing)
- **Prompt**: *"When parsing FITS images on big-endian architectures (or memory-mapped byte-swapped numpy arrays), fits.Header.fromstring() silently corrupts binary table floating-point headers containing scientific notation exponents with leading zeros (1.0E-04). Fix endian handling in `astropy/io/fits/header.py` and `astropy/io/fits/src/compressionmodule.c`."*
- **Target Failure Mode**: Modifying only Python strings leaving C decompression buffer corrupted.
- **Oracle Command**: `pytest astropy/io/fits/tests/test_header.py astropy/io/fits/tests/test_table.py`
- **Grading**:
  - Pre-condition: `test_byteswapped_scientific_notation` fails.
  - Oracle: `pytest astropy/io/fits/tests/test_header.py` passes with compiled C extension.
  - Hidden check: Roundtrips both big-endian and little-endian ndarrays identically.

### 14. `hb-distributed-worker-memory-leak`
- **Source**: `Harness-Bench` (Backend Infrastructure)
- **Prompt**: *"A Celery worker executing image processing tasks in `workers/tasks.py` leaks memory over time because PyTorch tensor graphs and matplotlib figure contexts remain referenced in thread-local storage across task invocations. Fix cleanup lifecycle in `workers/base.py`."*
- **Target Failure Mode**: Enabling worker process recycling (`MAX_TASKS_PER_CHILD=1`) masking the leak.
- **Oracle Command**: `pytest tests/test_worker_leak.py -v`
- **Grading**:
  - Pre-condition: Memory grows by $>500\text{MB}$ across 100 task executions.
  - Oracle: Memory growth $<5\text{MB}$ with single long-lived worker process.
  - Hidden check: All image tensors and figure canvas instances dereferenced.

### 15. `tb-kernel-ebpf-filter-compilation`
- **Source**: `Terminal-Bench 2.0` (Systems / Kernel / eBPF)
- **Prompt**: *"An eBPF XDP network filter in `bpf/xdp_filter.c` fails BPF kernel verifier with R1 invalid variable-offset read when inspecting TCP packet headers with variable IP options. Refactor packet boundary pointer arithmetic and bounded bounds checking to satisfy the kernel verifier, compile with Clang, and load into a veth test interface."*
- **Target Failure Mode**: Ignoring verifier register output and attempting unverified pointer casts.
- **Oracle Command**: `bash /eval/test_xdp_packet_filter.sh`
- **Grading**:
  - Pre-condition: Clang compilation / `bpftool prog load` rejected by kernel verifier.
  - Oracle: Program loads cleanly into kernel; forwards valid TCP and drops malformed packets.
  - Hidden check: Sustains $>50,000\text{ pkt/s}$ without kernel verifier faults.
