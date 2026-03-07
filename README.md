# IaC-Eval: Infrastructure as Code Evaluation Framework

Backend-only evaluation framework to benchmark SLMs/LLMs on Terraform generation for **Xen Orchestra / XCP-NG** VM workflows.

---

## Project implementation readiness

Yes — the repository is implementation-ready once dependencies and credentials are in place.

Use this quick readiness checklist before running benchmark jobs:

- [ ] Install dependencies: `pip install -r requirements.txt`
- [ ] Confirm core CLIs run: `python src/evaluate.py --help` and `python llm_judge.py --help`
- [ ] Run regression checks: `python -m pytest tests/test_bug_fixes.py -q` (or full `python -m pytest -q`)
- [ ] Export required environment variables (`OPENROUTER_API_KEY`, `XO_USERNAME`, `XO_PASSWORD`; optionally `HF_TOKEN`)

---

## 1) What this project evaluates

- Active benchmark scope: **10 tasks** from `tasks/vm_provisioning_tasks.csv`
- CRUD + chain-aware workflows:
  - Independent: `C1.1, C1.2, C2.2, C5.2`
  - Chain 1: `C1.3 -> U1.2 -> D1.2`
  - Chain 2: `C2.3 -> R1.2 -> D2.2`
- Providers supported through config:
  - OpenRouter / OpenAI-compatible endpoints
  - Ollama / local models
  - HuggingFace inference endpoints
  - LM Studio (OpenAI-compatible base URL pattern)

> Note: Earlier repository documentation and templates referenced a 13-task set. The current active runner and dataset in this repository enforce the 10-task benchmark order.

---

## 2) Quick setup (commands to run)

Run all commands from repository root.

```bash
# (Optional) Create and activate a virtual env
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Optional but useful if not present
pip install pytest
```

Set credentials and platform variables (or place equivalents in `.env` / config placeholders):

```bash
export OPENROUTER_API_KEY="sk-or-v1-..."
export XO_USERNAME="your-xo-username"
export XO_PASSWORD="your-xo-password"

# Only for HuggingFace inference endpoint models
export HF_TOKEN="hf_..."
```

Sanity checks:

```bash
python src/evaluate.py --help
python src/compute_metrics.py --help
python llm_judge.py --help
```

---

## 3) Validation & test commands

```bash
# Focused regression tests
python -m pytest tests/test_bug_fixes.py -q

# Full test suite
python -m pytest -q
```

If you only want dependent tfstate/context regressions:

```bash
python -m pytest tests/test_bug_fixes.py -k "resolve_tfstate_context_path or extract_infra_context" -q
```

---

## 4) Core evaluation commands

### 4.1 Single-task plan-only run (safe quick check)

```bash
python src/evaluate.py \
  --config config/openrouter_config.yaml \
  --dataset tasks/vm_provisioning_tasks.csv \
  --model phi4_openrouter \
  --task_id C1.1 \
  --plan-only \
  --samples 1 \
  --no-confirm
```

### 4.2 Single-task multiple samples (for pass@k inputs)

```bash
python src/evaluate.py \
  --model phi4_openrouter \
  --task_id C1.1 \
  --plan-only \
  --samples 5 \
  --seed 42 \
  --no-confirm
```

> Important: when `--samples > 1`, the runner executes each sample sequentially (sample N+1 starts only after sample N fully completes, including VM cleanup). Every sample is fully isolated — no state, workspace, or API client is shared across samples.

### 4.3 Full chain execution (stateful lifecycle)

```bash
python src/evaluate.py \
  --model phi4_openrouter \
  --chain C1.3,U1.2,D1.2 \
  --samples 1 \
  --seed 42 \
  --enhance-strat COT
```

Second chain:

```bash
python src/evaluate.py \
  --model phi4_openrouter \
  --chain C2.3,R1.2,D2.2 \
  --samples 1 \
  --seed 42 \
  --enhance-strat FSP
```

### 4.4 Full 10-task benchmark run (default mode)

When `--task_id` and `--chain` are omitted, the runner executes the fixed 10-task benchmark order:

`C1.1 → C1.2 → C2.2 → C5.2 → [C1.3→U1.2→D1.2] → [C2.3→R1.2→D2.2]`

Each task or chain group runs **sequentially**. After every task (or chain group) finishes — once all evaluation artifacts (dataset JSON, logs, Terraform files) have been saved — any VMs created are **destroyed** before the next task starts. This ensures each task begins on clean infrastructure.

- **Independent tasks** (`C1.1`, `C1.2`, `C2.2`, `C5.2`): no shared state with each other or with chain groups. VMs destroyed immediately after each one.
- **Chain groups** (`C1.3→U1.2→D1.2`, `C2.3→R1.2→D2.2`): each task in the group has its own workspace directory and writes its own Terraform code. The `terraform.tfstate` is the only thing that flows between tasks — it is **explicitly copied** into each task's workspace from the last successful task. This lets Terraform see the infrastructure that earlier tasks provisioned (e.g. UPDATE can modify the VM that CREATE built). State fallback: if the intermediate task fails (e.g. U1.2 fails), the cleanup task (D1.2) receives the last successfully applied state (C1.3's state), never a partially-failed state. VMs are destroyed after the entire group completes using the last executed task's workspace.

```bash
python src/evaluate.py \
  --model phi4_openrouter \
  --samples 1 \
  --seed 42 \
  --no-confirm
```

### 4.5 Prompt enhancement variants

```bash
# Baseline
python src/evaluate.py --model phi4_openrouter --task_id C1.1 --plan-only --enhance-strat "" --no-confirm

# Chain-of-Thought
python src/evaluate.py --model phi4_openrouter --task_id C1.1 --plan-only --enhance-strat COT --no-confirm

# Few-shot prompting
python src/evaluate.py --model phi4_openrouter --task_id C1.1 --plan-only --enhance-strat FSP --no-confirm
```

---

## 4.6 Experiment types — detailed explanation

This framework supports **three prompt strategies** and a **built-in multi-turn repair mechanism** that is always active. Here is exactly what each one is and when it runs.

---

### Experiment A — Baseline (no `--enhance-strat`)

**What it is:** The model receives the raw task prompt and nothing else.

**How it works:**  
The user message is prepended with `"Here is the actual prompt: "` and sent directly to the model. No examples, no reasoning hints. This is the control condition — how well does the model generate correct Terraform without any guidance?

**When to use:**  
Always run Baseline first. It establishes the floor performance for comparison against COT and FSP.

**Command:**
```bash
python src/evaluate.py --model phi4_openrouter --task_id C1.1 --plan-only --no-confirm
# (or explicitly: --enhance-strat "")
```

---

### Experiment B — Few-Shot Prompting (FSP, `--enhance-strat FSP`)

**What it is:** Two fully-worked Terraform examples are prepended to the user's prompt before the model sees the actual task.

**How it works:**  
`FSP_prompt()` in `prompt_templates.py` prepends a header and two complete HCL examples (a 2 GB build server and a 4 GB/4-CPU database node — tasks deliberately chosen outside the 10-task test set to avoid data leakage). The model sees:

```
Here are a few examples of correct Xen Orchestra Terraform configurations:

Example prompt 1: ...
Example output 1: <complete main.tf>

Example prompt 2: ...
Example output 2: <complete main.tf>

Here is the actual prompt:
<task prompt>
```

The examples show the correct provider block, data source structure, and resource format. The model is expected to copy the pattern.

**When to use:**  
Use FSP when the model generates structurally wrong code (wrong provider, missing data sources, etc.). The examples anchor the output format.

**Command:**
```bash
python src/evaluate.py --model phi4_openrouter --task_id C1.1 --plan-only --enhance-strat FSP --no-confirm
```

---

### Experiment C — Chain-of-Thought (COT, `--enhance-strat COT`)

**What it is:** Two worked examples with explicit step-by-step reasoning are prepended to the user's prompt.

**How it works:**  
`CoT_prompt()` in `prompt_templates.py` prepends the same two example tasks as FSP, but each example also includes a reasoning trace: *"Let's think step by step. First, identify the resources… Second, fill in the VM attributes… Third, connect resources together…"*. After the examples the model is told *"Here is the actual prompt to answer. Let's think step by step:"*.

The goal is to encourage the model to reason explicitly before generating code, reducing arithmetic errors (memory in bytes, disk in bytes) and structural mistakes.

**When to use:**  
Use COT when the model makes logical errors (wrong byte values, missing attributes) rather than purely structural ones. COT helps with tasks that require multi-step reasoning (e.g. "4 GB RAM in bytes = 4 × 1024³ = 4294967296").

**Command:**
```bash
python src/evaluate.py --model phi4_openrouter --task_id C1.1 --plan-only --enhance-strat COT --no-confirm
```

---

### Experiment D — Multi-turn Repair (always active, not a separate flag)

**What it is:** When Terraform fails (init / validate / plan / apply), the framework automatically sends a *repair prompt* and asks the model to fix its own code. This repeats up to 10 times.

**How it works:**  
`multi_turn_plan_error_prompt()` in `prompt_templates.py` is called on every failure. It builds a **stateless** repair message containing:  
1. The original task prompt  
2. The model's broken Terraform code  
3. The exact Terraform error message  
4. Requirements for the corrected code  

A fresh message list is created for each repair turn (no growing conversation history), matching the IaC-Eval paper's stateless multi-turn approach. The model gets the full context in one user message.

**This is NOT a separate experiment** — it runs on top of whichever enhance strategy you chose (Baseline, FSP, or COT). If the first generation fails, the multi-turn loop kicks in. If it passes on iteration 1, the loop never runs.

**Metrics tracking:**  
`iterations_needed` in the dataset JSON records how many turns were needed. `worked_as_generated = true` means iteration 1 succeeded; `worked_after_fixes = true` means a repair turn was needed.

**Key numbers:**
- Maximum repair turns: **10**  
- Spec-check failures allowed before stopping: **2**  
- Error history kept: **last 5 errors** (to avoid unbounded context growth)

---

### Summary table

| Experiment | Flag | What changes | Always active? |
|---|---|---|---|
| Baseline | `--enhance-strat ""` (default) | Raw prompt only | ✓ |
| Few-Shot (FSP) | `--enhance-strat FSP` | 2 worked HCL examples prepended | ✓ |
| Chain-of-Thought (COT) | `--enhance-strat COT` | 2 step-by-step reasoning examples prepended | ✓ |
| Multi-turn repair | (no flag) | Automatic self-correction on Terraform failure, up to 10 turns | Always on |

Each experiment produces its own output folder (e.g. `results/dataset/phi4_or_COT/`) so results never mix.

---

## 5) Metrics, analysis, and automation commands

### 5.1 Compute aggregate metrics from run outputs

Use model `folder_name` from `config/openrouter_config.yaml` (fallback is model key).

```bash
python src/compute_metrics.py results/dataset/Phi4_Ollama_Results tasks/vm_provisioning_tasks.csv
python src/compute_metrics.py results/dataset/phi4_or tasks/vm_provisioning_tasks.csv
```

### 5.2 Run packaged experiment script

```bash
bash run_experiments.sh
```

### 5.3 Complexity scoring

```bash
python src/complexity_scorer.py
python print_complexity.py
```

### 5.4 Optional post-hoc LLM judge

```bash
python llm_judge.py \
  --folder results/dataset/phi4_or \
  --config config/openrouter_config.yaml \
  --judge-model openai/gpt-4o
```

---

## 6) Output directories you should inspect

- Task result JSONs:
  - `results/dataset/<model_folder_name>/*.json`
- Terraform artifacts:
  - `results/terraform_code/<model_folder_name>/<task_id_lower>/...`
- Lockfile (present while evaluation is running):
  - `results/dataset/<model_folder_name>/.evaluation_in_progress`
- Pre-destroy state snapshots:
  - `<workspace>/state_snapshots/terraform_tfstate_pre_destroy_*.json`

---

## 7) Troubleshooting quick guide

- `Model '<name>' not found in config`  
  -> Use a model key defined in `config/openrouter_config.yaml`.

- `Unresolved API key placeholder ...`  
  -> Export the referenced environment variable before running.

- `Evaluation still running ... .evaluation_in_progress`  
  -> Wait for completion; do not run metric aggregation concurrently.

- HuggingFace inference failures  
  -> Set `HF_TOKEN`, verify endpoint/model access, and install `huggingface_hub`.

---

## 8) Internal modules (where each responsibility lives)

- `src/evaluate.py` — CLI, orchestration, mode/task resolution, lockfile lifecycle
- `src/eval_core.py` — per-task execution and iterative repair loop
- `src/api_client.py` — provider requests/retries/timeouts
- `src/prompt_templates.py` — prompt construction/enhancement templates
- `src/spec_checker.py` — CREATE/READ/UPDATE/DELETE intent validation
- `src/xo_client.py` — XO integration and VM verification
- `src/json_generator.py` — task-level result records
- `src/compute_metrics.py` — pass@k and semantic metrics

---

## 9) Explanation: Pipeline workflow (every layer, every stage)

### Layer A — Input & Configuration Layer
1. **Config load** (`evaluate.py`)  
   Reads `config/openrouter_config.yaml`, resolves env placeholders, validates schema.
2. **Dataset load** (`evaluate.py`)  
   Reads `tasks/vm_provisioning_tasks.csv`.
3. **Mode resolution** (`evaluate.py`)  
   Selects one of:
   - single task (`--task_id`)
   - chain (`--chain`)
   - full fixed 10-task benchmark (default).

### Layer B — Orchestration Layer
4. **Task ordering / chain policy** (`evaluate.py`)  
   Applies fixed benchmark order and chain fallback rules.
5. **Sample loop** (`evaluate.py`)  
   Executes requested `--samples` per task/chain **sequentially** (sample N+1 starts only after sample N fully completes, including VM cleanup). Within each sample, tasks and chain groups run **sequentially** in fixed order; VMs are destroyed after each task/group before the next begins.
6. **Workspace and lock management** (`evaluate.py`)  
   Creates output folders and `.evaluation_in_progress`, cleans up at completion.

### Layer C — Prompt & Inference Layer
7. **Prompt assembly** (`eval_core.py`, `prompt_templates.py`)  
   Builds system+user messages and applies strategy (`none`, `COT`, `FSP`).
8. **Model call** (`api_client.py`)  
   Sends request to configured provider/client with timeout/retry/seed handling.
9. **Output capture** (`eval_core.py`)  
   Stores raw LLM text and extracts Terraform/HCL snippet for execution.

### Layer D — Terraform Validation Layer
10. **Static checks** (`eval_core.py`, `eval_utils.py`)  
    Runs `terraform init`, `terraform validate`, `terraform plan`.
11. **Repair loop** (`eval_core.py`)  
    On failure, builds targeted fix prompt and retries up to configured limit.

### Layer E — Intent & State Validation Layer
12. **Spec validation** (`spec_checker.py`)  
    Parses plan JSON and validates task constraints by CRUD category.
13. **Post-state verification** (`xo_client.py`, `eval_core.py`)  
    For stateful tasks, confirms expected infrastructure outcomes.
14. **Dependent context injection** (`eval_core.py`)  
    READ/UPDATE/DELETE chain tasks can consume context extracted from the preceding/shared chain workspace tfstate (for dependent IDs/UUIDs).

### Layer F — Result & Metrics Layer
15. **Result record generation** (`json_generator.py`)  
    Persists task-level JSON with execution status, spec outcomes, timings, iterations.
16. **Artifact persistence** (`eval_core.py`)  
    Saves Terraform files, histories, logs, and state-related artifacts.
17. **Metrics aggregation** (`compute_metrics.py`)  
    Computes task-grouped pass@k (unbiased estimator) and optional BLEU/CodeBERT metrics.

### Layer G — Cleanup & Reproducibility Layer
18. **Cleanup destroy (when applicable)** (`evaluate.py`)  
    Handles destroy flow for independent tasks/chains as configured.
19. **Pre-destroy state snapshot** (`evaluate.py`)  
    Preserves `terraform.tfstate` JSON in `state_snapshots` before destroy.
20. **Run completion** (`evaluate.py`)  
    Removes lockfile and leaves deterministic artifacts for reproducibility/audit.
