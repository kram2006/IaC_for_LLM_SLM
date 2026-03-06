# IaC-Eval: Infrastructure as Code Evaluation Framework

An automated evaluation framework for benchmarking Small Language Models (SLMs) on Terraform code generation for **Xen Orchestra / XCP-NG** VM provisioning.

## Features

- **Asynchronous Execution Engine**: Fully 100% async pipeline using `asyncio` for high-concurrency evaluation.
- **Parallel Pass@k Sampling**: Execute independent samples (Pass@1, Pass@n) in parallel across multiple LLM calls and Terraform processes.
- **Research-Aligned Multi-turn**: Stateless self-correction pipeline that mirrors the original IaC-Eval (NeurIPS 2024) paper logic.
- **Rule-as-Strategy Validation**: Extensible Strategy Pattern for Terraform Plan validation (CREATE, READ, UPDATE, DELETE rules).
- **TTL & Resource Caching**: Optimized Xen Orchestra client with locking and TTL caching to handle parallel API requests efficiently.
- **Pydantic Schema Validation**: Robust configuration and task specification validation using Pydantic models.
- **Complexity Scoring**: Automated measurement of HCL complexity (LOC, resources, interconnections) to stratify difficulty levels (1-6).
- **Chained Task Execution**: Test sequential infrastructure lifecycles with state preservation across async workers.
- **Prompting Strategies**: Integrated support for Zero-Shot, Chain-of-Thought (CoT), Few-Shot Prompting (FSP), and Multi-turn Repair.

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run unit tests to verify compliance logic
python -m pytest tests/test_compliance.py

# 3. Set API Keys and Platform details in .env or config/openrouter_config.yaml
export OPENROUTER_API_KEY="sk-or-v1-..."
```

## Running Evaluations

### Standard Evaluation (Pass@1)
```bash
python src/evaluate.py --model phi4_openrouter --task_id C1.1 --plan-only
```

### Parallel Pass@5 Sampling
Evaluate a task 5 times in parallel to calculate Pass@5:
```bash
python src/evaluate.py --model phi4_openrouter --task_id C1.1 --samples 5 --plan-only --no-confirm
```

### Multi-turn Repair
The pipeline automatically enters multi-turn repair mode (stateless) if a model's first attempt fails the `terraform plan` phase.

### Chained Lifecycle (Sequential within Sample)
Evaluate a full Create → Update → Delete chain with state sharing:
```bash
python src/evaluate.py --model phi4_openrouter --chain C1.3,U1.2,D1.2 --enhance-strat COT
```

## Output & Metrics

### Computing Pass@k
The `compute_metrics.py` script automatically groups results by Task ID to calculate Pass@k metrics and semantic similarity:
```bash
python src/compute_metrics.py results/dataset/[ModelFolder] tasks/vm_provisioning_tasks.csv
```

### Structural Complexity Analysis
To analyze the difficulty distribution of your reference HCL files:
```bash
python src/complexity_scorer.py
```

## Internal Architecture

- **`src/evaluate.py`**: Main entry point (Asynchronous Orchestrator).
- **`src/eval_core.py`**: Core execution loop (Stateless for repairs).
- **`src/spec_checker.py`**: Strategy-based Plan Validation Engine.
- **`src/prompt_templates.py`**: Research-backed templates with hardcoded XO environment details.
- **`src/xo_client.py`**: Performance-optimized XO WebSocket client.
