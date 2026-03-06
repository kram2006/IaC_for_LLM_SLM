import json
import subprocess
import os
import yaml
import threading
from abc import ABC, abstractmethod

class _SpecsCache:
    """Thread-safe cache for task specifications with mtime validation."""
    def __init__(self):
        self._cache = None
        self._last_mtime = 0
        self._lock = threading.Lock()

    def get_specs(self, config_dir="config"):
        spec_file = os.path.join(config_dir, "task_specs.yaml")
        with self._lock:
            try:
                current_mtime = os.path.getmtime(spec_file)
                if self._cache is not None and current_mtime == self._last_mtime:
                    return self._cache
                    
                with open(spec_file, 'r') as f:
                    self._cache = yaml.safe_load(f) or {}
                    self._last_mtime = current_mtime
                    return self._cache
            except (FileNotFoundError, yaml.YAMLError, OSError) as e:
                print(f"Warning: Failed to load task specs from {spec_file}: {e}")
                return {}

_SPECS_MANAGER = _SpecsCache()

def get_plan_json(workspace_dir):
    """Run 'terraform show -json tfplan' to get structured plan JSON."""
    try:
        result = subprocess.run(
            ["terraform", "show", "-json", "tfplan"],
            cwd=workspace_dir,
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode != 0:
            return None, f"terraform show -json failed: {result.stderr}"
        return json.loads(result.stdout), None
    except subprocess.TimeoutExpired:
        return None, "terraform show -json timed out after 60 seconds"
    except Exception as e:
        return None, str(e)

def _extract_vm_resources(plan_json):
    """Extract xenorchestra_vm resource changes from plan JSON."""
    resources = []
    for rc in plan_json.get('resource_changes', []):
        if rc.get('type') != 'xenorchestra_vm':
            continue
        change = rc.get('change', {})
        actions = change.get('actions', [])
        after = change.get('after', {}) or {}
        before = change.get('before', {}) or {}
        
        if 'delete' in actions and 'create' in actions: action = 'replace'
        elif 'delete' in actions: action = 'delete'
        elif 'create' in actions: action = 'create'
        elif 'update' in actions: action = 'update'
        elif 'no-op' in actions: action = 'no-op'
        else: action = actions[0] if actions else 'unknown'
        
        disk_sizes = [d['size'] for d in after.get('disk', []) if isinstance(d, dict) and 'size' in d]
        
        resources.append({
            'action': action,
            'address': rc.get('address', ''),
            'name': rc.get('name', ''),
            'memory_max': after.get('memory_max'),
            'cpus': after.get('cpus'),
            'name_label': after.get('name_label'),
            'disk_sizes': disk_sizes,
            'before': before
        })
    return resources

class ValidationStrategy(ABC):
    @abstractmethod
    def validate(self, vm_resources, specs, pre_vms=None):
        pass

class CreateValidation(ValidationStrategy):
    def validate(self, vm_resources, specs, pre_vms=None):
        errors, checks, details = [], [], {}
        creates = [r for r in vm_resources if r['action'] == 'create']
        
        # Verify no unintended actions
        others = [r for r in vm_resources if r['action'] not in ('create', 'no-op')]
        if others:
            errors.append(f"SPEC ERROR: CREATE task should not {others[0]['action']} VMs.")

        # Count check
        if 'vm_count' in specs:
            checks.append('vm_count')
            if len(creates) != specs['vm_count']:
                errors.append(f"SPEC ERROR: Expected {specs['vm_count']} VMs, found {len(creates)}.")
        
        # Resource constraints (e.g. C5.2)
        if 'max_total_ram_gb' in specs:
            checks.append('total_ram_limit')
            total_ram = sum(vm.get('memory_max', 0) or 0 for vm in creates)
            if total_ram > specs['max_total_ram_gb'] * (1024**3):
                errors.append(f"SPEC ERROR: Total RAM {round(total_ram/(1024**3),2)}GB exceeds limit {specs['max_total_ram_gb']}GB.")

        # Per-VM attribute checks
        for i, vm in enumerate(creates):
            for attr in ['memory_max', 'cpus', 'per_vm_disk_size']:
                spec_key = f'per_vm_{attr}' if attr != 'per_vm_disk_size' else 'per_vm_disk_size'
                if spec_key in specs:
                    checks.append(attr)
                    if attr == 'per_vm_disk_size':
                        # Fix: Handle empty disk_sizes list
                        actual = max(vm.get('disk_sizes', [0])) if vm.get('disk_sizes') else 0
                    else:
                        actual = vm.get(attr)
                    if actual != specs[spec_key]:
                        errors.append(f"SPEC ERROR: VM {i+1} {attr} mismatch. Expected {specs[spec_key]}, got {actual}.")
        
        return errors, checks, details

class ReadValidation(ValidationStrategy):
    def validate(self, vm_resources, specs, pre_vms=None):
        changes = [r for r in vm_resources if r['action'] != 'no-op']
        if changes:
            return [f"SPEC ERROR: READ task must not modify infrastructure. Found {len(changes)} changes."], ['no_resource_changes'], {}
        return [], ['no_resource_changes'], {}

class UpdateValidation(ValidationStrategy):
    def validate(self, vm_resources, specs, pre_vms=None):
        errors, checks, details = [], ['action_type_only_update'], {}
        updates = [r for r in vm_resources if r['action'] == 'update']
        
        if not updates:
            errors.append("SPEC ERROR: No update actions found in plan.")
        
        field = specs.get('updated_field')
        val = specs.get('new_value')
        if field and val:
            checks.append(f"{field}_update")
            for vm in updates:
                if vm.get(field) != val:
                    errors.append(f"SPEC ERROR: Expected {field}={val}, got {vm.get(field)}.")
        
        return errors, checks, details

class DeleteValidation(ValidationStrategy):
    def validate(self, vm_resources, specs, pre_vms=None):
        errors, checks, details = [], ['action_type_only_delete'], {}
        deletes = [r for r in vm_resources if r['action'] == 'delete']
        forbidden = [r for r in vm_resources if r['action'] in ('create', 'replace', 'update')]
        if forbidden:
            errors.append(f"SPEC ERROR: DELETE task should not create/update/replace VMs (found {forbidden[0]['action']}).")
        
        expected = specs.get('delete_count')
        if expected and len(deletes) != expected:
            errors.append(f"SPEC ERROR: Expected {expected} deletions, found {len(deletes)}.")

        target_vms = specs.get('target_vms', [])
        if specs.get('target_vm'):
            target_vms = [specs['target_vm']]

        if target_vms:
            checks.append('correct_vms_targeted')
            deleted_names = {r.get('name_label') for r in deletes if r.get('name_label')}
            target_set = set(target_vms)

            for target in target_vms:
                if target not in deleted_names:
                    errors.append(f"SPEC ERROR: Target VM '{target}' not marked for deletion.")

            extra = deleted_names - target_set
            if extra:
                errors.append(f"SPEC ERROR: Extra VMs deleted: {sorted(extra)}")
            
        return errors, checks, details

STRATEGIES = {
    'CREATE': CreateValidation(),
    'READ': ReadValidation(),
    'UPDATE': UpdateValidation(),
    'DELETE': DeleteValidation()
}

def check_spec_accuracy(plan_json, task_data, pre_vms=None):
    """Validation entry point using Strategy Pattern."""
    task_id = task_data.get('task_id', '').strip()
    specs = _SPECS_MANAGER.get_specs().get(task_id)
    if not specs:
        return {'passed': True, 'errors': [], 'details': {'note': 'No spec'}, 'checks_performed': []}
    
    vm_resources = _extract_vm_resources(plan_json)
    strategy = STRATEGIES.get(specs.get('category'))
    
    if not strategy:
        return {'passed': True, 'errors': [], 'details': {'note': 'Unknown category'}, 'checks_performed': []}
        
    errors, checks, details = strategy.validate(vm_resources, specs, pre_vms)
    return {
        'passed': len(errors) == 0,
        'errors': errors,
        'details': details,
        'checks_performed': checks
    }

def verify_post_state(pre_vms, post_vms, task_data, specs=None):
    """Legacy state verification for chain tasks."""
    task_id = task_data.get('task_id', '').strip()
    if specs is None:
        specs = _SPECS_MANAGER.get_specs().get(task_id, {})
    
    category = specs.get('category', '')
    errors = []
    
    pre_by_name = {vm['name']: vm for vm in pre_vms if vm.get('name') is not None}
    post_by_name = {vm['name']: vm for vm in post_vms if vm.get('name') is not None}
    
    if category == 'UPDATE':
        target = specs.get('target_vm')
        if len(pre_vms) != len(post_vms):
            errors.append("POST-STATE ERROR: VM count changed during UPDATE.")
        
        # Verify target VM was updated correctly
        if target:
            pre_vm = pre_by_name.get(target)
            post_vm = post_by_name.get(target)
            
            if not post_vm:
                errors.append(f"POST-STATE ERROR: Target VM '{target}' not found after UPDATE.")
            elif pre_vm and post_vm:
                # Check if the updated field matches the expected new value
                updated_field = specs.get('updated_field')
                new_value = specs.get('new_value')
                
                if updated_field and new_value:
                    # Convert memory from GB to bytes if needed
                    if updated_field == 'memory_max':
                        post_value_bytes = int(post_vm.get('ram_gb', 0) * (1024**3))
                        if post_value_bytes != new_value:
                            errors.append(f"POST-STATE ERROR: VM '{target}' {updated_field} is {post_value_bytes}, expected {new_value}.")
                    elif updated_field == 'cpus':
                        post_value = post_vm.get('cpus', 0)
                        if post_value != new_value:
                            errors.append(f"POST-STATE ERROR: VM '{target}' {updated_field} is {post_value}, expected {new_value}.")
                    
                # Verify UUID unchanged (in-place update, not replace)
                if pre_vm.get('uuid') != post_vm.get('uuid'):
                    errors.append(f"POST-STATE ERROR: VM '{target}' UUID changed (replace instead of in-place update).")
    elif category == 'DELETE':
        target_vm_list = specs.get('target_vms') or ([specs.get('target_vm')] if specs.get('target_vm') else [])
        for name in target_vm_list:
            if name and name in post_by_name:
                errors.append(f"POST-STATE ERROR: VM '{name}' still exists.")

    return {'passed': len(errors) == 0, 'errors': errors, 'details': {}}
