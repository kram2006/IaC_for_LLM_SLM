import json
import pandas as pd
import os
import argparse

def consolidate_results(results_path, output_dir):
    
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
    parser = argparse.ArgumentParser(description="Consolidate comparison results into leaderboard CSV.")
    parser.add_argument(
        "--results-path",
        default="results/comparison_official/qwen_vs_claude_python_official.json",
        help="Path to qwen_vs_claude_python_official.json"
    )
    parser.add_argument(
        "--output-dir",
        default="results/comparison_official",
        help="Directory where performance_leaderboard_official.csv will be written"
    )
    args = parser.parse_args()
    consolidate_results(args.results_path, args.output_dir)
