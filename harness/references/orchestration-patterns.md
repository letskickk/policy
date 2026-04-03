# Policy Harness Orchestration Patterns

This repository borrows the useful parts of `revfactory/harness` without copying its Claude-specific plugin model.

## Patterns

- `pipeline`
  - Use when one tier is clearly failing and the repair path is sequential.
  - Flow: evaluate -> plan -> edit -> evaluate.

- `fan_out_fan_in`
  - Use when multiple tiers fail and their evidence can be inspected independently before a single repair round.
  - Flow: gather tier evidence in parallel -> merge priorities -> edit -> evaluate.

- `producer_reviewer`
  - Use when prompt or deploy changes are risky and the plan should explicitly include a review pass for scope and regression safety.
  - Flow: propose repair -> review allowed files and commands -> edit -> evaluate.

- `supervisor`
  - Use in breakthrough mode after stagnant rounds.
  - Flow: compare history -> pick the highest-leverage redesign surface -> constrain the next generator round.

## Repository-Specific Contracts

- Canonical result artifacts live under `harness/results/`, not legacy `harness/tier*-*/runs/` folders.
- The orchestrator owns full harness execution. Generator rounds must not call `harness/scripts/run_all_harness.py` or individual tier evaluators.
- Planner output must reference real repository paths or valid globs only.
