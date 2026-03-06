"""Full verification: cross-check comparison_dataset.json against ALL raw result JSONs."""
import json, glob, os

EXPECTED_TASKS = {"C1.1", "C1.2", "C1.3", "C2.2", "C2.3", "R1.2", "U1.2", "D1.2", "D2.2"}
SKIP_TASKS = {"C5.2"}

ds = json.load(open("comparison/comparison_dataset.json"))
ds_map = {r["task_id"]: r for r in ds}
ds_tasks = set(ds_map.keys()) - SKIP_TASKS

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

base = "results/dataset"
MODEL_DS_KEY_MAP = {
    "Qwen2.5_Coder_14B_Ollama_Results": "code_Qwen_14B_Ollama",
    "sonnet_4_5": "ref_Sonnet_4_5",
    "DeepSeek_v3.2_Results": "ref_DeepSeek_v3.2",
    "Gemini_3_Pro_Results": "ref_Gemini_3_Pro",
    "GPT_5.2_Codex_Results": "ref_GPT_5.2_Codex",
    "kimi_k2": "ref_Kimi_k2",
    "qwen_3_coder": "ref_Qwen_3_Coder",
}

errors = []

for subdir in sorted(os.listdir(base)):
    full = os.path.join(base, subdir)
    if not os.path.isdir(full):
        continue
    files = glob.glob(os.path.join(full, "*.json"))
    task_ids = []
    for f in files:
        raw = json.load(open(f))
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
    ds_key = MODEL_DS_KEY_MAP.get(subdir, None)
    if ds_key:
        for f in files:
            raw = json.load(open(f))
            tid = raw.get("task_metadata", {}).get("task_id", raw.get("task_id", "?"))
            if tid in SKIP_TASKS or tid not in ds_map:
                continue
            r = ds_map[tid]
            fo = raw["final_outcome"]
            # Check iterations
            ds_iters = r.get(f"{ds_key}_iterations")
            if ds_iters is not None and fo["total_iterations"] != ds_iters:
                errors.append(f"{tid}/{subdir}: iters raw={fo['total_iterations']} ds={ds_iters}")
            # Check first_try
            ds_ft = r.get(f"{ds_key}_first_try")
            if ds_ft is not None and fo["worked_as_generated"] != ds_ft:
                errors.append(f"{tid}/{subdir}: first_try raw={fo['worked_as_generated']} ds={ds_ft}")
            # Check exec_success
            ds_es = r.get(f"{ds_key}_exec_success")
            if ds_es is not None and fo["execution_successful"] != ds_es:
                errors.append(f"{tid}/{subdir}: exec_success mismatch")
            # Check meets_req
            ds_mr = r.get(f"{ds_key}_meets_req")
            if ds_mr is not None and fo["meets_requirements"] != ds_mr:
                errors.append(f"{tid}/{subdir}: meets_req mismatch")

# 3. Summary
print("\n" + "=" * 70)
print("  SUMMARY")
print("=" * 70)
if errors:
    print(f"ERRORS FOUND ({len(errors)}):")
    for e in errors:
        print(f"  - {e}")
else:
    print("ALL DATA MATCHES across all 7 model directories and comparison_dataset.json")
    print("No discrepancies found in: iterations, first_try, exec_success, meets_req")
