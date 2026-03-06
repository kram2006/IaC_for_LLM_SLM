import csv
with open('tasks/vm_provisioning_tasks.csv') as f:
    r = csv.DictReader(f)
    print("Task   | Lvl | LOC | Res | Inter")
    print("-" * 35)
    for x in r:
        print(f"{x['task_id']:<6} | Lvl {x['complexity_level']} | {x['complexity_loc']:<3} | {x['complexity_resources']:<3} | {x.get('complexity_interconnections', '0')}")
