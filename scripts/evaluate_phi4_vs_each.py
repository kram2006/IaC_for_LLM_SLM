"""
evaluate_phi4_vs_each.py
Individual Pair Comparisons: Phi-4 14B (Ollama) vs Each Reference Model

Produces a separate report for each pair:
  Phi-4 vs Sonnet 4.5
  Phi-4 vs DeepSeek v3.2
  Phi-4 vs Gemini 3 Pro
  Phi-4 vs GPT 5.2 Codex
  Phi-4 vs Kimi K2
  Phi-4 vs Qwen 3 Coder

Metrics:
  BLEU (NLTK Sentence), METEOR, ROUGE-3, CodeBLEU (tree-sitter-hcl),
  CodeBERTScore (lang='terraform'), Functional pass@1 & First-Try Rate
"""

import json, os, math, warnings, csv
from pathlib import Path
from collections import Counter

import nltk
from nltk.translate.bleu_score import sentence_bleu, SmoothingFunction
from nltk.translate.meteor_score import meteor_score as ms
from rouge_score import rouge_scorer

warnings.filterwarnings("ignore")

# -- Paths --
BASE_DIR = Path(r"c:\Users\kalar\Downloads\llm_eval_RK\iac-eval-main")
COMPARISON_JSON = BASE_DIR / "comparison" / "comparison_dataset.json"
OUTPUT_DIR = BASE_DIR / "results" / "comparison_official" / "phi4_individual"

PHI4_PREFIX = "code_Phi4_14B_Ollama"
SKIP_TASKS = set()

# Models to compare against (excluding Qwen 2.5 14B)
REFERENCE_MODELS = [
    {"name": "Sonnet 4.5",     "prefix": "ref_Sonnet_4_5",     "slug": "sonnet45"},
    {"name": "DeepSeek v3.2",  "prefix": "ref_DeepSeek_v3.2",  "slug": "deepseek"},
    {"name": "Gemini 3 Pro",   "prefix": "ref_Gemini_3_Pro",   "slug": "gemini3"},
    {"name": "GPT 5.2 Codex",  "prefix": "ref_GPT_5.2_Codex",  "slug": "gpt52"},
    {"name": "Kimi K2",        "prefix": "ref_Kimi_k2",         "slug": "kimi_k2"},
    {"name": "Qwen 3 Coder",   "prefix": "ref_Qwen_3_Coder",   "slug": "qwen3"},
]

# -- NLTK setup --
for res in ["wordnet", "omw-1.4"]:
    try:
        nltk.data.find(f"corpora/{res}")
    except LookupError:
        nltk.download(res, quiet=True)

def _tokenize(text):
    return text.split()

def get_hcl_keywords():
    return {
        "resource", "provider", "variable", "output", "module", "data",
        "locals", "terraform", "backend", "provisioner", "connection"
    }


# == Data Loading ==
def load_pair_data(ref_prefix):
    with open(COMPARISON_JSON, 'r', encoding='utf-8') as f:
        raw = json.load(f)
    result = []
    for row in raw:
        tid = row.get("task_id", "")
        if tid in SKIP_TASKS:
            continue
        phi4_code = row.get(PHI4_PREFIX, "")
        ref_code = row.get(ref_prefix, "")
        if phi4_code is None: phi4_code = ""
        if ref_code is None: ref_code = ""
        if not phi4_code and not ref_code:
            continue
        result.append({
            "task_id": tid,
            "candidate": phi4_code,
            "reference": ref_code,
            "cand_exec_success": row.get(f"{PHI4_PREFIX}_exec_success", False),
            "cand_meets_req": row.get(f"{PHI4_PREFIX}_meets_req", False),
            "cand_first_try": row.get(f"{PHI4_PREFIX}_first_try", False),
            "cand_iterations": row.get(f"{PHI4_PREFIX}_iterations", 1),
            "cand_fixes": row.get(f"{PHI4_PREFIX}_fixes_needed", 0),
            "ref_exec_success": row.get(f"{ref_prefix}_exec_success", False),
            "ref_meets_req": row.get(f"{ref_prefix}_meets_req", False),
            "ref_first_try": row.get(f"{ref_prefix}_first_try", False),
            "ref_iterations": row.get(f"{ref_prefix}_iterations", 1),
        })
    return result


# == Metrics ==
def compute_bleu(data):
    smoothie = SmoothingFunction().method1
    scores, per_task = [], []
    for d in data:
        ref_tokens = _tokenize(d["reference"])
        hyp_tokens = _tokenize(d["candidate"])
        score = sentence_bleu([ref_tokens], hyp_tokens, smoothing_function=smoothie)
        scores.append(score)
        per_task.append({"task_id": d["task_id"], "bleu": round(score * 100, 2)})
    avg = sum(scores) / len(scores) * 100 if scores else 0
    return round(avg, 2), per_task


def compute_meteor(data):
    scores, per_task = [], []
    for d in data:
        ref_tokens = _tokenize(d["reference"])
        hyp_tokens = _tokenize(d["candidate"])
        score = ms([ref_tokens], hyp_tokens)
        scores.append(score)
        per_task.append({"task_id": d["task_id"], "meteor": round(score * 100, 2)})
    avg = sum(scores) / len(scores) * 100 if scores else 0
    return round(avg, 2), per_task


def compute_rouge3(data):
    scorer = rouge_scorer.RougeScorer(['rouge3'], use_stemmer=True)
    scores, per_task = [], []
    for d in data:
        r = scorer.score(d["reference"], d["candidate"])
        f1 = r['rouge3'].fmeasure
        scores.append(f1)
        per_task.append({"task_id": d["task_id"], "rouge3_f1": round(f1 * 100, 2)})
    avg = sum(scores) / len(scores) * 100 if scores else 0
    return round(avg, 2), per_task


def compute_codebleu(data):
    try:
        from tree_sitter import Parser, Language
        import tree_sitter_hcl
        has_ts = True
    except ImportError:
        has_ts = False

    hcl_kw = get_hcl_keywords()
    scores, per_task = [], []

    def _weighted_bleu(cd_tokens, ref_tokens, keywords, kw=5):
        cd_count = Counter(cd_tokens)
        ref_count = Counter(ref_tokens)
        num = den = 0
        for token, count in cd_count.items():
            w = kw if token in keywords else 1
            num += w * min(count, ref_count.get(token, 0))
            den += w * count
        p1 = num / den if den > 0 else 0
        c, r = len(cd_tokens), len(ref_tokens)
        bp = 1 if c > r else math.exp(1 - r / c) if c > 0 else 0
        return bp * p1

    def _collect_types(node):
        types, stack = [], [node]
        while stack:
            curr = stack.pop()
            types.append(curr.type)
            for child in curr.children: stack.append(child)
        return types

    def _collect_ids(node, src):
        ids, stack = [], [node]
        while stack:
            curr = stack.pop()
            if curr.type == "identifier":
                text = src[curr.start_byte:curr.end_byte].decode("utf-8", errors="ignore")
                if text: ids.append(text)
            for child in curr.children: stack.append(child)
        return ids

    def _f1_overlap(a, b):
        if not a and not b: return 1.0
        if not a or not b: return 0.0
        ca, cb = Counter(a), Counter(b)
        overlap = sum(min(ca[k], cb.get(k, 0)) for k in ca)
        prec = overlap / sum(ca.values()) if ca else 0.0
        rec = overlap / sum(cb.values()) if cb else 0.0
        if prec + rec == 0: return 0.0
        return 2 * prec * rec / (prec + rec)

    for d in data:
        ref_code = d["reference"] or ""
        cand_code = d["candidate"] or ""
        ref_tokens = _tokenize(ref_code)
        hyp_tokens = _tokenize(cand_code)

        score_bleu = sentence_bleu([ref_tokens], hyp_tokens, smoothing_function=SmoothingFunction().method1)
        weighted = _weighted_bleu(hyp_tokens, ref_tokens, hcl_kw)
        syntax_score = dataflow_score = 0.0

        if has_ts:
            try:
                try:
                    parser = Parser(Language(tree_sitter_hcl.language()))
                except (TypeError, AttributeError):
                    lang = Language(tree_sitter_hcl.language())
                    parser = Parser()
                    parser.set_language(lang)
                rb = ref_code.encode("utf-8")
                hb = cand_code.encode("utf-8")
                rt = parser.parse(rb)
                ht = parser.parse(hb)
                syntax_score = _f1_overlap(_collect_types(ht.root_node), _collect_types(rt.root_node))
                dataflow_score = _f1_overlap(_collect_ids(ht.root_node, hb), _collect_ids(rt.root_node, rb))
            except Exception:
                pass

        cb = 0.25 * score_bleu + 0.25 * weighted + 0.25 * syntax_score + 0.25 * dataflow_score
        scores.append(cb)
        per_task.append({"task_id": d["task_id"], "codebleu": round(cb * 100, 2)})

    avg = sum(scores) / len(scores) * 100 if scores else 0
    return round(avg, 2), per_task


def compute_codebertscore(data):
    from code_bert_score import score as cbs_score
    scores, per_task = [], []
    for d in data:
        if not d["candidate"].strip():
            val = 0.0
        else:
            try:
                _, _, f1, _ = cbs_score(cands=[d["candidate"]], refs=[d["reference"]], lang="terraform")
                val = f1[0].item()
            except Exception:
                val = 0.0
        scores.append(val)
        per_task.append({"task_id": d["task_id"], "codebertscore_f1": round(val * 100, 2)})
    avg = sum(scores) / len(scores) * 100 if scores else 0
    return round(avg, 2), per_task


def compute_functional(data, prefix_type="cand"):
    p1_count = p2_count = p3_count = total_iters = 0
    iter_dist = {1: 0, 2: 0, 3: 0, 4: 0}
    per_task = []
    for d in data:
        success = d[f"{prefix_type}_exec_success"] and d[f"{prefix_type}_meets_req"]
        iters = d[f"{prefix_type}_iterations"]
        
        # pass@k: Success within k iterations
        p1 = 1 if (success and iters <= 1) else 0
        p2 = 1 if (success and iters <= 2) else 0
        p3 = 1 if (success and iters <= 3) else 0
        
        p1_count += p1
        p2_count += p2
        p3_count += p3
        
        total_iters += iters if iters > 0 else 1
        if iters >= 4: iter_dist[4] += 1
        elif iters in iter_dist: iter_dist[iters] += 1
        else: iter_dist[1] += 1
        
        per_task.append({
            "task_id": d["task_id"], 
            "pass@1": p1, 
            "pass@2": p2, 
            "pass@3": p3,
            "iterations": iters
        })
    n = len(data)
    r1 = round(p1_count / n * 100, 2) if n > 0 else 0
    r2 = round(p2_count / n * 100, 2) if n > 0 else 0
    r3 = round(p3_count / n * 100, 2) if n > 0 else 0
    avg_iter = round(total_iters / n, 2) if n > 0 else 0
    return r1, r2, r3, avg_iter, iter_dist, per_task


# == Run One Pair ==
def run_pair(ref_model):
    ref_name = ref_model["name"]
    ref_prefix = ref_model["prefix"]
    slug = ref_model["slug"]

    data = load_pair_data(ref_prefix)
    n = len(data)

    print(f"\n\n{'='*70}")
    print(f"  Phi-4 14B (Ollama) vs {ref_name}")
    print(f"  Tasks: {n}")
    print(f"{'='*70}")

    if not data:
        print(f"  [ERROR] No data for {ref_name}")
        return None

    print("  Computing BLEU...")
    bleu_score, bleu_per = compute_bleu(data)
    print("  Computing METEOR...")
    meteor_val, meteor_per = compute_meteor(data)
    print("  Computing ROUGE-3...")
    rouge3_val, rouge3_per = compute_rouge3(data)
    print("  Computing CodeBLEU...")
    codebleu_val, codebleu_per = compute_codebleu(data)
    print("  Computing CodeBERTScore...")
    cbs_val, cbs_per = compute_codebertscore(data)
    print("  Computing Functional...")
    p1, p2, p3, avg_iter, iter_dist, func_per = compute_functional(data, "cand")
    rp1, rp2, rp3, ravg_iter, riter_dist, _ = compute_functional(data, "ref")

    sim_avg = round((bleu_score + meteor_val + rouge3_val + codebleu_val + cbs_val) / 5, 2)
    overall = round((sim_avg + p3) / 2, 2) # Use pass@3 as functional representative

    # Print
    print(f"\n{'Metric':<20} {'Phi-4 14B':>18} {ref_name:>18}")
    print("-" * 58)
    print(f"{'BLEU':<20} {bleu_score:>17}%")
    print(f"{'METEOR':<20} {meteor_val:>17}%")
    print(f"{'ROUGE-3':<20} {rouge3_val:>17}%")
    print(f"{'CodeBLEU':<20} {codebleu_val:>17}%")
    print(f"{'CodeBERTScore':<20} {cbs_val:>17}%")
    print("-" * 58)
    print(f"{'pass@1':<20} {p1:>17}% {rp1:>17}%")
    print(f"{'pass@2':<20} {p2:>17}% {rp2:>17}%")
    print(f"{'pass@3':<20} {p3:>17}% {rp3:>17}%")
    print(f"{'Avg Iterations':<20} {avg_iter:>18} {ravg_iter:>18}")
    print("-" * 58)
    print(f"{'Overall Avg':<20} {overall:>17}%")
    print("=" * 58)

    # Per-task
    print(f"\n{'Task':<6} {'BLEU':>6} {'METR':>6} {'RGE3':>6} {'CBS':>7} | {'P@1':>3} {'P@2':>3} {'P@3':>3} | {'Iters':>5}")
    print("-" * 65)
    task_avgs = []
    for i, d in enumerate(data):
        b = bleu_per[i]["bleu"]
        m = meteor_per[i]["meteor"]
        r = rouge3_per[i]["rouge3_f1"]
        cs = cbs_per[i]["codebertscore_f1"]
        f_p1 = func_per[i]["pass@1"]
        f_p2 = func_per[i]["pass@2"]
        f_p3 = func_per[i]["pass@3"]
        it = func_per[i]["iterations"]
        sa = round((b + m + r + cs) / 4, 2)
        print(f"{d['task_id']:<6} {b:>6.1f} {m:>6.1f} {r:>6.1f} {cs:>7.1f} | {f_p1:>3} {f_p2:>3} {f_p3:>3} | {it:>5}")
        task_avgs.append({"task_id": d["task_id"], "sim_avg": sa})

    # Save JSON
    output = {
        "candidate": "Phi-4 14B (Ollama)",
        "reference": ref_name,
        "tasks_evaluated": n,
        "metrics": {
            "BLEU": bleu_score, "METEOR": meteor_val, "ROUGE-3": rouge3_val,
            "CodeBLEU": codebleu_val, "CodeBERTScore": cbs_val,
            "pass@1": p1, "pass@2": p2, "pass@3": p3, "avg_iterations": avg_iter,
            "overall_avg": overall,
        },
        "reference_functional": {
            "pass@1": rp1, "pass@2": rp2, "pass@3": rp3, "avg_iterations": ravg_iter
        },
        "per_task": {
            "bleu": bleu_per, "meteor": meteor_per, "rouge3": rouge3_per,
            "codebleu": codebleu_per, "codebertscore": cbs_per,
            "functional": func_per, "averages": task_avgs,
        },
    }

    json_path = OUTPUT_DIR / f"phi4_vs_{slug}.json"
    with open(json_path, 'w') as f:
        json.dump(output, f, indent=2)
    print(f"\n[OK] Saved to {json_path}")

    return {
        "ref": ref_name, "BLEU": bleu_score, "METEOR": meteor_val,
        "ROUGE-3": rouge3_val, "CodeBLEU": codebleu_val, "CodeBERTScore": cbs_val,
        "SimAvg": sim_avg, "pass@1": p1, "pass@2": p2, "pass@3": p3,
        "AvgIter": avg_iter, "OverallAvg": overall,
        "ref_pass1": rp1, "ref_pass2": rp2, "ref_pass3": rp3,
    }


# == MAIN ==
def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("  INDIVIDUAL PAIR COMPARISONS: Phi-4 14B vs Each Model")
    print("  (Excluding Qwen 2.5 14B)")
    print("=" * 70)

    all_results = []
    for ref in REFERENCE_MODELS:
        result = run_pair(ref)
        if result:
            all_results.append(result)

    # Summary table
    print(f"\n\n{'='*115}")
    print("  SUMMARY: Phi-4 14B (Ollama) vs Each Model")
    print(f"{'='*115}")
    print(f"{'Reference':<22} {'BLEU':>6} {'METR':>6} {'RGE3':>6} {'CBLEU':>6} {'CBS':>6} | {'SimAvg':>6} | {'P@1':>5} {'P@2':>5} {'P@3':>5} | {'OvAvg':>6}")
    print("-" * 115)
    for m in all_results:
        print(
            f"{m['ref']:<22} "
            f"{m['BLEU']:>5.1f}% {m['METEOR']:>5.1f}% {m['ROUGE-3']:>5.1f}% {m['CodeBLEU']:>5.1f}% {m['CodeBERTScore']:>5.1f}% | "
            f"{m['SimAvg']:>5.1f}% | "
            f"{m['pass@1']:>4.0f}% {m['pass@2']:>4.0f}% {m['pass@3']:>4.0f}% | "
            f"{m['OverallAvg']:>5.1f}%"
        )
    print("=" * 115)

    # Save summary CSV
    csv_path = OUTPUT_DIR / "phi4_individual_summary.csv"
    with open(csv_path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(["Reference", "BLEU", "METEOR", "ROUGE-3", "CodeBLEU", "CodeBERTScore",
                     "SimAvg", "Phi4_pass@1", "Phi4_pass@2", "Phi4_pass@3",
                     "Ref_pass@1", "Ref_pass@2", "Ref_pass@3",
                     "Phi4_AvgIter", "OverallAvg"])
        for m in all_results:
            w.writerow([m["ref"], m["BLEU"], m["METEOR"], m["ROUGE-3"],
                         m["CodeBLEU"], m["CodeBERTScore"], m["SimAvg"],
                         m["pass@1"], m["pass@2"], m["pass@3"],
                         m["ref_pass1"], m["ref_pass2"], m["ref_pass3"],
                         m["AvgIter"], m["OverallAvg"]])
    print(f"\n[OK] Summary CSV saved to {csv_path}")


if __name__ == "__main__":
    main()
