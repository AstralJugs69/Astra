# 📊 Astra Challenge Set — Benchmark Architecture & Specification

## 1. Context & Motivation
Traditional software repair benchmarks (e.g. raw SWE-bench or single-file synthetic bugs) fail to measure the true effectiveness of an agent companion. They either measure simple patch synthesis or contain dataset quality issues (underspecified instructions, flaky tests).

The **Astra Challenge Set** is a diagnostic evaluation system built specifically to measure the **Harness $\times$ Companion Augmentation Effect**:
$$\text{Performance} = \text{Model (Gemini 3.7 Flash)} + \text{Harness (Antigravity CLI)} + \text{Companion (Astra)}$$

The benchmark curates **15 hard, long-horizon tasks** sourced across 4 leading agent evaluation ecosystems:
1. **Terminal-Bench 2.x / 3.0**: Real Linux terminal environments, systems administration, kernel tools, network proxies, and binary repairs.
2. **SWE-Bench Pro**: Complex, multi-file software engineering bugs across large real-world repositories (Django, SymPy, Astropy, Pytest).
3. **Harness-Bench**: Harness-level agent interaction benchmarks evaluating async services, backpressure, distributed workers, and data leakage.
4. **SWE-smith**: Controlled program-repair tasks from real GitHub repositories with deterministic seed states.

---

## 2. Epistemic Failure Modes Tested

Each task in the challenge set is engineered to trigger at least one failure mode Astra is designed to supervise:
- ⚠️ **Premature Convergence**: Agent discovers a local symptom and attempts to stop without addressing the root cause.
- 🔄 **Circular Thrashing (`SAME_FILE_REPEATED_EDIT`)**: Agent repeatedly edits the same file without testing alternative hypotheses.
- 🛑 **Unverified Claims on Termination (`PREMATURE_TERMINATION`)**: Agent claims success on `Stop` without executing test verification.
- 🔀 **Model Laziness (`MISSING_ALTERNATIVE`)**: Agent implements crude workarounds (e.g., broad `try/except: pass`, artificial sleeps, or `sys.setrecursionlimit`) instead of proper architectural fixes.

---

## 3. Challenge Set Structure (15 Tasks Across 3 Difficulty Tiers)

```
                       ASTRA 15-TASK CHALLENGE SET
 ┌────────────────────────────────────────────────────────────────────────┐
 │ 🟢 TIER A: HARD (5 Tasks)                                              │
 │   1. tb-git-bisect-merge-conflict          (Terminal-Bench 2.0)        │
 │   2. swesmith-requests-chunked-close       (SWE-smith)                 │
 │   3. hb-fastapi-lifespan-deadlock          (Harness-Bench)             │
 │   4. swe-pytest-async-fixture-teardown     (SWE-Bench Pro)             │
 │   5. tb-nginx-reverse-proxy-ssl            (Terminal-Bench 2.0)        │
 ├────────────────────────────────────────────────────────────────────────┤
 │ 🟡 TIER B: VERY HARD (5 Tasks)                                         │
 │   6. swe-django-query-prefetch-cache       (SWE-Bench Pro)             │
 │   7. hb-ml-pipeline-feature-leakage        (Harness-Bench)             │
 │   8. swesmith-pandas-multiindex-sort       (SWE-smith)                 │
 │   9. tb-docker-multistage-caching          (Terminal-Bench 2.0)        │
 │  10. swe-sympy-matrix-branching-eval       (SWE-Bench Pro)             │
 ├────────────────────────────────────────────────────────────────────────┤
 │ 🔴 TIER C: EXTREME (5 Tasks)                                           │
 │  11. tb-sqlite-corrupt-btree-repair        (Terminal-Bench 3.0)        │
 │  12. hb-grpc-streaming-backpressure        (Harness-Bench)             │
 │  13. swe-astropy-fits-header-endianness    (SWE-Bench Pro)             │
 │  14. hb-distributed-worker-memory-leak     (Harness-Bench)             │
 │  15. tb-kernel-ebpf-filter-compilation     (Terminal-Bench 2.0)        │
 └────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Evaluation Protocol & Metrics

### 4.1 Paired Conditions
Each task is executed under two identical, isolated conditions:
- **Condition A (Baseline)**: Native Antigravity CLI (`agy`) without Astra hooks (`hooks.json` absent).
- **Condition B (With-Astra)**: Antigravity CLI (`agy`) supervised by Astra companion daemon (`hooks.json` active).

### 4.2 Primary Ground-Truth Metric: Turns-to-Fix ($T$)
Measured strictly from Antigravity's `.jsonl` transcript step boundaries. $T$ is defined as the turn index after which the oracle verification suite passes with exit code 0 and remains 0.

$$\text{Turn Reduction (\%)} = \frac{\bar{T}_{\text{baseline}} - \bar{T}_{\text{with\_astra}}}{\bar{T}_{\text{baseline}}} \times 100\%$$

### 4.3 Secondary Metrics
- **Verification Failure Rate**: Number of failing tests executed before resolution.
- **Unverified Termination Attempts**: Number of times the agent attempted to exit before running tests.
- **Astra Interventions**: Count of `ASSIST` suggestions and `INTERVENE` forced continuations.
- **Anti-Loop Safety**: Validates that no task exceeds the configured forced continuation cap (max 2 per signature).
- **Token & Latency Cost**: Sum of tokens consumed by Fast and Deep tiers.

---

## 5. Deterministic Grading Architecture

Every task defines three programmatic test gates:
1. **Pre-condition Failure Gate**: Verifies the task workspace is broken before the agent starts.
2. **Oracle Acceptance Command**: Automated pytest/bash test asserting correctness of the fix.
3. **Hidden Regression Gate**: Hidden test suite ensuring adjacent functionality was not broken or mocked out.
