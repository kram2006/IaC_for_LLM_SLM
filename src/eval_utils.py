import os
import sys
import time
import subprocess
import logging
import requests
import asyncio
import re
import copy
try:
    from logger import log_step, log_error
except ModuleNotFoundError:
    from src.logger import log_step, log_error

# ANSI Colors for terminal
GREEN = "\033[92m"
RED = "\033[91m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
MAGENTA = "\033[95m"
RESET = "\033[0m"
BOLD = "\033[1m"
SENSITIVE_FIELD_PATTERN = re.compile(
    r'(?i)\b(username|password|api[_-]?key|token)\b\s*([:=])\s*(".*?"|\'.*?\'|[^\s,\n}]+)'
)

def redact_sensitive_text(value):
    if not isinstance(value, str):
        return value
    return SENSITIVE_FIELD_PATTERN.sub(
        lambda m: f'{m.group(1)}{m.group(2)}"[REDACTED]"',
        value
    )

def redact_messages_for_logging(messages):
    redacted = copy.deepcopy(messages)
    for message in redacted:
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = redact_sensitive_text(content)
    return redacted

async def execute_command(command, cwd=None, timeout=None, print_output=True, env=None):
    """Run a shell command asynchronously and return output"""
    async def _kill_and_reap(proc):
        if proc.returncode is not None:
            return
        try:
            proc.kill()
        finally:
            try:
                await proc.wait()
            except Exception:
                pass

    try:
        if print_output:
            print(f"{BOLD}{CYAN}> Running: {command}{RESET}")
            
        start_time = time.time()
        
        run_env = os.environ.copy()
        if env:
            run_env.update(env)
            
        # Use asyncio for non-blocking execution
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=run_env
        )
        
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            stdout = stdout.decode() if stdout else ""
            stderr = stderr.decode() if stderr else ""
        except asyncio.TimeoutError:
            await _kill_and_reap(process)
            log_error(f"Command timed out after {timeout}s: {command}")
            return {"status": "timeout", "exit_code": -1, "stdout": "", "stderr": f"Timeout after {timeout}s", "execution_time_seconds": timeout or 0}
        except Exception as e:
            # Handle "I/O operation on closed pipe" or other pipe errors gracefully
            await _kill_and_reap(process)
            log_error(f"Pipe/Process error: {str(e)}")
            return {"status": "error", "exit_code": -1, "stdout": "", "stderr": str(e), "execution_time_seconds": 0}
        
        duration = time.time() - start_time
        
        return {
            "status": "success" if process.returncode == 0 else "failed",
            "exit_code": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "execution_time_seconds": duration
        }
    except Exception as e:
        log_error(f"Command execution error: {str(e)}")
        return {"status": "error", "exit_code": -1, "stdout": "", "stderr": str(e), "execution_time_seconds": 0}

def save_log(path, content):
    """Save content to log file"""
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
    except Exception as e:
        logging.error(f"Failed to save log to {path}: {e}")

async def execute_terraform_apply(workspace_dir, env=None):
    """Execute terraform apply with auto-approve"""
    # Use -no-color to keep logs clean
    cmd = "terraform apply -auto-approve -no-color"
    return await execute_command(cmd, cwd=workspace_dir, timeout=600, env=env)

def unload_ollama_model(model_config):
    """Unload Ollama model from VRAM by setting keep_alive to 0"""
    if not model_config:
        return
    
    # Check both the name field and base_url for Ollama indicators
    name = model_config.get('name', '').lower()
    base_url = model_config.get('base_url', '')
    
    is_ollama = 'ollama' in name or 'localhost:11434' in base_url
    if not is_ollama:
        return
        
    try:
        if 'localhost:11434' not in base_url:
            base_url = 'http://localhost:11434/v1'
        ollama_url = base_url.replace('/v1', '/api/generate').replace('/v1/chat/completions', '/api/generate')
        
        payload = {
            "model": model_config['name'],
            "keep_alive": 0
        }
        
        logging.debug(f"Unloading Ollama model: {model_config['name']}")
        requests.post(ollama_url, json=payload, timeout=5)
    except Exception as e:
        logging.warning(f"Failed to unload Ollama model: {e}")

def capture_screenshot(task_id, model_name, screenshot_type, screenshot_dir):
    """Legacy manual screenshot capture helper"""
    # This is currently a stub for future vision-based validation
    filename = f"{task_id}_{model_name}_{screenshot_type}_{int(time.time())}.png"
    filepath = os.path.join(screenshot_dir, filename)
    # real capture logic would go here
    return filepath

def extract_terraform_code(response_text):
    """
    Extract Terraform/HCL code from LLM response text.

    Strategy:
    1. Split on triple-backtick fences.  Every odd-indexed segment (1, 3, 5 …) is
       a code-block body; even-indexed segments are surrounding prose.
    2. Strip the optional language tag from the start of each block (hcl / terraform /
       tf / HCL / Terraform / TF — plus any Windows-style CR LF).  The tag is only
       stripped when it sits on its own line (i.e. immediately followed by a newline or
       end-of-string), so that `terraform {` at the top of a config is NOT eaten.
    3. Return the LAST non-empty code block.  LLMs responding to COT or FSP prompts
       often echo a worked example first and then write the actual answer; taking the
       last block avoids returning the example instead of the real code.
    4. Unclosed blocks (model output truncated by max_tokens) are handled naturally
       because the odd-index walk still yields the partial block content.
    5. If no fenced blocks are found, fall back to the full response only when it
       contains obvious HCL markers — prevents returning plain prose as Terraform code.
    """
    if not response_text:
        return ""

    # All language tags that LLMs commonly use for HCL / Terraform files.
    # Comparison is case-insensitive, so no need to list uppercase variants explicitly.
    KNOWN_LANG_TAGS = ("hcl", "terraform", "tf")

    if "```" in response_text:
        parts = response_text.split("```")
        # Odd indices are code-block bodies; even indices are surrounding text.
        code_blocks = []
        for idx in range(1, len(parts), 2):
            block = parts[idx]
            stripped_block = block.strip()
            # Strip the language tag when it is the first "word" on its own line.
            # Use case-insensitive check so hcl/HCL/Terraform/tf/TF etc. are all handled.
            block_lower = stripped_block.lower()
            for tag in KNOWN_LANG_TAGS:
                if block_lower.startswith(tag):
                    after_tag = stripped_block[len(tag):]
                    # Only treat as a language tag if followed by a newline (not by
                    # a character that makes it a keyword, e.g. `terraform {`).
                    if after_tag == "" or after_tag[0] in ("\n", "\r"):
                        stripped_block = after_tag.lstrip("\r\n")
                    break
            if stripped_block:
                code_blocks.append(stripped_block)

        if code_blocks:
            # Return the LAST block: for COT/FSP responses the real answer is last.
            return code_blocks[-1]

    # No fenced blocks — return the full response only when it looks like HCL.
    stripped = response_text.strip()
    terraform_markers = ('resource "', 'data "', 'provider "', 'terraform {', 'variable "', 'output "')
    return stripped if any(marker in stripped for marker in terraform_markers) else ""
