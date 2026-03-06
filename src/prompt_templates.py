"""
prompt_templates.py

Enhancement strategy prompts for IaC evaluation.
All XenOrchestra platform constants are injected via xo_config dict
(read from openrouter_config.yaml → xenorchestra: section).

CoT and FSP examples deliberately use tasks NOT in the test set
(build-01, db-node-01) to avoid data leakage.
"""


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _boilerplate():
    """
    Return the mandatory Terraform boilerplate block with real credentials.
    Used by both CoT and FSP examples so they stay in sync.
    Hardcoded to match the verified Ubuntu-22 pattern.
    """
    return """terraform {
  required_providers {
    xenorchestra = {
      source  = "terra-farm/xenorchestra"
      version = "~> 0.26.0"
    }
  }
}

provider "xenorchestra" {
  url      = "ws://localhost:8080/api"
  username = "admin@admin.net"
  password = "admin"
  insecure = true
}

data "xenorchestra_pool" "pool" {
  name_label = "DAO-Agentic-Infra"
}

data "xenorchestra_network" "net" {
  name_label = "Pool-wide network associated with eth0"
  pool_id    = data.xenorchestra_pool.pool.id
}

data "xenorchestra_sr" "sr" {
  name_label = "Local storage"
  pool_id    = data.xenorchestra_pool.pool.id
}

data "xenorchestra_template" "template" {
  name_label = "Ubuntu-22"
  pool_id    = data.xenorchestra_pool.pool.id
}"""


# ─────────────────────────────────────────────────────────────────────────────
# CHAIN-OF-THOUGHT PROMPT
# Based on IaC-Eval paper (NeurIPS 2024) CoT template (Appendix B.2)
# Examples use non-test tasks (build-01, db-node-01) to avoid data leakage.
# ─────────────────────────────────────────────────────────────────────────────

def CoT_prompt(question_prompt):
    """
    Wrap the user prompt with Chain-of-Thought instructions and 2 worked examples.

    Args:
        question_prompt: The raw task prompt from the CSV.
    """
    bp = _boilerplate()

    cot_header = (
        "Here are a few examples of how to reason about "
        "Xen Orchestra Terraform tasks:\n\n"
    )

    # ── Example 1: Simple VM (no explicit disk/CPU requirement) ──────────────
    example_1 = f"""Example prompt 1: Provision a build server named 'build-01' with 2GB RAM.

Example output 1: Let's think step by step.
First, identify the resources needed: one xenorchestra_vm resource, plus four data \
sources (pool, network, storage repository, template) to resolve their IDs.
Second, fill in the VM attributes from the prompt: name_label = "build-01", \
memory_max = 2147483648 (2 GB expressed in bytes). For attributes not explicitly \
stated, apply sensible defaults: cpus = 2, a 50 GB root disk, auto_poweron = true.
Third, connect resources together: template references \
data.xenorchestra_template.template.id, network_id references \
data.xenorchestra_network.net.id, sr_id references data.xenorchestra_sr.sr.id.
```hcl
{bp}

resource "xenorchestra_vm" "build_01" {{
  name_label   = "build-01"
  template     = data.xenorchestra_template.template.id
  cpus         = 2
  memory_max   = 2147483648
  auto_poweron = true

  network {{
    network_id = data.xenorchestra_network.net.id
  }}

  disk {{
    name_label = "build-01-disk"
    sr_id      = data.xenorchestra_sr.sr.id
    size       = 53687091200
  }}
}}
```
"""

    # ── Example 2: VM with explicit CPU and disk requirements ────────────────
    example_2 = f"""Example prompt 2: Create a database node 'db-node-01' with 4GB RAM, 4 CPUs, \
and a 100GB disk on the local storage repository.

Example output 2: Let's think step by step.
First, identify the resources needed: one xenorchestra_vm plus the same four \
data sources as always (pool, network, SR, template).
Second, fill in attributes from the prompt: name_label = "db-node-01", \
memory_max = 4294967296 (4 GB in bytes), cpus = 4. The disk size must be \
107374182400 bytes (100 GB * 1024^3).
Third, connect everything: template, network_id, and sr_id are all \
resolved from data source outputs — never hardcoded as raw strings.
```hcl
{bp}

resource "xenorchestra_vm" "db_node_01" {{
  name_label   = "db-node-01"
  template     = data.xenorchestra_template.template.id
  cpus         = 4
  memory_max   = 4294967296
  auto_poweron = true

  network {{
    network_id = data.xenorchestra_network.net.id
  }}

  disk {{
    name_label = "db-node-01-disk"
    sr_id      = data.xenorchestra_sr.sr.id
    size       = 107374182400
  }}
}}
```
"""

    footer = "\nHere is the actual prompt to answer. Let's think step by step:\n"
    return cot_header + example_1 + example_2 + footer + question_prompt


# ─────────────────────────────────────────────────────────────────────────────
# FEW-SHOT PROMPT
# Based on IaC-Eval paper (NeurIPS 2024) FSP template (Appendix B.1)
# Same non-test examples as CoT but without the reasoning text.
# ─────────────────────────────────────────────────────────────────────────────

def FSP_prompt(question_prompt):
    """
    Wrap the user prompt with 2 fully-worked few-shot HCL examples.

    Args:
        question_prompt: The raw task prompt from the CSV.
    """
    bp = _boilerplate()

    fsp_header = "Here are a few examples of correct Xen Orchestra Terraform configurations:\n\n"

    # ── Example 1: Simple VM ─────────────────────────────────────────────────
    example_1 = f"""Example prompt 1: Provision a build server named 'build-01' with 2GB RAM.

Example output 1:
```hcl
{bp}

resource "xenorchestra_vm" "build_01" {{
  name_label   = "build-01"
  template     = data.xenorchestra_template.template.id
  cpus         = 2
  memory_max   = 2147483648
  auto_poweron = true

  network {{
    network_id = data.xenorchestra_network.net.id
  }}

  disk {{
    name_label = "build-01-disk"
    sr_id      = data.xenorchestra_sr.sr.id
    size       = 53687091200
  }}
}}
```
"""

    # ── Example 2: VM with explicit CPU and disk ──────────────────────────────
    example_2 = f"""Example prompt 2: Create a database node 'db-node-01' with 4GB RAM, 4 CPUs, \
and a 100GB disk.

Example output 2:
```hcl
{bp}

resource "xenorchestra_vm" "db_node_01" {{
  name_label   = "db-node-01"
  template     = data.xenorchestra_template.template.id
  cpus         = 4
  memory_max   = 4294967296
  auto_poweron = true

  network {{
    network_id = data.xenorchestra_network.net.id
  }}

  disk {{
    name_label = "db-node-01-disk"
    sr_id      = data.xenorchestra_sr.sr.id
    size       = 107374182400
  }}
}}
```
"""

    footer = "\nHere is the actual prompt to answer:\n"
    return fsp_header + example_1 + example_2 + footer + question_prompt


# ─────────────────────────────────────────────────────────────────────────────
# REPAIR PROMPTS (Multi-turn)
# Based on IaC-Eval paper (NeurIPS 2024) Multi-turn approach.
# Used by eval_core.py when terraform init/validate/plan/apply fails.
# The paper is *stateless* — it re-sends the original prompt + code + error 
# as a single new user message, without retaining chat history.
# ─────────────────────────────────────────────────────────────────────────────

def multi_turn_plan_error_prompt(question_prompt, candidate_config, error_message):
    """
    Build the exact string format used by the IaC-Eval evaluation engine for multi-turn.
    Appends environment details so the AI generates correct Xen Orchestra IDs.
    """
    prompt = """
Here is the original prompt:
{}

Here is the incorrect configuration:
{}

Here is the Terraform plan error message (potentially empty):
{}

Requirements for the corrected program:
- Must be a complete standalone main.tf (provider block, all data sources, all resources).
- All resource IDs must come from data source outputs (never hardcode raw string IDs).
- Memory values must be in bytes as integer literals.

When writing the Terraform code, you must use the following Xen Orchestra environment details:
- Provider: url="ws://localhost:8080", username="admin@admin.net", password="admin", insecure=true.
- Data sources: Use name_label "DAO-Agentic-Infra" for the pool, "Pool-wide network associated with eth0" for the network, "Local storage" for the storage repository (sr), and "Ubuntu-22" for the OS template.
- All VMs must use the IDs resolved from these data sources.
""".format(question_prompt, candidate_config, error_message)
    return prompt
