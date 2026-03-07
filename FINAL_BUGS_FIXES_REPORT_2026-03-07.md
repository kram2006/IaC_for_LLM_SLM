# IaC_for_LLM_SLM — Complete Technical Audit & Repair Plan (Report-Only)

Date: 2026-03-07  
Repo: `https://github.com/kram2006/IaC_for_LLM_SLM`  
Mode: **Audit only** (no source fixes applied)

---

## 1) Repository architecture summary

### Core evaluation pipeline
- `src/evaluate.py` — CLI orchestrator (task selection, chain mode, sampling, locking)
- `src/eval_core.py` — generation + Terraform loop + retry/self-correction + spec checks
- `src/api_client.py` — OpenRouter/local transformer clients
- `src/eval_utils.py` — subprocess execution, Terraform helpers, redaction, extraction
- `src/spec_checker.py` — CREATE/READ/UPDATE/DELETE validators over Terraform plan JSON
- `src/xo_client.py` — Xen Orchestra WS verification/caching
- `src/json_generator.py` — dataset JSON output schema generator
- `src/compute_metrics.py` — pass@k + BLEU + CodeBERT aggregation

### Data/config and assets
- `tasks/vm_provisioning_tasks.csv` — 10-task dataset currently used
- `tasks/references/*.tf` — canonical task references
- `config/task_specs.yaml` — formal constraints used by spec checker
- `config/openrouter_config.yaml` — model + XO config

### Auxiliary tooling
- `run_experiments.sh`, `llm_judge.py`, `verify_fixes.py`
- `scripts/*` for external comparison/official metrics/reporting

---

## 2) List of detected issues

## Critical

### CR-01 — Timeout handling is broken in async command executor
- **File:** `src/eval_utils.py`
- **Root cause:** `except asyncio.TimeoutExpired` uses a non-existent asyncio exception class.
- **Evidence:** timed command returns `module 'asyncio' has no attribute 'TimeoutExpired'`.
- **Impact:** timeout path fails, process cleanup is unreliable, long Terraform commands can leak/linger.
- **Required fix:** replace with `except asyncio.TimeoutError:` and preserve kill/reap path.

### CR-02 — Apply-mode pass@k is not infrastructure-isolated across samples
- **Files:** `src/evaluate.py`, `src/eval_core.py`
- **Root cause:** samples use separate local workspaces but target same live XO infra and same VM names, with no automatic teardown after success.
- **Impact:** cross-sample contamination, duplicate-name/resource conflicts, biased pass@k.
- **Required fix:** enforce one of:
  1) per-sample VM name namespace, or
  2) full destroy/reset after each sample, or
  3) isolated test infrastructure per sample.

### CR-03 — Chain design conflicts with READ semantics (`C2.3,R1.2,D2.2`)
- **Files:** `src/evaluate.py`, `src/eval_core.py`, `run_experiments.sh`
- **Root cause:** chain shares Terraform state; READ task usually emits data-only config, which in shared state plans deletions of previously managed VMs.
- **Impact:** false failures and unstable behavior for READ in chain execution.
- **Required fix:** allow per-task plan-only override in chain (at least for READ), or execute READ in non-state-mutating mode.

## High

### H-01 — Official metrics setup is broken for ROUGE package source
- **File:** `scripts/setup_official_metrics.py`
- **Issue:** `ROUGE_ZIP_URL` currently resolves to HTTP 404.
- **Impact:** "official metrics" bootstrap is non-reproducible.
- **Required fix:** replace with valid source URL + checksum.

### H-02 — Plan-only runs are recorded as Terraform apply success
- **File:** `src/json_generator.py`
- **Issue:** `terraform_apply.status` becomes `success` in plan-only runs when `exit_code` is set to 0 (even though apply is skipped).
- **Impact:** misleading execution metadata in dataset JSON.
- **Required fix:** encode explicit status like `skipped_plan_only` in output JSON mapping.

### H-03 — Constraint checklist miscomputes multi-VM expected values
- **File:** `src/json_generator.py`
- **Issue:** for tasks like `C2.2/C2.3`, expected memory/cpu semantics can be interpreted inconsistently (total vs per-VM) in checklist compliance fields.
- **Impact:** checklist can mark correct outputs as non-compliant.
- **Required fix:** normalize schema to explicit `per_vm_*` and `total_*` keys and validate before scoring.

### H-04 — Config-defined OpenRouter timeout/retry are not fully honored
- **Files:** `config/openrouter_config.yaml`, `src/evaluate.py`, `src/api_client.py`
- **Issue:** timeout is hardcoded in client construction; retry count is fixed inside client.
- **Impact:** config is partially ignored; experiment reproducibility/controls weakened.
- **Required fix:** wire timeout/retries from config into `OpenRouterClient`.

### H-05 — Security exposure via hardcoded XO credentials in tracked config/prompts
- **Files:** `config/openrouter_config.yaml`, `src/prompt_templates.py`, README examples
- **Issue:** plaintext admin credentials are committed.
- **Impact:** credential hygiene and accidental reuse risk.
- **Required fix:** use env placeholders only and redact examples.

## Medium

### M-01 — Edge-value checks in validators rely on truthiness, not None-check
- **File:** `src/spec_checker.py`
- **Issue:** checks like `if field and val` or `if expected` skip valid zero values.
- **Impact:** silent logic errors in generalized task sets.
- **Required fix:** use explicit `is not None` guards.

### M-02 — Optional dependency scripts fail even for `--help`
- **File:** `scripts/evaluate_phi4_vs_each.py`
- **Issue:** heavy imports at module import-time (e.g., `rouge_score`) before argparse help.
- **Impact:** poor usability in partially provisioned environments.
- **Required fix:** move optional imports inside metric functions or guarded loader.

### M-03 — Some scripts fail hard when expected external datasets are missing
- **Files:** `scripts/compute_metrics.py`, others in `scripts/*`
- **Issue:** immediate traceback on absent `comparison/comparison_dataset.json`.
- **Impact:** brittle automation and poor DX.
- **Required fix:** add preflight path checks + actionable messages.

### M-04 — Task-scope mismatch between historical document and current repo needs explicit note
- **Evidence:** attachment describes 13 CRUD tasks; repo intentionally uses 10 tasks.
- **Impact:** benchmark comparability ambiguity unless documented.
- **Required fix:** add explicit scope statement in README/reporting: excluded tasks and rationale.

---

## 3) Files modified

No source-code files modified (as requested).  
Audit artifact added:
- `FINAL_BUGS_FIXES_REPORT_2026-03-07.md`

---

## 4) Code patches applied

None (report-only mode).

---

## 5) Explanation of each fix (patch plan)

### Patch Plan A (must do first)
1. Fix timeout exception class in `src/eval_utils.py`.
2. Add guaranteed subprocess kill/reap in all timeout/error branches.

### Patch Plan B (benchmark correctness)
1. Isolate apply-mode samples at infrastructure level (namespace or teardown).
2. Add optional post-sample cleanup for non-chain apply runs.
3. For chain mode, force READ tasks to non-mutating plan-only path.

### Patch Plan C (result integrity)
1. Correct plan-only apply status encoding in `json_generator.py`.
2. Normalize expected-resource semantics (`per_vm_*` vs `total_*`) and checklist computation.
3. Align OpenRouter timeout/retry to config.

### Patch Plan D (tooling + reproducibility)
1. Replace dead ROUGE URL and add checksums in setup script.
2. Defer optional imports in heavy scripts.
3. Add explicit README section: 10-task active benchmark scope vs 13-task historical template.

---

## 6) Final verification status

### What was run
- `python3 -m compileall -q .` ✅
- `python3 -m pytest -q` ✅ (after installing missing local dependency `nltk`)
- `python3 verify_fixes.py` ✅ (after `nltk` install)
- `python3 llm_judge.py --help` ✅
- Timeout reproduction for `execute_command()` ✅ reproduced defect
- URL check for ROUGE setup source ✅ reproduced 404 defect

### Overall status
The repository is significantly improved versus earlier snapshots and core tests now pass, but it is **not yet fully benchmark-reliable** due to the critical issues above (especially timeout handling and apply-mode isolation semantics).
