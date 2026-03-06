import json, glob
from pathlib import Path

BASE = Path(r"c:\Users\kalar\Downloads\llm_eval_RK\iac-eval-main")
RESULTS = BASE / "results" / "dataset" / "Phi4_14B_Ollama_Results"
TF_CODE = BASE / "results" / "terraform_code" / "Phi4_14B_Ollama_Results"
OUT = BASE / "phi4_verify.json"

result_files = sorted(RESULTS.glob("*.json"))
seen = set()
report = []

for rf in result_files:
    r = json.loads(rf.read_text(encoding="utf-8"))
    tid = r["task_id"]
    code = r.get("llm_response", {}).get("generated_code", "")
    outcome = r.get("final_outcome", {})
    tid_lower = tid.lower().replace(".", "_")
    task_dir = TF_CODE / tid_lower
    main_tf = task_dir / "main.tf"
    hist_dir = task_dir / "history"

    entry = {
        "task_id": tid,
        "file": rf.name,
        "duplicate": tid in seen,
        "code_len": len(code),
        "code_empty": len(code) == 0,
        "iters": outcome.get("total_iterations"),
        "first_try": outcome.get("worked_as_generated"),
        "exec_success": outcome.get("execution_successful"),
        "meets_req": outcome.get("meets_requirements"),
        "has_provider": "provider" in code,
        "has_resource_or_data": "resource" in code or "data" in code,
    }
    seen.add(tid)

    if main_tf.exists():
        tf = main_tf.read_text(encoding="utf-8").strip()
        entry["main_tf_match"] = tf == code.strip()
        entry["main_tf_len"] = len(tf)
    else:
        entry["main_tf_match"] = None
        entry["main_tf_len"] = None

    hist_files = sorted(hist_dir.glob("*.json")) if hist_dir.exists() else []
    entry["history_count"] = len(hist_files)
    report.append(entry)

OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
print(f"Wrote {len(report)} entries to {OUT}")
