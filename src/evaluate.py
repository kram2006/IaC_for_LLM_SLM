import os
import sys
import logging
import argparse
import hashlib
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
    unload_ollama_model, GREEN, RED, CYAN, YELLOW, BOLD, RESET
)
from eval_core import evaluate_task
from models import GlobalConfig, ModelConfig

MAX_CHAIN_SLUG_LENGTH = 50
CHAIN_HASH_LENGTH = 16

def _validate_local_path(path_value, arg_name):
    normalized = os.path.normpath(path_value)
    path_parts = normalized.split(os.sep)
    if ".." in path_parts:
        raise ValueError(f"Invalid {arg_name} path: parent directory traversal is not allowed.")
    return normalized

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
        base_url = model_config.get('base_url') or expanded_config.get('openrouter', {}).get('base_url', "https://openrouter.ai/api/v1/chat/completions")
        return OpenRouterClient(
            api_key=api_key,
            model_name=model_config['name'],
            temperature=model_config.get('temperature', 0.2),
            max_tokens=model_config.get('max_tokens', 4096),
            base_url=base_url,
            timeout=300,
            seed=sample_seed
        )

    # Load Tasks
    tasks = []
    with open(args.dataset, 'r', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if args.task_id and row['task_id'].lower() != args.task_id.lower():
                continue
            tasks.append(row)

    if not tasks:
        print(f"{RED}No tasks found matching criteria.{RESET}")
        return

    # Filter for chain if requested
    if args.chain:
        chain_ids = [tid.strip().lower() for tid in args.chain.split(',')]
        tasks = [t for t in tasks if t['task_id'].lower() in chain_ids]
        tasks.sort(key=lambda x: chain_ids.index(x['task_id'].lower()))

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
        
        has_previous_run = None
        workspace_dir = None
        
        if args.chain:
            # Chained mode: Shared workspace for all tasks in this sample
            chain_ids = [t['task_id'].replace('.', '_') for t in tasks]
            chain_slug = "_".join(chain_ids)
            if len(chain_slug) > MAX_CHAIN_SLUG_LENGTH:
                chain_slug = hashlib.sha256(chain_slug.encode("utf-8")).hexdigest()[:CHAIN_HASH_LENGTH]
            workspace_dir = os.path.join(args.output_dir, "terraform_code", model_config['folder_name'], f"chain_{chain_slug}_p{pass_num}")
            os.makedirs(workspace_dir, exist_ok=True)
            
            for i, task_spec in enumerate(tasks):
                has_previous_run = await evaluate_task(
                    task=task_spec,
                    config=expanded_config,
                    client=client,
                    output_dir=args.output_dir,
                    workspace_override=workspace_dir,
                    initial_history=has_previous_run,
                    plan_only=args.plan_only,
                    sample_num=pass_num,
                    chain_index=i,
                    no_confirm=args.no_confirm,
                    enhance_strat=args.enhance_strat
                )
        else:
            # Standalone mode: Each task gets its own workspace path
            for task_spec in tasks:
                tid = task_spec['task_id'].replace('.', '_')
                sample_workspace = os.path.join(args.output_dir, "terraform_code", model_config['folder_name'], f"{tid}_p{pass_num}")
                os.makedirs(sample_workspace, exist_ok=True)
                
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
        
        unload_ollama_model(model_config)

    # Launch parallel samples
    print(f"\n{BOLD}{CYAN}>>> Launching {num_passes} parallel samples...{RESET}")
    sample_tasks = [run_sample(p) for p in range(pass_start, pass_start + num_passes)]
    results = await asyncio.gather(*sample_tasks, return_exceptions=True)
    exceptions = [result for result in results if isinstance(result, Exception)]
    if exceptions:
        raise exceptions[0]

    print(f"\n{BOLD}{GREEN}Evaluation Complete. All files saved to: {os.path.abspath(args.output_dir)}{RESET}")

if __name__ == "__main__":
    asyncio.run(main())
