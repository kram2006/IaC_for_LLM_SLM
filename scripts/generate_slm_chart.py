import matplotlib.pyplot as plt
import numpy as np
import os

# Ensure output directory exists
os.makedirs("results", exist_ok=True)

# Data
models = [
    "Qwen 2.5 Coder 14B",
    "DeepSeek R1 14B",
    "Qwen 2.5 7B",
    "Qwen 3 1.7B",
    "Qwen 3 0.6B"
]
tasks_completed = [10, 7, 0, 0, 0]
colors = ['#2ca02c', '#ff7f0e', '#d62728', '#d62728', '#d62728']  # Green, Orange, Red

# Create Bar Chart
plt.figure(figsize=(10, 6))
bars = plt.bar(models, tasks_completed, color=colors, width=0.6)

plt.xlabel('Small Language Models (SLMs)', fontsize=12, labelpad=10)
plt.ylabel('Tasks Completed (out of 10)', fontsize=12, labelpad=10)
plt.title('Small Language Model (SLM) Performance on Terraform IaC Tasks', fontsize=14, pad=20)
plt.ylim(0, 11)
plt.yticks(np.arange(0, 11, 1))
plt.grid(axis='y', linestyle='--', alpha=0.5)

# Add values on top of bars
for bar in bars:
    height = bar.get_height()
    plt.text(bar.get_x() + bar.get_width() / 2.0, height + 0.2, f'{height}/10', ha='center', va='bottom', fontsize=10, fontweight='bold')

plt.tight_layout()
output_path = os.path.join("results", "slm_performance_chart.png")
plt.savefig(output_path, dpi=300)
print(f"Chart saved to {output_path}")
