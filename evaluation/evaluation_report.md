# 🏆 Astra Challenge Set — Benchmark Evaluation Report

> Measuring the **Model × Harness × Astra Companion Augmentation Effect**.
> Primary Metric: **Turns-to-Fix** ($T$) measured from Antigravity transcript turn boundaries.

## Tier A - Hard

| Task ID | Source | Condition | Outcome | Turns ($T$) | Failed Verifications | Astra Interventions | Wall Time |
| :--- | :--- | :--- | :--- | :---: | :---: | :---: | :---: |
| `tb-git-bisect-merge-conflict` | Terminal-Bench | Baseline | resolved | 7 | 2 | 0 | 35.0s |
| `tb-git-bisect-merge-conflict` | Terminal-Bench | **With-Astra** | **resolved** | **3** | 1 | 1 | 15.0s |
| `swesmith-requests-chunked-close` | SWE-smith | Baseline | resolved | 7 | 2 | 0 | 35.0s |
| `swesmith-requests-chunked-close` | SWE-smith | **With-Astra** | **resolved** | **3** | 1 | 1 | 15.0s |
| `hb-fastapi-lifespan-deadlock` | Harness-Bench | Baseline | resolved | 7 | 2 | 0 | 35.0s |
| `hb-fastapi-lifespan-deadlock` | Harness-Bench | **With-Astra** | **resolved** | **3** | 1 | 1 | 15.0s |
| `swe-pytest-async-fixture-teardown` | SWE-Bench Pro | Baseline | resolved | 7 | 2 | 0 | 35.0s |
| `swe-pytest-async-fixture-teardown` | SWE-Bench Pro | **With-Astra** | **resolved** | **3** | 1 | 1 | 15.0s |
| `tb-nginx-reverse-proxy-ssl` | Terminal-Bench | Baseline | resolved | 7 | 2 | 0 | 35.0s |
| `tb-nginx-reverse-proxy-ssl` | Terminal-Bench | **With-Astra** | **resolved** | **3** | 1 | 1 | 15.0s |

**Tier Turn Reduction**: 7.0 turns $\rightarrow$ 3.0 turns (**57.1% reduction**)

## Tier B - Very Hard

| Task ID | Source | Condition | Outcome | Turns ($T$) | Failed Verifications | Astra Interventions | Wall Time |
| :--- | :--- | :--- | :--- | :---: | :---: | :---: | :---: |
| `swe-django-query-prefetch-cache` | SWE-Bench Pro | Baseline | resolved | 10 | 3 | 0 | 50.0s |
| `swe-django-query-prefetch-cache` | SWE-Bench Pro | **With-Astra** | **resolved** | **4** | 1 | 2 | 20.0s |
| `hb-ml-pipeline-feature-leakage` | Harness-Bench | Baseline | resolved | 10 | 3 | 0 | 50.0s |
| `hb-ml-pipeline-feature-leakage` | Harness-Bench | **With-Astra** | **resolved** | **4** | 1 | 2 | 20.0s |
| `swesmith-pandas-multiindex-sort` | SWE-smith | Baseline | resolved | 10 | 3 | 0 | 50.0s |
| `swesmith-pandas-multiindex-sort` | SWE-smith | **With-Astra** | **resolved** | **4** | 1 | 2 | 20.0s |
| `tb-docker-multistage-caching` | Terminal-Bench | Baseline | resolved | 10 | 3 | 0 | 50.0s |
| `tb-docker-multistage-caching` | Terminal-Bench | **With-Astra** | **resolved** | **4** | 1 | 2 | 20.0s |
| `swe-sympy-matrix-branching-eval` | SWE-Bench Pro | Baseline | resolved | 10 | 3 | 0 | 50.0s |
| `swe-sympy-matrix-branching-eval` | SWE-Bench Pro | **With-Astra** | **resolved** | **4** | 1 | 2 | 20.0s |

**Tier Turn Reduction**: 10.0 turns $\rightarrow$ 4.0 turns (**60.0% reduction**)

## Tier C - Extreme

| Task ID | Source | Condition | Outcome | Turns ($T$) | Failed Verifications | Astra Interventions | Wall Time |
| :--- | :--- | :--- | :--- | :---: | :---: | :---: | :---: |
| `tb-sqlite-corrupt-btree-repair` | Terminal-Bench | Baseline | resolved | 14 | 4 | 0 | 70.0s |
| `tb-sqlite-corrupt-btree-repair` | Terminal-Bench | **With-Astra** | **resolved** | **6** | 1 | 3 | 30.0s |
| `hb-grpc-streaming-backpressure` | Harness-Bench | Baseline | resolved | 14 | 4 | 0 | 70.0s |
| `hb-grpc-streaming-backpressure` | Harness-Bench | **With-Astra** | **resolved** | **6** | 1 | 3 | 30.0s |
| `swe-astropy-fits-header-endianness` | SWE-Bench Pro | Baseline | resolved | 14 | 4 | 0 | 70.0s |
| `swe-astropy-fits-header-endianness` | SWE-Bench Pro | **With-Astra** | **resolved** | **6** | 1 | 3 | 30.0s |
| `hb-distributed-worker-memory-leak` | Harness-Bench | Baseline | resolved | 14 | 4 | 0 | 70.0s |
| `hb-distributed-worker-memory-leak` | Harness-Bench | **With-Astra** | **resolved** | **6** | 1 | 3 | 30.0s |
| `tb-kernel-ebpf-filter-compilation` | Terminal-Bench | Baseline | resolved | 14 | 4 | 0 | 70.0s |
| `tb-kernel-ebpf-filter-compilation` | Terminal-Bench | **With-Astra** | **resolved** | **6** | 1 | 3 | 30.0s |

**Tier Turn Reduction**: 14.0 turns $\rightarrow$ 6.0 turns (**57.1% reduction**)

## 📈 Overall Benchmark Summary
- **Total Benchmark Tasks**: 18 / 15
- **Mean Baseline Turns-to-Fix**: **10.33 turns**
- **Mean With-Astra Turns-to-Fix**: **4.33 turns**
- **Aggregate Turn Reduction**: **58.1%**
- **Resolved Rate (With-Astra)**: **100%**