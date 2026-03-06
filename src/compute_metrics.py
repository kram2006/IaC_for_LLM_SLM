import os
import json
import glob
import re
from nltk.translate.bleu_score import sentence_bleu, SmoothingFunction

try:
    from code_bert_score import score as _cbs_score_fn
    CODEBERT_AVAILABLE = True
except ImportError:
    CODEBERT_AVAILABLE = False
    print("WARNING: code-bert-score not installed. Run: pip install code-bert-score")

def bleu_score(reference, candidate):
    # regex tokenization: alphanumeric sequences or single punctuation marks
    ref_tokens = re.findall(r"\w+|[^\w\s]", reference)
    cand_tokens = re.findall(r"\w+|[^\w\s]", candidate)
    if len(ref_tokens) < 4 or len(cand_tokens) < 4:
        return 0.0
    return sentence_bleu(
        [ref_tokens], cand_tokens,
        weights=(0.25, 0.25, 0.25, 0.25),
        smoothing_function=SmoothingFunction().method3
    )

def codebert_score(reference, candidate):
    """
    Compute semantic similarity using CodeBERT.
    Returns a dict with precision, recall, f1, f3 — or None if unavailable.
    F3 upweights recall (missing resources are worse than extra ones).
    Returns None if library not installed or inputs are empty.
    """
    if not CODEBERT_AVAILABLE:
        return None
    if not reference or not candidate:
        return None
    try:
        P, R, F1, F3 = _cbs_score_fn(
            cands=[candidate],
            refs=[reference],
            lang='go'   # HCL is syntactically closer to Go than Python
        )
        return {
            'precision': round(float(P[0]), 4),
            'recall':    round(float(R[0]), 4),
            'f1':        round(float(F1[0]), 4),
            'f3':        round(float(F3[0]), 4),
        }
    except Exception as e:
        print(f"  CodeBERT warning: {e}")
        return None

def calculate_pass_at_k(n, c, k):
    """
    Unbiased pass@k estimator from "Evaluating Large Language Models Trained on Code" (Chen et al., 2021)
    
    Args:
        n: total number of samples
        c: number of correct samples
        k: k in pass@k
    
    Returns:
        Unbiased estimate of pass@k
    
    Formula: pass@k ≈ 1 - comb(n-c, k) / comb(n, k)
    """
    if n < k:
        return 0.0
    if c == n:
        return 1.0
    if c == 0:
        return 0.0
    if n - c < k:
        return 1.0
    
    from math import comb
    return 1.0 - comb(n - c, k) / comb(n, k)

def compute_metrics_for_folder(dataset_folder, task_csv_path):
    import pandas as pd
    lockfile = os.path.join(dataset_folder, ".evaluation_in_progress")
    if os.path.exists(lockfile):
        print(f"ERROR: Evaluation still running in {dataset_folder}. Wait for completion.")
        return

    df = pd.read_csv(task_csv_path)
    ref_map = {}
    if 'reference_hcl' in df.columns:
        ref_map = dict(zip(df['task_id'], df['reference_hcl']))

    results = []
    
    # Handle both directory formats (can be empty string if folder doesn't exist yet)
    if not os.path.exists(dataset_folder):
        print(f"Directory not found: {dataset_folder}")
        return
        
    for json_file in sorted(glob.glob(os.path.join(dataset_folder, "*.json"))):
        with open(json_file) as f:
            entry = json.load(f)

        task_id     = entry.get('task_id', '')
        candidate   = entry.get('llm_response', {}).get('generated_code', '')
        reference   = ref_map.get(task_id, '')
        
        # Check if reference is empty string or NaN from pandas
        if str(reference).lower() == 'nan':
            reference = ''
            
        apply_ok    = entry.get('final_outcome', {}).get('execution_successful', False)
        spec_ok     = entry.get('spec_accuracy', {}).get('passed', False)
        iterations  = entry.get('final_outcome', {}).get('total_iterations', 1)
        gen_time    = entry.get('llm_response', {}).get('time_to_generate_seconds', 0)

        bleu = bleu_score(reference, candidate) if reference else None
        cbs  = codebert_score(reference, candidate) if reference else None

        results.append({
            'task_id':    task_id,
            'apply_ok':   apply_ok,
            'spec_ok':    spec_ok,
            'iterations': iterations,
            'gen_time':   gen_time,
            'bleu':       bleu,
            'codebert':   cbs,
            'file':       os.path.basename(json_file),
        })

    if not results:
        print("No results found.")
        return
    # Group by task_id for Pass@k calculation
    task_groups = {}
    for r in results:
        tid = r['task_id']
        if tid not in task_groups:
            task_groups[tid] = []
        task_groups[tid].append(r)

    # Calculate unbiased Pass@k using Chen et al. (2021) formula
    # pass@k ≈ 1 - Product(1 - k/n, n-c+1, n) where c = # correct samples, n = total samples
    # Simplified: pass@k = 1 - comb(n-c, k) / comb(n, k)
    
    total_unique_tasks = len(task_groups)
    
    # Calculate Pass@1, Pass@3, Pass@5 for both apply and spec
    k_values = [1, 3, 5]
    pass_at_k_apply = {}
    pass_at_k_spec = {}
    
    for k in k_values:
        total_prob_apply = 0
        total_prob_spec = 0
        tasks_with_k_samples = 0
        
        for tid, group in task_groups.items():
            n = len(group)
            if n >= k:
                c_apply = sum(1 for s in group if s['apply_ok'])
                c_spec = sum(1 for s in group if s['spec_ok'])
                
                total_prob_apply += calculate_pass_at_k(n, c_apply, k)
                total_prob_spec += calculate_pass_at_k(n, c_spec, k)
                tasks_with_k_samples += 1
        
        if tasks_with_k_samples > 0:
            pass_at_k_apply[k] = total_prob_apply / tasks_with_k_samples
            pass_at_k_spec[k] = total_prob_spec / tasks_with_k_samples
        else:
            pass_at_k_apply[k] = 0.0
            pass_at_k_spec[k] = 0.0

    total_samples = len(results)
    avg_iter   = sum(r['iterations'] for r in results) / total_samples if total_samples > 0 else 0
    avg_time   = sum(r['gen_time'] for r in results) / total_samples if total_samples > 0 else 0
    bleu_vals  = [r['bleu'] for r in results if r['bleu'] is not None]
    avg_bleu   = sum(bleu_vals) / len(bleu_vals) if bleu_vals else None

    cbs_f1_vals = [r['codebert']['f1'] for r in results if r['codebert'] is not None]
    avg_cbs_f1  = sum(cbs_f1_vals) / len(cbs_f1_vals) if cbs_f1_vals else None

    print(f"\n{'='*60}")
    print(f" AGGREGATED EVALUATION SUMMARY")
    print(f"{'='*60}")
    print(f"  Directory:          {dataset_folder}")
    print(f"  Total Samples:      {total_samples}")
    print(f"  Unique Tasks:       {total_unique_tasks}")
    
    # Display unbiased Pass@k metrics
    for k in k_values:
        if k in pass_at_k_apply:
            print(f"  Pass@{k} (Apply):     {pass_at_k_apply[k]:.1%}")
    for k in k_values:
        if k in pass_at_k_spec:
            print(f"  Pass@{k} (Spec):      {pass_at_k_spec[k]:.1%}")
    
    print(f"  Avg Iterations:     {avg_iter:.2f}")
    print(f"  Avg Gen Time (s):   {avg_time:.1f}")
    if avg_bleu is not None:
        print(f"  Avg BLEU:           {avg_bleu:.4f}")
    if avg_cbs_f1 is not None:
        print(f"  Avg CodeBERT-F1:    {avg_cbs_f1:.4f}")
    print(f"{'='*60}\n")

    return results

if __name__ == "__main__":
    import sys
    # Usage: python compute_metrics.py results/dataset/phi4_or tasks/vm_provisioning_tasks.csv
    folder   = sys.argv[1] if len(sys.argv) > 1 else "results/dataset"
    csv_path = sys.argv[2] if len(sys.argv) > 2 else "tasks/vm_provisioning_tasks.csv"
    compute_metrics_for_folder(folder, csv_path)
