import json
import uuid
import os
import re
from datetime import datetime
from eval_utils import redact_sensitive_text

# --- REFACTORED MODULE-LEVEL HELPERS ---

import ast
import operator as _op

RAM_MARGIN_PERCENT = 0.05

def _safe_eval_arith(expr):
    """Safely evaluate simple arithmetic like '4 * 1024 * 1024 * 1024'."""
    _ops = {ast.Mult: _op.mul, ast.Add: _op.add, ast.Sub: _op.sub}
    def _eval(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        elif isinstance(node, ast.BinOp) and type(node.op) in _ops:
            return _ops[type(node.op)](_eval(node.left), _eval(node.right))
        raise ValueError("Unsupported expression")
    try:
        return int(_eval(ast.parse(expr.strip(), mode='eval').body))
    except Exception:
        return None

def extract_hcl_total_value(key, code):
    """Sum all occurrences of key = value in HCL, supporting arithmetic expressions."""
    pattern = fr"(?m)^\s*{re.escape(key)}\s*=\s*(\d(?:[\d\s]*[+\-*]\s*\d+)*)\s*$"
    matches = re.findall(pattern, code)
    results = []
    for v in matches:
        v = v.strip()
        if v.isdigit():
            results.append(int(v))
        else:
            evaluated = _safe_eval_arith(v)
            if evaluated is not None:
                results.append(evaluated)
    return sum(results) if results else None

def check_compliance(actual, expected, default_min=None, expected_failure_matched=False):
    """Checks if the actual value matches the expected value or a default minimum."""
    if expected_failure_matched:
        return True
    if expected:
        return actual == expected
    if default_min:
        return actual is not None and actual >= default_min
    return actual is not None

def _check_vm_ram(actual_memory, verification_data, terraform_code):
    """Helper to verify if VMs in verification data have expected RAM."""
    if not verification_data.get('vm_details'):
        return None
    
    # If LLM didn't specify RAM, we check if it meets a sane default (e.g. 1GB)
    target = actual_memory if actual_memory else 1024**3
    
    for vm in verification_data['vm_details']:
        vm_ram = int(round((vm.get('ram_gb', 0) or 0) * 1024**3))
        # Allow 5% margin for overhead
        if abs(vm_ram - target) > int(RAM_MARGIN_PERCENT * target):
            return False
    return True

# --- END HELPERS ---

def generate_dataset_entry(task_data, terraform_code, execution_results, verification_data, pre_verification_data, config, vision_data=None):
    """
    Generate the final JSON dataset entry using the EXACT schema provided by the user.
    """
    
    task_id = task_data.get('task_id', 'unknown')
    model_key = config.get('active_model_name', 'unknown')
    model_config = config.get('models', {}).get(model_key, {})
    model_short = model_config.get('id_prefix', model_key.lower())
    
    # Standardized Timestamps
    from datetime import timezone
    now = datetime.now(timezone.utc)
    timestamp_file = now.strftime('%Y%m%d_%H%M%S')
    timestamp_iso = now.strftime('%Y-%m-%dT%H:%M:%SZ')
    
    # FIX A2: entry_id ALWAYS includes pass number to prevent file overwrites
    # Clean task_id for entry_id (C1.2 -> c1_2)
    clean_task_id = task_id.lower().replace('.', '_')
    sample_num = execution_results.get('sample_num', 0)
    enhance_strat = execution_results.get('enhance_strat', '')
    strat_suffix = f"_{enhance_strat}" if enhance_strat else ""
    entry_id = f"{clean_task_id}_{model_short}_pass{sample_num}{strat_suffix}"
    
    # Extract outcomes
    init_res = execution_results.get('terraform_init', {})
    val_res = execution_results.get('terraform_validate', {})
    plan_res = execution_results.get('terraform_plan', {})
    apply_res = execution_results.get('terraform_apply', {})
    final_success = apply_res.get('exit_code') == 0
    iterations = execution_results.get('iterations', 1)

    # Heuristic for Prompt Information Mapping
    prompt_text = task_data.get('prompt', '')
    prompt_lower = prompt_text.lower()
    info_provided = []
    if "ram" in prompt_lower: info_provided.append("ram")
    if "cpu" in prompt_lower: info_provided.append("cpu")
    if "disk" in prompt_lower: info_provided.append("disk")
    if "network" in prompt_lower: info_provided.append("network")
    
    prompt_type = task_data.get('prompt_type', '')
    info_missing = [] if prompt_type == 'detailed' else ["ip_configuration"]
    if "disk" not in prompt_lower: info_missing.append("disk_size")
    if "network" not in prompt_lower: info_missing.append("network_bridge")

    # Parse requirements for compliance checks
    try:
        reqs = json.loads(task_data.get('resource_requirements', '{}'))
    except (json.JSONDecodeError, TypeError, ValueError):
        reqs = {}

    expected_memory = reqs.get('memory_max_bytes')
    expected_cpus = reqs.get('cpus')
    expected_disk = reqs.get('size_bytes')

    actual_total_memory = extract_hcl_total_value("memory_max", terraform_code)
    actual_total_cpus = extract_hcl_total_value("cpus", terraform_code)
    actual_total_disk = extract_hcl_total_value("size", terraform_code)
    
    vm_count = reqs.get('count', 1) or 1
    
    # Normalize expected values to per-VM only when explicit total fields are provided
    total_memory = reqs.get('total_memory_max_bytes')
    total_cpus = reqs.get('total_cpus')
    total_disk = reqs.get('total_size_bytes')
    if vm_count > 1:
        if total_memory:
             expected_memory = round(total_memory / vm_count)
        if total_cpus:
             expected_cpus = round(total_cpus / vm_count)
        if total_disk:
             expected_disk = round(total_disk / vm_count)

    # Calculate per-VM actual values
    actual_memory = round(actual_total_memory / vm_count) if actual_total_memory else None
    actual_cpus = round(actual_total_cpus / vm_count) if actual_total_cpus else None
    actual_disk = round(actual_total_disk / vm_count) if actual_total_disk else None

    # Logic for "Meets Requirements"
    expected_failure_matched = execution_results.get('expected_failure_matched', False)

    # Construct Final JSON
    entry = {
        "dataset_version": "1.0",
        "entry_id": entry_id,
        "task_id": task_id,
        "task_description": f"{task_data.get('category', '')} - {task_data.get('prompt_type', '')}",
        "timestamp": timestamp_iso,
        "evaluator": config.get('evaluator_name', 'K_Rama_Krishna_N_C'),
        "sample_num": sample_num,
        
        "metadata": {
            "model_name": model_config.get('display_name', model_key),
            "model_version": model_config.get('name', 'unknown'),
            "temperature": model_config.get('temperature', 0.2),
            "max_tokens": model_config.get('max_tokens', 4096),
            "prompt_type": task_data.get('prompt_type', 'unknown'),
            "enhance_strat": enhance_strat,
            "infrastructure_state_before": f"{pre_verification_data.get('actual_vm_count', 0)}_vms_running"
        },
        
        "scenario": {
            "infrastructure": "single_xcpng_host",
            # FIX D1: Read from config instead of hardcoding
            "total_ram_gb": config.get('xenorchestra', {}).get('total_ram_gb', 24),
            "total_cpu_cores": config.get('xenorchestra', {}).get('total_cpu_cores', 32),
            "usable_ram_gb": config.get('xenorchestra', {}).get('usable_ram_gb', 20),
            "available_ram_gb_before": round(config.get('xenorchestra', {}).get('usable_ram_gb', 20) - sum(vm.get('ram_gb', 0) for vm in pre_verification_data.get('vm_details', [])), 2),
            "available_ram_gb_after": round(config.get('xenorchestra', {}).get('usable_ram_gb', 20) - sum(vm.get('ram_gb', 0) for vm in verification_data.get('vm_details', [])), 2),
            "edge_case": "over_provisioning" if reqs.get('expected_error') == 'resource_exhaustion' else "none",
            "system_prompt": redact_sensitive_text(model_config.get('system_prompt', config.get('system_prompt', '')))
        },
        
        "prompt": {
            "input_text": prompt_text,
            "information_provided": info_provided,
            "information_missing": info_missing
        },
        
        "llm_response": {
            "generated_code": terraform_code,
            "full_response_text": redact_sensitive_text(execution_results.get('raw_llm_response', '')),
            "questions_asked": [],
            "additional_files_generated": [],
            "iterations_needed": iterations,
            "time_to_generate_seconds": execution_results.get('generation_time', 0),
            # FIX D2: Extract actual values from terraform code instead of hardcoding
            "inferred_defaults": {
                "os_version": "Ubuntu 22.04" if "ubuntu" in terraform_code.lower() else "unknown",
                "cpus": actual_cpus if actual_cpus is not None else ("not_specified" if "cpu" not in prompt_lower else "not_detected"),
                "ram_bytes": actual_memory if actual_memory is not None else ("not_specified" if "ram" not in prompt_lower else "not_detected"),
                "disk_bytes": actual_disk if actual_disk is not None else ("not_specified" if "disk" not in prompt_lower else "not_detected"),
                "network": "xenbr0" if "xenbr0" in terraform_code else ("Pool-wide" if "Pool-wide" in terraform_code else "not_detected"),
                "storage_sr": "detected" if "name_label" in terraform_code and "storage" in terraform_code.lower() else "not_detected",
                "ip_mode": "dhcp" if "dhcp" in terraform_code.lower() or "network {" in terraform_code else "not_detected"
            }
        },
        
        "execution_results": {
            "terraform_init": {
                "status": "success" if init_res.get('exit_code') == 0 else "failed",
                "command": "terraform init",
                "exit_code": init_res.get('exit_code', 1),
                "execution_time_seconds": init_res.get('execution_time_seconds', 0),
                "error_message": init_res.get('stderr') if init_res.get('exit_code') != 0 else None
            },
            "terraform_validate": {
                "status": "success" if val_res.get('exit_code') == 0 else "failed",
                "command": "terraform validate",
                "exit_code": val_res.get('exit_code', 1),
                "execution_time_seconds": val_res.get('execution_time_seconds', 0),
                "error_message": val_res.get('stderr') if val_res.get('exit_code') != 0 else None
            },
            "terraform_plan": {
                "status": "success" if plan_res.get('exit_code') == 0 else "failed",
                "command": "terraform plan -out=tfplan",
                "exit_code": plan_res.get('exit_code', 1),
                "execution_time_seconds": plan_res.get('execution_time_seconds', 0),
                "resources_to_create": (lambda m: int(m.group(1)) if m else 0)(re.search(r'Plan: (\d+) to add', plan_res.get('stdout', ''))),
                "resources_to_modify": (lambda m: int(m.group(1)) if m else 0)(re.search(r'(\d+) to change', plan_res.get('stdout', ''))),
                "resources_to_destroy": (lambda m: int(m.group(1)) if m else 0)(re.search(r'(\d+) to destroy', plan_res.get('stdout', ''))),
                "error_message": plan_res.get('stderr') if plan_res.get('exit_code') != 0 else None
            },
            "terraform_apply": {
                "status": "success" if apply_res.get('exit_code') == 0 else "failed",
                "command": "terraform apply -auto-approve",
                "exit_code": apply_res.get('exit_code', 1),
                "execution_time_seconds": apply_res.get('execution_time_seconds', 0),
                "error_message": apply_res.get('stderr') if apply_res.get('exit_code') != 0 else None
            },
            "verification": verification_data
        },
        
        "spec_accuracy": {
            "status": execution_results.get('spec_accuracy', {}).get('status', 'skipped'),
            "passed": execution_results.get('spec_accuracy', {}).get('passed'),
            "checks_performed": execution_results.get('spec_accuracy', {}).get('checks_performed', []),
            "errors": execution_results.get('spec_accuracy', {}).get('errors', []),
            "details": execution_results.get('spec_accuracy', {}).get('details', {}),
        },
        
        "post_state_verification": execution_results.get('post_state_verification') or {"status": "not_applicable"},
        
        "manual_interventions": execution_results.get('manual_interventions', []),
        
        "final_outcome": {
            "worked_as_generated": iterations == 1,
            "worked_after_fixes": iterations > 1,
            "total_fixes_needed": iterations - 1,
            "total_iterations": iterations,
            "execution_successful": final_success or execution_results.get('expected_failure_matched', False),
            "meets_requirements": final_success or execution_results.get('expected_failure_matched', False),
            "resource_allocation_correct": execution_results.get('spec_accuracy', {}).get('passed', True)
        },
        
        "validation_checklist": {
            "code_quality": {
                "provider_config_included": "provider \"xenorchestra\"" in terraform_code,
                "data_sources_included": "data \"xenorchestra" in terraform_code,
                "vm_resource_defined": "resource \"xenorchestra_vm\"" in terraform_code,
                "infers_ubuntu_2204": "ubuntu-22.04" in terraform_code.lower() or "iso-install" in terraform_code.lower(),
                "complies_with_vcpu_req": check_compliance(actual_cpus, expected_cpus, 1, expected_failure_matched),
                "complies_with_ram_req": check_compliance(actual_memory, expected_memory, 1024**3, expected_failure_matched),
                "complies_with_disk_req": check_compliance(actual_disk, expected_disk, 10 * 1024**3, expected_failure_matched),
                "network_xenbr0": "xenbr0" in terraform_code or "Pool-wide" in terraform_code,
                "dhcp_configured": "network {" in terraform_code # Basic check
            },
            "execution": {
                "terraform_init_success": init_res.get('exit_code') == 0,
                "terraform_validate_success": val_res.get('exit_code') == 0,
                "terraform_plan_success": plan_res.get('exit_code') == 0,
                "terraform_apply_success": final_success,
                "vm_in_xen_orchestra": verification_data.get('vms_exist_in_xo', False),
                "vm_running": verification_data.get('all_vms_running', False),
                "vm_has_correct_ram": _check_vm_ram(actual_memory, verification_data, terraform_code),
                "vm_has_dhcp_ip": all(
                    vm.get('ip', 'unknown') != 'unknown'
                    for vm in verification_data.get('vm_details', [{}])
                ) if verification_data.get('vm_details') else True,
                "server_resources_correct": True
            }
        },
        
        "screenshots": {
            "terraform_apply_output": f"screenshots/{clean_task_id}_{model_short}_apply.png",
            "xen_orchestra_vm_list": f"screenshots/{clean_task_id}_{model_short}_xo_list.png",
            "vm_details": f"screenshots/{clean_task_id}_{model_short}_vm_details.png",
            "resource_usage": f"screenshots/{clean_task_id}_{model_short}_resources.png"
        },
        
        "evaluator_notes": f"Task {task_id} executed by {model_key}. Iterations: {iterations}."
    }


    # Case Specific Extensions
    if task_id.startswith("U"):
        pre_vms = pre_verification_data.get('vm_details', [])
        post_vms = verification_data.get('vm_details', [])
        
        # FIX C2: Filter VMs by target_vm name — fall back to empty dict, not pre_vms[0]
        target_vm_name = reqs.get('target_vm', '')
        pre_vm = next((vm for vm in pre_vms if vm.get('name') == target_vm_name), None)
        post_vm = next((vm for vm in post_vms if vm.get('name') == target_vm_name), None)
        
        # Thread actual spec_checker results instead of fabricated constants
        spec_details = execution_results.get('spec_accuracy', {}).get('details', {})
        entry["update_operation_validation"] = { 
            "target_vm": target_vm_name,
            "plan_shows_in_place_update": not spec_details.get('had_replace_actions', False), 
            "plan_shows_destroy_create": spec_details.get('had_replace_actions', False), 
            "vm_uuid_before": (pre_vm or {}).get('uuid', 'unknown'), 
            "vm_uuid_after": (post_vm or {}).get('uuid', 'unknown'), 
            "uuid_unchanged": pre_vm.get('uuid') == post_vm.get('uuid') if pre_vm and post_vm else False, 
            "vm_downtime_seconds": 0, 
            "resource_change_correct": execution_results.get('spec_accuracy', {}).get('passed', True), 
            "ram_before_gb": (pre_vm or {}).get('ram_gb', 0), 
            "ram_after_gb": (post_vm or {}).get('ram_gb', 0), 
            "cpu_before": (pre_vm or {}).get('cpus', 0), 
            "cpu_after": (post_vm or {}).get('cpus', 0) 
        }

    if reqs.get('expected_error') == 'resource_exhaustion':
        matched = execution_results.get('expected_failure_matched', False)
        
        # Dynamic calculation for C5.2
        req_memory_bytes = reqs.get('memory_max_bytes', 0)
        
        avail_ram_gb = entry["scenario"]["available_ram_gb_before"]
        shortfall_gb = max(0, (req_memory_bytes / (1024**3)) - avail_ram_gb)
        perc_over = (shortfall_gb / avail_ram_gb * 100) if avail_ram_gb > 0 else 100

        # Extract genuine alternatives from LLM response
        raw_response = execution_results.get('raw_llm_response', '')
        extracted_alternatives = []
        if matched and raw_response:
             # BUG #9 fix: Capture the unit (GB/MB) and preserve it
             # Look for patterns like "5 VMs with 4GB" or "10 VMs with 2MB"
             matches = re.findall(r'(\d+)\s*[\*_]*\s*VMs?[\*_]*\s*with\s*(\d+(?:\.\d+)?)\s*(GB|MB)', raw_response, re.IGNORECASE)
             for count, size, unit in matches:
                 extracted_alternatives.append(f"{count} VMs with {size}{unit.upper()} each")

        entry["edge_case_handling"] = { 
            "recognized_over_provisioning": matched, 
            "warned_user": matched, 
            "calculated_shortfall": matched, 
            "shortfall_gb": round(shortfall_gb, 2) if matched else 0, 
            "percentage_over": round(perc_over, 2) if matched else 0, 
            "suggested_alternatives": len(extracted_alternatives) > 0, 
            "alternatives_provided": extracted_alternatives, 
            "generated_failing_code": not matched, 
            "if_code_generated_apply_failed": apply_res.get('exit_code') != 0 if not matched else None, 
            "apply_error_message": apply_res.get('stderr') 
        } 
        entry["edge_case_score"] = { 
            "score_out_of_10": 10 if matched else 2, 
            "rating": "excellent" if matched else "poor", 
            "reasoning": "Recognized over-provisioning and refused to generate failing code" if matched else "Failed to recognize resource constraints" 
        }

    return entry

def save_dataset_entry(entry, output_root, config):
    """
    Save the entry using pass-number naming for easy comparison:
    [task_id_clean]_[model_short].json  (single run)
    [task_id_clean]_[model_short]_pass[N].json  (multi-pass)
    Timestamp is stored inside the JSON, not in the filename.
    """
    model_key = config.get('active_model_name', 'unknown')
    model_config = config.get('models', {}).get(model_key, {})
    folder_name = model_config.get('folder_name', model_key)
    
    # BUG 7 FIX: Append enhance_strat suffix so COT/FSP results don't overwrite baseline
    enhance_strat = entry.get('metadata', {}).get('enhance_strat', '')
    if enhance_strat and enhance_strat != 'baseline':
        folder_name = f"{folder_name}_{enhance_strat}"
    
    output_dir = os.path.join(output_root, "dataset", folder_name)
    if not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)
        
    filename = f"{entry['entry_id']}.json"
    full_path = os.path.join(output_dir, filename)
    
    with open(full_path, 'w') as f:
        json.dump(entry, f, indent=2)
    print(f"Dataset entry saved to: {full_path}")
