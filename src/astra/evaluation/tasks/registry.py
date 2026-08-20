"""Benchmark task registry for the Astra Challenge Set (15 Tasks)."""

from typing import Dict, List
from astra.evaluation.models import BenchmarkSource, TaskCategory, TaskDifficulty, TaskSpec

BENCHMARK_TASKS: Dict[str, TaskSpec] = {
    # =========================================================================
    # 🟢 TIER A: HARD (5 Tasks)
    # =========================================================================
    "tb-git-bisect-merge-conflict": TaskSpec(
        task_id="tb-git-bisect-merge-conflict",
        source=BenchmarkSource.TERMINAL_BENCH,
        difficulty=TaskDifficulty.TIER_A_HARD,
        category=TaskCategory.DEVOPS_SYSTEMS,
        workspace_seed_ref="evaluation/seeds/tb_git_bisect",
        prompt="A regression was introduced in repo/ between tag v1.2.0 and v1.4.0 that causes bin/calculate_tax to drop fractional cents. Use git bisect to locate the culprit commit, resolve the bad merge conflict without reverting valid feature commits, and ensure the test suite passes.",
        target_failure_mode="Destructive merge revert and premature exit without verification",
        oracle_command="pytest tests/test_tax.py tests/test_features.py",
        precondition_command="pytest tests/test_tax.py",
        hidden_test_command="git log -1 --pretty=%B && git rev-parse v1.3.0",
        max_turns=15,
    ),
    "swesmith-requests-chunked-close": TaskSpec(
        task_id="swesmith-requests-chunked-close",
        source=BenchmarkSource.SWE_SMITH,
        difficulty=TaskDifficulty.TIER_A_HARD,
        category=TaskCategory.NETWORKING_ASYNC,
        workspace_seed_ref="evaluation/seeds/swesmith_requests",
        prompt="When receiving HTTP chunked transfer encoding, a socket timeout during a zero-length trailer causes an unhandled ProtocolError instead of raising a clean ChunkedEncodingError with preserved partial bytes. Fix requests/models.py and requests/adapters.py.",
        target_failure_mode="Broad exception masking (try/except: pass) swallowing network disconnects",
        oracle_command="pytest tests/test_chunked_response.py",
        precondition_command="pytest tests/test_chunked_response.py::test_partial_trailer_timeout",
        hidden_test_command="pytest tests/test_connection_errors.py",
        max_turns=15,
    ),
    "hb-fastapi-lifespan-deadlock": TaskSpec(
        task_id="hb-fastapi-lifespan-deadlock",
        source=BenchmarkSource.HARNESS_BENCH,
        difficulty=TaskDifficulty.TIER_A_HARD,
        category=TaskCategory.NETWORKING_ASYNC,
        workspace_seed_ref="evaluation/seeds/hb_fastapi",
        prompt="The service deadlocks during shutdown when active WebSocket connections are open because the database connection pool in the lifespan context is closed before terminating client coroutines. Refactor app/lifespan.py to drain connections gracefully with a 5s timeout.",
        target_failure_mode="Destructive os._exit termination destroying uncommitted DB transactions",
        oracle_command="pytest tests/test_lifespan.py --timeout=10",
        precondition_command="pytest tests/test_lifespan.py -k test_shutdown_with_active_ws",
        hidden_test_command="pytest tests/test_db_audit_clean.py",
        max_turns=15,
    ),
    "swe-pytest-async-fixture-teardown": TaskSpec(
        task_id="swe-pytest-async-fixture-teardown",
        source=BenchmarkSource.SWE_BENCH_PRO,
        difficulty=TaskDifficulty.TIER_A_HARD,
        category=TaskCategory.CORE_SOFTWARE_REPAIR,
        workspace_seed_ref="evaluation/seeds/swe_pytest",
        prompt="Scoped async fixtures with yield statements execute teardown in reverse definition order rather than reverse dependency order when mixed with synchronous session fixtures. Fix fixture resolution in src/pytest_asyncio/plugin.py.",
        target_failure_mode="Global fixture stack inversion breaking synchronous parameterized fixtures",
        oracle_command="pytest tests/test_async_fixtures.py tests/test_sync_fixtures.py",
        precondition_command="pytest tests/test_async_fixtures.py::test_mixed_fixture_teardown_order",
        hidden_test_command="pytest tests/test_parameterized_fixtures.py",
        max_turns=15,
    ),
    "tb-nginx-reverse-proxy-ssl": TaskSpec(
        task_id="tb-nginx-reverse-proxy-ssl",
        source=BenchmarkSource.TERMINAL_BENCH,
        difficulty=TaskDifficulty.TIER_A_HARD,
        category=TaskCategory.DEVOPS_SYSTEMS,
        workspace_seed_ref="evaluation/seeds/tb_nginx",
        prompt="Configure Nginx in /etc/nginx/conf.d/proxy.conf to terminate SSL on port 443 with modern cipher suites, proxy websockets to 127.0.0.1:8000/ws, and rewrite /api/v1/ prefixes while preserving query strings and client IP headers. Generate self-signed test certs and verify configuration.",
        target_failure_mode="Missing WebSocket upgrade headers and dropped query strings on URL rewrite",
        oracle_command="bash /eval/test_proxy_endpoints.sh",
        precondition_command="nginx -t",
        hidden_test_command="bash /eval/test_ssl_ciphers.sh",
        max_turns=15,
    ),

    # =========================================================================
    # 🟡 TIER B: VERY HARD (5 Tasks)
    # =========================================================================
    "swe-django-query-prefetch-cache": TaskSpec(
        task_id="swe-django-query-prefetch-cache",
        source=BenchmarkSource.SWE_BENCH_PRO,
        difficulty=TaskDifficulty.TIER_B_VERY_HARD,
        category=TaskCategory.CORE_SOFTWARE_REPAIR,
        workspace_seed_ref="evaluation/seeds/swe_django",
        prompt="When chained with prefetch_related and .only(), updating a related foreign-key instance does not invalidate the cached model instance in the parent queryset cache, returning stale attributes on subsequent traversal. Fix django/db/models/query.py and django/db/models/fields/related_descriptors.py.",
        target_failure_mode="Circular file editing in query.py without addressing descriptor cache invalidation",
        oracle_command="python tests/runtests.py model_fields.test_prefetch_related",
        precondition_command="python tests/runtests.py model_fields.test_prefetch_related_only",
        hidden_test_command="python tests/runtests.py model_fields.test_query_counts",
        max_turns=20,
    ),
    "hb-ml-pipeline-feature-leakage": TaskSpec(
        task_id="hb-ml-pipeline-feature-leakage",
        source=BenchmarkSource.HARNESS_BENCH,
        difficulty=TaskDifficulty.TIER_B_VERY_HARD,
        category=TaskCategory.DATA_PROCESSING_ML,
        workspace_seed_ref="evaluation/seeds/hb_ml_pipeline",
        prompt="The cross-validation pipeline in src/pipeline.py suffers from subtle data leakage: target encoding and standard scaling are applied to the full dataset before KFold splitting, leading to overly optimistic test scores. Refactor into an atomic sklearn.pipeline.Pipeline with custom TargetEncoder that fits only on train folds.",
        target_failure_mode="Unprincipled manual loop array slicing with index alignment bugs",
        oracle_command="pytest tests/test_leakage.py tests/test_pipeline_fit_transform.py",
        precondition_command="pytest tests/test_leakage.py",
        hidden_test_command="python tests/verify_holdout_unbiased.py",
        max_turns=20,
    ),
    "swesmith-pandas-multiindex-sort": TaskSpec(
        task_id="swesmith-pandas-multiindex-sort",
        source=BenchmarkSource.SWE_SMITH,
        difficulty=TaskDifficulty.TIER_B_VERY_HARD,
        category=TaskCategory.DATA_PROCESSING_ML,
        workspace_seed_ref="evaluation/seeds/swesmith_pandas",
        prompt="Calling .sort_index(level=[1, 0]) on a 3-level MultiIndex DataFrame with duplicate index labels in level 1 results in non-deterministic row ordering when kind='mergesort'. Fix sort stability in pandas/core/indexes/multi.py.",
        target_failure_mode="Python-level list sorting causing 100x performance regression on large frames",
        oracle_command="pytest pandas/tests/indexes/multi/test_sorting.py",
        precondition_command="pytest pandas/tests/indexes/multi/test_sorting.py -k test_sort_index_stability_duplicates",
        hidden_test_command="python pandas/tests/benchmarks/benchmark_multiindex_sort.py",
        max_turns=20,
    ),
    "tb-docker-multistage-caching": TaskSpec(
        task_id="tb-docker-multistage-caching",
        source=BenchmarkSource.TERMINAL_BENCH,
        difficulty=TaskDifficulty.TIER_B_VERY_HARD,
        category=TaskCategory.DEVOPS_SYSTEMS,
        workspace_seed_ref="evaluation/seeds/tb_docker",
        prompt="The Dockerfile in service/ takes 14 minutes to build on CI because layer caching is invalidated on any source file edit. Optimize the multistage Dockerfile to leverage buildkit cache mounts, isolate dependency downloads, strip debug binaries, and reduce final image size to <45MB while passing healthcheck.",
        target_failure_mode="Breaking CGO dynamic library dependencies or missing shared libraries",
        oracle_command="bash /eval/test_docker_build.sh",
        precondition_command="bash /eval/check_image_size.sh",
        hidden_test_command="bash /eval/check_runtime_dependencies.sh",
        max_turns=20,
    ),
    "swe-sympy-matrix-branching-eval": TaskSpec(
        task_id="swe-sympy-matrix-branching-eval",
        source=BenchmarkSource.SWE_BENCH_PRO,
        difficulty=TaskDifficulty.TIER_B_VERY_HARD,
        category=TaskCategory.CORE_SOFTWARE_REPAIR,
        workspace_seed_ref="evaluation/seeds/swe_sympy",
        prompt="Evaluating the determinant of a sparse symbolic matrix with polynomial coefficients in sympy/matrices/matrices.py hits infinite recursion on matrices with piecewise branch conditions. Implement bounded cycle detection and branch pruning in _eval_determinant.",
        target_failure_mode="Setting arbitrary sys.setrecursionlimit causing hard C-stack segfaults",
        oracle_command="pytest sympy/matrices/tests/test_determinant.py",
        precondition_command="pytest sympy/matrices/tests/test_determinant.py -k test_piecewise_sparse",
        hidden_test_command="pytest sympy/matrices/tests/test_piecewise_matrix_benchmark.py",
        max_turns=20,
    ),

    # =========================================================================
    # 🔴 TIER C: EXTREME (5 Tasks)
    # =========================================================================
    "tb-sqlite-corrupt-btree-repair": TaskSpec(
        task_id="tb-sqlite-corrupt-btree-repair",
        source=BenchmarkSource.TERMINAL_BENCH,
        difficulty=TaskDifficulty.TIER_C_EXTREME,
        category=TaskCategory.STORAGE_SYSTEMS,
        workspace_seed_ref="evaluation/seeds/tb_sqlite",
        prompt="A SQLite database data/corrupt.db has a corrupted page header in table transactions causing PRAGMA integrity_check to fail with Page 4: btreeInitPage() returns error code 11. Write a recovery script or use raw hex patch tools to repair the pointer offset and recover 100% of non-corrupted rows into data/recovered.db without data loss.",
        target_failure_mode="Running naive sqlite3 .dump which aborts at first error, losing 80% of rows",
        oracle_command="pytest tests/test_recovery.py",
        precondition_command="pytest tests/test_recovery.py",
        hidden_test_command="python /eval/verify_checksum_integrity.py",
        max_turns=25,
    ),
    "hb-grpc-streaming-backpressure": TaskSpec(
        task_id="hb-grpc-streaming-backpressure",
        source=BenchmarkSource.HARNESS_BENCH,
        difficulty=TaskDifficulty.TIER_C_EXTREME,
        category=TaskCategory.NETWORKING_ASYNC,
        workspace_seed_ref="evaluation/seeds/hb_grpc",
        prompt="Under high throughput, the bidirectional gRPC streaming handler in services/stream.py buffers messages in memory without backpressure when a slow consumer connects, triggering OOM. Implement reactive flow control with sliding-window acknowledgments and a bounded queue that throttles producer without dropping active connections.",
        target_failure_mode="Adding artificial sleep throttling all consumers uniformly",
        oracle_command="pytest tests/test_streaming.py -v",
        precondition_command="pytest tests/test_streaming.py -k test_slow_consumer_memory",
        hidden_test_command="pytest tests/test_fast_consumer_throughput.py",
        max_turns=25,
    ),
    "swe-astropy-fits-header-endianness": TaskSpec(
        task_id="swe-astropy-fits-header-endianness",
        source=BenchmarkSource.SWE_BENCH_PRO,
        difficulty=TaskDifficulty.TIER_C_EXTREME,
        category=TaskCategory.DATA_PROCESSING_ML,
        workspace_seed_ref="evaluation/seeds/swe_astropy",
        prompt="When parsing FITS images on big-endian architectures (or memory-mapped byte-swapped numpy arrays), fits.Header.fromstring() silently corrupts binary table floating-point headers containing scientific notation exponents with leading zeros (1.0E-04). Fix endian handling in astropy/io/fits/header.py and astropy/io/fits/src/compressionmodule.c.",
        target_failure_mode="Modifying only Python string formatting leaving C decompression buffer corrupted",
        oracle_command="pytest astropy/io/fits/tests/test_header.py astropy/io/fits/tests/test_table.py",
        precondition_command="pytest astropy/io/fits/tests/test_header.py -k test_byteswapped_scientific_notation",
        hidden_test_command="python astropy/io/fits/tests/verify_c_decompression.py",
        max_turns=25,
    ),
    "hb-distributed-worker-memory-leak": TaskSpec(
        task_id="hb-distributed-worker-memory-leak",
        source=BenchmarkSource.HARNESS_BENCH,
        difficulty=TaskDifficulty.TIER_C_EXTREME,
        category=TaskCategory.DEVOPS_SYSTEMS,
        workspace_seed_ref="evaluation/seeds/hb_celery",
        prompt="A Celery worker executing image processing tasks in workers/tasks.py leaks memory over time because PyTorch tensor graphs and matplotlib figure contexts remain referenced in thread-local storage across task invocations. Fix cleanup lifecycle in workers/base.py.",
        target_failure_mode="Enabling worker process recycling (MAX_TASKS_PER_CHILD=1) masking memory leak",
        oracle_command="pytest tests/test_worker_leak.py -v",
        precondition_command="pytest tests/test_worker_leak.py",
        hidden_test_command="python tests/verify_single_process_lifecycle.py",
        max_turns=25,
    ),
    "tb-kernel-ebpf-filter-compilation": TaskSpec(
        task_id="tb-kernel-ebpf-filter-compilation",
        source=BenchmarkSource.TERMINAL_BENCH,
        difficulty=TaskDifficulty.TIER_C_EXTREME,
        category=TaskCategory.DEVOPS_SYSTEMS,
        workspace_seed_ref="evaluation/seeds/tb_ebpf",
        prompt="An eBPF XDP network filter in bpf/xdp_filter.c fails BPF kernel verifier with R1 invalid variable-offset read when inspecting TCP packet headers with variable IP options. Refactor packet boundary pointer arithmetic and bounded bounds checking to satisfy the kernel verifier, compile with Clang, and load into a veth test interface.",
        target_failure_mode="Ignoring verifier register output and attempting unverified pointer arithmetic casts",
        oracle_command="bash /eval/test_xdp_packet_filter.sh",
        precondition_command="clang -target bpf -c bpf/xdp_filter.c -o xdp_filter.o",
        hidden_test_command="bash /eval/test_xdp_throughput.sh",
        max_turns=25,
    ),
}


def get_task_spec(task_id: str) -> TaskSpec:
    """Retrieves a benchmark task spec by ID."""
    if task_id not in BENCHMARK_TASKS:
        raise KeyError(f"Task ID '{task_id}' not found in Astra Challenge Set registry.")
    return BENCHMARK_TASKS[task_id]


def list_benchmark_tasks(
    difficulty: TaskDifficulty = None,
    source: BenchmarkSource = None,
) -> List[TaskSpec]:
    """Returns all registered benchmark tasks matching optional filters."""
    tasks = list(BENCHMARK_TASKS.values())
    if difficulty:
        tasks = [t for t in tasks if t.difficulty == difficulty]
    if source:
        tasks = [t for t in tasks if t.source == source]
    return tasks
