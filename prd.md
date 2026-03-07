# Product Requirements Document (PRD)
## IaC_for_LLM_SLM — Backend Evaluation Framework (Target Design)

## 1. Product Overview
Build a backend-only, reproducible benchmark platform for evaluating SLM/LLM Terraform generation quality across a fixed 10-task CRUD benchmark with dependency chains. The system must be provider-agnostic and produce reliable, comparable experiment outputs.

## 2. System Goals
1. Benchmark models fairly across providers (OpenRouter, Ollama, LM Studio-compatible endpoints, HuggingFace).
2. Evaluate both execution correctness and requirement compliance.
3. Support lifecycle dependency chains with correct shared-state semantics.
4. Preserve full experiment traceability (inputs, outputs, logs, metadata).
5. Enable reproducible and auditable benchmark runs.

## 3. Target Users
- AI systems engineers benchmarking model families.
- GenAI researchers comparing prompting strategies.
- Infra automation engineers validating Terraform-generation safety.
- MLOps/platform teams integrating benchmark jobs into CI.

## 4. Supported Model Providers
Required adapter interface:
- `chat_completion(messages)`
- model/provider identity metadata
- timeout + retry control
- deterministic seed support when provider permits
- normalized error categories (`timeout`, `rate_limit`, `auth`, `provider_error`)

Minimum provider set:
- OpenRouter/OpenAI-compatible HTTP APIs
- Ollama local server
- LM Studio (OpenAI-compatible endpoint)
- HuggingFace inference API

## 5. Evaluation Pipeline Design

### Stage 1 — Task Definition
- Source of truth: `tasks/vm_provisioning_tasks.csv`
- Constraint source: `config/task_specs.yaml`
- Hard fail on schema/task-id mismatch.

### Stage 2 — Prompt Construction
- Deterministic prompt assembly from system template + task prompt.
- Optional strategy layer (`none`, `COT`, `FSP`) must be tagged in outputs.

### Stage 3 — Model Execution
- Provider adapter call with bounded retries and global timeout.
- Record raw provider response for each iteration.

### Stage 4 — Output Capture
- Extract Terraform/HCL deterministically.
- Persist both raw response and extracted code.

### Stage 5 — Static Validation
- `terraform init`, `terraform validate`, `terraform plan`.
- Persist command outputs and exit codes.

### Stage 6 — Intent/Constraint Validation
- Category strategy checks (CREATE/READ/UPDATE/DELETE) from plan JSON.
- Post-state verification for stateful tasks where required.

### Stage 7 — Metric Calculation
- Core metrics:
  - `plan_success_rate`
  - `apply_success_rate`
  - `spec_pass_rate`
  - `pass@k` (unbiased estimator)
- Optional semantic metrics (BLEU, CodeBERTScore) as secondary indicators.

### Stage 8 — Result Logging
- One immutable JSON per task-run sample.
- Per-task artifacts: prompts, history, terraform logs, screenshots (if any), state snapshots.

### Stage 9 — Experiment Metadata Recording
- Run manifest required fields:
  - model key + provider + endpoint
  - model resolved folder name
  - seed
  - dataset hash + config hash
  - git commit SHA
  - timestamp (UTC)

## 6. Task Execution Architecture

### Active benchmark set (exactly 10 tasks)
`C1.1, C1.2, C2.2, C5.2, C1.3, U1.2, D1.2, C2.3, R1.2, D2.2`

This list reflects the evaluator's fixed full-benchmark execution order in code (grouped by independent tasks first, then chain tasks).

### Execution groups
- Independent: `C1.1, C1.2, C2.2, C5.2`
- Chain 1: `C1.3 -> U1.2 -> D1.2`
- Chain 2: `C2.3 -> R1.2 -> D2.2`

### Chain requirements
- Deterministic chain order.
- Fallback rules must be explicit and test-covered.
- READ steps may use isolated execution workspace but must resolve dependent context from shared chain state where required.

### State lifecycle requirements
- Preserve pre-destroy tfstate snapshots as JSON artifacts before cleanup destroy.
- Keep cleanup behavior deterministic and auditable.

## 7. Metrics and Benchmarking Strategy

### Functional outcome contract
- `execution_successful`: Terraform execution outcome.
- `meets_requirements`: `execution_successful AND spec_passed AND post_state_passed` (or expected-failure match logic when task defines expected failure).
- Always report plan/apply/spec separately.

### pass@k requirements
- Formula: `1 - C(n-c, k) / C(n, k)`.
- Compute at task granularity before macro aggregation.
- Report support counts (`n`) per task for each `k`.

### Reporting slices
- Independent tasks only
- Chain tasks only
- Overall benchmark

## 8. Data Flow (Text)
1. Load config + dataset + task specs.
2. Resolve benchmark mode (single, chain, full 10-task).
3. For each sample and task:
   - Build prompt -> call provider -> extract code.
   - Run Terraform checks and spec validation.
   - Run post-state verification when applicable.
   - Emit task JSON and artifacts.
4. Aggregate metrics from emitted JSONs.
5. Emit run summary + manifest.

## 9. Reproducibility Requirements
- Deterministic task ordering and chain policies.
- Explicit seed propagation and logging.
- Stable output folder naming (config-driven folder_name).
- Concurrency lockfile for output dataset folder.
- Canonical metric implementation path (single source of truth).

## 10. System Architecture Diagram (Textual)

```text
[CLI Runner]
  -> [Config + Dataset + Spec Validators]
  -> [Task Orchestrator (single/chain/full)]
  -> [Prompt Builder]
  -> [Provider Adapter]
  -> [Code Extractor]
  -> [Terraform Executor]
  -> [Spec Checker + Post-State Verifier]
  -> [Result/Artifact Writer + State Snapshotter]
  -> [Metrics Engine]
  -> [Run Manifest + Summary Reporter]
```

Cross-cutting concerns:
- Structured logging + redaction
- Timeout/retry policy
- Lockfile-based write protection
- Security controls for optional tool downloads/parsing

## 11. Future Improvements
1. True safe concurrent sampling (or explicit sequential guarantee if retained).
2. Single canonical metrics package with deprecation path for legacy scripts.
3. Built-in dataset/spec lint command and CI gate.
4. Provider capability matrix and compatibility tests.
5. Replay mode for deterministic re-scoring without rerunning providers.
6. Chain continuity scorecards and richer failure taxonomy.
