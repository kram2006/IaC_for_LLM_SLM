# Product Requirements Document (PRD)
## IaC Benchmark Framework for SLM/LLM Terraform Generation

## 1. Product Overview
Build a backend-only, provider-agnostic evaluation platform that benchmarks SLMs/LLMs on Terraform CRUD workflows for Xen Orchestra/XCP-NG. The platform must execute a strict 10-task benchmark with deterministic orchestration, robust validation, and reproducible metrics.

## 2. System Goals
1. Provide fair, reproducible benchmarking across multiple model providers.
2. Evaluate both syntactic correctness and task-intent correctness.
3. Support task dependency chains that emulate lifecycle workflows.
4. Generate machine-readable experiment artifacts for later analysis.
5. Minimize false positives/false negatives in pass/fail judgments.

## 3. Target Users
- AI systems engineers running model benchmarks.
- GenAI researchers comparing prompting or model strategies.
- Infrastructure automation teams validating IaC generation safety.
- MLOps teams integrating benchmark runs into CI/CD.

## 4. Supported Model Providers
Must support pluggable provider adapters with unified interface:
- OpenRouter-compatible APIs
- Ollama (local)
- LM Studio (OpenAI-compatible endpoint)
- HuggingFace inference endpoints

### Adapter requirements
- `chat_completion(messages, config)`
- deterministic seed handling when supported
- timeout + retry + backoff policy
- structured error classification (`rate_limit`, `timeout`, `bad_response`, `auth`)

## 5. Evaluation Pipeline Design

### Stage 1: Task Definition
- Canonical source: `tasks/vm_provisioning_tasks.csv`
- Canonical constraints: `config/task_specs.yaml`
- Hard fail if task IDs mismatch between CSV and spec.

### Stage 2: Prompt Construction
- Baseline prompt + optional enhancement strategy (`none|COT|FSP`).
- Task prompt templating must be deterministic and fully logged.

### Stage 3: Model Execution
- Provider adapter call with strict timeout and bounded retries.
- Capture raw model output exactly once per iteration.

### Stage 4: Output Capture
- Extract Terraform from response with deterministic parser.
- Persist both raw response and extracted code.

### Stage 5: Static Validation
- `terraform init` → `terraform validate` → `terraform plan`.
- Store stdout/stderr and exit code for each command.

### Stage 6: Intent/Constraint Validation
- Run category-specific checks over plan JSON:
  - CREATE, READ, UPDATE, DELETE
- Include chain-aware post-state verification for UPDATE/DELETE.

### Stage 7: Metric Calculation
- Functional metrics:
  - `plan_pass_rate`
  - `apply_pass_rate`
  - `spec_pass_rate`
  - `pass@k` (unbiased estimator)
- Semantic metrics (optional): BLEU, CodeBERTScore.
- Never label plan-only outcomes as apply outcomes.

### Stage 8: Result Logging
- Per-task artifact directory:
  - prompts, responses, tf code, terraform logs, spec logs, post-state logs.
- Atomic JSON write for final task record.

### Stage 9: Experiment Metadata Recording
- Required metadata:
  - model id/version
  - provider
  - seed
  - dataset hash
  - config hash
  - git commit SHA
  - timestamp UTC

## 6. Task Execution Architecture

## Supported benchmark set (exactly 10 tasks)
`C1.1, C1.2, C1.3, C2.2, C2.3, R1.2, U1.2, D1.2, D2.2, C5.2`

## Independent tasks
- `C1.1, C1.2, C2.2, C5.2`

## Chain A
- `C1.3 -> U1.2 -> D1.2`

## Chain B
- `C2.3 -> R1.2 -> D2.2`

### Chain execution requirements
- Preserve explicit order from chain definition.
- Reject unknown/missing chain task IDs.
- READ steps in chains must run in non-mutating mode and must not contaminate apply metrics.
- Workspace policy:
  - shared workspace for lifecycle continuity where needed,
  - isolated non-mutating workspace for READ verification when required.

## 7. Metrics and Benchmarking Strategy

### Functional scoring
- `task_success = apply_success AND spec_passed` (apply mode)
- `task_success = plan_success AND spec_passed` (plan-only mode)
- Track these as separate metric families.

### pass@k
- Use unbiased estimator:
  - `1 - C(n-c, k)/C(n, k)`
- Compute per task, then macro-average across tasks with sufficient samples.
- Report sample count used per task for each k.

### Aggregation policy
- Macro averages across tasks (not micro over files).
- Separate aggregate tables for:
  - independent tasks
  - chain tasks
  - all tasks

## 8. Data Flow
1. Config + dataset loaded.
2. Task list resolved (single task, all tasks, or chain).
3. For each sample and task:
   - prompt built -> model called -> code extracted.
   - Terraform static checks executed.
   - spec and post-state checks executed.
   - JSON entry emitted.
4. Metrics engine consumes JSON entries + reference data.
5. Experiment summary + leaderboard generated.

## 9. Reproducibility Requirements
- Deterministic task ordering.
- Seed propagation to provider adapters.
- Immutable run manifest and artifact paths.
- Lockfile to prevent concurrent writes to same output folder.
- No silent fallbacks for missing tasks/specs/required assets.
- Version pinning guidance for dependencies and Terraform provider.

## 10. System Architecture Diagram (Textual)

```text
[CLI / Runner]
   -> [Config Loader + Validator]
   -> [Task Resolver: all|single|chain]
   -> [Prompt Builder]
   -> [Provider Adapter Layer]
   -> [Code Extractor]
   -> [Terraform Executor]
   -> [Spec Validator + Post-State Verifier]
   -> [Result Writer (task JSON + artifacts)]
   -> [Metrics Engine]
   -> [Summary Reporter]
```

Cross-cutting services:
- Logging + redaction
- Retry/timeout policy
- Run manifest + metadata capture
- Concurrency guard (lockfile)

## 11. Future Improvements
1. Add strict JSON schema validation for all emitted task result files.
2. Add provider capability matrix (seed support, token limits, streaming support).
3. Add offline replay mode for deterministic re-scoring from saved responses.
4. Add chain-level scorecards (continuity correctness across chain transitions).
5. Add contamination checks for prompts/examples to prevent benchmark leakage.
6. Add CI job that validates dataset/spec consistency and script path integrity.
7. Add migration plan to deprecate legacy metric scripts and keep one canonical evaluator.
