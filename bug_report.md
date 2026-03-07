# Bug & Technical Audit Report — IaC_for_LLM_SLM

## 1) Scope, Method, and Constraints
- Repository audited at: `/home/runner/work/IaC_for_LLM_SLM/IaC_for_LLM_SLM`
- Files inspected recursively: **53/53** non-`.git` files in working tree (tracked + untracked docs/artifacts present at audit time).
- Note on prior counts: earlier reports using `git ls-files` showed fewer files because that command only counts tracked files.
- CI investigation performed via GitHub Actions API for `kram2006/IaC_for_LLM_SLM`:
  - Recent runs listed.
  - Latest completed run inspected for jobs.
  - No failed jobs found in the inspected recent run.
- Referenced paper `IaC.pdf` was not present in repository checkout, so paper-comparison is based on repository claims/README and implementation behavior.

---

## 2) Architecture Summary

### Core evaluation pipeline
- `src/evaluate.py`
  - CLI entrypoint, config/task loading, fixed 10-task ordering, pass sampling loop, chain orchestration, workspace cleanup.
- `src/eval_core.py`
  - Per-task execution loop (prompting, retries, Terraform `init/validate/plan/apply`, spec checks, post-state checks, artifact writes).
- `src/api_client.py`
  - Provider calls (OpenRouter/OpenAI-compatible, HF inference, local transformers client).
- `src/spec_checker.py`
  - CREATE/READ/UPDATE/DELETE strategy validation against Terraform plan JSON.
- `src/xo_client.py`
  - Xen Orchestra websocket access + cached VM verification.
- `src/json_generator.py`
  - Normalized JSON result records and final outcome semantics.
- `src/compute_metrics.py`
  - Task-level aggregation, pass@k, optional BLEU/CodeBERT metrics.

### Data + specs
- Task dataset: `tasks/vm_provisioning_tasks.csv`
- Task constraints: `config/task_specs.yaml`
- Model/provider config: `config/openrouter_config.yaml`
- Terraform references: `tasks/references/*.tf`

### Supported 10-task benchmark (verified)
- Independent: `C1.1, C1.2, C2.2, C5.2`
- Chain 1: `C1.3 -> U1.2 -> D1.2`
- Chain 2: `C2.3 -> R1.2 -> D2.2`

---

## 3) Pipeline Verification Against Expected Benchmark Flow

Expected stages:
1. Task Definition
2. Prompt Construction
3. Model Execution
4. Output Capture
5. Static Validation
6. Intent/Constraint Validation
7. Metric Calculation
8. Result Logging
9. Experiment Metadata Recording

Status:
- 1–8 are implemented and connected in code.
- Stage 9 is partially implemented (metadata exists in output JSON, but cross-script reproducibility metadata standards are inconsistent).

---

## 4) Confirmed Bugs / Weaknesses

## A. Confirmed implementation/documentation mismatches

### A1) README claims parallel pass@k, but evaluator runs samples sequentially
- **Evidence**
  - README claims: parallel pass@k (`README.md` features + examples).
  - Actual execution: sequential `for` loop with `await run_sample(p)` in `src/evaluate.py`.
- **Impact**
  - Throughput and runtime expectations are incorrect.
  - Reported architecture capability is overstated.
- **Recommended fix (not implemented here)**
  - Either implement true concurrent sample execution safely, or update README language to “sequential sample execution.”

### A2) Comparison scripts depend on external assets not shipped in core repo flow
- **Evidence**
  - `scripts/evaluate_phi4_vs_each.py`, `scripts/verify_dataset.py`, and inject/verification scripts require `comparison/comparison_dataset.json` and specific derived result structures.
- **Impact**
  - Fresh users can see script failures despite core benchmark being healthy.
- **Recommended fix**
  - Add explicit preflight checks + README section marking these as optional/offline analysis scripts.

## B. Architectural and evaluation risks (high-priority)

### B1) Cache staleness risk in post-state validation windows
- **Evidence**
  - `src/xo_client.py` uses TTL cache (`_cache_ttl = 10`) with `verify_vms(force_refresh=...)`.
- **Impact**
  - In fast CREATE/UPDATE/DELETE sequences, stale XO state can affect post-state checks in edge timing scenarios.
- **Recommended fix**
  - Add chain-aware freshness policy (force refresh + retry backoff around post-apply verification).

### B2) Mixed metric stacks in `src/` and `scripts/` can produce divergent results
- **Evidence**
  - Canonical metrics in `src/compute_metrics.py`, plus separate metric/evaluation scripts in `scripts/` with different assumptions.
- **Impact**
  - Confusingly different benchmark numbers from different entrypoints.
- **Recommended fix**
  - Define one canonical metric pipeline and label auxiliary scripts as experimental.

### B3) Reproducibility manifests not standardized across all workflows
- **Evidence**
  - Core outputs include useful fields, but there is no single run-manifest schema consistently emitted across all tooling.
- **Impact**
  - Harder exact reruns/comparisons across branches/runs.
- **Recommended fix**
  - Add run manifest with config hash, dataset hash, model/provider version, seed, git SHA.

## C. Security and operational posture observations

### C1) Reference `.tf` files include placeholder credentials
- **Evidence**
  - `tasks/references/*.tf` include `admin` placeholders.
- **Impact**
  - Not production secrets, but poor secure-default examples.
- **Recommended fix**
  - Replace with explicit placeholders and warning comments in docs.

### C2) Optional-dependency runtime paths should be more explicit
- **Evidence**
  - Some features rely on optional extras (HF, code_bert_score, etc.).
- **Impact**
  - User confusion when running optional scripts in a minimal environment.
- **Recommended fix**
  - Split docs into “core required deps” vs “optional analysis deps.”

---

## 5) Metric Validation Summary

### pass@k
- Current formula in `src/compute_metrics.py` is mathematically aligned with the standard unbiased estimator.
- Aggregation is task-level and then averaged.

### Key caution
- Ensure consumers differentiate:
  - `plan_success`
  - `apply_success`
  - `meets_requirements`

The codebase has improved this semantics in result generation, but external scripts must keep the same interpretation to avoid drift.

---

## 6) Dataset/Task Validation Summary
- `tasks/vm_provisioning_tasks.csv` contains the active 10-task benchmark rows.
- Chain logic and fallback behavior are implemented in evaluator orchestration.
- Dependent-context injection for U1.2/D1.2/R1.2/D2.2 is present.

---

## 7) Model Execution Validation Summary
- Provider support present for OpenRouter/OpenAI-compatible, local (Ollama path), and HF inference mode.
- Timeout/retry handling exists in core API path.
- Terraform command execution is wrapped with structured status/result outputs.

Primary gap is not basic functionality but consistency and reproducibility across core vs auxiliary toolchains.

---

## 8) Reproducibility Assessment

Strengths:
- Fixed 10-task benchmark ordering in full mode.
- Seed plumbing exists in model config path.
- Artifact capture and per-task JSON outputs are detailed.

Gaps:
- Parallelism claim mismatch vs actual sequential execution.
- No universal run-manifest contract across all scripts.
- Auxiliary scripts rely on external datasets and assumptions.

---

## 9) Prioritized Remediation Plan (No code changes proposed in this report)

1. **Align contract with implementation**
   - Fix README “parallel samples” claim or implement true safe concurrency.
2. **Unify metrics pipeline**
   - Canonicalize `src/compute_metrics.py`; mark script variants as optional/experimental.
3. **Standardize reproducibility manifest**
   - Emit a single run-level manifest JSON for every benchmark invocation.
4. **Harden post-state freshness**
   - Add refresh/backoff policy around XO verification after apply.
5. **Improve optional tooling UX**
   - Document prerequisites and required external assets for comparison scripts.

---

## 10) Final Verdict
The repository’s **core benchmark engine is functional and structurally solid** for the active 10-task CRUD evaluation. The most meaningful issues are **evaluation-operational consistency** (contract mismatch, multi-tool metric drift, reproducibility metadata standardization), rather than fundamental absence of pipeline stages.
