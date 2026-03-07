# IaC_for_LLM_SLM — Complete Technical Audit (Report-Only, No Code Changes Applied)

Date: 2026-03-07  
Repository: `https://github.com/kram2006/IaC_for_LLM_SLM`  
Audit mode: **Deep audit + fix plan only** (no source patch applied)

---

## 1) Repository architecture summary

### Core backend evaluation pipeline
- `src/evaluate.py` → CLI orchestrator (task loading, chain mode, sampling, lockfile)
- `src/eval_core.py` → per-task execution loop (LLM call, terraform init/validate/plan/apply, retries)
- `src/api_client.py` → model adapters (OpenRouter-compatible + local transformers)
- `src/spec_checker.py` → rule-based constraint validation per task category
- `src/xo_client.py` → Xen Orchestra state checks via WebSocket
- `src/json_generator.py` → output dataset entry generation
- `src/compute_metrics.py` → pass@k + BLEU + CodeBERT summary

### Data/config layer
- `tasks/vm_provisioning_tasks.csv` → prompt/task dataset (10 tasks)
- `config/task_specs.yaml` → independent spec rules
- `tasks/references/*.tf` → canonical HCL references

### Auxiliary scripts
- `run_experiments.sh` (batch execution)
- `llm_judge.py` (LLM-as-judge)
- `scripts/*` (comparison metrics, reporting, dataset checks)

---

## 2) Pipeline verification against required benchmark workflow

Required stages vs implementation status:

1. Task Prompt Generation → ✅ Implemented (`evaluate.py` + prompt templates)
2. Model Output Generation → ✅ Implemented (`api_client.py`)
3. Static Validation / Compile Checks → ✅ Implemented (`terraform init/validate/plan` in `eval_core.py`)
4. Intent/Constraint Validation → ✅ Implemented (`spec_checker.py`)
5. Metric Calculation → ✅ Implemented (`src/compute_metrics.py`, `scripts/*`)
6. Result Logging → ✅ Implemented (`json_generator.py`, per-iteration logs)

**Conclusion:** Stages are present and connected, but several correctness defects materially reduce reliability/reproducibility (listed below).

---

## 3) Task-scope validation (your 10-task requirement)

Verified in repo:
- Independent: `C1.1, C1.2, C2.2, C5.2`
- Chain A: `C1.3, U1.2, D1.2`
- Chain B: `C2.3, R1.2, D2.2`

`run_experiments.sh` correctly includes both chains and the 4 independent tasks.

---

## 4) Detected issues (complete list with impact + required edits)

## Critical

### CR-01 — Dataset CSV schema corruption breaks downstream metrics and tooling
- **Files:** `tasks/vm_provisioning_tasks.csv`, consumers in `src/compute_metrics.py`, `populate_references.py`, `src/complexity_scorer.py`
- **Evidence:**
  - `csv.DictReader` rows contain extra `None` field.
  - `reference_hcl` is empty in parsed rows.
  - `complexity_loc` field contains full HCL text.
  - `pandas.read_csv` interprets `task_id` as categories (`CREATE/READ/UPDATE/DELETE`) instead of `C1.1...`.
- **Impact:**
  - Reference mapping for BLEU/CodeBERT becomes invalid.
  - CSV rewriting utilities crash and can truncate dataset.
  - Complexity/reporting outputs are incorrect.
- **Required edit:** rebuild CSV with valid quoting/column alignment (or move `reference_hcl` out of CSV and reference external TF files).

### CR-02 — `llm_judge.py` fails when run as documented
- **Files:** `llm_judge.py`, `src/eval_utils.py`
- **Evidence:** `python llm_judge.py --help` fails with `ModuleNotFoundError: No module named 'logger'`.
- **Root cause:** mixed import style (`from src.eval_utils import ...` but inside `eval_utils` imports `logger` as top-level module).
- **Impact:** judge pipeline unusable from normal entrypoint.
- **Required edit:** normalize package imports (recommended: make `src` proper package and use relative imports consistently) or add explicit `src` path bootstrap in `llm_judge.py`.

### CR-03 — DELETE target validation is logically incorrect for real Terraform plans
- **Files:** `src/spec_checker.py` (`_extract_vm_resources`, `DeleteValidation`)
- **Evidence:** delete plans typically store resource identifiers in `change.before`; current extractor only uses `after` for `name_label`, which is `None` on delete.
- **Impact:** false failures for correct delete operations (target VM appears “not deleted”).
- **Required edit:** in extraction, fallback to `before` fields for delete/replace actions (at least `name_label`).

### CR-04 — Mutating CSV tools crash and can destroy dataset file
- **Files:** `populate_references.py`, `src/complexity_scorer.py`
- **Evidence:** running either script triggers `ValueError: dict contains fields not in fieldnames: None`, and writing starts before failure.
- **Impact:** partial writes/truncated CSV; non-reproducible data pipeline.
- **Required edit:** sanitize rows (`row.pop(None, None)`), enforce strict CSV schema validation before writing, and use atomic temp-file write then replace.

### CR-05 — Metrics reference mapping is wrong due pandas parse on malformed CSV
- **File:** `src/compute_metrics.py`
- **Evidence:** `ref_map` keys become `CREATE/READ/UPDATE/DELETE` instead of task IDs.
- **Impact:** similarity metrics are detached from actual tasks (BLEU/CodeBERT validity compromised).
- **Required edit:** stop relying on pandas for this CSV mapping; parse with `csv.DictReader` + explicit schema checks.

## High

### H-01 — No timeout on `terraform init/validate/plan` can hang evaluations indefinitely
- **File:** `src/eval_core.py`
- **Evidence:** `execute_command(...)` called without timeout for init/validate/plan.
- **Impact:** deadlock/hanging runs in unstable infra conditions.
- **Required edit:** add explicit bounded timeouts for all terraform subprocess calls.

### H-02 — Official metrics setup script contains dead ROUGE URL
- **File:** `scripts/setup_official_metrics.py`
- **Evidence:** `ROUGE_ZIP_URL` currently returns HTTP 404.
- **Impact:** setup is non-reproducible; “official metrics setup” incomplete.
- **Required edit:** replace with valid source and checksum verification.

### H-03 — Current test suite misses critical dataset integrity failures
- **Files:** `tests/test_bug_fixes.py`, `verify_fixes.py`
- **Evidence:** tests pass even while CSV is structurally malformed.
- **Impact:** false confidence; regressions undetected.
- **Required edit:** add assertions for:
  - absence of `None` key in CSV rows,
  - non-empty `reference_hcl` where expected,
  - strict task_id format (`C\d\.\d|R\d\.\d|U\d\.\d|D\d\.\d`),
  - standalone `llm_judge.py` import smoke test.

### H-04 — Ambiguous resource requirement semantics for multi-VM tasks
- **Files:** `tasks/vm_provisioning_tasks.csv`, `src/json_generator.py`, `config/task_specs.yaml`
- **Evidence:** `C2.2/C2.3` store totals under `memory_max_bytes`, while code path also treats memory keys as per-VM in places.
- **Impact:** checklist/resource-compliance fields can become internally inconsistent.
- **Required edit:** split keys explicitly into `per_vm_*` vs `total_*` and enforce schema validation.

### H-05 — Benchmark reporting can output misleading pass@k when k > available samples
- **File:** `src/compute_metrics.py`
- **Evidence:** fixed k set `[1,3,5]`; with sample count <5, reported pass@5 becomes 0.0 instead of N/A.
- **Impact:** misleading benchmark interpretation.
- **Required edit:** print `N/A` and exclude unavailable k from aggregate summary.

### H-06 — Sensitive defaults committed in config and prompts
- **Files:** `config/openrouter_config.yaml`, `config/prompt templates`, README examples
- **Evidence:** plain XO username/password present in tracked files.
- **Impact:** security/operational risk if reused beyond local test lab.
- **Required edit:** move credentials fully to env placeholders and redact docs/examples.

## Medium

### M-01 — `scripts/evaluate_bleu_codebertscore.py` default CSV path points to non-existent `data.csv`
- **Impact:** immediate failure for default run.
- **Required edit:** default to repo dataset path or require explicit argument.

### M-02 — `scripts/compute_metrics.py` has no argparse/help path and executes immediately
- **Impact:** poor operability; accidental execution failures when expected data absent.
- **Required edit:** add CLI parser and explicit input validation before main execution.

### M-03 — Existing checked-in bug reports are stale/inconsistent with current codebase
- **Files:** `BUGS_FIXES_REPORT.md`, `bugs_fixes_report.json`
- **Impact:** engineering decisions based on outdated defects.
- **Required edit:** replace with current, evidence-based audit report (this file).

---

## 5) Files modified

No source code fixes were applied (as requested).  
Only audit artifact created:
- `DEEP_BUGS_FIXES_REPORT.md`

---

## 6) Code patches applied

**None applied** (report-only mode by request).

---

## 7) Required fix plan (patch-ready, NOT applied)

### Patch Plan A — Stabilize dataset schema first (blocker)
1. Rebuild `tasks/vm_provisioning_tasks.csv` with valid CSV quoting and exact 12-column schema.
2. Add `scripts/verify_dataset_schema.py` (strict parser + row-level validation).
3. Fail CI/tests if extra `None` CSV keys are found.

### Patch Plan B — Repair broken execution tools
1. `llm_judge.py`: make imports executable from CLI entrypoint.
2. `src/spec_checker.py`: use `before.name_label` for delete actions.
3. `populate_references.py` + `src/complexity_scorer.py`: sanitize rows and atomic write.

### Patch Plan C — Restore metric correctness
1. `src/compute_metrics.py`: use `csv.DictReader` for task/reference map.
2. Mark unavailable pass@k as `N/A`.
3. Add dataset schema sanity check before metric computation.

### Patch Plan D — Reliability + security hardening
1. Add subprocess timeouts for init/validate/plan.
2. Replace dead ROUGE URL and add checksums.
3. Remove hardcoded credentials from tracked config/prompts.

---

## 8) Final verification status

### Executed checks
- `python -m pytest -q` → passed (18 tests)
- `python verify_fixes.py` → passed (7/7)
- `python -m compileall -q .` → passed
- `python llm_judge.py --help` → failed (import-path issue)
- `populate_references.populate(...)` on temp CSV copy → failed (`dict contains fields not in fieldnames: None`)
- `src/complexity_scorer.py` on temp copy → failed same CSV schema issue
- ROUGE download URL in `scripts/setup_official_metrics.py` → HTTP 404

### Overall status
The framework has the right architecture but is **not yet benchmark-reliable** due to dataset schema corruption and several execution/validation defects.  
Priority order for remediation: **CR-01 → CR-03 → CR-02 → CR-04/CR-05 → H-group**.

---

## 9) Note on attachments

`IaC.pdf` was not accessible in the runtime asset store during this audit session, so this report is based on full repository inspection and executable checks only.
