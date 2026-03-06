# IaC_for_LLM_SLM — Deep Technical Audit (Report-Only)

## 0) Scope, Constraints, and Audit Mode
- **Mode requested:** Report-only (no source code fixes applied)
- **Repository audited:** `https://github.com/kram2006/IaC_for_LLM_SLM` (cloned locally)
- **Coverage:** All non-git files in repo were read, including:
  - root files (`README.md`, scripts, configs)
  - `src/` pipeline code
  - `scripts/` research/metrics utilities
  - `tasks/` dataset + references
  - `tests/` and verification scripts
  - `.gitignore`
- **Attachment status:** runtime asset store contained **no uploaded files**, so `IaC.pdf` could not be audited from this environment.

---

## 1) Repository Architecture Summary

### Core evaluation pipeline (backend)
1. **Task load & run orchestration**: `src/evaluate.py`
2. **Per-task execution loop**: `src/eval_core.py`
3. **Model inference adapters**: `src/api_client.py`
4. **Terraform execution utils**: `src/eval_utils.py`
5. **Spec/intent validation**: `src/spec_checker.py` + `config/task_specs.yaml`
6. **XO state verification**: `src/xo_client.py`
7. **Result JSON generation**: `src/json_generator.py`
8. **Metric aggregation**: `src/compute_metrics.py`

### Dataset and task assets
- Main dataset: `tasks/vm_provisioning_tasks.csv`
- Task specs: `config/task_specs.yaml`
- Reference HCLs: `tasks/references/*.tf`

### Extra/auxiliary tooling
- LLM judge: `llm_judge.py`
- Experiment runner: `run_experiments.sh`
- Research scripts in `scripts/` (comparison and chart tooling)

---

## 2) Pipeline Verification (Stage-by-stage)

Expected robust workflow:
1. Task Prompt Generation ✅
2. Model Output Generation ✅
3. Static Validation / Compile Checks ✅ (`terraform init/validate/plan`)
4. Intent/Constraint Validation ✅ (`spec_checker`)
5. Metric Calculation ✅ (`src/compute_metrics.py`)
6. Result Logging ✅ (JSON + per-iteration logs)

**Important caveat:** all six stages exist, but multiple correctness/reproducibility defects (below) can invalidate benchmark fidelity.

---

## 3) Dataset & Task Scope Validation

- `tasks/vm_provisioning_tasks.csv` contains **10 tasks** (as requested):
  `C1.1, C1.2, C1.3, C2.2, C2.3, R1.2, U1.2, D1.2, D2.2, C5.2`
- Your specified structure is representable in repo:
  - independent: `C1.1,C1.2,C2.2,C5.2`
  - chain A: `C1.3,U1.2,D1.2`
  - chain B: `C2.3,R1.2,D2.2`

**Mismatch found:** `run_experiments.sh` currently runs `C2.3,D2.2` chain and executes `R1.2` separately (not in chain).

---

## 4) Detected Issues (with priority)

## Critical

### C-01 — `llm_judge.py` is broken at import time
- **File:** `llm_judge.py:23`
- **Issue:** `from utils import extract_terraform_code` but no `utils.py` in repo.
- **Impact:** LLM judge cannot run at all.
- **Required fix:** Replace import with `from src.eval_utils import extract_terraform_code` (or local helper copy + path-safe import handling).

### C-02 — Pass@k parallel sampling is not isolated at infrastructure level
- **Files:** `src/evaluate.py` + `src/eval_core.py`
- **Issue:** `asyncio.gather` runs all samples in parallel against same Xen Orchestra environment and same VM names.
- **Impact:** sample interference, race conditions, nondeterministic failures, invalid pass@k.
- **Required fix:** either:
  1) run samples sequentially for apply mode, or
  2) namespace VM names/resources per sample, or
  3) use true isolated backends/sandboxes per sample.

### C-03 — Artifact/log directory collisions across parallel samples
- **File:** `src/eval_core.py` (task artifact dir uses only task_id)
- **Issue:** multiple samples write into same `terraform_code/<model>/<task>/history` path.
- **Impact:** overwritten iteration logs, mixed artifacts, non-reproducible debugging.
- **Required fix:** include `sample_num` in artifact/history path.

### C-04 — XO caching can return stale post-state verification
- **File:** `src/xo_client.py` (TTL cache in `verify_vms`)
- **Issue:** pre-check and post-check can hit same 10s cache window.
- **Impact:** false verification results (especially UPDATE/DELETE correctness).
- **Required fix:** add `force_refresh=True` for post-checks, or disable cache for correctness-critical checks.

### C-05 — Critical chain spec mismatch in automation script
- **File:** `run_experiments.sh:58`
- **Issue:** chain executes `C2.3,D2.2` while expected chain is `C2.3,R1.2,D2.2`.
- **Impact:** chain benchmark definition diverges from intended experiment protocol.
- **Required fix:** update chain invocation to include `R1.2` in-sequence.

### C-06 — Config/script model mismatch causes immediate experiment failures
- **Files:** `run_experiments.sh`, `config/openrouter_config.yaml`
- **Issue:** script references model keys not present in config (`qwen25coder_*`, `codestral_*`, `mistral7b_*`).
- **Impact:** experiment script fails for most listed models.
- **Required fix:** either add missing model configs or align script to available model keys.

---

## High

### H-01 — Incorrect verdict fallback parsing in `llm_judge.py`
- **File:** `llm_judge.py:118-121`
- **Issue:** `endswith('correct')` can misclassify `incorrect` text as `Correct` in fallback path.
- **Impact:** wrong judge labels in edge responses.
- **Required fix:** check `incorrect` before `correct`, or use strict regex with line boundary.

### H-02 — `README` references non-existent test file
- **File:** `README.md:24`
- **Issue:** suggests `tests/test_compliance.py`; only `tests/test_bug_fixes.py` exists.
- **Impact:** onboarding confusion and failed setup commands.
- **Required fix:** update README test command.

### H-03 — Spec checker ignores `min_vm_count`/`max_vm_count`
- **Files:** `config/task_specs.yaml` + `src/spec_checker.py` (`CreateValidation`)
- **Issue:** C1.1 spec uses min/max bounds, validator only checks exact `vm_count`.
- **Impact:** C1.1 can pass with invalid VM counts.
- **Required fix:** implement min/max count handling.

### H-04 — UPDATE strategy does not reject forbidden actions
- **File:** `src/spec_checker.py` (`UpdateValidation`)
- **Issue:** checks for update presence but doesn’t explicitly fail create/delete/replace side actions.
- **Impact:** invalid update plans can pass.
- **Required fix:** add forbidden action checks similar to DELETE strategy.

### H-05 — READ validation only inspects `xenorchestra_vm` changes
- **File:** `src/spec_checker.py` (`_extract_vm_resources`, `ReadValidation`)
- **Issue:** non-VM infrastructure modifications are invisible to read validator.
- **Impact:** false positives for READ tasks.
- **Required fix:** for READ tasks validate **all** resource changes from plan, not VM-only subset.

### H-06 — Plan-only mode can mark success when spec extraction fails
- **File:** `src/eval_core.py:322-325`
- **Issue:** if `terraform show -json` fails, spec is marked skipped; logic can still pass plan-only success.
- **Impact:** false pass in degraded/partial execution.
- **Required fix:** treat `plan_json` extraction failure as evaluation failure (or explicit indeterminate status).

### H-07 — Missing fail-fast on unresolved API key placeholders
- **Files:** `src/evaluate.py`, `src/api_client.py`
- **Issue:** unresolved `${OPENROUTER_API_KEY}` can flow as literal string.
- **Impact:** late, confusing API auth failures.
- **Required fix:** explicitly reject unresolved placeholder strings before client creation.

### H-08 — `final_outcome.meets_requirements` is decoupled from spec result
- **File:** `src/json_generator.py`
- **Issue:** set equal to execution success (or expected-failure matched), not true semantic/spec pass.
- **Impact:** functional success can be overstated.
- **Required fix:** compute from `execution_successful && spec_accuracy.passed` (with explicit exception policy for C5.2).

### H-09 — Required dependencies are incomplete for full repo functionality
- **Files:** `requirements.txt` + multiple scripts/modules
- **Issue:** missing runtime deps used in repo (`matplotlib`, `sacrebleu`, `rouge-score`, `codebleu`, `code-bert-score`, `tree_sitter*`, `transformers`, `torch`, etc.).
- **Impact:** many scripts fail on clean install.
- **Required fix:** split into core vs optional extras (`requirements-core.txt`, `requirements-metrics.txt`) or include guarded installation docs.

### H-10 — Multiple scripts hardcode Windows absolute paths
- **Files:** several under `scripts/`
- **Issue:** `c:\Users\kalar\...` paths baked into code.
- **Impact:** non-portable; immediate FileNotFound outside author machine.
- **Required fix:** use CLI args + relative paths + env variables.

---

## Medium

### M-01 — `scripts/evaluate_qwen_vs_claude_official.py` crashes if dataset missing
- **Issue:** no pre-check around required file path.
- **Fix:** add `Path.exists()` guard and clear actionable error.

### M-02 — `scripts/verify_dataset.py` assumes missing directories/files exist
- **Issue:** unguarded `open()` and `os.listdir(base)`.
- **Fix:** preflight checks with explicit diagnostics.

### M-03 — Security risk in archive extraction
- **File:** `scripts/setup_official_metrics.py`
- **Issue:** unvalidated `tar.extractall` and insecure HTTP URL for METEOR.
- **Fix:** safe extraction (path traversal guard) + HTTPS or checksum verification.

### M-04 — OpenRouter utility lacks robust HTTP error handling
- **File:** `scripts/evaluate_bleu_codebertscore.py` (`call_openrouter`)
- **Issue:** no timeout / status checks / retry logic.
- **Fix:** add timeout, `raise_for_status`, retry/backoff, and JSON error guards.

### M-05 — Potential division-by-zero in custom weighted BLEU helper
- **File:** `scripts/evaluate_bleu_codebertscore.py` (`compute_weighted_bleu`)
- **Issue:** brevity penalty uses `r/c` without explicit `c==0` guard.
- **Fix:** return 0.0 if candidate token count is zero.

### M-06 — Lockfile protocol is half-implemented
- **Files:** `src/compute_metrics.py` expects `.evaluation_in_progress` but evaluator never creates/removes it.
- **Fix:** create lock at evaluation start and remove on completion/failure using `try/finally`.

### M-07 — Unknown specs/categories silently pass
- **File:** `src/spec_checker.py`
- **Issue:** missing spec or unknown category returns pass=True.
- **Fix:** emit explicit warning status and optionally fail in strict mode.

### M-08 — `check_compliance()` truthiness bug for zero values
- **File:** `src/json_generator.py`
- **Issue:** `if expected:` treats `0` as absent.
- **Fix:** use `if expected is not None:`.

### M-09 — Baseline prompt not persisted correctly in output JSON
- **File:** `src/json_generator.py` (`scenario.system_prompt`)
- **Issue:** looks at `system_prompt` key only; baseline prompt key is `baseline_system_prompt`.
- **Fix:** align to same prompt resolution strategy as evaluator.

