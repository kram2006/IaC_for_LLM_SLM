"""
Inject Phi-4 14B Ollama results into comparison_dataset.json.

Reads each result JSON from results/dataset/Phi4_14B_Ollama_Results/,
extracts the generated HCL code and functional metadata,
and injects them into comparison/comparison_dataset.json with prefix 'code_Phi4_14B_Ollama'.
"""

import json
import os
import glob
from pathlib import Path

BASE_DIR = Path(r"c:\Users\kalar\Downloads\llm_eval_RK\iac-eval-main")
PHI4_RESULTS_DIR = BASE_DIR / "results" / "dataset" / "Phi4_14B_Ollama_Results"
COMPARISON_JSON = BASE_DIR / "comparison" / "comparison_dataset.json"
PREFIX = "code_Phi4_14B_Ollama"

def main():
    # Load existing comparison dataset
    with open(COMPARISON_JSON, 'r', encoding='utf-8') as f:
        dataset = json.load(f)

    # Build task_id -> index map
    task_map = {}
    for i, row in enumerate(dataset):
        task_map[row["task_id"]] = i

    # Find all Phi-4 result JSONs
    phi4_files = sorted(glob.glob(str(PHI4_RESULTS_DIR / "*.json")))
    print(f"Found {len(phi4_files)} Phi-4 result files")

    # Track which task_ids we've seen (handle duplicates by picking the latest)
    injected = {}

    for fpath in phi4_files:
        with open(fpath, 'r', encoding='utf-8') as f:
            result = json.load(f)

        task_id = result.get("task_id", "")
        if not task_id:
            print(f"  SKIP {fpath}: no task_id")
            continue

        # Extract generated code
        code = result.get("llm_response", {}).get("generated_code", "")
        
        # Extract functional metadata
        outcome = result.get("final_outcome", {})
        exec_results = result.get("execution_results", {})
        llm_resp = result.get("llm_response", {})
        
        iterations = outcome.get("total_iterations", 1)
        exec_success = outcome.get("execution_successful", False)
        meets_req = outcome.get("meets_requirements", False)
        first_try = outcome.get("worked_as_generated", False)
        fixes = outcome.get("total_fixes_needed", 0)
        time_s = llm_resp.get("time_to_generate_seconds", -1)
        
        # Terraform step statuses
        init_ok = exec_results.get("terraform_init", {}).get("status") == "success"
        validate_ok = exec_results.get("terraform_validate", {}).get("status") == "success"
        plan_ok = exec_results.get("terraform_plan", {}).get("status") == "success"
        apply_ok = exec_results.get("terraform_apply", {}).get("status") == "success"

        # If duplicate task_id, keep the one with successful execution (or the latest)
        if task_id in injected:
            prev = injected[task_id]
            if prev["exec_success"] and not exec_success:
                print(f"  SKIP {os.path.basename(fpath)}: duplicate {task_id}, keeping previous successful run")
                continue
        
        injected[task_id] = {
            "code": code,
            "iterations": iterations,
            "exec_success": exec_success,
            "meets_req": meets_req,
            "first_try": first_try,
            "fixes": fixes,
            "time_s": round(time_s, 2) if time_s > 0 else -1,
            "init": init_ok,
            "validate": validate_ok,
            "plan": plan_ok,
            "apply": apply_ok,
        }
        print(f"  {task_id}: code_len={len(code)}, iters={iterations}, success={exec_success}, first_try={first_try}")

    # Inject into dataset
    injected_count = 0
    for task_id, data in injected.items():
        if task_id not in task_map:
            print(f"  WARNING: {task_id} not found in comparison_dataset.json!")
            continue

        idx = task_map[task_id]
        row = dataset[idx]

        row[f"{PREFIX}"] = data["code"]
        row[f"{PREFIX}_iterations"] = data["iterations"]
        row[f"{PREFIX}_time_s"] = data["time_s"]
        row[f"{PREFIX}_exec_success"] = data["exec_success"]
        row[f"{PREFIX}_meets_req"] = data["meets_req"]
        row[f"{PREFIX}_fixes_needed"] = data["fixes"]
        row[f"{PREFIX}_first_try"] = data["first_try"]
        row[f"{PREFIX}_init"] = data["init"]
        row[f"{PREFIX}_validate"] = data["validate"]
        row[f"{PREFIX}_plan"] = data["plan"]
        row[f"{PREFIX}_apply"] = data["apply"]
        injected_count += 1

    # Save updated dataset
    with open(COMPARISON_JSON, 'w', encoding='utf-8') as f:
        json.dump(dataset, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Injected Phi-4 data for {injected_count}/{len(injected)} tasks into {COMPARISON_JSON}")
    print(f"   Prefix: {PREFIX}")


if __name__ == "__main__":
    main()
