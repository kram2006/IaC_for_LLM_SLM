import json
import pandas as pd
import os

def consolidate_results():
    results_path = r"c:\Users\kalar\Downloads\llm_eval_RK\iac-eval-main\results\comparison_official\qwen_vs_claude_python_official.json"
    output_dir = r"c:\Users\kalar\Downloads\llm_eval_RK\iac-eval-main\results\comparison_official"
    
    if not os.path.exists(results_path):
        print(f"Results not found at {results_path}")
        return

    with open(results_path, 'r') as f:
        data = json.load(f)
    
    metrics = data.get("metrics", {})
    
    # Flatten results for CSV
    summary_data = {
        "Candidate": [data.get("candidate", "Qwen-14B-Ollama")],
        "Reference": [data.get("reference", "Claude-4.5-Sonnet")],
    }
    for k, v in metrics.items():
        summary_data[k] = [v]
        
    df = pd.DataFrame(summary_data)
    csv_path = os.path.join(output_dir, "performance_leaderboard_official.csv")
    df.to_csv(csv_path, index=False)
    print(f"✅ Consolidated CSV saved to {csv_path}")

if __name__ == "__main__":
    consolidate_results()
