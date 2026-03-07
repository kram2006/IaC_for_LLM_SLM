import os
import sys
import logging
import argparse
import hashlib
import re
import yaml
import csv
import asyncio
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Fix for Windows asyncio subprocesses
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# Add src to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from api_client import OpenRouterClient, LocalTransformersClient
from logger import setup_logger, log_step, log_error
from eval_utils import (
    unload_ollama_model, execute_command, GREEN, RED, CYAN, YELLOW, BOLD, RESET
)
from eval_core import evaluate_task
from models import GlobalConfig, ModelConfig

MAX_CHAIN_SLUG_LENGTH = 50
CHAIN_HASH_LENGTH = 16
PLACEHOLDER_PATTERN = re.compile(r'^\$\{[^}]+\}$')
DEFAULT_OPENROUTER_TIMEOUT = 300
DEFAULT_OPENROUTER_MAX_RETRIES = 3

def _validate_local_path(path_value, arg_name):
    normalized = os.path.normpath(path_value)
    path_parts = normalized.split(os.sep)
    if ".." in path_parts:
        raise ValueError(f"Invalid {arg_name} path: parent directory traversal is not allowed.")
    return normalized

def _is_unresolved_placeholder(value):
    return isinstance(value, str) and bool(PLACEHOLDER_PATTERN.match(value.strip()))

def _normalize_positive_int(value, fallback):
    try:
        parsed = int(value)
        return parsed if parsed > 0 else fallback
    except (TypeError, ValueError):
        return fallback

def load_config(config_path):
    import re
    # Custom loader to handle env vars
    pattern = re.compile(r'\$\{([^}^{]+)\}')
    
    # Use a custom Loader class to avoid global state pollution
    class EnvVarLoader(yaml.SafeLoader):
        pass
    
    def env_var_constructor(loader, node):
        value = loader.construct_scalar(node)
        match = pattern.match(value)
        if match:
            env_var = match.group(1)
            return os.environ.get(env_var, value)
        return value

    # Register the resolver only for this specific loader instance
    EnvVarLoader.add_implicit_resolver('!env', pattern, None)
    EnvVarLoader.add_constructor('!env', env_var_constructor)
    
    with open(config_path, 'r') as f:
        config = yaml.load(f, Loader=EnvVarLoader)
        
    def expand_env_vars(data):
        if isinstance(data, dict):
            return {k: expand_env_vars(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [expand_env_vars(v) for v in data]
        elif isinstance(data, str):
            res = pattern.search(data)
            if res:
                var_name = res.group(1)
                val = os.environ.get(var_name)
                if val:
                    return data.replace(res.group(0), val)
            return data
        return data
        
    expanded = expand_env_vars(config)
    
    # Validate with Pydantic
    try:
        GlobalConfig(**expanded)
        logging.info(f"Config {config_path} validated successfully.")
    except Exception as e:
        raise ValueError(f"Config validation failed for {config_path}: {e}") from e
        
    return expanded

async def main():
    parser = argparse.ArgumentParser(description="IaC Evaluation Framework")
    parser.add_argument("--config", default="config/openrouter_config.yaml", help="Path to config file")
    parser.add_argument("--output_dir", default="results", help="Directory to save results")
    parser.add_argument("--dataset", default="tasks/vm_provisioning_tasks.csv", help="Path to dataset")
    parser.add_argument("--model", default="phi4_openrouter", help="Model key from config")
    parser.add_argument("--task_id", help="Run specific task ID")
    parser.add_argument("--chain", help="Comma-separated list of task IDs to run as a chain (sharing state)")
    parser.add_argument("--samples", type=int, default=1, help="Number of independent samples per task for Pass@k (default=1)")
    parser.add_argument("--pass", type=int, default=None, dest="pass_num", help="Run as a specific pass number (1-indexed).")
    parser.add_argument("--plan-only", action="store_true", dest="plan_only", help="Skip terraform apply, evaluate based on Plan only")
    parser.add_argument("--no-confirm", action="store_true", dest="no_confirm", help="Skip manual authorization prompts")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for LLM calls")
    parser.add_argument("--enhance-strat", "-e", dest="enhance_strat", choices=["", "COT", "FSP"], default="", help="Prompt enhancement strategy")
  
    args = parser.parse_args()
    args.config = _validate_local_path(args.config, "--config")
    args.dataset = _validate_local_path(args.dataset, "--dataset")
    args.output_dir = _validate_local_path(args.output_dir, "--output_dir")

    if args.chain and args.plan_only:
        print(f"\n{RED}{BOLD}ERROR: --plan-only is incompatible with --chain.{RESET}")
        return
    
    setup_logger(args.output_dir)
    expanded_config = load_config(args.config)
    
    model_name = args.model
    if model_name not in expanded_config['models']:
        available = ", ".join(sorted(expanded_config.get('models', {}).keys()))
        print(f"{RED}Error: Model '{model_name}' not found in config. Available models: {available}{RESET}")
        return
    
    expanded_config['active_model_name'] = model_name
    model_config = expanded_config['models'][model_name]
    
    base_seed = args.seed if args.seed is not None else model_config.get('seed')

    def create_client(sample_seed=None):
        if model_config.get('local'):
            return LocalTransformersClient(
                model_name=model_config['name'],
                temperature=model_config.get('temperature', 0.2),
                max_tokens=model_config.get('max_tokens', 4096),
                seed=sample_seed
            )

        api_key = model_config.get('api_key') or os.environ.get('OPENROUTER_API_KEY') or expanded_config.get('openrouter', {}).get('api_key')
        if _is_unresolved_placeholder(api_key):
            raise ValueError(
                f"Unresolved API key placeholder for model '{model_name}': {api_key}. "
                "Set the referenced environment variable before running evaluation."
            )
        base_url = model_config.get('base_url') or expanded_config.get('openrouter', {}).get('base_url', "https://openrouter.ai/api/v1/chat/completions")
        openrouter_cfg = expanded_config.get('openrouter', {})
        timeout = _normalize_positive_int(
            model_config.get('timeout', openrouter_cfg.get('timeout', DEFAULT_OPENROUTER_TIMEOUT)),
            DEFAULT_OPENROUTER_TIMEOUT
        )
        max_retries = _normalize_positive_int(
            model_config.get('max_retries', openrouter_cfg.get('max_retries', DEFAULT_OPENROUTER_MAX_RETRIES)),
            DEFAULT_OPENROUTER_MAX_RETRIES
        )
        return OpenRouterClient(
            api_key=api_key,
            model_name=model_config['name'],
            temperature=model_config.get('temperature', 0.2),
            max_tokens=model_config.get('max_tokens', 4096),
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            seed=sample_seed
        )

    # Load Tasks
    dataset_tasks = []
    with open(args.dataset, 'r', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            dataset_tasks.append(row)

    tasks = dataset_tasks
    if args.task_id:
        if args.chain:
            print(f"{RED}Error: --task_id cannot be used together with --chain.{RESET}")
            return
        tasks = [row for row in dataset_tasks if row['task_id'].lower() == args.task_id.lower()]

    if not tasks:
        print(f"{RED}No tasks found matching criteria.{RESET}")
        return

    # Filter for chain if requested
    if args.chain:
        chain_ids = [tid.strip().lower() for tid in args.chain.split(',') if tid.strip()]
        if not chain_ids:
            print(f"{RED}Error: --chain must contain at least one task ID.{RESET}")
            return

        duplicates = sorted({tid for tid in chain_ids if chain_ids.count(tid) > 1})
        if duplicates:
            print(f"{RED}Error: Duplicate task IDs in --chain: {', '.join(duplicates)}{RESET}")
            return

        all_tasks_by_id = {row['task_id'].lower(): row for row in dataset_tasks}
        missing = [tid for tid in chain_ids if tid not in all_tasks_by_id]
        if missing:
            available = ", ".join(sorted(all_tasks_by_id.keys()))
            print(
                f"{RED}Error: Unknown task IDs in --chain: {', '.join(missing)}. "
                f"Available task IDs: {available}{RESET}"
            )
            return

        tasks = [all_tasks_by_id[tid] for tid in chain_ids]

    # Pass@k Loop
    num_passes = args.samples
    pass_start = 0
    if args.pass_num is not None:
        num_passes = 1
        pass_start = args.pass_num - 1
    
    # --- Parallel Execution Logic ---
    async def run_sample(pass_idx):
        """Run a single Pass@k sample (standalone or chain)."""
        pass_num = pass_idx + 1
        sample_seed = (base_seed + pass_idx) if base_seed is not None else None
        client = create_client(sample_seed)
        log_step(f"Starting Pass {pass_num}")
        cleanup_workspaces = []
        xo_cfg = expanded_config.get('xenorchestra', {})
        tf_env = {
            'TF_VAR_xo_username': xo_cfg.get('username') or os.environ.get('XO_USERNAME', ''),
            'TF_VAR_xo_password': xo_cfg.get('password') or os.environ.get('XO_PASSWORD', '')
        }
        
        has_previous_run = None
        workspace_dir = None
        base_folder_name = model_config.get('folder_name', model_name)
        effective_folder_name = f"{base_folder_name}_{args.enhance_strat}" if args.enhance_strat else base_folder_name
        
        if args.chain:
            # Chained mode: Shared workspace for all tasks in this sample
            chain_ids = [t['task_id'].replace('.', '_') for t in tasks]
            chain_slug = "_".join(chain_ids)
            if len(chain_slug) > MAX_CHAIN_SLUG_LENGTH:
                chain_slug = hashlib.sha256(chain_slug.encode("utf-8")).hexdigest()[:CHAIN_HASH_LENGTH]
            workspace_dir = os.path.join(
                args.output_dir,
                "terraform_code",
                effective_folder_name,
                f"chain_{chain_slug}_p{pass_num}"
            )
            os.makedirs(workspace_dir, exist_ok=True)
            cleanup_workspaces.append(workspace_dir)
            
            for i, task_spec in enumerate(tasks):
                task_category = task_spec.get('category', '').strip().upper()
                task_plan_only = args.plan_only or (task_category == 'READ')
                task_workspace = workspace_dir
                if task_category == 'READ':
                    task_workspace = os.path.join(
                        args.output_dir,
                        "terraform_code",
                        effective_folder_name,
                        f"chain_{chain_slug}_read_{task_spec['task_id'].replace('.', '_')}_p{pass_num}"
                    )
                    os.makedirs(task_workspace, exist_ok=True)
                    cleanup_workspaces.append(task_workspace)

                has_previous_run = await evaluate_task(
                    task=task_spec,
                    config=expanded_config,
                    client=client,
                    output_dir=args.output_dir,
                    workspace_override=task_workspace,
                    initial_history=has_previous_run,
                    plan_only=task_plan_only,
                    sample_num=pass_num,
                    chain_index=i,
                    no_confirm=args.no_confirm,
                    enhance_strat=args.enhance_strat
                )
        else:
            # Standalone mode: Each task gets its own workspace path
            for task_spec in tasks:
                tid = task_spec['task_id'].replace('.', '_')
                sample_workspace = os.path.join(
                    args.output_dir,
                    "terraform_code",
                    effective_folder_name,
                    f"{tid}_p{pass_num}"
                )
                os.makedirs(sample_workspace, exist_ok=True)
                cleanup_workspaces.append(sample_workspace)
                
                await evaluate_task(
                    task=task_spec,
                    config=expanded_config,
                    client=client,
                    output_dir=args.output_dir,
                    workspace_override=sample_workspace,
                    sample_num=pass_num,
                    plan_only=args.plan_only,
                    no_confirm=args.no_confirm,
                    enhance_strat=args.enhance_strat
                )

        should_cleanup = not args.plan_only and args.samples > 1
        if should_cleanup:
            for cleanup_workspace in cleanup_workspaces:
                tfstate_path = os.path.join(cleanup_workspace, "terraform.tfstate")
                if not os.path.exists(tfstate_path):
                    continue
                destroy_res = await execute_command(
                    "terraform destroy -auto-approve -no-color",
                    cwd=cleanup_workspace,
                    timeout=300,
                    env=tf_env
                )
                if destroy_res.get('exit_code') != 0:
                    log_error(f"Post-sample cleanup failed for {cleanup_workspace}: {destroy_res.get('stderr', '')}")
        
        unload_ollama_model(model_config)

    dataset_lock_dir = os.path.join(args.output_dir, "dataset", model_config.get("folder_name", model_name))
    os.makedirs(dataset_lock_dir, exist_ok=True)
    lockfile_path = os.path.join(dataset_lock_dir, ".evaluation_in_progress")
    if os.path.exists(lockfile_path):
        print(
            f"{RED}ERROR: Evaluation already in progress for this model output folder "
            f"({lockfile_path}). Remove the lockfile if no evaluation is running.{RESET}"
        )
        return

    try:
        with open(lockfile_path, "w", encoding="utf-8") as lock_file:
            lock_file.write(f"model={model_name}\n")

        if not args.plan_only and num_passes > 1:
            print(f"\n{BOLD}{CYAN}>>> Running {num_passes} samples sequentially for apply-mode isolation...{RESET}")
            for p in range(pass_start, pass_start + num_passes):
                await run_sample(p)
        else:
            print(f"\n{BOLD}{CYAN}>>> Launching {num_passes} parallel samples...{RESET}")
            sample_tasks = [run_sample(p) for p in range(pass_start, pass_start + num_passes)]
            results = await asyncio.gather(*sample_tasks, return_exceptions=True)
            exceptions = [result for result in results if isinstance(result, Exception)]
            if exceptions:
                raise exceptions[0]
    finally:
        if os.path.exists(lockfile_path):
            os.remove(lockfile_path)

    print(f"\n{BOLD}{GREEN}Evaluation Complete. All files saved to: {os.path.abspath(args.output_dir)}{RESET}")

if __name__ == "__main__":
    asyncio.run(main())
