import os
import sys
import tempfile
import subprocess

import pytest


SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from eval_utils import extract_terraform_code
from eval_utils import redact_sensitive_text as redact_eval_sensitive_text, redact_messages_for_logging
from spec_checker import DeleteValidation
from compute_metrics import compute_metrics_for_folder, calculate_pass_at_k
from evaluate import _validate_local_path, load_config
from spec_checker import get_plan_json
from json_generator import redact_sensitive_text as redact_json_sensitive_text


def test_extract_terraform_code_keeps_non_empty_when_language_line_has_no_newline():
    assert extract_terraform_code("```hcl```") == "hcl"


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
