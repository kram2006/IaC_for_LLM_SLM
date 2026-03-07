import os
import sys
import tempfile
import subprocess
import csv
import re
import asyncio
import shlex
from pathlib import Path

import pytest


SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from eval_utils import extract_terraform_code
from eval_utils import execute_command
from eval_utils import redact_sensitive_text as redact_eval_sensitive_text, redact_messages_for_logging
from spec_checker import DeleteValidation
from spec_checker import CreateValidation, ReadValidation, UpdateValidation
from compute_metrics import compute_metrics_for_folder, calculate_pass_at_k
from evaluate import (
    _validate_local_path,
    _next_chain_index_after_result,
    _order_fixed_benchmark_tasks,
    _preserve_tfstate_snapshot,
    _copy_chain_tfstate,
    load_config,
)
from eval_core import _extract_infra_context_from_tfstate, _resolve_tfstate_context_path, _verify_vms_with_retry
from spec_checker import get_plan_json, _extract_vm_resources
from json_generator import redact_sensitive_text as redact_json_sensitive_text, check_compliance
from json_generator import generate_dataset_entry
from llm_judge import parse_verdict
from populate_references import populate
from complexity_scorer import score_dataset


def test_extract_terraform_code_keeps_non_empty_when_language_line_has_no_newline():
    # A code fence with only a language tag and no body produces no usable code.
    # The function should return empty string rather than the tag word itself.
    assert extract_terraform_code("```hcl```") == ""


def test_execute_command_timeout_returns_timeout_status():
    result = asyncio.run(
        execute_command(
            f"{shlex.quote(sys.executable)} -c \"import time; time.sleep(0.2)\"",
            timeout=0.01,
            print_output=False
        )
    )
    assert result["status"] == "timeout"
    assert result["exit_code"] == -1


def test_extract_terraform_code_parses_hcl_block_with_language_tag():
    response = "```hcl\nresource \"x\" \"y\" {}\n```"
    assert extract_terraform_code(response) == 'resource "x" "y" {}'


def test_delete_validation_checks_target_vm_names():
    validator = DeleteValidation()
    vm_resources = [
        {"action": "delete", "name_label": "web-01"},
        {"action": "delete", "name_label": "web-02"},
    ]
    specs = {"delete_count": 2, "target_vms": ["web-02", "web-03"]}

    errors, checks, _ = validator.validate(vm_resources, specs)

    assert "correct_vms_targeted" in checks
    assert any("Target VM 'web-03'" in e for e in errors)
    assert any("Extra VMs deleted" in e for e in errors)


def test_compute_metrics_exits_when_evaluation_lockfile_exists(capsys):
    with tempfile.TemporaryDirectory() as tmpdir:
        open(os.path.join(tmpdir, ".evaluation_in_progress"), "w").close()
        csv_path = os.path.join(tmpdir, "tasks.csv")
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("task_id,reference_hcl\n")

        result = compute_metrics_for_folder(tmpdir, csv_path)

        out = capsys.readouterr().out
        assert result is None
        assert "Evaluation still running" in out


def test_calculate_pass_at_k_handles_high_success_edge_case():
    assert calculate_pass_at_k(5, 4, 3) == 1.0


def test_extract_terraform_code_returns_empty_for_non_terraform_text():
    assert extract_terraform_code("Here is an explanation with no code.") == ""


def test_delete_validation_rejects_unexpected_create_actions():
    validator = DeleteValidation()
    vm_resources = [
        {"action": "delete", "name_label": "web-01"},
        {"action": "create", "name_label": "web-new"},
    ]
    specs = {"delete_count": 1, "target_vm": "web-01"}

    errors, _, _ = validator.validate(vm_resources, specs)

    assert any("should not create/update/replace" in e for e in errors)


def test_get_plan_json_reports_timeout(monkeypatch):
    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="terraform", timeout=60)

    monkeypatch.setattr("spec_checker.subprocess.run", _raise_timeout)
    plan, err = get_plan_json(".")
    assert plan is None
    assert "timed out" in err.lower()


def test_validate_local_path_blocks_traversal():
    with pytest.raises(ValueError):
        _validate_local_path("../config.yaml", "--config")


def test_load_config_raises_for_invalid_config():
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write("{}")
        temp_path = f.name
    try:
        with pytest.raises(ValueError):
            load_config(temp_path)
    finally:
        os.remove(temp_path)


def test_redact_sensitive_text_masks_credentials_and_tokens():
    # Intentionally mixes "=" and ":" assignment styles because logs include both Python/HCL/YAML patterns.
    raw = 'provider "xenorchestra" { username = "admin@admin.net" password = "supersecret" api_key: sk-abc token=xyz }'
    redacted = redact_eval_sensitive_text(raw)
    assert 'admin@admin.net' not in redacted
    assert 'supersecret' not in redacted
    assert 'sk-abc' not in redacted
    assert 'xyz' not in redacted
    assert 'token="[REDACTED]"' in redacted
    assert redacted.count('[REDACTED]') == 4


def test_redact_messages_for_logging_masks_message_content():
    messages = [
        {"role": "system", "content": 'username="admin" password="pw"'},
        {"role": "user", "content": "Generate terraform"},
    ]
    redacted = redact_messages_for_logging(messages)
    assert messages[0]["content"] != redacted[0]["content"]
    assert "admin" in messages[0]["content"]
    assert "admin" not in redacted[0]["content"]
    assert "Generate terraform" == redacted[1]["content"]


def test_json_generator_redacts_system_prompt_text():
    raw = 'Provider username: admin@admin.net password: admin'
    redacted = redact_json_sensitive_text(raw)
    assert 'admin@admin.net' not in redacted
    assert 'password: admin' not in redacted


def test_parse_verdict_does_not_misclassify_incorrect_suffix():
    assert parse_verdict("Final assessment: incorrect") == "Incorrect"

def test_llm_judge_cli_help_runs_standalone():
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    result = subprocess.run(
        [sys.executable, "llm_judge.py", "--help"],
        cwd=repo_root,
        capture_output=True,
        text=True
    )
    assert result.returncode == 0
    assert "LLM-as-Judge" in result.stdout

def test_extract_vm_resources_uses_before_name_for_delete_actions():
    plan_json = {
        "resource_changes": [
            {
                "type": "xenorchestra_vm",
                "address": "xenorchestra_vm.vm",
                "change": {
                    "actions": ["delete"],
                    "before": {"name_label": "legacy-vm"},
                    "after": None,
                },
            }
        ]
    }
    resources = _extract_vm_resources(plan_json)
    assert resources[0]["action"] == "delete"
    assert resources[0]["name_label"] == "legacy-vm"

def test_dataset_csv_schema_integrity():
    csv_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tasks", "vm_provisioning_tasks.csv"))
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert rows, "Dataset must contain rows"
    for row in rows:
        assert None not in row.keys()
        assert re.fullmatch(r"[CRUD]\d\.\d", row["task_id"])
        assert (row.get("reference_hcl") or "").strip()

def test_compute_metrics_shows_na_for_unavailable_k(capsys):
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = os.path.join(tmpdir, "tasks.csv")
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("task_id,reference_hcl\nC1.1,resource \"x\" \"y\" {}\n")

        result_path = os.path.join(tmpdir, "sample.json")
        with open(result_path, "w", encoding="utf-8") as f:
            f.write(
                '{"task_id":"C1.1","llm_response":{"generated_code":"resource \\"x\\" \\"y\\" {}","time_to_generate_seconds":0},'
                '"final_outcome":{"execution_successful":true,"total_iterations":1},"spec_accuracy":{"passed":true}}'
            )

        compute_metrics_for_folder(tmpdir, csv_path)
        out = capsys.readouterr().out
        assert "Pass@3 (Plan):      N/A" in out
        assert "Pass@3 (Apply):     N/A" in out
        assert "Pass@5 (Spec):      N/A" in out

def test_populate_references_tolerates_legacy_overflow_columns(tmp_path):
    csv_path = tmp_path / "tasks.csv"
    refs_dir = tmp_path / "refs"
    refs_dir.mkdir()
    (refs_dir / "C1.1.tf").write_text('resource "xenorchestra_vm" "vm" {}', encoding="utf-8")
    csv_path.write_text(
        "task_id,reference_hcl,complexity_level\n"
        "C1.1,,3,EXTRA\n",
        encoding="utf-8"
    )
    populate(str(csv_path), str(refs_dir))
    with open(csv_path, newline="", encoding="utf-8") as f:
        row = next(csv.DictReader(f))
    assert row["reference_hcl"].strip()

def test_complexity_scorer_tolerates_legacy_overflow_columns(tmp_path):
    csv_path = tmp_path / "tasks.csv"
    csv_path.write_text(
        "task_id,reference_hcl,complexity_loc,complexity_resources,complexity_interconnections,complexity_level\n"
        "C1.1,\"resource \\\"xenorchestra_vm\\\" \\\"vm\\\" {}\",0,0,0,0,EXTRA\n",
        encoding="utf-8"
    )
    score_dataset(str(csv_path))
    with open(csv_path, newline="", encoding="utf-8") as f:
        row = next(csv.DictReader(f))
    assert row["complexity_level"] in {"1", "2", "3", "4", "5", "6"}


def test_check_compliance_handles_zero_expected_value():
    assert check_compliance(actual=0, expected=0, default_min=1) is True
    assert check_compliance(actual=1, expected=0, default_min=1) is False


def test_create_validation_supports_min_max_vm_count():
    validator = CreateValidation()
    vm_resources = [{"action": "create"}, {"action": "create"}, {"action": "create"}]
    specs = {"min_vm_count": 2, "max_vm_count": 4}
    errors, checks, _ = validator.validate(vm_resources, specs)
    assert errors == []
    assert "min_vm_count" in checks
    assert "max_vm_count" in checks


def test_update_validation_rejects_create_delete_replace():
    validator = UpdateValidation()
    vm_resources = [{"action": "update", "memory_max": 10}, {"action": "replace"}]
    specs = {"updated_field": "memory_max", "new_value": 10}
    errors, _, details = validator.validate(vm_resources, specs)
    assert any("should not create/delete/replace" in e for e in errors)
    assert details.get("had_replace_actions") is True

def test_update_validation_returns_early_when_forbidden_actions_present():
    validator = UpdateValidation()
    vm_resources = [{"action": "replace"}]
    specs = {"updated_field": "cpus", "new_value": 2}
    errors, checks, _ = validator.validate(vm_resources, specs)
    assert any("should not create/delete/replace" in e for e in errors)
    assert all("No update actions found" not in e for e in errors)
    assert checks == ["action_type_only_update"]


def test_update_validation_handles_zero_new_value():
    validator = UpdateValidation()
    vm_resources = [{"action": "update", "cpus": 0}]
    specs = {"updated_field": "cpus", "new_value": 0}
    errors, checks, _ = validator.validate(vm_resources, specs)
    assert errors == []
    assert "cpus_update" in checks


def test_read_validation_detects_non_vm_resource_changes():
    validator = ReadValidation()
    changes = [{"action": "update", "address": "xenorchestra_network.main"}]
    errors, checks, _ = validator.validate(changes, {})
    assert "no_resource_changes" in checks
    assert any("must not modify infrastructure" in e for e in errors)

def test_verify_vms_with_retry_retries_and_returns_last_result():
    class _StubXOClient:
        def __init__(self):
            self.calls = 0
            self.force_refresh_values = []
        async def verify_vms(self, force_refresh=False):
            self.calls += 1
            self.force_refresh_values.append(force_refresh)
            return {"actual_vm_count": self.calls, "force_refresh": force_refresh}

    xo_client = _StubXOClient()
    result = asyncio.run(_verify_vms_with_retry(xo_client, attempts=3, delay_seconds=0))
    assert xo_client.calls == 3
    assert xo_client.force_refresh_values == [True, True, True]
    assert result["actual_vm_count"] == 3
    assert result["force_refresh"] is True

def test_verify_vms_with_retry_handles_non_positive_attempts_and_negative_delay():
    class _StubXOClient:
        def __init__(self):
            self.calls = 0
        async def verify_vms(self, force_refresh=False):
            self.calls += 1
            return {"actual_vm_count": self.calls}

    xo_client = _StubXOClient()
    result = asyncio.run(_verify_vms_with_retry(xo_client, attempts=0, delay_seconds=-5))
    assert xo_client.calls == 1
    assert result["actual_vm_count"] == 1


def test_delete_validation_enforces_zero_delete_count():
    validator = DeleteValidation()
    vm_resources = [{"action": "delete", "name_label": "unexpected-vm"}]
    specs = {"delete_count": 0}
    errors, _, _ = validator.validate(vm_resources, specs)
    assert any("Expected 0 deletions" in e for e in errors)


def test_generate_dataset_entry_marks_plan_only_apply_as_skipped():
    task = {
        "task_id": "C2.3",
        "category": "CREATE",
        "prompt_type": "detailed",
        "prompt": "Create two VMs",
        "resource_requirements": '{"count": 2, "total_memory_max_bytes": 8589934592, "total_cpus": 4, "total_size_bytes": 21474836480}'
    }
    execution_results = {
        "terraform_init": {"exit_code": 0, "execution_time_seconds": 0, "stderr": ""},
        "terraform_validate": {"exit_code": 0, "execution_time_seconds": 0, "stderr": ""},
        "terraform_plan": {"exit_code": 0, "execution_time_seconds": 0, "stdout": "Plan: 2 to add", "stderr": ""},
        "terraform_apply": {"status": "skipped_plan_only", "exit_code": 0, "execution_time_seconds": 0, "stderr": "Skipped (plan-only)"},
        "spec_accuracy": {"status": "executed", "passed": True, "errors": [], "checks_performed": []},
        "iterations": 1,
        "generation_time": 0,
        "sample_num": 1,
        "raw_llm_response": "",
        "enhance_strat": ""
    }
    config = {
        "active_model_name": "m",
        "models": {"m": {"id_prefix": "m", "display_name": "Model", "name": "model"}}
    }
    entry = generate_dataset_entry(
        task_data=task,
        terraform_code='resource "xenorchestra_vm" "a" { memory_max = 4294967296 cpus = 2 size = 10737418240 }\n'
                       'resource "xenorchestra_vm" "b" { memory_max = 4294967296 cpus = 2 size = 10737418240 }',
        execution_results=execution_results,
        verification_data={},
        pre_verification_data={},
        config=config
    )
    assert entry["execution_results"]["terraform_apply"]["status"] == "skipped_plan_only"
    assert entry["validation_checklist"]["execution"]["terraform_apply_success"] is False
    assert entry["resource_expectations"]["expected"]["per_vm_memory_max_bytes"] == 4294967296
    assert entry["resource_expectations"]["expected"]["per_vm_cpus"] == 2
    assert entry["final_outcome"]["plan_success"] is True
    assert entry["final_outcome"]["apply_success"] is False


def test_generate_dataset_entry_fails_requirements_when_post_state_fails():
    task = {
        "task_id": "U1.2",
        "category": "UPDATE",
        "prompt_type": "detailed",
        "prompt": "Increase RAM",
        "resource_requirements": '{"count": 1, "target_vm": "app-01"}'
    }
    execution_results = {
        "terraform_init": {"exit_code": 0, "execution_time_seconds": 0, "stderr": ""},
        "terraform_validate": {"exit_code": 0, "execution_time_seconds": 0, "stderr": ""},
        "terraform_plan": {"exit_code": 0, "execution_time_seconds": 0, "stdout": "Plan: 0 to add, 1 to change", "stderr": ""},
        "terraform_apply": {"status": "success", "exit_code": 0, "execution_time_seconds": 0, "stderr": ""},
        "spec_accuracy": {"status": "executed", "passed": True, "errors": [], "checks_performed": []},
        "post_state_verification": {"status": "executed", "passed": False, "errors": ["vm mismatch"], "details": {}},
        "iterations": 1,
        "generation_time": 0,
        "sample_num": 1,
        "raw_llm_response": "",
        "enhance_strat": ""
    }
    config = {
        "active_model_name": "m",
        "models": {"m": {"id_prefix": "m", "display_name": "Model", "name": "model"}}
    }
    entry = generate_dataset_entry(
        task_data=task,
        terraform_code='resource "xenorchestra_vm" "a" { memory_max = 6442450944 cpus = 2 size = 10737418240 name_label = "app-01" }',
        execution_results=execution_results,
        verification_data={},
        pre_verification_data={"vm_details": []},
        config=config
    )
    assert entry["final_outcome"]["execution_successful"] is True
    assert entry["final_outcome"]["meets_requirements"] is False


def test_generate_dataset_entry_separates_execution_from_spec_validation():
    task = {
        "task_id": "C1.1",
        "category": "CREATE",
        "prompt_type": "detailed",
        "prompt": "Create one VM",
        "resource_requirements": '{"count": 1}'
    }
    execution_results = {
        "terraform_init": {"exit_code": 0, "execution_time_seconds": 0, "stderr": ""},
        "terraform_validate": {"exit_code": 0, "execution_time_seconds": 0, "stderr": ""},
        "terraform_plan": {"exit_code": 0, "execution_time_seconds": 0, "stdout": "Plan: 1 to add", "stderr": ""},
        "terraform_apply": {"status": "success", "exit_code": 0, "execution_time_seconds": 0, "stderr": ""},
        "spec_accuracy": {"status": "skipped", "passed": None, "errors": ["terraform show failed"], "checks_performed": []},
        "iterations": 1,
        "generation_time": 0,
        "sample_num": 1,
        "raw_llm_response": "",
        "enhance_strat": ""
    }
    config = {
        "active_model_name": "m",
        "models": {"m": {"id_prefix": "m", "display_name": "Model", "name": "model"}}
    }
    entry = generate_dataset_entry(
        task_data=task,
        terraform_code='resource "xenorchestra_vm" "a" { memory_max = 2147483648 cpus = 2 size = 10737418240 }',
        execution_results=execution_results,
        verification_data={},
        pre_verification_data={},
        config=config
    )
    assert entry["final_outcome"]["plan_success"] is True
    assert entry["final_outcome"]["apply_success"] is True
    assert entry["final_outcome"]["execution_successful"] is True
    assert entry["final_outcome"]["meets_requirements"] is False


def test_create_validation_enforces_vm_name_and_cpu_limits():
    validator = CreateValidation()
    vm_resources = [
        {"action": "create", "name_label": "web-01", "cpus": 20, "memory_max": 2147483648, "disk_sizes": [1]},
        {"action": "create", "name_label": "web-03", "cpus": 20, "memory_max": 2147483648, "disk_sizes": [1]},
    ]
    specs = {"vm_names": ["web-01", "web-02"], "max_total_cpus": 32}
    errors, checks, _ = validator.validate(vm_resources, specs)
    assert "vm_names" in checks
    assert "total_cpu_limit" in checks
    assert any("Missing expected VM names" in e for e in errors)
    assert any("Unexpected VM names created" in e for e in errors)
    assert any("Total CPUs" in e for e in errors)


def test_update_validation_enforces_target_vm():
    validator = UpdateValidation()
    vm_resources = [{"action": "update", "memory_max": 1, "name_label": "not-target"}]
    specs = {"updated_field": "memory_max", "new_value": 1, "target_vm": "app-01"}
    errors, checks, _ = validator.validate(vm_resources, specs)
    assert "target_vm" in checks
    assert any("Expected target VM 'app-01'" in e for e in errors)


def test_evaluate_chain_rejects_unknown_task_ids():
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    result = subprocess.run(
        [sys.executable, "src/evaluate.py", "--model", "phi4_ollama", "--chain", "C1.3,UNKNOWN"],
        cwd=repo_root,
        capture_output=True,
        text=True
    )
    assert result.returncode == 0
    assert "Unknown task IDs in --chain" in result.stdout


def test_next_chain_index_after_result_respects_cleanup_progression():
    chain_tasks = [
        {"task_id": "C1.3", "category": "CREATE"},
        {"task_id": "U1.2", "category": "UPDATE"},
        {"task_id": "D1.2", "category": "DELETE"},
    ]
    assert _next_chain_index_after_result(chain_tasks, 0, True) == 1
    assert _next_chain_index_after_result(chain_tasks, 0, False) == 2
    assert _next_chain_index_after_result(chain_tasks, 1, False) == 2
    assert _next_chain_index_after_result(chain_tasks, 2, False) is None


def test_next_chain_index_after_result_chain2_falls_back_to_d2_2():
    chain_tasks = [
        {"task_id": "C2.3", "category": "CREATE"},
        {"task_id": "R1.2", "category": "READ"},
        {"task_id": "D2.2", "category": "DELETE"},
    ]
    assert _next_chain_index_after_result(chain_tasks, 0, False) == 2
    assert _next_chain_index_after_result(chain_tasks, 1, False) == 2


def test_evaluate_orchestration_does_not_pass_previous_history_between_chain_tasks():
    evaluate_source = Path(SRC_DIR, "evaluate.py").read_text(encoding="utf-8")
    assert "initial_history=previous_messages" not in evaluate_source
    assert "initial_history=previous_chain_messages" not in evaluate_source


def test_extract_infra_context_from_tfstate_returns_ids_and_uuids(tmp_path):
    tfstate_path = tmp_path / "terraform.tfstate"
    tfstate_path.write_text(
        """
{
  "resources": [
    {
      "mode": "data",
      "type": "xenorchestra_pool",
      "instances": [
        {"attributes": {"id": "pool-id-1", "name_label": "DAO-Agentic-Infra"}}
      ]
    },
    {
      "mode": "managed",
      "type": "xenorchestra_vm",
      "instances": [
        {"attributes": {"id": "vm-id-1", "uuid": "vm-uuid-1", "name_label": "app-01", "cpus": 2, "memory_max": 4294967296}}
      ]
    }
  ]
}
""".strip(),
        encoding="utf-8"
    )

    context = _extract_infra_context_from_tfstate(str(tfstate_path))
    assert context["data_resources"][0]["id"] == "pool-id-1"
    assert context["managed_vms"][0]["id"] == "vm-id-1"
    assert context["managed_vms"][0]["uuid"] == "vm-uuid-1"


def test_resolve_tfstate_context_path_defaults_to_execution_workspace():
    path = _resolve_tfstate_context_path("/tmp/exec_workspace")
    assert path == "/tmp/exec_workspace/terraform.tfstate"


def test_resolve_tfstate_context_path_prefers_state_workspace_override():
    path = _resolve_tfstate_context_path("/tmp/read_workspace", "/tmp/shared_chain_workspace")
    assert path == "/tmp/shared_chain_workspace/terraform.tfstate"


def test_order_fixed_benchmark_tasks_returns_expected_sequence():
    task_ids = ["D2.2", "C2.2", "C1.1", "U1.2", "C2.3", "R1.2", "C5.2", "D1.2", "C1.3", "C1.2"]
    dataset_tasks = [{"task_id": tid} for tid in task_ids]
    ordered = _order_fixed_benchmark_tasks(dataset_tasks)
    assert [row["task_id"].lower() for row in ordered] == [
        "c1.1", "c1.2", "c2.2", "c5.2", "c1.3", "u1.2", "d1.2", "c2.3", "r1.2", "d2.2"
    ]


def test_order_fixed_benchmark_tasks_raises_for_missing_required_task():
    dataset_tasks = [{"task_id": "C1.1"}, {"task_id": "C1.2"}]
    with pytest.raises(ValueError, match="missing required benchmark tasks"):
        _order_fixed_benchmark_tasks(dataset_tasks)


def test_preserve_tfstate_snapshot_writes_named_json_copy(tmp_path):
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    tfstate_path = workspace_dir / "terraform.tfstate"
    tfstate_path.write_text('{"resources":[{"type":"xenorchestra_vm"}]}', encoding="utf-8")

    snapshot_path = _preserve_tfstate_snapshot(str(workspace_dir), snapshot_label="C1_1_p1")

    expected = workspace_dir / "state_snapshots" / "terraform_tfstate_pre_destroy_C1_1_p1.json"
    assert snapshot_path == str(expected)
    assert expected.exists()
    assert '"xenorchestra_vm"' in expected.read_text(encoding="utf-8")


def test_preserve_tfstate_snapshot_returns_none_when_tfstate_missing(tmp_path):
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    assert _preserve_tfstate_snapshot(str(workspace_dir), snapshot_label="C1_2_p1") is None


# ---------------------------------------------------------------------------
# Sequential benchmark mode orchestration tests
# ---------------------------------------------------------------------------

def test_benchmark_mode_uses_sequential_task_execution():
    """Benchmark mode must execute tasks sequentially (no asyncio.gather over task groups)."""
    evaluate_source = Path(SRC_DIR, "evaluate.py").read_text(encoding="utf-8")
    # Sequential path uses direct awaits, not a gathered coroutine list.
    assert "await asyncio.gather(*all_coroutines)" not in evaluate_source
    assert "await run_independent_task(task_spec)" in evaluate_source
    assert "await run_chain_group(chain_group)" in evaluate_source


def test_samples_run_sequentially_not_in_parallel():
    """Samples must run one-after-another; asyncio.gather over samples is strictly forbidden."""
    evaluate_source = Path(SRC_DIR, "evaluate.py").read_text(encoding="utf-8")
    # asyncio.gather must not be used to dispatch multiple samples concurrently.
    assert "asyncio.gather(" not in evaluate_source
    # The sequential loop must await each sample individually.
    assert "await run_sample(p)" in evaluate_source
    # Log message must describe sequential execution, not parallel.
    assert "sequentially" in evaluate_source
    assert "in parallel" not in evaluate_source


def test_benchmark_mode_independent_task_ids_are_correct():
    """The four independent task IDs in the benchmark must not belong to any chain group."""
    from evaluate import PARTIAL_CHAIN_GROUPS_BY_START, FIXED_BENCHMARK_TASK_ORDER, INDEPENDENT_TASK_IDS

    all_chain_ids = set()
    for group in PARTIAL_CHAIN_GROUPS_BY_START.values():
        all_chain_ids.update(group)

    benchmark_ids = set(FIXED_BENCHMARK_TASK_ORDER)
    expected_independent = benchmark_ids - all_chain_ids
    assert expected_independent == INDEPENDENT_TASK_IDS


def test_benchmark_sequential_dispatch_produces_six_groups():
    """
    The sequential dispatch loop must produce exactly 6 execution units:
    4 independent task steps + 2 chain-group steps.
    """
    from evaluate import PARTIAL_CHAIN_GROUPS_BY_START, FIXED_BENCHMARK_TASK_ORDER, INDEPENDENT_TASK_IDS

    seen_chain_ids = set()
    group_labels = []
    for task_id_normalized in FIXED_BENCHMARK_TASK_ORDER:
        if task_id_normalized in seen_chain_ids:
            continue
        chain_group = PARTIAL_CHAIN_GROUPS_BY_START.get(task_id_normalized)
        if chain_group:
            group_labels.append(("chain", tuple(chain_group)))
            seen_chain_ids.update(chain_group)
        else:
            group_labels.append(("independent", task_id_normalized))

    independent_labels = [lbl for kind, lbl in group_labels if kind == "independent"]
    chain_labels = [lbl for kind, lbl in group_labels if kind == "chain"]

    assert len(group_labels) == 6
    assert set(independent_labels) == INDEPENDENT_TASK_IDS
    assert len(chain_labels) == 2


def test_benchmark_mode_independent_task_always_destroys_after_completion():
    """
    run_independent_task must destroy the workspace after every task, not only for
    tasks in INDEPENDENT_TASK_IDS. The old guard on INDEPENDENT_TASK_IDS must be absent.
    """
    evaluate_source = Path(SRC_DIR, "evaluate.py").read_text(encoding="utf-8")
    # The old guard conditioned cleanup on task_id membership in INDEPENDENT_TASK_IDS.
    assert "in INDEPENDENT_TASK_IDS" not in evaluate_source


def test_benchmark_mode_chain_group_destroys_workspace_after_completion():
    """run_chain_group must call cleanup_workspace_if_state_exists on the last task workspace."""
    evaluate_source = Path(SRC_DIR, "evaluate.py").read_text(encoding="utf-8")
    # Cleanup uses chain_last_task_workspace (the per-task workspace of the last executed step).
    assert "await cleanup_workspace_if_state_exists(chain_last_task_workspace)" in evaluate_source


def test_explicit_chain_mode_destroys_workspace_after_chain_completes():
    """The --chain explicit mode must destroy the last task workspace after all tasks finish."""
    evaluate_source = Path(SRC_DIR, "evaluate.py").read_text(encoding="utf-8")
    # Both benchmark and explicit-chain modes use chain_last_task_workspace for cleanup.
    assert "await cleanup_workspace_if_state_exists(chain_last_task_workspace)" in evaluate_source


# ---------------------------------------------------------------------------
# _copy_chain_tfstate helper tests
# ---------------------------------------------------------------------------

def test_copy_chain_tfstate_copies_state_file(tmp_path):
    """_copy_chain_tfstate must copy terraform.tfstate from src workspace to dst workspace."""
    src_ws = tmp_path / "src_ws"
    dst_ws = tmp_path / "dst_ws"
    src_ws.mkdir()
    dst_ws.mkdir()
    state_content = '{"resources":[{"type":"xenorchestra_vm","instances":[{"attributes":{"id":"abc"}}]}]}'
    (src_ws / "terraform.tfstate").write_text(state_content, encoding="utf-8")

    _copy_chain_tfstate(str(src_ws), str(dst_ws))

    assert (dst_ws / "terraform.tfstate").exists()
    assert (dst_ws / "terraform.tfstate").read_text(encoding="utf-8") == state_content


def test_copy_chain_tfstate_skips_when_source_absent(tmp_path):
    """_copy_chain_tfstate must be a no-op when the source state file does not exist."""
    src_ws = tmp_path / "src_ws"
    dst_ws = tmp_path / "dst_ws"
    src_ws.mkdir()
    dst_ws.mkdir()

    _copy_chain_tfstate(str(src_ws), str(dst_ws))

    assert not (dst_ws / "terraform.tfstate").exists()


def test_copy_chain_tfstate_skips_when_source_too_small(tmp_path):
    """_copy_chain_tfstate must ignore state files that are below TFSTATE_MIN_VALID_BYTES (stub)."""
    src_ws = tmp_path / "src_ws"
    dst_ws = tmp_path / "dst_ws"
    src_ws.mkdir()
    dst_ws.mkdir()
    (src_ws / "terraform.tfstate").write_text("{}", encoding="utf-8")  # 2 bytes — stub

    _copy_chain_tfstate(str(src_ws), str(dst_ws))

    assert not (dst_ws / "terraform.tfstate").exists()


# ---------------------------------------------------------------------------
# Per-task workspace and state-passing contract tests
# ---------------------------------------------------------------------------

def test_chain_tasks_use_per_task_workspaces():
    """Each chain task must have its own workspace directory (not a single shared dir)."""
    evaluate_source = Path(SRC_DIR, "evaluate.py").read_text(encoding="utf-8")
    # Both modes must contain the per-task directory pattern (task_id slug in the path).
    assert "chain_{chain_slug}_{chain_task_id_slug}_p{pass_num}" in evaluate_source
    assert "chain_{chain_slug}_{task_id_slug}_p{pass_num}" in evaluate_source
    # The old shared workspace variable must not be used as an assignment target or in
    # function calls (ensure it's not in any active code path).
    import re as _re
    # Match assignment or function-call usage — not bare comments/docstrings.
    assert not _re.search(r'\bshared_chain_workspace\s*=', evaluate_source), \
        "shared_chain_workspace must not be assigned in evaluate.py"
    assert "cleanup_workspace_if_state_exists(shared_chain_workspace)" not in evaluate_source


def test_chain_state_passes_forward_only_on_success():
    """chain_state_workspace must only be updated after a successful non-READ task."""
    evaluate_source = Path(SRC_DIR, "evaluate.py").read_text(encoding="utf-8")
    # The state source advances only when success AND not READ.
    assert 'chain_result.get("success") and chain_task_category != \'READ\'' in evaluate_source
    assert 'task_result.get("success") and task_category != \'READ\'' in evaluate_source


def test_chain_state_fallback_uses_copy_helper():
    """_copy_chain_tfstate must be called to pass state into each chain task."""
    evaluate_source = Path(SRC_DIR, "evaluate.py").read_text(encoding="utf-8")
    assert "_copy_chain_tfstate(chain_state_workspace, per_task_workspace)" in evaluate_source
    assert "_copy_chain_tfstate(chain_state_workspace, task_workspace)" in evaluate_source


def test_chain_cleanup_uses_last_task_workspace():
    """Cleanup must use chain_last_task_workspace, not any earlier stale workspace."""
    evaluate_source = Path(SRC_DIR, "evaluate.py").read_text(encoding="utf-8")
    assert "await cleanup_workspace_if_state_exists(chain_last_task_workspace)" in evaluate_source
    # Old variables must not appear in cleanup positions.
    assert "await cleanup_workspace_if_state_exists(workspace_dir)" not in evaluate_source
    assert "await cleanup_workspace_if_state_exists(shared_chain_workspace)" not in evaluate_source


# ---------------------------------------------------------------------------
# extract_terraform_code — bug-fix regression tests
# ---------------------------------------------------------------------------

_VM = 'resource "xenorchestra_vm" "vm" {}'

def test_extract_terraform_code_strips_tf_language_tag():
    """Bug fix: 'tf' and 'TF' language tags must be stripped, not left in the output."""
    for tag in ("tf", "TF"):
        result = extract_terraform_code(f"```{tag}\n{_VM}\n```")
        assert result == _VM, f"tag '{tag}' was not stripped: {repr(result)}"

    # Code block with no language tag at all must still be extracted correctly.
    result_no_tag = extract_terraform_code(f"```\n{_VM}\n```")
    assert result_no_tag == _VM, f"no-tag block broken: {repr(result_no_tag)}"


def test_extract_terraform_code_handles_unclosed_fence():
    """Bug fix: model output truncated by max_tokens leaves an unclosed fence.
    The function must still return the code without the backtick/tag prefix."""
    truncated = f"```hcl\n{_VM}"   # no closing ```
    result = extract_terraform_code(truncated)
    assert "```" not in result, "backtick prefix leaked into extracted code"
    assert "hcl" not in result, "language tag leaked into extracted code"
    assert _VM in result


def test_extract_terraform_code_returns_last_block_for_multi_block_response():
    """Bug fix: when the LLM repeats an example code block before the real answer
    (common in COT/FSP responses), the LAST code block must be returned."""
    example_vm = 'resource "xenorchestra_vm" "build_01" { name_label = "build-01" }'
    real_vm    = 'resource "xenorchestra_vm" "app_01" { name_label = "app-01" }'
    response = (
        "Here is a worked example:\n"
        f"```hcl\n{example_vm}\n```\n\n"
        "My actual answer:\n"
        f"```hcl\n{real_vm}\n```"
    )
    result = extract_terraform_code(response)
    assert "app_01" in result,   f"last (real) block not returned; got: {repr(result[:80])}"
    assert "build_01" not in result, "first (example) block was returned instead of last"


def test_extract_terraform_code_does_not_strip_terraform_keyword():
    """'terraform {' at the top of a config must NOT have its keyword removed."""
    config = "```\nterraform {\n  required_providers {}\n}\n```"
    result = extract_terraform_code(config)
    assert result.startswith("terraform {"), f"terraform keyword stripped: {repr(result[:40])}"


def test_extract_terraform_code_handles_leading_newline_before_lang_tag():
    """Blank line between opening fence and language tag must not leave tag in output.
    Input structure: ``` ↵ hcl ↵ <code> ``` — 'hcl' is on its own line after the fence."""
    response = f"```\nhcl\n{_VM}\n```"   # newline THEN hcl THEN code
    result = extract_terraform_code(response)
    assert _VM in result, f"code not found in result: {repr(result)}"
    # The word 'hcl' must not appear at the start of the extracted code.
    assert not result.startswith("hcl"), f"language tag leaked into start of output: {repr(result[:30])}"
    # Ensure the tag is gone from the output entirely (not just shifted).
    assert result.strip() == _VM, f"unexpected content: {repr(result)}"


def test_extract_terraform_code_crlf_line_endings():
    """Windows CRLF line endings inside a code fence must be handled cleanly."""
    result = extract_terraform_code(f"```hcl\r\n{_VM}\r\n```")
    assert _VM in result


def test_extract_terraform_code_returns_empty_for_plain_prose():
    """A plain-text response with no HCL markers must return an empty string."""
    assert extract_terraform_code("I cannot generate Terraform code for this task.") == ""
