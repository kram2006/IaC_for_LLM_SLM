"""Full verification: cross-check comparison_dataset.json against ALL raw result JSONs."""
import argparse
import json
import glob
import os

EXPECTED_TASKS = {"C1.1", "C1.2", "C1.3", "C2.2", "C2.3", "R1.2", "U1.2", "D1.2", "D2.2"}
SKIP_TASKS = {"C5.2"}


def main():
    parser = argparse.ArgumentParser(description="Verify comparison dataset consistency against result folders.")
    parser.add_argument("--comparison-json", default="comparison/comparison_dataset.json", help="Path to comparison dataset JSON")
    parser.add_argument("--results-base", default="results/dataset", help="Path to results/dataset directory")
    args = parser.parse_args()

    if not os.path.exists(args.comparison_json):
        print(f"ERROR: comparison dataset not found: {args.comparison_json}")
        return
    if not os.path.isdir(args.results_base):
        print(f"ERROR: results dataset directory not found: {args.results_base}")
        return

    with open(args.comparison_json, encoding="utf-8") as fp:
        ds = json.load(fp)
    ds_map = {r["task_id"]: r for r in ds}

    print("=" * 70)
    print("  VERIFICATION: comparison_dataset.json vs results/dataset")
    print("=" * 70)

    # 1. Check dataset task coverage
    print(f"\nDataset entries: {len(ds)}")
    print(f"Dataset task IDs: {sorted(ds_map.keys())}")
    print(f"Expected (excl C5.2): {sorted(EXPECTED_TASKS)}")
    missing_from_ds = EXPECTED_TASKS - set(ds_map.keys())
    extra_in_ds = (set(ds_map.keys()) - SKIP_TASKS) - EXPECTED_TASKS
    if missing_from_ds:
        print(f"  MISSING from dataset: {missing_from_ds}")
    if extra_in_ds:
        print(f"  EXTRA in dataset: {extra_in_ds}")
    if not missing_from_ds and not extra_in_ds:
        print("  OK: Dataset has exactly the expected 9 tasks (+ C5.2 filtered)")

    # 2. Check ALL model directories in results/dataset
    print("\n" + "-" * 70)
    print("  MODEL DIRECTORIES AUDIT")
    print("-" * 70)

    model_ds_key_map = {
        "Qwen2.5_Coder_14B_Ollama_Results": "code_Qwen_14B_Ollama",
        "sonnet_4_5": "ref_Sonnet_4_5",
        "DeepSeek_v3.2_Results": "ref_DeepSeek_v3.2",
        "Gemini_3_Pro_Results": "ref_Gemini_3_Pro",
        "GPT_5.2_Codex_Results": "ref_GPT_5.2_Codex",
        "kimi_k2": "ref_Kimi_k2",
        "qwen_3_coder": "ref_Qwen_3_Coder",
    }

    errors = []

    for subdir in sorted(os.listdir(args.results_base)):
        full = os.path.join(args.results_base, subdir)
        if not os.path.isdir(full):
            continue
        files = glob.glob(os.path.join(full, "*.json"))
        task_ids = []
        for file_path in files:
            with open(file_path, encoding="utf-8") as fp:
                raw = json.load(fp)
            tid = raw.get("task_metadata", {}).get("task_id", raw.get("task_id", "?"))
            task_ids.append(tid)
        task_ids_set = set(task_ids) - SKIP_TASKS
        missing = EXPECTED_TASKS - task_ids_set
        extra = task_ids_set - EXPECTED_TASKS

        status = "OK" if not missing and not extra else "ISSUE"
        print(f"\n  {subdir}: {len(files)} files, tasks={sorted(task_ids)}")
        if missing:
            print(f"    MISSING: {sorted(missing)}")
            errors.append(f"{subdir}: missing {sorted(missing)}")
        if extra:
            print(f"    EXTRA: {sorted(extra)}")
            errors.append(f"{subdir}: extra {sorted(extra)}")
        if not missing and not extra:
            print(f"    {status}: All 9 expected tasks present")

        # Cross-check metadata against comparison_dataset.json
        ds_key = model_ds_key_map.get(subdir, None)
        if ds_key:
            for file_path in files:
                with open(file_path, encoding="utf-8") as fp:
                    raw = json.load(fp)
                tid = raw.get("task_metadata", {}).get("task_id", raw.get("task_id", "?"))
                if tid in SKIP_TASKS or tid not in ds_map:
                    continue
                row = ds_map[tid]
                fo = raw["final_outcome"]
                ds_iters = row.get(f"{ds_key}_iterations")
                if ds_iters is not None and fo["total_iterations"] != ds_iters:
                    errors.append(f"{tid}/{subdir}: iters raw={fo['total_iterations']} ds={ds_iters}")
                ds_ft = row.get(f"{ds_key}_first_try")
                if ds_ft is not None and fo["worked_as_generated"] != ds_ft:
                    errors.append(f"{tid}/{subdir}: first_try raw={fo['worked_as_generated']} ds={ds_ft}")
                ds_es = row.get(f"{ds_key}_exec_success")
                if ds_es is not None and fo["execution_successful"] != ds_es:
                    errors.append(f"{tid}/{subdir}: exec_success mismatch")
                ds_mr = row.get(f"{ds_key}_meets_req")
                if ds_mr is not None and fo["meets_requirements"] != ds_mr:
                    errors.append(f"{tid}/{subdir}: meets_req mismatch")

    # 3. Summary
    print("\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    if errors:
        print(f"ERRORS FOUND ({len(errors)}):")
        for error in errors:
            print(f"  - {error}")
    else:
        print("ALL DATA MATCHES across all 7 model directories and comparison_dataset.json")
        print("No discrepancies found in: iterations, first_try, exec_success, meets_req")


if __name__ == "__main__":
    main()
