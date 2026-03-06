"""
evaluate_qwen_vs_claude_official.py
Qwen2.5-Coder-14B (Ollama) vs Claude Sonnet 4.5 (Ground Truth)

Reads from the golden comparison_dataset.json and computes:
  1. BLEU (SacreBLEU)
  2. METEOR (NLTK)
  3. ROUGE-3 (rouge-score)
  4. CodeBLEU (weighted n-gram, tree-sitter HCL)
  5. CodeBERTScore (code-bert-score)
  6. pass@1  (execution_successful && meets_requirements)
  7. First-Try Rate (worked_as_generated)
"""

import json, os, math, logging, warnings
from pathlib import Path
from collections import Counter

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────
BASE_DIR = Path(r"c:\Users\kalar\Downloads\llm_eval_RK\iac-eval-main")
COMPARISON_JSON = BASE_DIR / "comparison" / "comparison_dataset.json"
OUTPUT_DIR = BASE_DIR / "results" / "comparison_official"

# ── Keys in comparison_dataset.json ────────────────────────────────────────
CANDIDATE_CODE_KEY = "code_Qwen_14B_Ollama"
REFERENCE_CODE_KEY = "ref_Sonnet_4_5"

CANDIDATE_PREFIX = "code_Qwen_14B_Ollama"
REFERENCE_PREFIX = "ref_Sonnet_4_5"

# ── HCL keywords for weighted n-gram ──────────────────────────────────────
keyword_weight = 3

def get_hcl_keywords():
    return {
        "terraform", "required_providers", "source", "version",
        "provider", "data", "resource", "variable", "output", "locals",
        "module", "name_label", "pool_id", "network_id", "sr_id",
        "template", "cpus", "memory_max", "auto_poweron", "size",
        "tags", "cdrom", "disk", "network", "insecure",
        "xenorchestra", "xenorchestra_vm", "xenorchestra_pool",
        "xenorchestra_network", "xenorchestra_sr", "xenorchestra_template",
    }


def _tokenize(code):
    import re
    return re.findall(r'\w+|[^\s\w]', code)


# ═══════════════════════════════════════════════════════════════════════════
# Data Loading
# ═══════════════════════════════════════════════════════════════════════════
def load_data():
    with open(COMPARISON_JSON, 'r', encoding='utf-8') as f:
        data = json.load(f)
    result = []
    for row in data:
        tid = row.get("task_id", "")
        # Filter out C5.2 as requested
        if tid == "C5.2":
            continue

        candidate_code = row.get(CANDIDATE_CODE_KEY, "")
        reference_code = row.get(REFERENCE_CODE_KEY, "")
        if candidate_code and reference_code:
            result.append({
                "task_id": row["task_id"],
                "task_description": row.get("task_description", ""),
                "candidate": candidate_code,
                "reference": reference_code,
                # Functional metadata — candidate
                "cand_exec_success": row.get(f"{CANDIDATE_PREFIX}_exec_success", False),
                "cand_meets_req": row.get(f"{CANDIDATE_PREFIX}_meets_req", False),
                "cand_first_try": row.get(f"{CANDIDATE_PREFIX}_first_try", False),
                "cand_iterations": row.get(f"{CANDIDATE_PREFIX}_iterations", -1),
                "cand_fixes": row.get(f"{CANDIDATE_PREFIX}_fixes_needed", -1),
                # Functional metadata — reference
                "ref_exec_success": row.get(f"{REFERENCE_PREFIX}_exec_success", False),
                "ref_meets_req": row.get(f"{REFERENCE_PREFIX}_meets_req", False),
                "ref_first_try": row.get(f"{REFERENCE_PREFIX}_first_try", False),
                "ref_iterations": row.get(f"{REFERENCE_PREFIX}_iterations", -1),
                "ref_fixes": row.get(f"{REFERENCE_PREFIX}_fixes_needed", -1),
            })
    return result


# ═══════════════════════════════════════════════════════════════════════════
# METRIC 1: BLEU (SacreBLEU)
# ═══════════════════════════════════════════════════════════════════════════
def compute_bleu(data):
    import sacrebleu
    candidates = [d["candidate"] for d in data]
    references = [[d["reference"]] for d in data]
    # sacrebleu expects references as list of lists (one list per reference stream)
    ref_stream = [[d["reference"] for d in data]]
    corpus = sacrebleu.corpus_bleu(candidates, ref_stream)
    per_task = []
    for d in data:
        s = sacrebleu.sentence_bleu(d["candidate"], [d["reference"]])
        per_task.append({"task_id": d["task_id"], "bleu": round(s.score, 2)})
    return round(corpus.score, 2), per_task


# ═══════════════════════════════════════════════════════════════════════════
# METRIC 2: METEOR
# ═══════════════════════════════════════════════════════════════════════════
def compute_meteor(data):
    import nltk
    from nltk.translate.meteor_score import meteor_score
    for res in ['wordnet', 'omw-1.4']:
        try: nltk.data.find(f'corpora/{res}')
        except LookupError: nltk.download(res, quiet=True)

    scores = []
    per_task = []
    for d in data:
        cand_tokens = d["candidate"].split()
        ref_tokens = d["reference"].split()
        s = meteor_score([ref_tokens], cand_tokens) if cand_tokens else 0.0
        scores.append(s)
        per_task.append({"task_id": d["task_id"], "meteor": round(s * 100, 2)})
    avg = sum(scores) / len(scores) * 100 if scores else 0
    return round(avg, 2), per_task


# ═══════════════════════════════════════════════════════════════════════════
# METRIC 3: ROUGE-3
# ═══════════════════════════════════════════════════════════════════════════
def compute_rouge3(data):
    from rouge_score import rouge_scorer
    scorer = rouge_scorer.RougeScorer(['rouge3'], use_stemmer=False)
    scores = []
    per_task = []
    for d in data:
        s = scorer.score(d["reference"], d["candidate"])
        f1 = s['rouge3'].fmeasure
        scores.append(f1)
        per_task.append({"task_id": d["task_id"], "rouge3_f1": round(f1 * 100, 2)})
    avg = sum(scores) / len(scores) * 100 if scores else 0
    return round(avg, 2), per_task


# ═══════════════════════════════════════════════════════════════════════════
# METRIC 4: CodeBLEU (weighted n-gram + tree-sitter HCL)
# ═══════════════════════════════════════════════════════════════════════════
def compute_weighted_bleu(cd_tokens, ref_tokens, keywords):
    cd_count = Counter(cd_tokens)
    ref_count = Counter(ref_tokens)
    numerator = denominator = 0
    for token, count in cd_count.items():
        w = keyword_weight if token in keywords else 1
        numerator += w * min(count, ref_count.get(token, 0))
        denominator += w * count
    p1 = numerator / denominator if denominator > 0 else 0
    c, r = len(cd_tokens), len(ref_tokens)
    if c == 0:
        return 0.0
    bp = 1 if c > r else math.exp(1 - r / c)
    return bp * p1


def compute_codebleu(data):
    from nltk.translate.bleu_score import sentence_bleu, SmoothingFunction
    try:
        import tree_sitter_hcl
        from tree_sitter import Parser, Language
        has_ts = True
    except ImportError:
        has_ts = False

    keywords = get_hcl_keywords()
    scores = []
    per_task = []

    for d in data:
        ref_tokens = _tokenize(d["reference"])
        hyp_tokens = _tokenize(d["candidate"])

        if not hyp_tokens:
            scores.append(0.0)
            per_task.append({"task_id": d["task_id"], "codebleu": 0.0})
            continue

        # 1. BLEU component
        bleu = sentence_bleu([ref_tokens], hyp_tokens, smoothing_function=SmoothingFunction().method1)

        # 2. Weighted n-gram
        weighted = compute_weighted_bleu(hyp_tokens, ref_tokens, keywords)

        # 3. Syntactic AST match
        syntax_score = 0.0
        dataflow_score = 0.0
        if has_ts:
            try:
                # Try new API (0.22+)
                try:
                    parser = Parser(Language(tree_sitter_hcl.language()))
                except (TypeError, AttributeError):
                    # Fallback or old API
                    language = Language(tree_sitter_hcl.language())
                    parser = Parser()
                    parser.set_language(language)

                def _collect_types(node):
                    types, stack = [], [node]
                    while stack:
                        curr = stack.pop()
                        types.append(curr.type)
                        for child in curr.children:
                            stack.append(child)
                    return types

                def _collect_ids(node, src):
                    ids, stack = [], [node]
                    while stack:
                        curr = stack.pop()
                        if curr.type == "identifier":
                            text = src[curr.start_byte:curr.end_byte].decode("utf-8", errors="ignore")
                            if text:
                                ids.append(text)
                        for child in curr.children:
                            stack.append(child)
                    return ids

                ref_tree = parser.parse(d["reference"].encode("utf-8"))
                hyp_tree = parser.parse(d["candidate"].encode("utf-8"))

                ref_types = Counter(_collect_types(ref_tree.root_node))
                hyp_types = Counter(_collect_types(hyp_tree.root_node))
                common = sum((ref_types & hyp_types).values())
                total = sum(hyp_types.values())
                syntax_score = common / total if total > 0 else 0

                ref_ids = Counter(_collect_ids(ref_tree.root_node, d["reference"].encode("utf-8")))
                hyp_ids = Counter(_collect_ids(hyp_tree.root_node, d["candidate"].encode("utf-8")))
                common_ids = sum((ref_ids & hyp_ids).values())
                total_ids = sum(hyp_ids.values())
                dataflow_score = common_ids / total_ids if total_ids > 0 else 0
            except Exception as e:
                # Suppress error cleanly so script continues
                # logger.warning(f"Tree-sitter parse failed: {e}")
                pass

        cb = 0.25 * bleu + 0.25 * weighted + 0.25 * syntax_score + 0.25 * dataflow_score
        scores.append(cb)
        per_task.append({"task_id": d["task_id"], "codebleu": round(cb * 100, 2)})

    avg = sum(scores) / len(scores) * 100 if scores else 0
    return round(avg, 2), per_task


# ═══════════════════════════════════════════════════════════════════════════
# METRIC 5: CodeBERTScore
# ═══════════════════════════════════════════════════════════════════════════
def compute_codebertscore(data):
    from code_bert_score import score as cbs_score

    scores = []
    per_task = []
    for d in data:
        if not d["candidate"].strip():
            scores.append(0.0)
            per_task.append({"task_id": d["task_id"], "codebertscore_f1": 0.0})
            continue
        try:
            _, _, f1, _ = cbs_score(cands=[d["candidate"]], refs=[d["reference"]], lang="python")
            val = f1[0].item()
        except Exception as e:
            logger.warning(f"CodeBERTScore error for {d['task_id']}: {e}")
            val = 0.0
        scores.append(val)
        per_task.append({"task_id": d["task_id"], "codebertscore_f1": round(val * 100, 2)})

    avg = sum(scores) / len(scores) * 100 if scores else 0
    return round(avg, 2), per_task


# ═══════════════════════════════════════════════════════════════════════════
# METRIC 6 & 7: pass@1 + First-Try Rate
# ═══════════════════════════════════════════════════════════════════════════
def compute_functional(data):
    pass_count = 0
    first_try_count = 0
    total_iterations = 0
    iter_dist = {1: 0, 2: 0, 3: 0, 4: 0}  # iter@1, iter@2, iter@3, iter@4+
    per_task = []

    for d in data:
        passed = d["cand_exec_success"] and d["cand_meets_req"]
        first = d["cand_first_try"]
        iters = d["cand_iterations"]
        if passed:
            pass_count += 1
        if first:
            first_try_count += 1
        total_iterations += iters if iters > 0 else 1
        # Iteration distribution
        if iters >= 4:
            iter_dist[4] += 1
        elif iters in iter_dist:
            iter_dist[iters] += 1
        else:
            iter_dist[1] += 1

        per_task.append({
            "task_id": d["task_id"],
            "pass@1": 1 if passed else 0,
            "first_try": 1 if first else 0,
            "iterations": iters,
            "fixes_needed": d["cand_fixes"],
        })

    n = len(data)
    pass1 = round(pass_count / n * 100, 2) if n > 0 else 0
    ftr = round(first_try_count / n * 100, 2) if n > 0 else 0
    avg_iter = round(total_iterations / n, 2) if n > 0 else 0
    return pass1, ftr, avg_iter, iter_dist, per_task


# ═══════════════════════════════════════════════════════════════════════════
# Also compute reference (Sonnet 4.5) functional for comparison
# ═══════════════════════════════════════════════════════════════════════════
def compute_reference_functional(data):
    pass_count = 0
    first_try_count = 0
    total_iterations = 0
    iter_dist = {1: 0, 2: 0, 3: 0, 4: 0}
    for d in data:
        if d["ref_exec_success"] and d["ref_meets_req"]:
            pass_count += 1
        if d["ref_first_try"]:
            first_try_count += 1
        iters = d["ref_iterations"]
        total_iterations += iters if iters > 0 else 1
        if iters >= 4:
            iter_dist[4] += 1
        elif iters in iter_dist:
            iter_dist[iters] += 1
        else:
            iter_dist[1] += 1

    n = len(data)
    pass1 = round(pass_count / n * 100, 2) if n > 0 else 0
    ftr = round(first_try_count / n * 100, 2) if n > 0 else 0
    avg_iter = round(total_iterations / n, 2) if n > 0 else 0
    return pass1, ftr, avg_iter, iter_dist


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════
def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("  Qwen2.5-Coder-14B (Ollama) vs Claude Sonnet 4.5 (Ground Truth)")
    print("  Source: comparison/comparison_dataset.json")
    print("=" * 70)

    data = load_data()
    print(f"\n[OK] Loaded {len(data)} tasks\n")
    if not data:
        print("[ERROR] No data found. Check comparison_dataset.json keys.")
        return

    # ── Compute all metrics ────────────────────────────────────────────────
    print("Computing BLEU (SacreBLEU)...")
    bleu_score, bleu_per = compute_bleu(data)

    print("Computing METEOR...")
    meteor_score_val, meteor_per = compute_meteor(data)

    print("Computing ROUGE-3...")
    rouge3_score, rouge3_per = compute_rouge3(data)

    print("Computing CodeBLEU (weighted n-gram + tree-sitter HCL)...")
    codebleu_score, codebleu_per = compute_codebleu(data)

    print("Computing CodeBERTScore...")
    cbs_score_val, cbs_per = compute_codebertscore(data)

    print("Computing Functional pass@1 & first-try rate...")
    pass1, ftr, avg_iter, iter_dist, func_per = compute_functional(data)
    ref_pass1, ref_ftr, ref_avg_iter, ref_iter_dist = compute_reference_functional(data)

    # ── Results ────────────────────────────────────────────────────────────
    # ── Results Dictionary ─────────────────────────────────────────────────
    # Averages
    similarity_avg = round((bleu_score + meteor_score_val + rouge3_score + codebleu_score + cbs_score_val) / 5, 2)
    all_avg = round((bleu_score + meteor_score_val + rouge3_score + codebleu_score + cbs_score_val + pass1 + ftr) / 7, 2)
    functional_avg_qwen = round((pass1 + ftr) / 2, 2)
    functional_avg_ref = round((ref_pass1 + ref_ftr) / 2, 2)

    # Define output structure EARLY so we can append per-task data to it
    metrics = {
        "BLEU": bleu_score,
        "METEOR": meteor_score_val,
        "ROUGE-3": rouge3_score,
        "CodeBLEU": codebleu_score,
        "CodeBERTScore": cbs_score_val,
        "pass@1": pass1,
        "first_try_rate": ftr,
        "avg_iterations": avg_iter,
        "iter@1": iter_dist[1],
        "iter@2": iter_dist[2],
        "iter@3": iter_dist[3],
        "iter@4+": iter_dist[4],
        "similarity_avg": similarity_avg,
        "functional_avg": functional_avg_qwen,
        "overall_avg": all_avg,
    }

    ref_metrics = {
        "pass@1": ref_pass1,
        "first_try_rate": ref_ftr,
        "avg_iterations": ref_avg_iter,
        "iter@1": ref_iter_dist[1],
        "iter@2": ref_iter_dist[2],
        "iter@3": ref_iter_dist[3],
        "iter@4+": ref_iter_dist[4],
        "functional_avg": functional_avg_ref,
    }

    n = len(data)

    output = {
        "candidate": "Qwen2.5-Coder-14B (Ollama)",
        "reference_ground_truth": "Claude Sonnet 4.5",
        "source": str(COMPARISON_JSON),
        "tasks_evaluated": n,
        "metrics": metrics,
        "reference_functional": ref_metrics,
        "per_task": {
            "bleu": bleu_per,
            "meteor": meteor_per,
            "rouge3": rouge3_per,
            "codebleu": codebleu_per,
            "codebertscore": cbs_per,
            "functional": func_per,
            "averages": []  # Will be populated below
        },
    }

    # ── Print Leaderboard ──────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  COMPARISON RESULTS")
    print("=" * 70)
    print(f"\n{'Metric':<20} {'Qwen-14B (SLM)':>18} {'Sonnet 4.5 (GT)':>18}")
    print("-" * 58)
    print(f"{'BLEU':<20} {bleu_score:>17}%")
    print(f"{'METEOR':<20} {meteor_score_val:>17}%")
    print(f"{'ROUGE-3':<20} {rouge3_score:>17}%")
    print(f"{'CodeBLEU':<20} {codebleu_score:>17}%")
    print(f"{'CodeBERTScore':<20} {cbs_score_val:>17}%")
    print("-" * 58)
    print(f"{'Similarity Avg':<20} {similarity_avg:>17}%")
    print("-" * 58)
    print(f"{'pass@1':<20} {pass1:>17}% {ref_pass1:>17}%")
    print(f"{'First-Try Rate':<20} {ftr:>17}% {ref_ftr:>17}%")
    print(f"{'Functional Avg':<20} {functional_avg_qwen:>17}% {functional_avg_ref:>17}%")
    print(f"{'Avg Iterations':<20} {avg_iter:>18} {ref_avg_iter:>18}")
    print(f"{'iter@1 (1st try)':<20} {iter_dist[1]:>14}/{n} {ref_iter_dist[1]:>14}/{n}")
    print(f"{'iter@2':<20} {iter_dist[2]:>14}/{n} {ref_iter_dist[2]:>14}/{n}")
    print(f"{'iter@3':<20} {iter_dist[3]:>14}/{n} {ref_iter_dist[3]:>14}/{n}")
    print(f"{'iter@4+':<20} {iter_dist[4]:>14}/{n} {ref_iter_dist[4]:>14}/{n}")
    print("-" * 58)
    print(f"{'Overall Avg (7)':<20} {all_avg:>17}%")
    print("=" * 58)
    print(f"\nNote: BLEU/METEOR/ROUGE/CodeBLEU/CBS measure similarity")
    print(f"      of Qwen-14B code AGAINST Sonnet 4.5 as reference.")

    # ── Per-task Breakdown ─────────────────────────────────────────────────
    print("\n" + "-" * 110)
    print("  PER-TASK BREAKDOWN (SimAvg=5 metrics, FuncAvg=Pass+1stTry, AllAvg=7 metrics)")
    print("-" * 110)
    print(f"{'Task':<6} {'BLEU':>6} {'METHR':>6} {'ROGE3':>6} {'CBLEU':>6} {'CBS-F1':>7} | {'SimAvg':>6} | {'Pass':>4} {'1st':>3} | {'FuncAvg':>7} | {'AllAvg':>6} | {'Iters':>5}")
    print("-" * 110)

    per_task_results = []
    
    for i, d in enumerate(data):
        tid = d["task_id"]
        b = bleu_per[i]["bleu"]
        m = meteor_per[i]["meteor"]
        r = rouge3_per[i]["rouge3_f1"]
        cb = codebleu_per[i]["codebleu"]
        cs = cbs_per[i]["codebertscore_f1"]
        
        # Functional raw
        p_raw = func_per[i]["pass@1"]
        ft_raw = func_per[i]["first_try"]
        iters = func_per[i]["iterations"]
        
        # Functional display
        p_disp = "PASS" if p_raw else "FAIL"
        ft_disp = "YES" if ft_raw else "NO"
        
        # Averages per task
        sim_avg = round((b + m + r + cb + cs) / 5, 2)
        func_avg = round((p_raw * 100 + ft_raw * 100) / 2, 2)
        all_avg = round((b + m + r + cb + cs + p_raw * 100 + ft_raw * 100) / 7, 2)
        
        per_task_results.append({
            "task_id": tid,
            "sim_avg": sim_avg,
            "func_avg": func_avg,
            "all_avg": all_avg
        })

        print(f"{tid:<6} {b:>6.1f} {m:>6.1f} {r:>6.1f} {cb:>6.1f} {cs:>7.1f} | {sim_avg:>6.1f} | {p_disp:>4} {ft_disp:>3} | {func_avg:>7.1f} | {all_avg:>6.1f} | {iters:>5}")

    # Add per-task averages to output json
    output["per_task"]["averages"] = per_task_results

    # ── Save JSON ──────────────────────────────────────────────────────────
    json_path = OUTPUT_DIR / "qwen_vs_claude_python_official.json"
    with open(json_path, 'w') as f:
        json.dump(output, f, indent=2)
    print(f"\n[OK] JSON results saved to {json_path}")

    # ── Save CSV ───────────────────────────────────────────────────────────
    import csv
    csv_path = OUTPUT_DIR / "performance_leaderboard_official.csv"
    with open(csv_path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(["Metric", "Qwen-14B (SLM)", "Sonnet 4.5 (GT)"])
        w.writerow(["BLEU", bleu_score, ""])
        w.writerow(["METEOR", meteor_score_val, ""])
        w.writerow(["ROUGE-3", rouge3_score, ""])
        w.writerow(["CodeBLEU", codebleu_score, ""])
        w.writerow(["CodeBERTScore", cbs_score_val, ""])
        w.writerow(["pass@1", pass1, ref_pass1])
        w.writerow(["First-Try Rate", ftr, ref_ftr])
    print(f"[OK] CSV results saved to {csv_path}")


if __name__ == "__main__":
    main()
