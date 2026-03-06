import csv
import os

def load_hcls(refs_dir="tasks/references"):
    """Load all .tf files from the references directory."""
    hcls = {}
    if not os.path.exists(refs_dir):
        print(f"Warning: References directory {refs_dir} not found.")
        return hcls
        
    for filename in os.listdir(refs_dir):
        if filename.endswith(".tf"):
            task_id = filename[:-3] # Remove .tf
            filepath = os.path.join(refs_dir, filename)
            with open(filepath, 'r') as f:
                hcls[task_id] = f.read()
    return hcls

def populate(csv_path='tasks/vm_provisioning_tasks.csv', refs_dir="tasks/references"):
    """Sync the reference_hcl column in the CSV with the .tf files in refs_dir."""
    hcls = load_hcls(refs_dir)
    if not hcls:
        print("No HCL files found to populate.")
        return

    rows = []
    with open(csv_path, 'r', newline='') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            tid = row['task_id']
            if tid in hcls:
                row['reference_hcl'] = hcls[tid]
            rows.append(row)
            
    with open(csv_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    
    print(f"Propagated {len(hcls)} references from {refs_dir} to {csv_path}.")

if __name__ == "__main__":
    populate()
