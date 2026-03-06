#!/bin/bash
# Run all 4 experiments for SLM comparison
# Usage: bash run_experiments.sh

# Change directory to the python project root so relative paths like config/ open correctly
cd "$(dirname "$0")"

MODELS=("phi4_ollama" "phi4_openrouter" "qwen25coder_7b_ollama" "qwen25coder_openrouter" "codestral_openrouter" "mistral7b_lmstudio")
SAMPLES=3
SEED=42
TASK_CSV="tasks/vm_provisioning_tasks.csv"
CONFIG="config/openrouter_config.yaml"

echo "====== EXPERIMENT 1: Baseline Plan-Only ======"
for MODEL in "${MODELS[@]}"; do
    echo "Running baseline plan-only for: $MODEL"
    python src/evaluate.py \
        --config $CONFIG \
        --model $MODEL \
        --plan-only \
        --samples $SAMPLES \
        --seed $SEED \
        --no-confirm \
        --enhance-strat ""
done

echo "====== EXPERIMENT 2: CoT Plan-Only ======"
for MODEL in "${MODELS[@]}"; do
    echo "Running CoT plan-only for: $MODEL"
    python src/evaluate.py \
        --config $CONFIG \
        --model $MODEL \
        --plan-only \
        --samples $SAMPLES \
        --seed $SEED \
        --no-confirm \
        --enhance-strat COT
done

echo "====== EXPERIMENT 3: FSP Plan-Only ======"
for MODEL in "${MODELS[@]}"; do
    echo "Running FSP plan-only for: $MODEL"
    python src/evaluate.py \
        --config $CONFIG \
        --model $MODEL \
        --plan-only \
        --samples $SAMPLES \
        --seed $SEED \
        --no-confirm \
        --enhance-strat FSP
done

echo "====== EXPERIMENT 4: Full Apply - Best Models (edit list after Exp 1-3) ======"
BEST_MODELS=("phi4_openrouter" "qwen25coder_openrouter" "codestral_openrouter")
for MODEL in "${BEST_MODELS[@]}"; do
    echo "Running full apply chain for: $MODEL"
    python src/evaluate.py --config $CONFIG --model $MODEL --chain C1.3,U1.2,D1.2 --samples $SAMPLES --seed $SEED
    python src/evaluate.py --config $CONFIG --model $MODEL --chain C2.3,D2.2 --samples $SAMPLES --seed $SEED
    python src/evaluate.py --config $CONFIG --model $MODEL --task_id C1.1 --samples $SAMPLES --seed $SEED --no-confirm
    python src/evaluate.py --config $CONFIG --model $MODEL --task_id C1.2 --samples $SAMPLES --seed $SEED --no-confirm
    python src/evaluate.py --config $CONFIG --model $MODEL --task_id C2.2 --samples $SAMPLES --seed $SEED --no-confirm
    python src/evaluate.py --config $CONFIG --model $MODEL --task_id R1.2 --plan-only --samples $SAMPLES --seed $SEED --no-confirm
    python src/evaluate.py --config $CONFIG --model $MODEL --task_id C5.2 --plan-only --samples $SAMPLES --seed $SEED --no-confirm
done

echo "====== POST-RUN METRICS ======"
for MODEL in "${MODELS[@]}"; do
    python src/compute_metrics.py results/dataset/${MODEL} $TASK_CSV
    python src/compute_metrics.py results/dataset/${MODEL}_COT $TASK_CSV
    python src/compute_metrics.py results/dataset/${MODEL}_FSP $TASK_CSV
done

echo "All experiments complete."
