# 🧠 Astra Reasoning Benchmark System — MR-Ben & ProcessBench Architecture

## 1. Overview
The evaluation harness has been transformed into a **purely process-based Meta-Reasoning Benchmark**, integrating the official datasets and scoring engines from:
1. **MR-Ben** (JIA-Lab / ACL): Official Meta-Reasoning & System-2 Diagnostic Benchmark.
2. **ProcessBench** (QwenLM / ACL 2025): Official Process-Level Error Localization Benchmark.

Cloned Repositories:
- `benchmarks/mr_ben/` (from `https://github.com/JIA-Lab-research/Mr-Ben`)
- `benchmarks/process_bench/` (from `https://github.com/QwenLM/ProcessBench`)

---

## 2. Benchmark Task Catalog (15 Hardest Tasks)

### 💻 A. Coding Meta-Reasoning (5 Tasks from MR-Ben Coding)
1. `mrben-coding-01`: Asynchronous LRU Cache with TTL Eviction (Flaw: `popitem(last=True)` evicting MRU instead of LRU).
2. `mrben-coding-02`: Maximum Subarray Sum in Circular Array (Flaw: All-negative array edge-case producing invalid empty subarray).
3. `mrben-coding-03`: Thread-Safe Singleton Pattern (Flaw: Missing second check inside critical lock section).
4. `mrben-coding-04`: Preorder Binary Tree Serialization with Null Sentinels (Valid sound proof).
5. `mrben-coding-05`: Kahn's Topological Sort & Cycle Detection (Flaw: `<=` condition pushing vertices to queue multiple times).

### 🧩 B. Logic & Constraint Reasoning (5 Tasks from MR-Ben Logic)
6. `mrben-logic-01`: Knights and Knaves Multi-Agent Deduction (Valid sound proof).
7. `mrben-logic-02`: Round-Robin Tournament Win Distribution (Flaw: False contradiction claim on transitive tournaments).
8. `mrben-logic-03`: Symmetry and Permutation Invariance in Sampling Without Replacement (Valid sound proof).
9. `mrben-logic-04`: Syllogistic Deduction and Quantifier Fallacies (Flaw: Affirming the consequent).
10. `mrben-logic-05`: Optimal Ternary Weighings for Counterfeit Coin Identification (Valid sound proof).

### 📐 C. Olympiad Mathematical Process Reasoning (5 Tasks from ProcessBench)
11. `processbench-olympiad-01`: Algebraic Nested Radical Equation (Flaw: Dropping absolute values yielding single point instead of interval $[5, 10]$).
12. `processbench-olympiad-02`: Prime Number Form $2^p + p^2$ (Valid sound proof using mod 3 residues).
13. `processbench-olympiad-03`: Infinite Telescoping Product Convergence (Valid sound derivation).
14. `processbench-olympiad-04`: Constrained Quadratic Minimization (Valid sound derivation).
15. `processbench-olympiad-05`: Diophantine Difference of Squares Parity Obstruction (Valid sound proof).

---

## 3. Official Scoring Rubric & Metrics

The official MR-Ben scoring engine grades model outputs along three axes:
1. **Classification Accuracy**: Binary match on whether the multi-step trace is sound (`correct`) or contains a flaw (`incorrect`).
2. **Earliest Error Step Localization**: Exact integer match on the earliest 0-indexed paragraph index where reasoning diverged.
3. **Composite Meta-Reasoning Score (MR-Score)**:
   - **`1.0`**: Identified correctness and localized the exact earliest faulty step.
   - **`0.5`**: Identified that an error exists, but localized the wrong step.
   - **`0.0`**: Misclassified correctness.

---

## 4. Running the Benchmark

```bash
# Run the 15-task reasoning benchmark suite
python scripts/run_reasoning_eval.py

# Fast dry-run / unit verification
python scripts/run_reasoning_eval.py --mock
```
