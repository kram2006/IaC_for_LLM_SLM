"""
llm_judge.py

Post-hoc LLM-as-Judge evaluation for IaC-Eval XCP-NG pipeline.
Reads all JSON result files in a results folder, calls an LLM judge,
and computes precision/recall of spec_checker vs the judge's verdicts.

Based on the MT-Bench / Judging LLM-as-a-Judge paper (arXiv:2306.05685).

Usage:
    python llm_judge.py --folder results/dataset/phi4_openrouter --config config/openrouter_config.yaml
    python llm_judge.py --folder results/dataset/phi4_openrouter --config config/openrouter_config.yaml --judge-model openai/gpt-4o
"""

import os
import sys
import json
import glob
import time
import argparse
import requests
import re

try:
    from src.eval_utils import extract_terraform_code
except ImportError:
    src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    from eval_utils import extract_terraform_code

# ─────────────────────────────────────────────────────────────────────────────
# JUDGE PROMPT TEMPLATE
# Adapted from arXiv:2306.05685 for XenOrchestra Terraform HCL evaluation
# ─────────────────────────────────────────────────────────────────────────────

JUDGE_SYSTEM_PROMPT = """You are an expert evaluator of Terraform HCL code for XCP-NG / Xen Orchestra infrastructure.
You will be given a task description and a generated Terraform configuration.
Your job is to determine whether the generated code correctly and completely fulfills the task.

Evaluation criteria:
1. The code must include valid xenorchestra provider configuration
2. Required VM attributes must be present and correct (name_label, memory_max, cpus, template, auto_poweron)
3. Resource requirements from the task (VM count, RAM, CPU, disk size) must match
4. For UPDATE tasks: the code must modify the correct VM attribute without destroying/recreating the VM
5. For DELETE tasks: the code must remove only the specified VM(s), not all infrastructure
6. For READ tasks: the code must use data sources and output blocks, not resource blocks

End your response with exactly one of these two lines (no other text after it):
Rating: Correct
Rating: Incorrect"""

JUDGE_USER_TEMPLATE = """Task Description:
{task_prompt}

Task Category: {category}

Generated Terraform Code:
```hcl
{terraform_code}
```

Does this generated code correctly fulfill the task? Evaluate it carefully, then end with your rating."""


# ─────────────────────────────────────────────────────────────────────────────
# LLM CALL (direct requests, no dependency on api_client.py to keep standalone)
# ─────────────────────────────────────────────────────────────────────────────

def call_judge(api_key, judge_model, task_prompt, category, terraform_code, base_url, timeout=120):
    """Call the judge LLM and return the raw response text."""
    user_content = JUDGE_USER_TEMPLATE.format(
        task_prompt=task_prompt[:10000],  # Increased cap to avoid truncation of long prompts
        category=category,
        terraform_code=terraform_code[:8000]  # Increased cap for complex HCL
    )
    payload = {
        "model": judge_model,
        "messages": [
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {"role": "user",   "content": user_content},
        ],
        "temperature": 0.0,   # deterministic judging
        "max_tokens":  512,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
        "HTTP-Referer":  "https://github.com/iac-eval-xcpng",
        "X-Title":       "IaC-Eval-Judge",
    }
    for attempt in range(3):
        try:
            r = requests.post(base_url, headers=headers, json=payload, timeout=timeout)
            if r.status_code == 200:
                return r.json()['choices'][0]['message']['content']
            elif r.status_code == 429:
                wait = (2 ** attempt) * 3
                print(f"    Rate limited. Waiting {wait}s...")
                time.sleep(wait)
            else:
                print(f"    API error {r.status_code}: {r.text[:200]}")
                time.sleep(2)
        except Exception as e:
            print(f"    Request failed: {e}")
            time.sleep(2)
    return None


def parse_verdict(response_text):
    """
    Extract 'Correct' or 'Incorrect' from the judge response.
    Looks for 'Rating: Correct' or 'Rating: Incorrect' at the end.
    Returns 'Correct', 'Incorrect', or 'Unknown'.
    """
    if not response_text:
        return 'Unknown'
    # Search for the rating anywhere in the response (case-insensitive)
    match = re.search(r'Rating:\s*(Correct|Incorrect)', response_text, re.IGNORECASE)
    if match:
        verdict = match.group(1).capitalize()
        return verdict  # 'Correct' or 'Incorrect'
    # Fallback: look for the words at end of response
    lower = response_text.lower().strip()
    if lower.endswith('incorrect') or 'rating: incorrect' in lower:
        return 'Incorrect'
    if lower.endswith('correct') or 'rating: correct' in lower:
        return 'Correct'
    return 'Unknown'


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="LLM-as-Judge post-hoc evaluation")
    parser.add_argument("--folder",      required=True,
                        help="Path to results folder, e.g. results/dataset/phi4_openrouter")
    parser.add_argument("--config",      default="config/openrouter_config.yaml",
                        help="Path to openrouter_config.yaml")
    parser.add_argument("--judge-model", default="openai/gpt-4o",
                        help="Model to use as judge (default: openai/gpt-4o)")
    parser.add_argument("--skip-existing", action="store_true",
                        help="Skip files that already have a judge_verdict field")
    args = parser.parse_args()

    # Load config for API key and base URL
    import yaml
    with open(args.config, 'r') as f:
        config = yaml.safe_load(f)

    import os as _os
    api_key  = _os.environ.get('OPENROUTER_API_KEY') or config['openrouter'].get('api_key', '')
    # Resolve env var placeholder
    if api_key.startswith('${'):
        api_key = _os.environ.get(api_key[2:-1], '')
    base_url = config['openrouter'].get('base_url', 'https://openrouter.ai/api/v1/chat/completions')
    timeout  = config['openrouter'].get('timeout', 120)

    if not api_key or api_key.startswith('${'):
        print("ERROR: OPENROUTER_API_KEY not found in environment or config.")
        print("Set it with: export OPENROUTER_API_KEY=your_key_here")
        sys.exit(1)

    # Load all JSON result files
    json_files = sorted(glob.glob(os.path.join(args.folder, "*.json")))
    if not json_files:
        print(f"No JSON files found in: {args.folder}")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f" LLM-as-Judge Evaluation")
    print(f" Judge model:  {args.judge_model}")
    print(f" Results dir:  {args.folder}")
    print(f" Files found:  {len(json_files)}")
    print(f"{'='*60}\n")

    # ── Run judge on each file ────────────────────────────────────────────────
    judge_results = []

    for json_path in json_files:
        with open(json_path, 'r') as f:
            entry = json.load(f)

        task_id       = entry.get('task_id', 'unknown')
        task_prompt   = entry.get('prompt', {}).get('input_text', '')
        category      = entry.get('task_description', '').split(' - ')[0]
        # Use centralized utility for consistent extraction
        terraform_code = extract_terraform_code(entry.get('llm_response', {}).get('raw_response', ''))
        if not terraform_code:
            terraform_code = entry.get('llm_response', {}).get('generated_code', '')
        spec_passed   = entry.get('spec_accuracy', {}).get('passed', None)
        apply_success = entry.get('final_outcome', {}).get('execution_successful', False)

        # Skip if already judged (and --skip-existing flag is set)
        if args.skip_existing and 'judge_verdict' in entry:
            existing = entry['judge_verdict']
            print(f"  {task_id:<8}  SKIPPED (already judged: {existing.get('verdict','?')})")
            judge_results.append({
                'task_id':     task_id,
                'spec_passed': spec_passed,
                'apply_ok':    apply_success,
                'verdict':     existing.get('verdict', 'Unknown'),
                'file':        os.path.basename(json_path),
            })
            continue

        # Skip if there's no code (e.g. C5.2 over-provisioning refusal)
        if not terraform_code or not terraform_code.strip():
            print(f"  {task_id:<8}  SKIPPED (no generated code — over-provisioning refusal?)")
            judge_results.append({
                'task_id':     task_id,
                'spec_passed': spec_passed,
                'apply_ok':    apply_success,
                'verdict':     'Skip',
                'file':        os.path.basename(json_path),
            })
            continue

        print(f"  {task_id:<8}  Calling judge...", end='', flush=True)

        response_text = call_judge(
            api_key=api_key,
            judge_model=args.judge_model,
            task_prompt=task_prompt,
            category=category,
            terraform_code=terraform_code,
            base_url=base_url,
            timeout=timeout,
        )

        verdict = parse_verdict(response_text)
        print(f"  →  {verdict}")

        # Write verdict back into the JSON entry and save
        entry['judge_verdict'] = {
            'verdict':      verdict,        # 'Correct', 'Incorrect', or 'Unknown'
            'judge_model':  args.judge_model,
            'raw_response': response_text,
        }
        with open(json_path, 'w') as f:
            json.dump(entry, f, indent=2)

        judge_results.append({
            'task_id':     task_id,
            'spec_passed': spec_passed,
            'apply_ok':    apply_success,
            'verdict':     verdict,
            'file':        os.path.basename(json_path),
        })

        # Brief pause to avoid rate limiting
        time.sleep(0.5)

    # ── Confusion matrix: spec_checker vs judge ───────────────────────────────
    print(f"\n{'='*60}")
    print(f" Results Summary")
    print(f"{'='*60}")

    # Only include rows where we have both a judge verdict and a spec result
    valid = [r for r in judge_results
             if r['verdict'] in ('Correct', 'Incorrect')
             and r['spec_passed'] is not None]

    if not valid:
        print("  Not enough data for confusion matrix (need both judge verdict and spec result).")
    else:
        # Judge = ground truth, spec_checker = prediction
        # True Positive  (TP): judge=Correct   AND spec=True   → spec correctly approved
        # False Positive (FP): judge=Incorrect AND spec=True   → spec wrongly approved (too lenient)
        # False Negative (FN): judge=Correct   AND spec=False  → spec wrongly rejected (too strict)
        # True Negative  (TN): judge=Incorrect AND spec=False  → spec correctly rejected

        TP = sum(1 for r in valid if r['verdict'] == 'Correct'   and r['spec_passed'] == True)
        FP = sum(1 for r in valid if r['verdict'] == 'Incorrect' and r['spec_passed'] == True)
        FN = sum(1 for r in valid if r['verdict'] == 'Correct'   and r['spec_passed'] == False)
        TN = sum(1 for r in valid if r['verdict'] == 'Incorrect' and r['spec_passed'] == False)

        precision = TP / (TP + FP) if (TP + FP) > 0 else 0.0
        recall    = TP / (TP + FN) if (TP + FN) > 0 else 0.0
        accuracy  = (TP + TN) / len(valid) if valid else 0.0
        f1        = (2 * precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0

        print(f"\n  Confusion Matrix  (judge = ground truth, spec_checker = prediction)")
        print(f"  ┌─────────────────────────────────────────────────┐")
        print(f"  │               │  Judge: Correct  │  Judge: Wrong │")
        print(f"  │ Spec: PASSED  │  TP = {TP:<3}         │  FP = {FP:<3}        │")
        print(f"  │ Spec: FAILED  │  FN = {FN:<3}         │  TN = {TN:<3}        │")
        print(f"  └─────────────────────────────────────────────────┘")

        print(f"\n  Precision:  {precision:.2%}  (when spec says PASS, how often is judge also Correct?)")
        print(f"  Recall:     {recall:.2%}  (of all judge-Correct cases, how many did spec catch?)")
        print(f"  Accuracy:   {accuracy:.2%}  (overall spec_checker agreement with judge)")
        print(f"  F1:         {f1:.2%}")

        if FP > 0:
            fp_tasks = [r['task_id'] for r in valid if r['verdict'] == 'Incorrect' and r['spec_passed'] == True]
            print(f"\n  ⚠  spec_checker is TOO LENIENT on: {fp_tasks}")
            print(f"     These passed spec but judge says they are WRONG.")
        if FN > 0:
            fn_tasks = [r['task_id'] for r in valid if r['verdict'] == 'Correct' and r['spec_passed'] == False]
            print(f"\n  ⚠  spec_checker is TOO STRICT on: {fn_tasks}")
            print(f"     These failed spec but judge says they are CORRECT.")

    # Per-task summary table
    print(f"\n  {'Task':<8}  {'Spec':<6}  {'Apply':<7}  {'Judge':<12}  {'Agreement'}")
    print(f"  {'-'*8}  {'-'*6}  {'-'*7}  {'-'*12}  {'-'*10}")
    for r in judge_results:
        spec_str  = '✓' if r['spec_passed'] else ('✗' if r['spec_passed'] is False else '?')
        apply_str = '✓' if r['apply_ok'] else '✗'
        agree     = ''
        if r['verdict'] in ('Correct', 'Incorrect') and r['spec_passed'] is not None:
            judge_correct = r['verdict'] == 'Correct'
            agree = '✓ agree' if judge_correct == r['spec_passed'] else '✗ DISAGREE'
        print(f"  {r['task_id']:<8}  {spec_str:<6}  {apply_str:<7}  {r['verdict']:<12}  {agree}")

    print(f"\n  Judge verdicts written into each JSON file under 'judge_verdict' key.")
    print(f"  Run again with --skip-existing to skip already-judged files.")


if __name__ == "__main__":
    main()
