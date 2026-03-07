# Product Requirements Document
## IaC_for_LLM_SLM — Backend Evaluation Framework

## 1) Product Overview
Build a backend-only benchmark platform that evaluates SLMs/LLMs on Terraform generation for Xen Orchestra workflows using a fixed 10-task CRUD benchmark. The product must provide reproducible experiment execution, strict validation, and audit-friendly artifacts.

## 2) System Goals
1. Benchmark model quality consistently across providers.
2. Enforce semantic correctness, not just syntactic Terraform validity.
3. Support dependent CRUD chains with deterministic execution flow.
4. Produce reliable pass/fail and pass@k outputs.
5. Preserve artifacts/metadata required for technical audit and reproducibility.

## 3) Target Users
- AI/LLM evaluation engineers
- Infra automation engineers
- GenAI researchers comparing prompting/model variants
- Platform teams running recurring benchmark jobs in CI

## 4) Supported Model Providers
Required provider adapters:
- OpenRouter / OpenAI-compatible APIs
- Ollama (local endpoint)
- LM Studio (OpenAI-compatible endpoint mode)
- HuggingFace inference endpoints

Adapter contract requirements:
- `chat_completion(messages)` interface
- timeout and retry configuration
- seed support when provider supports deterministic generation
- normalized error handling (`auth`, `timeout`, `rate_limit`, `provider_error`)

## 5) Evaluation Pipeline Design
Canonical pipeline stages:
1. Task definition load (CSV + task spec YAML)
2. Prompt construction (baseline/COT/FSP + optional dependent context)
3. Model execution
4. Output capture (raw + extracted HCL)
5. Static validation (`init`, `validate`, `plan`, optional `apply`)
6. Intent/constraint validation (CRUD-specific rule engine)
7. Metric calculation (task-level and aggregate)
8. Result logging (JSON + artifact logs)
9. Experiment metadata recording (run metadata + model/settings)

Pipeline requirements:
- Stage contracts must be explicit.
- Any failed mandatory stage must propagate to final pass/fail semantics.
- Error states must be machine-readable in output JSON.

## 6) Task Execution Architecture
### 6.1 Fixed benchmark scope (exactly 10 tasks)
`C1.1, C1.2, C2.2, C5.2, C1.3, U1.2, D1.2, C2.3, R1.2, D2.2`

### 6.2 Required execution groups
- Independent tasks: `C1.1, C1.2, C2.2, C5.2`
- Chain 1: `C1.3 -> U1.2 -> D1.2`
- Chain 2: `C2.3 -> R1.2 -> D2.2`

### 6.3 Chain requirements
- Deterministic chain order and fallback policy.
- Shared state context where dependency requires it.
- Chain progression should use semantic outcome gates (not execution-only pass).

### 6.4 Workspace requirements
- Per-run deterministic workspace structure.
- Per-sample artifact isolation.
- Cleanup policy must be explicit and auditable.

## 7) Metrics Strategy
Primary metrics:
- `plan_success`
- `apply_success`
- `spec_pass`
- `post_state_pass` (when applicable)
- `meets_requirements`
- pass@k (unbiased estimator)

pass@k requirements:
- Formula: `1 - comb(n-c, k)/comb(n, k)`
- Aggregate by task first, then macro-average
- Report N/A for tasks with insufficient samples

Secondary (non-gating) metrics:
- BLEU
- CodeBERT-based similarity

## 8) Data Flow
1. Load config, dataset, task specs.
2. Resolve mode: single task / chain / full benchmark.
3. For each sample/task:
   - Build prompt
   - Call model provider
   - Extract Terraform
   - Run Terraform validation and apply path (if enabled)
   - Run spec + post-state checks
   - Persist per-task JSON and logs
4. Aggregate metrics from task JSON artifacts.
5. Emit run summary and reproducibility metadata.

## 9) Reproducibility Standards
Mandatory reproducibility fields:
- model key/version
- provider endpoint/base URL
- seed used per sample
- benchmark task order used
- config snapshot/hash
- dataset version/hash
- timestamp (UTC)
- git commit SHA (recommended)

Execution reproducibility requirements:
- deterministic task order
- deterministic chain order
- explicit handling for non-deterministic provider behavior

## 10) System Architecture Description
Textual architecture:

`CLI Orchestrator`  
→ `Config + Dataset Loader`  
→ `Task Scheduler (single/chain/full)`  
→ `Prompt Builder`  
→ `Provider Adapter`  
→ `Terraform Executor`  
→ `Spec/Post-State Validators`  
→ `Result/Artifact Writer`  
→ `Metrics Aggregator`  
→ `Run Summary & Metadata`

Cross-cutting concerns:
- lockfile and run isolation
- retry/timeout policy
- sensitive value redaction
- structured error handling

## 11) Future Improvements
1. Promote semantic validation failures to orchestration-level failure gates.
2. Unify script ecosystem around one canonical metrics and reporting module.
3. Add strict run-manifest format with hashes and provenance metadata.
4. Add a dataset/spec linter command for CI preflight validation.
5. Add provider capability matrix and smoke tests for all supported providers.
