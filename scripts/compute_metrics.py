"""
Step 2 & 3: Compute similarity metrics + functional pass@k evaluation.

Metrics computed:
  - BLEU (via sacrebleu, multi-reference)
  - METEOR (via nltk)
  - ROUGE-3 (via rouge-score)
  - CodeBLEU (n-gram only for HCL — AST/dataflow not supported)
  - CodeBERTScore (via code_bert_score, best-match among references)
  - Functional pass@1 (execution_successful && meets_requirements)

References: 3 LLM outputs (DeepSeek, Gemini, GPT)
Candidate: Qwen 14B Ollama (SLM)
"""

import json
import os
import sys
import warnings
warnings.filterwarnings("ignore")

# ── Config ──────────────────────────────────────────────────────────────────────
COMPARISON_JSON = r"c:\Users\kalar\Downloads\llm_eval_RK\iac-eval-main\comparison\comparison_dataset.json"
OUTPUT_DIR = r"c:\Users\kalar\Downloads\llm_eval_RK\iac-eval-main\comparison"

CANDIDATE_KEY = "code_Qwen_14B_Ollama"
REFERENCE_KEYS = ["ref_DeepSeek_v3.2", "ref_Gemini_3_Pro", "ref_GPT_5.2_Codex"]

ALL_MODEL_KEYS = {
    "Qwen_14B_Ollama": "code_Qwen_14B_Ollama",
    "Phi4_14B_Ollama": "code_Phi4_14B_Ollama",
    "DeepSeek_v3.2": "ref_DeepSeek_v3.2",
    "Gemini_3_Pro": "ref_Gemini_3_Pro",
    "GPT_5.2_Codex": "ref_GPT_5.2_Codex",
    "Sonnet_4_5": "ref_Sonnet_4_5",
    "Kimi_k2": "ref_Kimi_k2",
    "Qwen_3_Coder": "ref_Qwen_3_Coder",
}


def load_comparison_data():
    with open(COMPARISON_JSON, 'r', encoding='utf-8') as f:
        return json.load(f)


# ═══════════════════════════════════════════════════════════════════════════════
# METRIC 1: BLEU (SacreBLEU — multi-reference)
# ═══════════════════════════════════════════════════════════════════════════════
def compute_bleu_scores(data):
    """Compute corpus-level and per-task BLEU using SacreBLEU."""
    try:
        import sacrebleu
    except ImportError:
        print("  ⚠ sacrebleu not installed. Run: pip install sacrebleu")
        return None

    results = {}
    for model_name, model_key in ALL_MODEL_KEYS.items():
        # For each model, compute BLEU against the OTHER models as references
        other_keys = [v for k, v in ALL_MODEL_KEYS.items() if k != model_name]

        per_task = []
        candidates = []
        refs_lists = [[] for _ in other_keys]

        for row in data:
            cand = row.get(model_key, "")
            if not cand:
                cand = " "  # sacrebleu needs non-empty
            candidates.append(cand)
            for i, ref_key in enumerate(other_keys):
                ref = row.get(ref_key, "")
                if not ref:
                    ref = " "
                refs_lists[i].append(ref)

            # Per-task BLEU
            task_refs = [[row.get(rk, " ") or " "] for rk in other_keys]
            task_bleu = sacrebleu.sentence_bleu(cand, [r[0] for r in task_refs])
            per_task.append({
                "task_id": row["task_id"],
                "bleu": round(task_bleu.score, 2)
            })

        # Corpus-level BLEU
        corpus_bleu = sacrebleu.corpus_bleu(candidates, refs_lists)
        results[model_name] = {
            "corpus_bleu": round(corpus_bleu.score, 2),
            "signature": str(corpus_bleu),
            "per_task": per_task
        }

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# METRIC 2: METEOR
# ═══════════════════════════════════════════════════════════════════════════════
def compute_meteor_scores(data):
    """Compute METEOR scores using nltk."""
    try:
        import nltk
        from nltk.translate.meteor_score import meteor_score
        try:
            nltk.data.find('corpora/wordnet')
        except LookupError:
            nltk.download('wordnet', quiet=True)
            nltk.download('omw-1.4', quiet=True)
    except ImportError:
        print("  ⚠ nltk not installed. Run: pip install nltk")
        return None

    results = {}
    for model_name, model_key in ALL_MODEL_KEYS.items():
        other_keys = [v for k, v in ALL_MODEL_KEYS.items() if k != model_name]

        per_task = []
        all_scores = []

        for row in data:
            cand = row.get(model_key, "") or ""
            refs = [row.get(rk, "") or "" for rk in other_keys]

            # METEOR expects tokenized (list of words)
            cand_tokens = cand.split()
            ref_tokens_list = [r.split() for r in refs]

            if not cand_tokens:
                score = 0.0
            else:
                score = meteor_score(ref_tokens_list, cand_tokens)

            per_task.append({"task_id": row["task_id"], "meteor": round(score, 4)})
            all_scores.append(score)

        avg = sum(all_scores) / len(all_scores) if all_scores else 0
        results[model_name] = {
            "average_meteor": round(avg, 4),
            "per_task": per_task
        }

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# METRIC 3: ROUGE-3
# ═══════════════════════════════════════════════════════════════════════════════
def compute_rouge3_scores(data):
    """Compute ROUGE-3 (trigram) F1 scores."""
    try:
        from rouge_score import rouge_scorer
    except ImportError:
        print("  ⚠ rouge-score not installed. Run: pip install rouge-score")
        return None

    scorer = rouge_scorer.RougeScorer(['rouge3'], use_stemmer=False)

    results = {}
    for model_name, model_key in ALL_MODEL_KEYS.items():
        other_keys = [v for k, v in ALL_MODEL_KEYS.items() if k != model_name]

        per_task = []
        all_f1 = []

        for row in data:
            cand = row.get(model_key, "") or ""
            refs = [row.get(rk, "") or "" for rk in other_keys]

            # Multi-reference: take the max ROUGE-3 F1 across references
            best_f1 = 0
            for ref in refs:
                if ref.strip():
                    scores = scorer.score(ref, cand)
                    f1 = scores['rouge3'].fmeasure
                    best_f1 = max(best_f1, f1)

            per_task.append({"task_id": row["task_id"], "rouge3_f1": round(best_f1, 4)})
            all_f1.append(best_f1)

        avg = sum(all_f1) / len(all_f1) if all_f1 else 0
        results[model_name] = {
            "average_rouge3_f1": round(avg, 4),
            "per_task": per_task
        }

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# METRIC 4: CodeBLEU (n-gram only — no AST/dataflow for HCL)
# ═══════════════════════════════════════════════════════════════════════════════
def compute_codebleu_scores(data):
    """
    Compute CodeBLEU n-gram component only.
    NOTE: Full CodeBLEU (with AST match + data-flow match) is NOT supported
    for Terraform/HCL. We compute only the n-gram and weighted n-gram components.
    This limitation MUST be documented in the paper.
    """
    try:
        from codebleu import calc_codebleu
    except ImportError:
        print("  ⚠ codebleu not installed. Run: pip install codebleu")
        print("  ℹ Will skip CodeBLEU. Note: HCL is not officially supported by CodeBLEU.")
        return None

    results = {}
    for model_name, model_key in ALL_MODEL_KEYS.items():
        other_keys = [v for k, v in ALL_MODEL_KEYS.items() if k != model_name]

        per_task = []
        all_scores = []

        for row in data:
            cand = row.get(model_key, "") or ""
            refs = [row.get(rk, "") or "" for rk in other_keys]

            if not cand.strip():
                per_task.append({"task_id": row["task_id"], "codebleu": 0.0})
                all_scores.append(0.0)
                continue

            try:
                # Use python as closest proxy for HCL structure (keyword-heavy)
                result = calc_codebleu(
                    references=[refs],
                    predictions=[cand],
                    lang="python",
                    weights=(0.5, 0.5, 0.0, 0.0),  # n-gram + weighted n-gram only
                )
                score = result.get("codebleu", 0.0)
            except Exception as e:
                print(f"  ⚠ CodeBLEU error for {row['task_id']}: {e}")
                score = 0.0

            per_task.append({"task_id": row["task_id"], "codebleu": round(score, 4)})
            all_scores.append(score)

        avg = sum(all_scores) / len(all_scores) if all_scores else 0
        results[model_name] = {
            "average_codebleu": round(avg, 4),
            "note": "n-gram components only (weights: 0.5 ngram + 0.5 weighted_ngram + 0.0 AST + 0.0 dataflow). HCL not natively supported.",
            "per_task": per_task
        }

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# METRIC 5: CodeBERTScore
# ═══════════════════════════════════════════════════════════════════════════════
def compute_codebertscore(data):
    """
    Compute CodeBERTScore using code_bert_score.
    For multi-reference: compute per (candidate, ref_j) pair, take max F1.
    NOTE: HCL is not officially supported. We use lang='python' as proxy.
    """
    try:
        from code_bert_score import score as cbs_score
    except ImportError:
        print("  ⚠ code-bert-score not installed. Run: pip install code-bert-score")
        return None

    results = {}
    for model_name, model_key in ALL_MODEL_KEYS.items():
        other_keys = [v for k, v in ALL_MODEL_KEYS.items() if k != model_name]

        per_task = []
        all_f1 = []

        for row in data:
            cand = row.get(model_key, "") or ""
            refs = [row.get(rk, "") or "" for rk in other_keys]

            if not cand.strip():
                per_task.append({"task_id": row["task_id"], "codebertscore_f1": 0.0})
                all_f1.append(0.0)
                continue

            # Compute against each reference, take max F1
            best_f1 = 0.0
            for ref in refs:
                if not ref.strip():
                    continue
                try:
                    p, r, f1, f3 = cbs_score(
                        cands=[cand],
                        refs=[ref],
                        lang="python"
                    )
                    best_f1 = max(best_f1, f1[0].item())
                except Exception as e:
                    print(f"  ⚠ CodeBERTScore error for {row['task_id']}: {e}")

            per_task.append({"task_id": row["task_id"], "codebertscore_f1": round(best_f1, 4)})
            all_f1.append(best_f1)

        avg = sum(all_f1) / len(all_f1) if all_f1 else 0
        results[model_name] = {
            "average_codebertscore_f1": round(avg, 4),
            "note": "lang='python' used as proxy for HCL. Max F1 across references.",
            "per_task": per_task
        }

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# METRIC 6: Functional pass@1
# ═══════════════════════════════════════════════════════════════════════════════
def compute_functional_scores(data):
    """
    Compute pass@k functional evaluation.
    pass@k = 1 if (execution_successful AND meets_requirements) within k iterations.
    """
    results = {}
    for model_name, model_key in ALL_MODEL_KEYS.items():
        prefix = model_key

        per_task = []
        p1_count = p2_count = p3_count = total_success = 0

        for row in data:
            success = row.get(f"{prefix}_exec_success", False) and row.get(f"{prefix}_meets_req", False)
            iters = row.get(f"{prefix}_iterations", -1)
            
            p1 = 1 if (success and iters <= 1) else 0
            p2 = 1 if (success and iters <= 2) else 0
            p3 = 1 if (success and iters <= 3) else 0
            
            p1_count += p1
            p2_count += p2
            p3_count += p3
            if success: total_success += 1

            per_task.append({
                "task_id": row["task_id"],
                "pass@1": p1,
                "pass@2": p2,
                "pass@3": p3,
                "final_success": 1 if success else 0,
                "iterations": iters,
            })

        n = len(data)
        results[model_name] = {
            "pass@1_rate": round(p1_count / n, 4) if n > 0 else 0,
            "pass@2_rate": round(p2_count / n, 4) if n > 0 else 0,
            "pass@3_rate": round(p3_count / n, 4) if n > 0 else 0,
            "final_success_rate": round(total_success / n, 4) if n > 0 else 0,
            "total_tasks": n,
            "per_task": per_task,
        }

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN: Run all metrics and produce final report
# ═══════════════════════════════════════════════════════════════════════════════
def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("Loading comparison dataset...")
    data = load_comparison_data()
    print(f"  {len(data)} tasks loaded.\n")

    all_metrics = {}

    # 1. BLEU
    print("Computing BLEU (SacreBLEU)...")
    bleu = compute_bleu_scores(data)
    if bleu:
        all_metrics["bleu"] = bleu
        for model, result in bleu.items():
            print(f"  {model}: Corpus BLEU = {result['corpus_bleu']}")

    # 2. METEOR
    print("\nComputing METEOR...")
    meteor = compute_meteor_scores(data)
    if meteor:
        all_metrics["meteor"] = meteor
        for model, result in meteor.items():
            print(f"  {model}: Avg METEOR = {result['average_meteor']}")

    # 3. ROUGE-3
    print("\nComputing ROUGE-3...")
    rouge3 = compute_rouge3_scores(data)
    if rouge3:
        all_metrics["rouge3"] = rouge3
        for model, result in rouge3.items():
            print(f"  {model}: Avg ROUGE-3 F1 = {result['average_rouge3_f1']}")

    # 4. CodeBLEU (n-gram only)
    print("\nComputing CodeBLEU (n-gram only, HCL limitation noted)...")
    codebleu = compute_codebleu_scores(data)
    if codebleu:
        all_metrics["codebleu"] = codebleu
        for model, result in codebleu.items():
            print(f"  {model}: Avg CodeBLEU = {result['average_codebleu']}")

    # 5. CodeBERTScore
    print("\nComputing CodeBERTScore...")
    cbs = compute_codebertscore(data)
    if cbs:
        all_metrics["codebertscore"] = cbs
        for model, result in cbs.items():
            print(f"  {model}: Avg CodeBERTScore F1 = {result['average_codebertscore_f1']}")

    # 6. Functional
    print("\nComputing Functional pass@k...")
    functional = compute_functional_scores(data)
    if functional:
        all_metrics["functional"] = functional
        for model, result in functional.items():
            print(f"  {model}: p@1={result['pass@1_rate']}, p@2={result['pass@2_rate']}, p@3={result['pass@3_rate']}")

    # Save full metrics report
    report_path = os.path.join(OUTPUT_DIR, "evaluation_metrics.json")
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(all_metrics, f, indent=2, ensure_ascii=False)
    print(f"\n✅ Full evaluation metrics saved to: {report_path}")

    # ── Summary Leaderboard ─────────────────────────────────────────────────
    print("\n" + "="*80)
    print("                    EVALUATION LEADERBOARD")
    print("="*80)
    print(f"{'Model':<22} {'BLEU':>8} {'METEOR':>8} {'ROUGE-3':>9} {'CodeBLEU':>10} {'CBS-F1':>8} {'p@1':>6} {'p@2':>6} {'p@3':>6}")
    print("-"*80)

    for model_name in ALL_MODEL_KEYS:
        bleu_val = bleu[model_name]["corpus_bleu"] if bleu and model_name in bleu else "N/A"
        meteor_val = meteor[model_name]["average_meteor"] if meteor and model_name in meteor else "N/A"
        rouge_val = rouge3[model_name]["average_rouge3_f1"] if rouge3 and model_name in rouge3 else "N/A"
        cb_val = codebleu[model_name]["average_codebleu"] if codebleu and model_name in codebleu else "N/A"
        cbs_val = cbs[model_name]["average_codebertscore_f1"] if cbs and model_name in cbs else "N/A"
        p1 = functional[model_name]["pass@1_rate"] if functional and model_name in functional else 0
        p2 = functional[model_name]["pass@2_rate"] if functional and model_name in functional else 0
        p3 = functional[model_name]["pass@3_rate"] if functional and model_name in functional else 0

        marker = " ★" if model_name == "Qwen_14B_Ollama" else ""
        print(f"{model_name + marker:<22} {bleu_val:>8} {meteor_val:>8} {rouge_val:>9} {cb_val:>10} {cbs_val:>8} {p1:>6.2f} {p2:>6.2f} {p3:>6.2f}")

    print("="*80)
    print("★ = SLM Candidate (local Ollama)")
    print("\nNotes:")
    print("  - BLEU: SacreBLEU corpus-level, multi-reference (Papineni et al., 2002)")
    print("  - METEOR: Banerjee & Lavie (2005), averaged over tasks")
    print("  - ROUGE-3: Lin (2004), max F1 across references per task")
    print("  - CodeBLEU: Ren et al. (2020), n-gram components only (HCL not supported)")
    print("  - CBS-F1: CodeBERTScore, max F1 across refs, lang='python' proxy")
    print("  - pass@1: Functional success (exec_successful && meets_requirements)")
    print("  - 1st-try: worked_as_generated (no retries needed)")


if __name__ == "__main__":
    main()
