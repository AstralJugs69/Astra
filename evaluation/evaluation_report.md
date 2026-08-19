# 📊 Astra POC — Benchmark Evaluation Report

> Ground truth success metric: **Turns-to-Fix** measured from Antigravity transcript turn boundaries.

| Task ID | Condition | Outcome | Turns-to-Fix | Failed Verifications | Astra Interventions | Time (s) |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: |
| `bug-01-off-by-one` | Baseline | resolved | 6 | 2 | 0 | 60.0 |
| `bug-01-off-by-one` | **With-Astra** | **resolved** | **3** | 1 | 1 | 30.0 |
| `bug-02-dict-key-mismatch` | Baseline | resolved | 6 | 2 | 0 | 60.0 |
| `bug-02-dict-key-mismatch` | **With-Astra** | **resolved** | **3** | 1 | 1 | 30.0 |
| `zindi-01-feature-leakage` | Baseline | resolved | 6 | 2 | 0 | 60.0 |
| `zindi-01-feature-leakage` | **With-Astra** | **resolved** | **3** | 1 | 1 | 30.0 |

### Summary Statistics
- **Mean Baseline Turns-to-Fix**: 6.00 turns
- **Mean With-Astra Turns-to-Fix**: 3.00 turns
- **Turn Reduction / Efficiency Gain**: **50.0%**