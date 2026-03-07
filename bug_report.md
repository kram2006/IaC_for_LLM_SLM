# Bug & Technical Audit Report — IaC_for_LLM_SLM

## 1) Audit Scope and Method
- Repository audited locally at `/home/runner/work/IaC_for_LLM_SLM/IaC_for_LLM_SLM`.
- All tracked files were inspected (`git ls-files` = 49 files).
- CI workflow runs were checked via GitHub Actions API:
  - Latest run: `in_progress`
  - Recent completed runs: `success`
  - No failed jobs were reported for current run.
- `IaC.pdf` was **not present** in the repository checkout (`**/*.pdf` returned no matches), so paper-to-code comparison is limited to references in source comments and README.

---

## 2) Repository Architecture Summary

### Core backend components
- **Orchestration / CLI:** `src/evaluate.py`
  - Parses CLI args, loads config+dataset, handles task mode vs chain mode, pass sampling, lockfile management.
- **Task execution loop:** `src/eval_core.py`
  - Prompt build, LLM call, terraform init/validate/plan/apply, retries, spec checks, post-state checks.
- **Model clients:** `src/api_client.py`
  - OpenRouter-compatible HTTP client, Hugging Face inference path, local transformers path.
- **Execution utils:** `src/eval_utils.py`
  - Async shell execution, redaction, Terraform apply helper, code extraction.
- **Spec validator:** `src/spec_checker.py`
  - Strategy-pattern validators for CREATE/READ/UPDATE/DELETE using Terraform plan JSON.
- **XO verification:** `src/xo_client.py`
  - WebSocket JSON-RPC calls to Xen Orchestra; VM inventory validation.
- **Result schema generation:** `src/json_generator.py`
  - Builds detailed task-level JSON record and outcome fields.
- **Metrics:** `src/compute_metrics.py`
  - pass@k estimator + BLEU + CodeBERT (if installed), grouped by task.

### Data/config
- **Tasks dataset:** `tasks/vm_provisioning_tasks.csv` (10 tasks)
- **Task rules:** `config/task_specs.yaml`
- **Provider/model config:** `config/openrouter_config.yaml`
- **Reference Terraform snippets:** `tasks/references/*.tf`

### Task dependency model verification
- Independent tasks present: **C1.1, C1.2, C2.2, C5.2** ✅
- Chain 1 present: **C1.3 → U1.2 → D1.2** ✅
- Chain 2 present: **C2.3 → R1.2 → D2.2** ✅
- In code, chain ordering is preserved from `--chain` argument order (`evaluate.py`).

---

## 3) Evaluation Pipeline Verification (Expected 9-stage flow)

| Stage | Status | Notes |
|---|---|---|
| 1. Task Definition | ✅ | CSV task rows + YAML specs used.
| 2. Prompt Construction | ✅ | Baseline, CoT, FSP, and repair prompts implemented.
| 3. Model Execution | ✅ | OpenRouter/HF/local clients integrated.
| 4. Output Capture | ✅ | Full response + extracted HCL logged per iteration.
| 5. Static Validation | ✅ | `terraform init/validate/plan` checks.
| 6. Intent/Constraint Validation | ✅ | `spec_checker` strategy validators by category.
| 7. Metric Calculation | ⚠️ | pass@k math is correct; semantic/functional labeling has caveats (see issues).
| 8. Result Logging | ✅ | JSON outputs + per-iteration artifacts.
| 9. Experiment Metadata | ⚠️ | Metadata exists, but some scripts are inconsistent with folder naming and dataset assumptions.

---

## 4) Confirmed Technical Issues

## Critical / High Impact

### A. Experiment script uses wrong output folders for metrics aggregation
- **File:** `run_experiments.sh` lines 66–69
- **Issue:** Script computes metrics from `results/dataset/${MODEL}` and suffix variants, but runtime outputs use `folder_name` from config (`Phi4_Ollama_Results`, `phi4_or`, etc.).
- **Impact:** Post-run metrics commands can target non-existent folders or miss produced files.
- **Suggested fix:** Resolve output folders from config model metadata or pass explicit folder paths.

### B. Functional "apply" metric label can be misleading in plan-only runs
- **Files:** `src/eval_core.py` lines 324–329, `src/json_generator.py` lines 285–287, `src/compute_metrics.py` lines 124–182
- **Issue:** Plan-only successful runs set `execution_successful=True`, and metrics present this as `Pass@k (Apply)`.
- **Impact:** Dashboard wording implies apply success even when apply was skipped.
- **Suggested fix:** Separate `plan_success` from `apply_success` metrics and relabel output accordingly.

### C. Chain validation can silently accept partial/invalid chain input
- **File:** `src/evaluate.py` lines 191–195
- **Issue:** Unknown task IDs in `--chain` are silently dropped after filtering.
- **Impact:** Operator may think a full chain ran while only a subset executed.
- **Suggested fix:** Validate `--chain` IDs strictly and fail fast on missing/invalid IDs.

### D. Outdated auxiliary scripts point to stale dataset paths/prefixes
- **Files:** `scripts/verify_phi4_codes.py`, `scripts/inject_phi4_into_dataset.py`, `scripts/verify_dataset.py`
- **Issue:** Defaults assume directories/prefixes not aligned with current config naming or rely on external `comparison/` assets absent in repo.
- **Impact:** Scripts fail in clean checkout or produce false diagnostics.
- **Suggested fix:** Centralize path/prefix configuration and validate required assets up front.

### E. `scripts/evaluate_phi4_vs_each.py` has unresolved symbol in CodeBLEU path
- **File:** `scripts/evaluate_phi4_vs_each.py` line 193
- **Issue:** `compute_codebleu()` references `sentence_bleu` and `SmoothingFunction` without importing them in that scope.
- **Impact:** Runtime `NameError` during metric computation.
- **Suggested fix:** Add local imports in `compute_codebleu()` or module-level import.

### F. Baseline environment requires dependencies before CLI even for `--help`
- **Files:** `src/evaluate.py`, `src/api_client.py`, `src/xo_client.py`
- **Issue:** Top-level imports require optional runtime packages (`huggingface_hub`, `websockets`) before argument parsing.
- **Impact:** `python src/evaluate.py --help` fails in minimally provisioned env.
- **Suggested fix:** Delay provider-specific imports or provide graceful dependency error messaging.

---

## Medium Impact / Design Weaknesses

### G. Legacy/duplicate evaluation stacks increase maintenance risk
- **Files:** `src/compute_metrics.py` and `scripts/compute_metrics.py`; plus large legacy scripts under `scripts/`
- **Issue:** Multiple overlapping metric implementations with different assumptions.
- **Impact:** Results drift, reproducibility confusion, operator error.
- **Suggested fix:** Declare one canonical metric pipeline and deprecate/archive others.

### H. Inconsistent schema assumptions across scripts
- **Examples:**
  - `scripts/evaluate_bleu_codebertscore.py` expects CSV columns like `Prompt`, `Intent`, `Reference output`.
  - Core pipeline uses `tasks/vm_provisioning_tasks.csv` schema with `task_id`, `prompt`, `resource_requirements`, etc.
- **Impact:** Auxiliary scripts cannot run on canonical dataset without transformation.
- **Suggested fix:** Normalize script inputs to one schema or add adapters.

### I. Hardcoded reference credentials in task reference `.tf` files
- **Files:** `tasks/references/*.tf`
- **Issue:** `username = "admin@admin.net"`, `password = "admin"` present in reference artifacts.
- **Impact:** Not a live secret, but bad security posture for examples and can leak into generated outputs.
- **Suggested fix:** Replace with placeholders and explicit documentation.

### J. Metadata reproducibility gaps in multi-script workflows
- **Issue:** Core runner supports seed and model metadata, but external scripts do not consistently track model/version/config fingerprints.
- **Impact:** Hard to compare runs across script families.
- **Suggested fix:** Standardize run manifest (config hash, dataset hash, model identifier, seed, timestamp, git SHA).

---

## 5) Metrics Validation Findings

### pass@k
- **Implementation status:** Core estimator in `src/compute_metrics.py` is mathematically correct (Chen et al. unbiased estimator).
- **Concern:** Interpretation layer labels apply-centric metrics even when results may be plan-only.

### Semantic metrics
- BLEU/CodeBERT are optional and gracefully degrade in core metrics script.
- Auxiliary scripts use mixed implementations and languages (`lang='python'`, `lang='terraform'`, proxy assumptions) without unified policy.

### Potential evaluation mistakes to avoid (observed risk points)
- Mixing plan-only and apply outcomes in a single “apply” KPI.
- Comparing outputs from non-canonical scripts with different tokenization/metric definitions.
- Treating absent external assets (`comparison/`) as repository bugs instead of optional data dependencies.

---

## 6) Dataset and Task Validation Findings

- `tasks/vm_provisioning_tasks.csv` contains exactly **10 tasks**, one row each, no malformed rows.
- Task set matches required benchmark subset (no extra 13-task legacy execution in core runner).
- `config/task_specs.yaml` aligns with all 10 tasks, including chain-dependent tasks.
- Reference HCL files exist for all 10 tasks.
- Task order in dataset is deterministic; chain execution order follows explicit `--chain` argument ordering.

---

## 7) Model Execution Validation Findings

### Strengths
- OpenRouter retry strategy and timeout handling in client.
- Async Terraform command execution with timeout path.
- Explicit lockfile (`.evaluation_in_progress`) prevents simultaneous writes in core metrics pipeline.

### Weaknesses
- Dependency loading is eager (not lazy), reducing CLI robustness.
- External scripts have inconsistent timeout/retry/error strategies.
- Some scripts assume local services/files that may not exist by default.

---

## 8) Reproducibility Findings

### Present controls
- Seed support in evaluate CLI and OpenRouter/local generation paths.
- Deterministic dataset row order by CSV read order.
- Per-run artifacts and JSON logs.

### Gaps
- No single canonical experiment manifest across all script families.
- Naming/path mismatches in helper scripts can break automated reruns.
- Optional scripts not pinned to same evaluation semantics as core pipeline.

---

## 9) Suggested Remediation Roadmap (No code changes applied)

1. **Stabilize core contract**
   - Enforce strict chain validation.
   - Split plan vs apply metrics and labels.
2. **Unify output discovery**
   - Resolve result directories via config metadata, not model keys.
3. **Consolidate metrics stack**
   - Keep one canonical metric implementation.
4. **Harden script compatibility**
   - Make auxiliary scripts schema-aware and path-configurable.
5. **Improve bootstrap UX**
   - Lazy imports / explicit dependency checks for provider-specific modules.
6. **Standardize reproducibility metadata**
   - Emit run manifest including dataset hash + config hash + git SHA + model/version.

---

## 10) Final Audit Verdict
The core backend evaluation framework is structurally sound and already aligned with the **10-task** benchmark and required chains. The biggest practical reliability problems are not in the core CRUD validators themselves, but in **operational consistency**: script/path drift, mixed metric semantics across toolchains, and labeling/contract issues that can mislead benchmark interpretation.
