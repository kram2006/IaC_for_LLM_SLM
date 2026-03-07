# IaC_for_LLM_SLM — Deep Technical Audit Report

## 0) Audit Scope & Method

- **Repository audited**: `https://github.com/kram2006/IaC_for_LLM_SLM`
- **Branch / commit audited**: `main` @ `f65f0bced3d13678e40825f3ba41856286dbbe57`
- **Files read**: all **49 tracked files** (`git ls-files`) + attached reference PDFs (`IaC.pdf`, CRUD task spec PDF, golden dataset PDF)
- **Constraints honored**: report-only; no source-code modifications.

### Runtime checks executed

- `python3 -m compileall -q .` ✅
- `python3 llm_judge.py --help` ✅
- `python3 -m pytest -q` ❌ (`ModuleNotFoundError: nltk` during test collection)
- `python3 verify_fixes.py` ❌ (2/7 checks failed due missing `nltk` in env)
- `python3 scripts/evaluate_bleu_codebertscore.py --help` ❌ (top-level `nltk` import failure)

---

## 1) Repository Architecture Summary

## 1.1 Core architecture (actual)

1. **Orchestration**: `src/evaluate.py`
   - CLI parsing, config loading, task filtering, pass/sample execution, optional chain mode.
2. **Task execution engine**: `src/eval_core.py`
   - Prompt building, LLM call, Terraform init/validate/plan/apply loop, retries, spec check, JSON output.
3. **Model clients**: `src/api_client.py`
   - OpenAI-compatible HTTP client (OpenRouter/Ollama-compatible), HF inference path, local transformers path.
4. **Execution utilities**: `src/eval_utils.py`
   - Async subprocess execution, sensitive-text redaction, Terraform apply helper.
5. **Task-rule validation**: `src/spec_checker.py`
   - Strategy-based CREATE/READ/UPDATE/DELETE plan checks from `config/task_specs.yaml`.
6. **State verification**: `src/xo_client.py`
   - Xen Orchestra WS query and VM inventory extraction.
7. **Result schema generation**: `src/json_generator.py`
   - Rich JSON artifact with metadata, checklist, and outcomes.
8. **Metric aggregation**: `src/compute_metrics.py`
   - pass@k (unbiased), BLEU, CodeBERT aggregation over generated JSON outputs.

## 1.2 Data/config assets

- `tasks/vm_provisioning_tasks.csv`: active dataset (10 tasks)
- `config/task_specs.yaml`: validator specs for those 10 tasks
- `tasks/references/*.tf`: reference IaC samples
- `config/openrouter_config.yaml`: prompt templates + model/XO config

## 1.3 Auxiliary scripts

- Batch experiment runner (`run_experiments.sh`)
- Post-hoc LLM judge (`llm_judge.py`)
- Multiple standalone research metrics scripts under `scripts/`

---

## 2) Pipeline Verification Against Expected Benchmark Workflow

Expected stage | Implemented? | Notes
---|---|---
Task definition | ✅ | CSV + YAML specs.
Prompt construction | ✅ | Baseline + CoT/FSP + repair prompts.
Model execution | ✅ | OpenAI-compatible + HF + local models.
Output capture | ✅ | Raw response + extracted code + per-iteration logs.
Static validation | ✅ | `terraform init/validate/plan` (and apply when enabled).
Intent/constraint validation | ⚠️ Partial | Rule-based checks exist but incompletely enforce task intent.
Metric calculation | ⚠️ Partial | pass@k implemented; semantic metrics inconsistent across scripts.
Result logging | ✅ | JSON artifacts and logs generated.
Experiment metadata recording | ⚠️ Partial | Basic model/task metadata present, but missing full reproducibility metadata.

---

## 3) Dataset, Task Set, and Dependency Chain Validation

## 3.1 Active task set verification

- Repository dataset includes **exactly 10 tasks**:
  `C1.1, C1.2, C1.3, C2.2, C2.3, R1.2, U1.2, D1.2, D2.2, C5.2`
- Task specs file contains the same 10 IDs.

## 3.2 Required chain logic verification

- Independent tasks present: `C1.1, C1.2, C2.2, C5.2`
- Chain 1 representable and runnable: `C1.3 -> U1.2 -> D1.2`
- Chain 2 representable and runnable: `C2.3 -> R1.2 -> D2.2`

## 3.3 Chain correctness risks

- No formal chain schema/DAG validator exists (chain correctness is caller-enforced).
- READ in chain is executed in a separate workspace, which weakens strict state-linked reasoning traceability.

---

## 4) Critical Bugs & Design Flaws

## CR-01 — **Evaluation success ignores runtime post-state correctness**
- **Where**: `src/json_generator.py` (`final_outcome.meets_requirements`), `src/eval_core.py`
- **Issue**: Final pass status is based mainly on `apply exit_code` + spec pass; post-state verification failures are not part of pass/fail gating.
- **Impact**: A run can be marked successful even if resulting VM state is wrong.
- **Fix**: Make final success require `(terraform success) AND (spec pass) AND (post-state pass when applicable)`.

## CR-02 — **READ task validation is too weak (false positives likely)**
- **Where**: `src/spec_checker.py` (`ReadValidation`)
- **Issue**: READ validation only checks “no resource changes”, but does not enforce expected read outputs or required data-source semantics.
- **Impact**: Semantically wrong READ outputs can pass.
- **Fix**: Validate presence/shape of required outputs and data source references for READ tasks.

## CR-03 — **Task intent mismatch for vague task C1.1**
- **Where**: dataset/task design vs execution loop (`src/eval_core.py`)
- **Issue**: C1.1 (“Create a VM”) in source documents expects clarifying interaction, but evaluator treats non-code response as failure and forces code generation behavior.
- **Impact**: Penalizes models that correctly ask clarifying questions.
- **Fix**: Add explicit “clarification-required” task mode and separate scoring path.

## CR-04 — **Experiment contamination risk across prompt strategies/workspaces**
- **Where**: `src/evaluate.py` (workspace pathing), `src/eval_core.py` (tfstate injection)
- **Issue**: Workspace paths do not include strategy suffix; residual `terraform.tfstate` can bleed context between runs/strategies.
- **Impact**: Non-independent experiments and biased benchmark results.
- **Fix**: Namespace workspaces by `(model, strategy, run_id, sample)` and hard-reset workspace at run start.

## CR-05 — **Parallel architecture claims but model calls are blocking**
- **Where**: async task loop in `src/eval_core.py` vs `requests` calls in `src/api_client.py`
- **Issue**: Blocking HTTP calls run inside async flow, reducing true concurrency.
- **Impact**: Throughput unpredictability; possible starvation/time skew in pass@k parallel mode.
- **Fix**: use async HTTP client (e.g., `aiohttp`/`httpx`) or isolate sync calls in thread executor.

## CR-06 — **Resource expectation normalization is semantically wrong for multi-VM tasks**
- **Where**: `src/json_generator.py` (`_normalize_expected_resources`)
- **Issue**: `memory_max_bytes` is interpreted as per-VM, but in dataset rows (e.g., C2.2/C2.3) it appears to represent totals.
- **Impact**: Wrong expected-vs-actual reporting in validation checklist.
- **Fix**: enforce explicit schema (`per_vm_*` vs `total_*`) and migrate dataset accordingly.

---

## 5) High-Severity Issues

## H-01 — UPDATE validator does not enforce target VM identity
- **Where**: `src/spec_checker.py` (`UpdateValidation`)
- **Impact**: updating the wrong VM can still pass if field/value match.
- **Fix**: require target VM match (`target_vm`) in update action set.

## H-02 — CREATE validator ignores `vm_names` constraints
- **Where**: `src/spec_checker.py` (`CreateValidation`) + `config/task_specs.yaml`
- **Impact**: C1.3/C2.3 naming requirements not enforced.
- **Fix**: validate generated/deleted resource names against spec.

## H-03 — C5.2 CPU cap in specs not enforced
- **Where**: `config/task_specs.yaml` (`max_total_cpus`) not checked in validator.
- **Impact**: partial constraint enforcement.
- **Fix**: add CPU aggregate checks analogous to RAM.

## H-04 — Plan-only and apply semantics mixed in outcome fields
- **Where**: `src/json_generator.py`, `src/compute_metrics.py`
- **Issue**: plan-only successful runs can still contribute to “execution_successful” metrics.
- **Impact**: benchmark interpretation ambiguity.
- **Fix**: split `plan_success` and `apply_success` metrics clearly.

## H-05 — `run_experiments.sh` plan-only sweeps include state-dependent UPDATE/DELETE tasks
- **Where**: `run_experiments.sh`
- **Impact**: unfair failure rates for tasks requiring pre-existing infra state.
- **Fix**: run stateful tasks only in chains with controlled prior state.

## H-06 — Incomplete provider support relative to stated scope
- **Where**: config + docs vs user-required provider set
- **Issue**: OpenRouter/Ollama paths are explicit; LM Studio/HuggingFace are only implicit/generic and under-documented.
- **Impact**: operational ambiguity and onboarding friction.
- **Fix**: add explicit provider profiles + tests for OpenRouter/Ollama/LM Studio/HF.

## H-07 — Security hygiene issues (credentials in tracked references)
- **Where**: `tasks/references/*.tf`, `tasks/vm_provisioning_tasks.csv`
- **Impact**: credential leakage pattern in repository artifacts.
- **Fix**: remove hardcoded credentials from datasets/references; use placeholders.

## H-08 — `scripts/evaluate_bleu_codebertscore.py` unusable in minimal env even for `--help`
- **Where**: top-level imports in script
- **Impact**: script crashes before argument parsing if optional deps are missing.
- **Fix**: lazy-import optional dependencies within compute paths.

---

## 6) Medium Issues

## M-01 — No canonical run manifest (config hash, dataset hash, git SHA in result artifacts)
- **Impact**: limited reproducibility and auditability.
- **Fix**: add per-run `experiment_manifest.json`.

## M-02 — Result overwrite risk (same entry IDs across reruns)
- **Where**: `save_dataset_entry()` filename strategy
- **Impact**: historical runs can be silently overwritten.
- **Fix**: include run_id/timestamp namespace folder.

## M-03 — Multiple metric scripts implement inconsistent methodologies
- **Where**: `src/compute_metrics.py` vs `scripts/*`
- **Impact**: non-comparable numbers across reports.
- **Fix**: define one canonical metrics module and deprecate ad-hoc variants.

## M-04 — XO verification caching may conceal fast state transitions
- **Where**: `src/xo_client.py` TTL cache
- **Impact**: stale observations possible in rapid mutation scenarios.
- **Fix**: force refresh in all correctness-critical checkpoints.

## M-05 — Packaging/import style is fragile
- **Where**: mixed `src.*` and flat imports; manual `sys.path` edits
- **Impact**: execution behavior varies by entrypoint.
- **Fix**: package project properly (`pyproject.toml`, relative imports, console scripts).

---

## 7) Metric Validation (Pass@k and Similarity)

## Pass@k

- `src/compute_metrics.py` uses the unbiased estimator formula from Chen et al. (`1 - C(n-c,k)/C(n,k)`), which is correct.
- Aggregation currently averages over tasks with `n >= k` and prints N/A for unavailable k — good.

## Key metric caveats

- Metrics can mix **plan-only** and **apply** semantics in an ambiguous way.
- Success labels include special expected-failure behavior (C5.2), which should be explicitly segmented from normal success.
- Semantic metrics are single-reference in core pipeline; auxiliary scripts use varying tokenizers/scales/language proxies.

---

## 8) Reproducibility Assessment

Current reproducibility posture: **partial**

What is good:
- deterministic CSV order
- configurable seeds
- lockfile mechanism exists

What is missing/weak:
- run-level manifest (git SHA, dependency lock, exact config snapshot)
- strict workspace isolation per run
- deterministic dependency pinning for all scripts
- uniform metric pipeline with one source of truth

---

## 9) Divergence from IaC.pdf Methodology

This repository is a custom CRUD/Xen-Orchestra benchmark and diverges substantially from the paper’s AWS/OPA design:

- Paper uses large AWS scenario benchmark + OPA/Rego intent checking.
- Repo uses compact 10-task CRUD benchmark + rule-based YAML spec checker.
- Paper framing emphasizes broader IaC benchmark generality; repo is lab-specific and operationally narrower.

These divergences are acceptable for a custom benchmark, but must be clearly documented to avoid apples-to-oranges comparisons.

---

## 10) Prioritized Remediation Roadmap (No Code Changes Applied)

## P0 (must-fix for benchmark trustworthiness)
1. Gate final pass/fail on post-state correctness where applicable.
2. Strengthen READ/UPDATE/CREATE validators (output semantics, target VM, vm_names, C5.2 CPU caps).
3. Isolate workspaces by run/strategy/sample and prevent cross-run contamination.
4. Separate plan-only vs apply metrics and reporting.

## P1
1. Canonicalize metrics implementation and remove inconsistent script paths.
2. Add explicit provider profiles/tests for OpenRouter, Ollama, LM Studio, HuggingFace.
3. Add run manifest + immutable artifact namespace.

## P2
1. Package cleanup (`src` package hygiene).
2. Harden script UX (lazy imports, clearer errors).
3. Security scrub of credentials in reference assets.

---

## 11) Final Status

- Repository has a solid skeleton for an IaC evaluation harness.
- However, several correctness and evaluation-design issues currently prevent it from being considered fully benchmark-reliable for comparative model claims.
- **No source files were modified in this audit.**
