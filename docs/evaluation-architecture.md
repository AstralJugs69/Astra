# Astra POC — Comparative Evaluation Architecture

This document expands the evaluation-harness contract referenced by the main Astra technical architecture. It is an implementation specification, not a benchmark result. The purpose is to measure whether Astra improves an otherwise identical Antigravity coding-agent run without allowing the evaluator to become part of Astra's runtime decision logic.

## 1. Evaluation objective

The primary experiment is a controlled A/B comparison of the same coding task under two independent conditions:

- **Baseline:** Antigravity runs with Astra completely inactive. No Astra hooks are registered for the evaluation workspace.
- **With Astra:** the same Antigravity version, model version, task prompt, repository state, verification command, and turn budget are used, but Astra's normal `PostToolUse` and `Stop` hooks are active.

The two conditions are independent trials. Astra is never enabled or disabled halfway through a single agent session. Each trial starts from a fresh copy of the exact benchmark workspace state and receives a fresh Antigravity session. This prevents Astra state, filesystem changes, transcript history, or learned context from leaking between conditions.

The evaluator therefore answers a narrow question: **does adding Astra to the coding-agent loop change bug-fixing outcomes and efficiency under otherwise controlled conditions?**

## 2. Evaluation boundary

The evaluation harness is external to Astra's production decision pipeline.

```text
                    BENCHMARK TASK
                         |
                         v
                 Evaluation Runner
                         |
              +----------+----------+
              |                     |
              v                     v
        BASELINE TRIAL         ASTRA TRIAL
        Astra hooks OFF        Astra hooks ON
              |                     |
              v                     v
        Antigravity CLI        Antigravity CLI
              |                     |
      transcript + patch     transcript + Astra logs + patch
              |                     |
              +----------+----------+
                         |
                         v
                External verifier
              (SWE-bench / Zindi)
                         |
                         v
                  RunRecord + metrics
```

The evaluator may launch Antigravity, prepare workspaces, enable or disable hooks, collect transcripts, collect patches, and invoke an external benchmark verifier. It must not modify Astra's production policy, inject benchmark-specific logic into `domain`, or bypass Astra's normal hook/API path in the With-Astra condition.

## 3. Trial isolation

Each `(task_id, condition)` run receives a unique temporary workspace.

The workspace preparation sequence is:

1. Resolve the task to an immutable benchmark revision.
2. Create a fresh workspace from that revision.
3. Verify that the untouched workspace passes the benchmark's preflight condition. If the task's baseline tests are already broken in the seed, mark the task specification invalid rather than blaming the agent.
4. Install the exact Antigravity CLI/runtime version used for the experiment.
5. Record the model identifier and relevant runtime configuration in a run manifest.
6. For `BASELINE`, ensure Astra hooks are absent from the workspace's active Antigravity hook configuration.
7. For `WITH_ASTRA`, install the normal Astra hook configuration using the repository's hook installer and point it at the evaluation Astra backend.
8. Launch a fresh Antigravity session with the byte-identical task prompt.
9. Capture the transcript, hook events, final workspace diff, and process outcome.
10. Destroy the temporary workspace after artifacts are persisted.

Baseline and With-Astra workspaces are never reused. A failed setup invalidates the pair rather than only one side of the comparison.

## 4. Condition control

The condition toggle belongs in the evaluation infrastructure, not in Astra's domain or application code.

```text
EvaluationCondition.BASELINE
    -> do not register Astra hooks

EvaluationCondition.WITH_ASTRA
    -> register PostToolUse + Stop hooks
    -> route hooks to the normal Astra backend
```

Astra itself should not contain code such as `if evaluation_mode`. Production Astra must behave identically whether it is serving a benchmark run or a real developer session.

The baseline is intentionally defined as **Astra hooks absent**, rather than Astra present but internally disabled. This measures the actual cost/benefit of adding Astra to the developer workflow and avoids measuring no-op Astra overhead as the baseline.

## 5. Benchmark adapter model

The evaluator uses a benchmark-neutral `TaskSpec` and benchmark-specific adapters.

```text
Benchmark adapter
    -> TaskSpec
    -> Workspace preparation metadata
    -> Prompt
    -> Verification command
    -> Ground-truth identifier
```

The existing `TaskCategory` values remain:

- `reproducible_bug`
- `zindi`

A future `swebench` category may be added without changing the production Astra architecture.

For SWE-style tasks, the adapter should preserve:

- repository identifier
- exact base commit
- instance ID
- issue/problem statement
- expected test mapping
- patch/evaluation metadata

The evaluator should not translate benchmark tasks into artificial Astra-specific prompts. The original task statement should be passed to Antigravity as faithfully as the benchmark permits.

## 6. SWE-bench pre-Zindi validation

Before the official Zindi evaluation, the recommended external benchmark is SWE-bench Lite, using its official task format and verifier. A small development subset should be used first to validate the evaluator end-to-end; only after the runner is stable should the experiment expand to a larger sample.

The evaluator's responsibility ends at producing a candidate patch and a complete run record. SWE-bench's official harness remains responsible for applying the patch in its evaluation environment and determining whether the task's tests pass.

The same architecture can later support other SWE-bench variants without changing Astra's production path.

## 7. Antigravity execution adapter

The evaluation runner should isolate process control from benchmark logic in a dedicated adapter.

```text
antigravity_runner.py
    - prepare invocation
    - launch `agy`
    - pass exact task prompt
    - enforce max-turn / wall-clock limits
    - capture stdout/stderr
    - persist session/transcript metadata
    - return process outcome
```

The adapter should not interpret Astra signals. It only records what happened.

Where the Antigravity CLI exposes turn boundaries, those boundaries are the authoritative source for `turns_to_fix`. Hook invocations are events within a turn, not independent agent turns unless the transcript explicitly establishes a new turn.

## 8. Hook controller

The evaluator should provide a small `hooks_controller.py` abstraction around the existing hook installation scripts.

```text
HookController.enable(workspace)
HookController.disable(workspace)
HookController.validate(workspace, expected_state)
```

`enable()` uses the same production hook registration mechanism used by a developer. `disable()` removes or neutralizes the active Astra hook registration for the evaluation workspace. The controller must validate the resulting workspace rather than assuming installation succeeded.

Any hook infrastructure failure in the With-Astra condition invalidates that task pair. The run must not be treated as an ordinary failed agent attempt because the measured system was not actually present in the intended configuration.

## 9. Execution protocol for each task

For every benchmark task, the evaluator executes:

```text
for task in benchmark:
    prepare pristine seed

    run baseline trial:
        condition = BASELINE
        Astra hooks = OFF
        record transcript + patch + verifier result

    prepare another pristine seed

    run Astra trial:
        condition = WITH_ASTRA
        Astra hooks = ON
        record transcript + Astra logs + patch + verifier result

    compare the paired outcomes
```

The order should normally be recorded and can be randomized at the batch level if doing so is practical. Randomization is useful for reducing bias from transient model/service changes, but the order itself must always be persisted.

## 10. Verification protocol

The evaluator separates agent execution from correctness verification.

During a run, the benchmark's verification command may be executed when appropriate to provide feedback to the agent, and the evaluator must record each verification result. Final correctness is determined by the benchmark verifier against the final patch.

A successful verification is not enough by itself to declare the task solved if the external benchmark verifier reports failure. Conversely, a transient failure during an intermediate turn does not make the final run unsuccessful when the benchmark verifier later confirms the final patch.

## 11. Turns-to-fix

`turns_to_fix` is the primary metric.

Definition:

> The turn index of the first agent turn after which the benchmark verification condition is satisfied and remains satisfied through the end of the trial.

Rules:

1. Turn boundaries come from Antigravity's transcript, never from Astra's self-report.
2. A passing test result that is later broken does not count as the fix point.
3. A Stop intervention that causes the main agent to continue within the same transcript turn does not itself add a turn.
4. If the agent resolves the issue only on a later turn, the later turn is counted.
5. Reaching `max_turns` without a stable verified fix yields `outcome = unresolved` and `turns_to_fix = null`.
6. Invalid infrastructure runs are excluded from aggregates and rerun as paired trials.

The evaluator should report both the raw distribution and summary statistics. Means alone are insufficient because unresolved tasks and outliers can distort the result.

## 12. Required metrics

### Primary

- `turns_to_fix`

### Outcome metrics

- resolution rate
- unresolved rate
- invalid-trial rate
- benchmark verifier pass rate

### Agent-efficiency metrics

- time to fix
- total tool calls
- failed verification attempts
- unnecessary changes / final diff noise
- number of Stop attempts

### Astra-specific metrics

- Astra interventions
- Astra assists
- blocked Stop events
- Fast-tier model calls
- Deep-tier model calls
- total Astra model cost
- Astra-added latency

### Reliability metrics

- hook transport failures
- backend timeouts
- Firestore degradation events
- fail-open events
- missing/invalid hook payloads

The evaluator must keep infrastructure failures separate from agent failures.

## 13. Run manifest and artifacts

Every trial should persist an immutable run manifest containing at minimum:

```text
run_id
pair_id
task_id
condition
benchmark_name
benchmark_version_or_revision
workspace_seed_ref
repository_base_commit
antigravity_version
main_agent_model_version
astra_version_or_git_commit
started_at
finished_at
max_turns
prompt_hash
verification_command_hash
workspace_status
process_exit_status
outcome
invalid_reason
```

Artifacts should include, where available:

```text
transcript.jsonl
hook_events.jsonl
astra_interventions.jsonl
final.patch
verification_results.jsonl
run_manifest.json
```

The final patch and verifier result are the authoritative correctness artifacts. Logs are supporting evidence.

## 14. Pairing and statistical treatment

Baseline and With-Astra trials are paired by `pair_id` and `task_id`.

The first comparison should emphasize paired differences:

```text
Δturns = turns_to_fix_with_astra - turns_to_fix_baseline
Δtime  = time_with_astra - time_baseline
```

Report:

- median turns-to-fix by condition
- mean turns-to-fix for resolved runs
- resolution rate by condition
- paired per-task differences
- number of tasks where Astra improved, regressed, or had no change

Do not exclude a task merely because Astra made no intervention. A non-intervention is an observed Astra behavior and belongs in the evaluation population.

## 15. Invalid-trial policy

A run pair is invalid and should be rerun when the experimental infrastructure itself is compromised.

Examples:

- benchmark seed cannot be reproduced
- required dependencies cannot be installed for reasons unrelated to the task
- Antigravity cannot launch
- Astra hooks are not actually enabled in the With-Astra condition
- hook transport fails in a way that activates degraded-mode behavior and prevents a faithful Astra run
- the untouched task does not satisfy its preflight condition
- wall-clock infrastructure failure unrelated to the configured task/turn limit

A normal bug-fix failure is **not** invalid. An agent that cannot solve the task has produced a valid negative benchmark result.

When one side of a pair becomes invalid, rerun both sides from pristine workspaces rather than substituting a new baseline or preserving the valid half of the old pair.

## 16. Reproducibility controls

The evaluator should pin or record every factor capable of changing the result:

- benchmark revision
- repository base commit
- Antigravity CLI version
- main-agent model identifier
- Astra commit
- Python/runtime version
- task prompt hash
- maximum turns
- verification command
- relevant environment configuration

Credentials and secrets must never be written into run artifacts.

## 17. Development stages

The evaluation implementation should be introduced incrementally:

### Stage A — evaluator plumbing

Use one tiny reproducible local bug task. Prove that the runner can create two fresh workspaces, toggle hooks, launch Antigravity, collect transcripts, collect patches, and write paired `RunRecord`s.

### Stage B — one SWE-bench Lite development instance

Run exactly one task through both conditions and verify that the external SWE-bench verifier agrees with the evaluator's recorded final result.

### Stage C — small SWE-bench Lite development batch

Run a small development sample to validate pairing, invalid-trial handling, transcript parsing, intervention counting, and aggregate reporting.

### Stage D — broader pre-Zindi benchmark

Expand to a meaningful SWE-bench Lite sample and use the results to identify false interventions, missed verification failures, latency problems, and evaluator edge cases.

### Stage E — Zindi integration

Once the agent-runner and metric plumbing are trusted, implement the Zindi adapter using the exact same `BASELINE` vs `WITH_ASTRA` execution protocol. Zindi remains the official evaluation domain; SWE-bench is a preflight validation benchmark, not a substitute for the official test.

## 18. Architectural boundary for benchmark knowledge

Benchmark-specific knowledge must remain in `astra/evaluation/` and its adapters.

The following must never enter `domain/`, `application/`, `engines/`, or production hook code:

- SWE-bench-specific repository names
- Zindi task identifiers
- benchmark-specific prompt rewriting
- benchmark-specific success heuristics
- benchmark-specific intervention rules

Astra should receive ordinary Antigravity events and operate exactly as it does in a real developer session. The benchmark changes only the environment in which those events are generated and the external mechanism used to judge the resulting patch.

## 19. Target implementation layout

The existing evaluation package should evolve toward:

```text
src/astra/evaluation/
├── __init__.py
├── models.py
├── runner.py                 # paired trial orchestration
├── workspace.py              # pristine workspace lifecycle
├── antigravity_runner.py     # agy process/session adapter
├── hooks_controller.py       # Astra ON/OFF control
├── benchmark_protocol.py     # benchmark-neutral task interface
├── swebench_adapter.py       # SWE-bench task/result adapter
├── metrics.py                # turns-to-fix + secondary metrics
├── storage.py                # isolated SQLite/JSONL records
├── report.py                 # paired comparison reports
└── tasks/
    ├── __init__.py
    └── registry.py
```

The production Astra pipeline remains unchanged by the benchmark runner.

## 20. Completion criteria

The evaluation architecture is considered implemented only when the following can be demonstrated end-to-end:

1. One benchmark task can be executed from a pristine workspace with Astra disabled.
2. The same task can be executed from a second pristine workspace with Astra enabled.
3. The two runs receive identical task instructions and pinned software/model metadata.
4. The evaluator captures real Antigravity transcript turns rather than simulated turns.
5. Astra interventions are derived from actual hook/backend events.
6. The resulting patches are independently verified by the benchmark evaluator.
7. `RunRecord` contains the paired outcome and required secondary metrics.
8. Infrastructure failures are distinguished from genuine agent failures.
9. A comparative report can show per-task and aggregate baseline-vs-Astra differences.
10. The same runner can accept a Zindi adapter without changing the core comparative protocol.

A mock evaluation path may remain for unit tests and dry runs, but it must never be presented as evidence of Astra's effectiveness.