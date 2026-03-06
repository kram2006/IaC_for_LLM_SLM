import os
import csv
import json
import subprocess
import shutil
from typing import Dict, Any, List

def fixed_loc(reference: str) -> int:
    return sum(1 for line in reference.splitlines() if line.strip())

def fixed_findkeys(node: Any, kv: str):
    """Recursively yield all items under the given key in a nested data structure."""
    if isinstance(node, list):
        for i in node:
            for x in fixed_findkeys(i, kv):
                yield x
    elif isinstance(node, dict):
        if kv in node:
            yield node[kv]
        for j in node.values():
            for x in fixed_findkeys(j, kv):
                yield x

import re

def extract_resource_types_from_hcl(hcl_text: str) -> List[str]:
    """Parse the HCL text and extract the list of resource types statically."""
    # Matches: resource "type" "name" { or: data "type" "name" {
    res_types = re.findall(r'(?:resource|data)\s+"([^"]+)"', hcl_text)
    return sorted(list(set(res_types)))

def get_difficulty_level(LOC: int, num_resources: int, num_interconnections: int) -> str:
    """Assign a 1-6 difficulty level based on the R1 stratification thresholds."""
    if LOC < 10  and num_resources < 2  and num_interconnections < 2:  return "1"
    if LOC < 20  and num_resources < 4  and num_interconnections < 4:  return "2"
    if LOC < 40  and num_resources < 6  and num_interconnections < 6:  return "3"
    if LOC < 60  and num_resources < 8  and num_interconnections < 8:  return "4"
    if LOC < 80  and num_resources < 10 and num_interconnections < 10: return "5"
    return "6"

def analyze_hcl_complexity(hcl_text: str, workspace_dir: str = None) -> Dict[str, Any]:
    """Calculate the 3 complexity metrics using static string analysis."""
    if not hcl_text or not hcl_text.strip():
        hcl_text = ""
        
    loc = fixed_loc(hcl_text)
    
    # 1. Total resources defined in the configuration (resources + data blocks)
    res_count = hcl_text.count('resource "') + hcl_text.count('data "')
    
    # 2. Total cross-references
    # XenOrchestra IDs and names references. Exclude 'name_label =' to avoid double counting.
    inter_count = hcl_text.count(".id") + hcl_text.count(".name") - hcl_text.count("name_label")
    
    # 3. Resource types (R6)
    resource_types = extract_resource_types_from_hcl(hcl_text)
    
    level = get_difficulty_level(loc, res_count, inter_count)
    
    return {
        "loc": loc,
        "resources": res_count,
        "interconnections": inter_count,
        "level": level,
        "resource_types": resource_types
    }

def score_dataset(csv_path: str):
    """Score all tasks in the dataset and rewrite with a Difficulty column."""
    workspace = ".complexity_workspace"
    rows = []
    resource_counts = {} # Global tracker for R6
    
    with open(csv_path, 'r', newline='') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        if 'complexity_level' not in fieldnames:
            fieldnames.extend(['complexity_loc', 'complexity_resources', 'complexity_interconnections', 'complexity_level'])
            
        for row in reader:
            hcl = row.get('reference_hcl', '')
            if not hcl:
                rows.append(row)
                continue
                
            print(f"Analyzing {row['task_id']}...", end=" ", flush=True)
            metrics = analyze_hcl_complexity(hcl, workspace)
            
            row['complexity_loc'] = metrics['loc']
            row['complexity_resources'] = metrics['resources']
            row['complexity_interconnections'] = metrics['interconnections']
            row['complexity_level'] = metrics['level']
            
            # Track resource distribution (R6)
            for rtype in metrics['resource_types']:
                resource_counts[rtype] = resource_counts.get(rtype, 0) + 1
                
            print(f"Level {metrics['level']} (LOC: {metrics['loc']}, Res: {metrics['resources']}, Inter: {metrics['interconnections']})")
            rows.append(row)
            
        
    with open(csv_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    # ── Resource Type Distribution Summary (R6) ───────────────────────────────
    if resource_counts:
        print(f"\n{'='*60}")
        print(f" RESOURCE TYPE DISTRIBUTION")
        print(f"{'='*60}")
        print(f"  {'Resource Type':<40}  {'Tasks':<6}  Bar")
        print(f"  {'-'*40}  {'-'*6}  {'-'*10}")
        for rtype, count in sorted(resource_counts.items(), key=lambda x: -x[1]):
            bar = "█" * count
            print(f"  {rtype:<40}  {count:<6}  {bar}")

        dist_path = os.path.join(os.path.dirname(csv_path), "resource_distribution.json")
        dist_output = {
            "total_tasks_scored": len([r for r in rows if r.get('complexity_level')]),
            "resource_type_counts": resource_counts
        }
        with open(dist_path, 'w', encoding='utf-8') as f:
            json.dump(dist_output, f, indent=2)
        print(f"\n  ✓ Resource distribution saved to: {dist_path}")
        
if __name__ == "__main__":
    score_dataset('tasks/vm_provisioning_tasks.csv')
