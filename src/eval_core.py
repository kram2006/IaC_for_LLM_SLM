import os
import sys
import time
import json
import logging
import asyncio
from datetime import datetime

from eval_utils import (
    execute_command, save_log, execute_terraform_apply, redact_sensitive_text, redact_messages_for_logging,
    capture_screenshot, GREEN, RED, CYAN, YELLOW, MAGENTA, RESET, BOLD
)
from logger import log_step, log_error
from json_generator import generate_dataset_entry, save_dataset_entry
from xo_client import XenOrchestraClient
from spec_checker import check_spec_accuracy, get_plan_json, verify_post_state
from prompt_templates import CoT_prompt, FSP_prompt, multi_turn_plan_error_prompt

# Allowed task categories expected by strategy validators and chain post-state checks.
VALID_TASK_CATEGORIES = {"CREATE", "READ", "UPDATE", "DELETE"}
# Keep only a bounded tail of retry errors to prevent unbounded in-memory context growth.
MAX_ERROR_HISTORY = 5
RESOURCE_EXHAUSTION_MARKERS = ('insufficient memory', 'out of memory', 'not enough memory')

async def evaluate_task(task, config, client, output_dir, workspace_override=None, initial_history=None, plan_only=False, sample_num=0, chain_index=0, no_confirm=False, enhance_strat=""):
    """
    Core evaluation logic for a single task and sample.
    Orchestrates LLM generation, Terraform execution, and state verification.
    """
    task_id = task['task_id'].lower().replace('.', '_') # Convert C1.2 to c1_2
    task_category = task.get('category', '').strip().upper()
    if task_category and task_category not in VALID_TASK_CATEGORIES:
        raise ValueError(f"Unsupported task category '{task.get('category')}' for task {task.get('task_id')}")
    model_name = config['active_model_name']
    model_config = config['models'][model_name]
    
    folder_name = model_config.get('folder_name', model_name)
    if enhance_strat:
        folder_name = f"{folder_name}_{enhance_strat}"
    
    # 1. Model Specific JSON directory
    json_dir = os.path.join(output_dir, "dataset", folder_name)
    os.makedirs(json_dir, exist_ok=True)
    
    # 2. Terraform Code Directory (Execution Context)
    if workspace_override:
        workspace_dir = workspace_override
        log_step(f"Using shared workspace: {workspace_dir}")
    else:
        workspace_dir = os.path.join(output_dir, "terraform_code", folder_name, task_id)
        os.makedirs(workspace_dir, exist_ok=True)

    # 3. Task Log Directory (Artifacts - Always unique to the current task)
    task_artifact_dir = os.path.join(output_dir, "terraform_code", folder_name, task_id)
    os.makedirs(task_artifact_dir, exist_ok=True)
    task_log_dir = os.path.join(task_artifact_dir, "history") 
    os.makedirs(task_log_dir, exist_ok=True)
    
    # 4. Global Screenshots directory
    screenshot_dir = os.path.join(output_dir, "screenshots")
    os.makedirs(screenshot_dir, exist_ok=True)

    # Chat History Setup - Support model-specific overrides with safe fallback
    system_prompt = model_config.get('system_prompt') or config.get('baseline_system_prompt') or config.get('system_prompt')
    if not system_prompt:
        log_error("No system prompt found! Model-specific, baseline_system_prompt, or system_prompt required.")
        system_prompt = "You are a Terraform infrastructure engineer. Generate valid HCL code."
    
    # Inject XO credentials/URL into system prompt
    xo_cfg = config.get('xenorchestra', {})
    url = xo_cfg.get('url', 'ws://localhost:8080/api/')
    url = url.removesuffix('/api/').removesuffix('/api')
    system_prompt = system_prompt.replace("{XO_URL}", url)
    
    # Pre-compute TF_VARs for terraform subprocesses
    tf_env = {
        'TF_VAR_xo_username': xo_cfg.get('username', ''),
        'TF_VAR_xo_password': xo_cfg.get('password', '')
    }
    
    if workspace_override:
        tfstate_path = os.path.join(workspace_dir, "terraform.tfstate")
        if os.path.exists(tfstate_path) and os.path.getsize(tfstate_path) > 10:
            try:
                with open(tfstate_path, "r", encoding="utf-8") as f:
                    tfstate_content = f.read()
                # FLAW FIX: Cap tfstate to 4000 chars to prevent system prompt bloat
                if len(tfstate_content) > 4000:
                    tfstate_content = tfstate_content[:4000] + "\n... [TRUNCATED - full state in terraform.tfstate]"
                system_prompt += f"\n\nExisting Infrastructure (CURRENT STATE from terraform.tfstate):\n```json\n{tfstate_content}\n```\n"
                log_step("Injected terraform.tfstate into system prompt to save context memory")
            except Exception as e:
                log_error(f"Failed to read tfstate for context: {e}")
        else:
            log_step("No terraform.tfstate found yet (first task or failed apply). Proceeding without state context.")
                
    user_prompt = task['prompt']
    if enhance_strat == "COT":
        user_prompt = CoT_prompt(user_prompt)
    elif enhance_strat == "FSP":
        user_prompt = FSP_prompt(user_prompt)
    else:
        user_prompt = "Here is the actual prompt: " + user_prompt

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    if initial_history is not None:
        log_step("Starting fresh history with tfstate context (Chained Task memory optimization)")
    else:
        log_step("Starting fresh conversation history")

    print(f"\n{BOLD}{MAGENTA}" + "!"*30 + " TASK DATA " + "!"*30 + f"{RESET}")
    print(f"{BOLD}Task ID:{RESET} {task['task_id']}")
    if workspace_override:
         print(f"{BOLD}Mode:{RESET}    CHAINED EXECUTION (Shared State)")
    print(f"{BOLD}User Prompt:{RESET}\n{task['prompt']}")
    print(f"{BOLD}{MAGENTA}" + "!"*71 + f"{RESET}\n")

    log_step(f"Starting Task: {task_id}")
    
    # --- Loop for Retries/Fixes ---
    iteration = 0
    success = False
    
    execution_results = {}
    manual_interventions = [] 
    terraform_code = ""
    response_content = ""
    generation_time = 0
    spec_accuracy_result = None
    spec_fail_count = 0
    
    MAX_CONTEXT_PAIRS = 2
    MAX_ITERATIONS = 10
    
    base_messages = messages.copy()
    error_history = []
    
    init_res = {"status": "skipped", "exit_code": -1, "stderr": "Not executed", "stdout": "", "execution_time_seconds": 0}
    val_res = {"status": "skipped", "exit_code": -1, "stderr": "Not executed", "stdout": "", "execution_time_seconds": 0}
    plan_res = {"status": "skipped", "exit_code": -1, "stderr": "Not executed", "stdout": "", "execution_time_seconds": 0}
    apply_res = {"status": "skipped", "exit_code": -1, "stderr": "Not executed", "stdout": "", "execution_time_seconds": 0}
    spec_res = {"status": "skipped", "passed": None, "errors": [], "checks_performed": []}
    
    xo_conf = config.get('xenorchestra', {})
    xo_client = XenOrchestraClient(
        xo_conf.get('url', "ws://localhost:8080/api/"), 
        xo_conf.get('username', "admin@admin.net"), 
        xo_conf.get('password', "admin")
    )
    if plan_only:
        pre_verification = {"actual_vm_count": 0, "vm_details": [], "note": "Skipped (plan-only mode)"}
    else:
        pre_verification = await xo_client.verify_vms()

    expected_error = None
    try:
        reqs = json.loads(task.get('resource_requirements', '{}'))
        expected_error = reqs.get('expected_error')
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    while True:
        iteration += 1
        if iteration > MAX_ITERATIONS:
            log_error(f"Max iterations ({MAX_ITERATIONS}) reached. Stopping.")
            print(f"\n{BOLD}{RED}MAX ITERATIONS REACHED ({MAX_ITERATIONS}). Giving up on this task.{RESET}")
            break
        
        log_step(f"Iteration {iteration}/{MAX_ITERATIONS}")
        
        if iteration > 1:
            tfstate_file = os.path.join(workspace_dir, "terraform.tfstate")
            if os.path.exists(tfstate_file) and os.path.getsize(tfstate_file) > 50:
                if chain_index == 0:
                    log_step("Cleaning workspace state before retry (terraform destroy)")
                    destroy_res = await execute_command("terraform destroy -auto-approve", cwd=workspace_dir, timeout=300, env=tf_env)
                    if destroy_res.get('exit_code') == 0:
                        print(f"{GREEN}Workspace cleaned successfully.{RESET}")
                    else:
                        print(f"{YELLOW}Warning: Destroy returned non-zero. Continuing anyway.{RESET}")
                else:
                    log_step("Chained task retry \u2014 preserving state from previous chain steps (no destroy)")

            # Multi-turn repair logic using research-backed semantic pattern (Stateless)
            last_error = error_history[-1] if error_history else "Unknown error"
            
            # Construct the single monolithic prompt with original task, failed code, and error
            repair_message = multi_turn_plan_error_prompt(task['prompt'], terraform_code, last_error)
            
            print(f"\n{BOLD}{YELLOW}--- RETRYING (Self-Correction Turn {iteration-1}) ---{RESET}")
            
            messages = []
            
            # Set system prompt to the multi-turn specific prompt
            multi_turn_sys = config.get('multi_turn_system_prompt')
            if multi_turn_sys:
                messages.append({"role": "system", "content": multi_turn_sys})
            elif len(base_messages) > 0 and base_messages[0].get('role') == 'system':
                messages.append(base_messages[0])
                
            # Send the stateless repair message
            messages.append({"role": "user", "content": repair_message})
        
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Waiting for LLM response...")
        gen_start = time.time()
        response_content = client.chat_completion(messages)
        gen_end = time.time()
        generation_time += gen_end - gen_start
            
        if not response_content:
            log_error("Failed to get response from LLM")
            break
            
        print(f"\n{BOLD}{CYAN}" + "="*30 + " FULL LLM RESPONSE " + "="*30 + f"{RESET}")
        print(response_content)
        print(f"{CYAN}" + "="*79 + f"{RESET}\n")
            
        messages.append({"role": "assistant", "content": response_content})
        terraform_code = client.extract_terraform_code(response_content)
        
        is_code_empty = not terraform_code.strip()
        if not is_code_empty and task.get('category') not in ['DELETE', 'READ']:
            has_resources = "resource \"" in terraform_code
            has_data_blocks = "data \"" in terraform_code
            if not has_resources and not has_data_blocks:
                is_code_empty = True
        
        if is_code_empty:
            log_error("Empty Terraform code (no resources) generated. Skipping execution.")
            if expected_error == 'resource_exhaustion':
                print(f"{GREEN}SUCCESS: LLM correctly refused to generate code for over-provisioning.{RESET}")
                success = True
                terraform_code = f"""
terraform {{
  required_providers {{
    xenorchestra = {{
      source  = "terra-farm/xenorchestra"
      version = "~> 0.26.0"
    }}
  }}
}}
provider "xenorchestra" {{
  url      = "{url}"
  username = "${{var.xo_username}}"
  password = "${{var.xo_password}}"
  insecure = true
}}
"""
                is_code_empty = False
            else:
                init_res["status"] = "failed"
                if iteration < MAX_ITERATIONS:
                    error_history.append("Your response contained no valid Terraform code. You must write a complete main.tf file with hcl syntax.")
                    error_history = error_history[-MAX_ERROR_HISTORY:]
                    continue
                else: break

        with open(os.path.join(workspace_dir, "main.tf"), "w") as f:
            f.write(terraform_code)
        
        redacted_response_content = redact_sensitive_text(response_content)
        save_log(os.path.join(task_log_dir, f"llm_response_iter{iteration}.txt"), redacted_response_content)
        save_log(os.path.join(task_log_dir, f"main_iter{iteration}.tf"), terraform_code)
        
        # Save iteration-specific history
        with open(os.path.join(task_log_dir, f"conversation_history_iter{iteration}.json"), "w", encoding='utf-8') as f:
            json.dump(redact_messages_for_logging(messages), f, indent=2)

        log_step("Running terraform init")
        init_res = await execute_command("terraform init", cwd=workspace_dir, env=tf_env)
        save_log(os.path.join(task_log_dir, f"init_iter{iteration}.log"), init_res.get('stdout', '') + init_res.get('stderr', ''))
        if init_res['exit_code'] != 0:
            error_history.append(f"Init failed:\n{init_res.get('stderr', '')}")
            error_history = error_history[-MAX_ERROR_HISTORY:]
            continue

        log_step("Running terraform validate")
        val_res = await execute_command("terraform validate", cwd=workspace_dir, env=tf_env)
        save_log(os.path.join(task_log_dir, f"validate_iter{iteration}.log"), val_res.get('stdout', '') + val_res.get('stderr', ''))
        if val_res['exit_code'] != 0:
            error_history.append(f"Validation failed:\n{val_res.get('stderr', '')}")
            error_history = error_history[-MAX_ERROR_HISTORY:]
            continue

        log_step("Running terraform plan")
        plan_res = await execute_command("terraform plan -out=tfplan", cwd=workspace_dir, env=tf_env)
        save_log(os.path.join(task_log_dir, f"plan_iter{iteration}.log"), plan_res.get('stdout', '') + plan_res.get('stderr', ''))
        
        if expected_error == 'resource_exhaustion':
             stderr_lower = plan_res.get('stderr', '').lower()
             if plan_res['exit_code'] != 0 and any(marker in stderr_lower for marker in RESOURCE_EXHAUSTION_MARKERS):
                 success = True
                 execution_results = {'outcome': 'success', 'details': 'Expected failure verified'}
                 break
        if plan_res['exit_code'] != 0:
            error_history.append(f"Plan failed:\n{plan_res.get('stderr', '')}")
            error_history = error_history[-MAX_ERROR_HISTORY:]
            continue

        log_step("Running Spec Accuracy Check")
        plan_json, plan_json_err = get_plan_json(workspace_dir)
        if plan_json is None:
            spec_res = {"status": "skipped", "passed": None, "errors": [plan_json_err], "checks_performed": []}
        else:
            is_chained = workspace_override and chain_index is not None and chain_index > 0
            pre_vms_for_spec = pre_verification.get('vm_details') if is_chained else None
            spec_accuracy_result = check_spec_accuracy(plan_json, task, pre_vms=pre_vms_for_spec)
            spec_res = {"status": "executed", "passed": spec_accuracy_result['passed'], "errors": spec_accuracy_result['errors'], "checks_performed": spec_accuracy_result['checks_performed']}
            save_log(os.path.join(task_log_dir, f"spec_check_iter{iteration}.json"), json.dumps(spec_res, indent=2))
            
            if not spec_accuracy_result['passed']:
                spec_fail_count += 1
                if spec_fail_count >= 2: break
                error_history.append("SPEC ACCURACY ERRORS:\n" + "\n".join(spec_accuracy_result['errors']))
                error_history = error_history[-MAX_ERROR_HISTORY:]
                continue

        if plan_only:
            spec_passed = True if spec_res.get('status') == 'skipped' else spec_res.get('passed', False)
            success = plan_res['exit_code'] == 0 and spec_passed is not False
            apply_res = {"status": "skipped_plan_only", "exit_code": 0 if success else -1, "stderr": "Skipped (plan-only)", "stdout": "", "execution_time_seconds": 0}
            execution_results = {'outcome': 'success' if success else 'failure', 'iterations': iteration}
            break

        log_step("Running terraform apply")
        apply_res = await execute_terraform_apply(workspace_dir, env=tf_env)
        save_log(os.path.join(task_log_dir, f"apply_iter{iteration}.log"), apply_res.get('stdout', '') + apply_res.get('stderr', ''))
        if apply_res['exit_code'] != 0:
            error_history.append(f"Apply failed:\n{apply_res.get('stderr', '')}")
            error_history = error_history[-MAX_ERROR_HISTORY:]
            continue
            
        success = True
        execution_results = {'outcome': 'success', 'iterations': iteration}
        break

    if not plan_only:
        post_verification = await xo_client.verify_vms()
    else:
        post_verification = {"actual_vm_count": 0, "vm_details": [], "note": "Skipped (plan-only mode)"}
    
    post_state_result = {'status': 'skipped', 'passed': None, 'errors': [], 'details': {'note': 'Not executed'}}
    if workspace_override and not plan_only and success:
        if task_category in ('UPDATE', 'DELETE'):
            post_state_result = verify_post_state(pre_verification.get('vm_details', []), post_verification.get('vm_details', []), task)
    
    full_execution_results = {
        'terraform_init': init_res, 'terraform_validate': val_res, 'terraform_plan': plan_res, 'terraform_apply': apply_res,
        'spec_accuracy': spec_res, 'post_state_verification': post_state_result, 'iterations': iteration,
        'generation_time': generation_time, 'sample_num': sample_num, 'expected_failure_matched': success and expected_error == 'resource_exhaustion',
        'raw_llm_response': redact_sensitive_text(response_content), 'enhance_strat': enhance_strat
    }

    entry = generate_dataset_entry(
        task_data=task, terraform_code=terraform_code, execution_results=full_execution_results,
        verification_data=post_verification, pre_verification_data=pre_verification, config=config
    )
    save_dataset_entry(entry, output_dir, config)
    return messages
