import os
import sys
import tempfile
import subprocess
import csv
import re
import asyncio

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
from evaluate import _validate_local_path, load_config
from spec_checker import get_plan_json, _extract_vm_resources
from json_generator import redact_sensitive_text as redact_json_sensitive_text, check_compliance
from json_generator import generate_dataset_entry
from llm_judge import parse_verdict
from populate_references import populate
from complexity_scorer import score_dataset


def test_extract_terraform_code_keeps_non_empty_when_language_line_has_no_newline():
    assert extract_terraform_code("```hcl```") == "hcl"


def test_execute_command_timeout_returns_timeout_status():
    result = asyncio.run(
        execute_command(
            f"{sys.executable} -c \"import time; time.sleep(0.2)\"",
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
