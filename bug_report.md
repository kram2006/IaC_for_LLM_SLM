# Bug & Technical Audit Report

## 1) Audit Scope and Method
- Repository audited: `/home/runner/work/IaC_for_LLM_SLM/IaC_for_LLM_SLM`
- Inspected scope: source code, configs, scripts, tests, dataset, task specs, reference IaC files, and existing repo docs/artifacts.
- CI check performed via GitHub Actions MCP for `kram2006/IaC_for_LLM_SLM`:
  - Recent workflow runs were listed.
  - No failed jobs were found in inspected recent run(s).
- `IaC.pdf` was not present in the repository tree in this environment.

---

## 2) Architecture Summary

### Core backend architecture
- `src/evaluate.py`: CLI orchestration, task selection, fixed 10-task ordering, chain handling, lockfile.
- `src/eval_core.py`: per-task execution loop (prompting, LLM call, terraform init/validate/plan/apply, retries).
- `src/api_client.py`: provider adapters (OpenRouter/OpenAI-compatible, HF inference, local transformers).
- `src/spec_checker.py`: CREATE/READ/UPDATE/DELETE strategy validation from plan JSON.
- `src/xo_client.py`: Xen Orchestra verification over WebSocket.
- `src/json_generator.py`: task result JSON schema + output entry generation.
- `src/compute_metrics.py`: pass@k + BLEU + CodeBERT aggregation.

### Data/config layer
- `tasks/vm_provisioning_tasks.csv`: task dataset
- `config/task_specs.yaml`: task constraints/specs
- `tasks/references/*.tf`: reference Terraform snippets
- `config/openrouter_config.yaml`: model/provider/system settings

---

## 3) Component-Level Findings

### `src/evaluate.py`
- Fixed 10-task ordering is explicitly enforced.
- Chain groups and fallback progression logic are implemented.
- Lockfile lifecycle exists.
- **No issues detected in this component.**

### `src/eval_core.py`
- Core execution loop and retry mechanics are present.
- Terraform static validation and spec-check integration are present.
- **Confirmed issue detected** (see Issue #1 below).

### `src/spec_checker.py`
- CRUD strategy pattern implemented and mapped to task categories.
- Plan parsing and resource extraction logic are clear.
- **No issues detected in this component.**

### `src/api_client.py`
- Retry/backoff and status handling exist for standard HTTP provider path.
- Seed propagation logic exists.
- **No issues detected in this component.**

### `src/json_generator.py`
- Output schema captures plan/apply/spec/post-state fields and metadata.
- `meets_requirements` semantics include spec/post-state constraints.
- **No issues detected in this component.**

### `src/compute_metrics.py`
- pass@k estimator implementation is correct (Chen et al.-style unbiased form).
- Handles insufficient sample counts by outputting N/A.
- **No issues detected in this component.**

### `src/xo_client.py`
- WS login/call flow implemented with timeouts.
- Force-refresh support exists for consistency-sensitive checks.
- **No issues detected in this component.**

### Auxiliary scripts (`scripts/*.py`, root utility scripts)
- Utilities are generally functional but inconsistent in maturity.
- **Confirmed issues detected** in utility/script layer (Issues #2 and #3 below).

---

## 4) Evaluation Pipeline Verification (Expected 9 Stages)

1. **Task Definition** (`evaluate.py` + CSV)  
   **Implementation appears correct based on the inspected code.**

2. **Prompt Construction** (`eval_core.py` + `prompt_templates.py`)  
   **Implementation appears correct based on the inspected code.**

3. **Model Execution** (`eval_core.py` + `api_client.py`)  
   **Implementation appears correct based on the inspected code.**

4. **Output Capture** (raw response + extracted HCL + artifact files)  
   **Implementation appears correct based on the inspected code.**

5. **Static Validation** (`terraform init/validate/plan`)  
   **Implementation appears correct based on the inspected code.**

6. **Intent/Constraint Validation** (`spec_checker.py`)  
   Implemented, but see **Issue #1** for a failure-path gap when plan JSON extraction fails.

7. **Metric Calculation** (`src/compute_metrics.py`)  
   **Implementation appears correct based on the inspected code.**

8. **Result Logging** (`json_generator.py` + logs/history files)  
   **Implementation appears correct based on the inspected code.**

9. **Experiment Metadata Recording** (entry metadata fields in output JSON)  
   **Implementation appears correct based on the inspected code.**

---

## 5) Confirmed Issues

## Issue #1
- **Severity:** High  
- **Confidence:** Confirmed  
- **Component:** Evaluation logic (`src/eval_core.py`)  
- **File:** `src/eval_core.py`

**Evidence snippet:**
```python
plan_json, plan_json_err = get_plan_json(workspace_dir)
if plan_json is None:
    spec_res = {"status": "skipped", "passed": None, "errors": [plan_json_err], ...}
...
log_step("Running terraform apply")
apply_res = await execute_terraform_apply(workspace_dir, env=tf_env)
...
success = True
```

**Reasoning:**
When `terraform show -json tfplan` fails, spec validation is marked as skipped, but apply still executes and can set `success = True`. This means task-level success used by orchestration can be true even though intent/spec validation was not executed.

---

## Issue #2
- **Severity:** Medium  
- **Confidence:** Confirmed  
- **Component:** Utility verification script  
- **File:** `scripts/verify_phi4_codes.py`

**Evidence snippet:**
```python
tid_lower = tid.lower().replace(".", "_")
task_dir = TF_CODE / tid_lower
main_tf = task_dir / "main.tf"
hist_dir = task_dir / "history"
```

Paired with artifact layout in evaluator:
```python
task_artifact_dir = os.path.join(output_dir, "terraform_code", folder_name, task_id, sample_suffix)
task_log_dir = os.path.join(task_artifact_dir, "history")
```

**Reasoning:**
The script checks `results/terraform_code/<model>/<task>/main.tf`, but evaluator writes artifacts under `.../<task>/sample_<n>/main.tf`. This mismatch can cause false negatives (`main_tf_match=None`, empty history counts) in verification output.

---

## Issue #3
- **Severity:** Low  
- **Confidence:** Confirmed  
- **Component:** Documentation vs CLI behavior  
- **Files:** `README.md`, `src/compute_metrics.py`

**Evidence snippet (README):**
```bash
python src/compute_metrics.py --help
```

**Evidence snippet (script entrypoint):**
```python
folder = sys.argv[1] if len(sys.argv) > 1 else "results/dataset"
csv_path = sys.argv[2] if len(sys.argv) > 2 else "tasks/vm_provisioning_tasks.csv"
```

**Reasoning:**
`src/compute_metrics.py` does not implement argparse/`--help`; passing `--help` is interpreted as a folder path. README “sanity check” is therefore inaccurate.

---

## 6) Potential Risks (Not Confirmed Bugs)

1. **Potential Risk:** Metric fragmentation across multiple standalone scripts vs core `src/compute_metrics.py` may produce non-comparable outputs.  
   - **Severity:** Medium  
   - **Confidence:** Potential

2. **Potential Risk:** Reference IaC files and dataset embed lab credentials/placeholders (`admin@admin.net`, `admin`) which are not production-safe examples.  
   - **Severity:** Low  
   - **Confidence:** Potential

---

## 7) Metric Validation
- `src/compute_metrics.py` pass@k uses:
  - `1 - comb(n-c, k) / comb(n, k)`
- This is the expected unbiased estimator form for pass@k.
- Task grouping and k-availability handling (`N/A` when insufficient samples) are implemented.

**Metric implementation appears correct.**

---

## 8) Dataset and Task Validation
- Dataset contains the active 10-task benchmark.
- Fixed benchmark execution order in orchestration:
  - `C1.1, C1.2, C2.2, C5.2, C1.3, U1.2, D1.2, C2.3, R1.2, D2.2`
- Required chains are represented and orchestrated:
  - `C1.3 -> U1.2 -> D1.2`
  - `C2.3 -> R1.2 -> D2.2`

**Implementation appears correct based on the inspected code.**

---

## 9) Model Execution Validation
- OpenRouter/OpenAI-compatible endpoint support: implemented.
- HuggingFace inference path: implemented.
- Local transformers path: implemented.
- Retry and timeout handling: implemented in model client and command executor.

**Implementation appears correct based on the inspected code.**

---

## 10) Reproducibility Check
- Deterministic fixed task order is implemented.
- Seed handling is propagated to clients.
- Lockfile prevents overlapping writes per dataset/model folder.

**Implementation appears correct based on the inspected code**, with the caveat that script ecosystem consistency can still affect reproducible reporting.

---

## 11) Suggested Improvements
1. Treat spec-check extraction failure (`plan_json is None`) as task failure for orchestration success semantics.
2. Align `scripts/verify_phi4_codes.py` with `sample_<n>` artifact structure.
3. Update README sanity check command for metrics script (or add argparse help support).
4. Consider consolidating auxiliary metrics scripts around a canonical metrics API.

