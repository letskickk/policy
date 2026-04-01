# Policy Harness System

## Overview
This repository uses a `policy`-specific generator-evaluator harness.
The loop is designed for Codex to run up to 15 rounds:

1. Run the harness.
2. Read the failing criteria and reports.
3. Edit code or prompts.
4. Re-run the harness.
5. Stop after 3 consecutive passes or 15 rounds.

This is a Python/FastAPI project, not the Expo project that the original `saju` harness targeted.

## Commands

```bash
# Full evaluation
python harness/scripts/run_all_harness.py --rounds 15

# Individual tiers
python harness/scripts/run_prompt_harness.py
python harness/scripts/run_ux_harness.py
python harness/scripts/run_deploy_harness.py
```

On Windows, prefer:

```bash
.venv\Scripts\python.exe harness/scripts/run_all_harness.py --rounds 15
```

## Target Areas

### Tier 1: Prompt and LLM Output Quality
- Files:
  - `prompts/당_부합_점검_시스템.txt`
  - `prompts/당_부합_점검_유저.txt`
  - `prompts/공약_챗봇_시스템.txt`
  - `prompts/정책_생성_시스템.txt`
  - `prompts/정책_생성_유저.txt`
  - `backend/prompts.py`
  - `backend/check_service.py`
  - `backend/policy_drafter.py`
- Goal:
  - required prompt files exist
  - prompt templates still include required placeholders
  - formatting rules are preserved
  - backend prompt loading hooks are intact

### Tier 2: UX and Static Frontend Quality
- Files:
  - `static/*.html`
  - `static/admin/*.html`
- Goal:
  - key pages exist
  - `lang`, `title`, and basic meta tags exist
  - accessibility attributes are not obviously regressing
  - inline hardcoded styling is not exploding

### Tier 3: Deploy Safety
- Commands:
  - `python -m py_compile backend/*.py tests/*.py scripts/*.py`
  - `pytest -q`
- Goal:
  - Python modules compile
  - existing tests stay green
  - import-time regressions are caught early

## Round Workflow

For each round:

1. Run `python harness/scripts/run_all_harness.py --rounds 15`
2. Read:
   - `harness/RESULTS.md`
   - latest files under `harness/tier1-prompt/runs/`
   - latest files under `harness/tier2-ux/runs/`
   - latest files under `harness/tier3-deploy/runs/`
3. Fix the highest-signal failures first:
   - Tier 1 failures: edit prompt files or prompt-loading code
   - Tier 2 failures: edit `static/` pages
   - Tier 3 failures: fix code, tests, or imports
4. Re-run the harness

## Stop Conditions
- Stop early after 3 consecutive passing rounds
- Otherwise stop after round 15 and keep the best result

## Rules
- Do not assume `npm` scripts exist in this repository
- Do not assume React Native or Supabase Edge Functions exist here
- Prefer editing `prompts/`, `backend/`, and `static/` directly
- Keep reports updated in `harness/RESULTS.md`

## Codex Stability
- For browser verification on this repo, do not call `expect-cli` with its old `--cookies` example. The installed CLI supports `--no-cookies` instead.
- Use `powershell -ExecutionPolicy Bypass -File scripts/run_expect_codex.ps1 -Message "..."` so the agent is pinned to `codex`, cookies are disabled, and the base URL is set consistently.
