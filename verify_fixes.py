#!/usr/bin/env python3
"""
Comprehensive verification script for the SLM Evaluation Framework.
Tests all critical components and validates bug fixes.
"""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

def test_imports():
    """Test all critical imports work"""
    print("Testing imports...")
    try:
        from evaluate import load_config
        from api_client import OpenRouterClient, LocalTransformersClient
        from eval_utils import extract_terraform_code, execute_command, execute_terraform_apply
        from spec_checker import check_spec_accuracy, verify_post_state
        from compute_metrics import bleu_score, calculate_pass_at_k
        from json_generator import generate_dataset_entry
        from xo_client import XenOrchestraClient
        from models import GlobalConfig, ModelConfig, XenOrchestraConfig
        print("✓ All imports successful")
        return True
    except ImportError as e:
        print(f"✗ Import failed: {e}")
        return False

def test_extract_terraform_code():
    """Test terraform code extraction"""
    print("\nTesting terraform code extraction...")
    from eval_utils import extract_terraform_code
    
    # Test with code blocks
    response1 = """Here is the code:
```hcl
resource "test" "example" {
  name = "test"
}
```
"""
    code1 = extract_terraform_code(response1)
    assert 'resource "test"' in code1, "Failed to extract code from backticks"
    
    # Test with plain text
    response2 = 'resource "test" "example" { }'
    code2 = extract_terraform_code(response2)
    assert code2 == response2, "Failed to extract plain text"
    
    print("✓ Terraform code extraction working")
    return True

def test_pass_at_k():
    """Test unbiased Pass@k estimator"""
    print("\nTesting Pass@k estimator...")
    from compute_metrics import calculate_pass_at_k
    
    # Test perfect score
    assert calculate_pass_at_k(10, 10, 1) == 1.0
    assert calculate_pass_at_k(10, 10, 5) == 1.0
    
    # Test zero score
    assert calculate_pass_at_k(10, 0, 1) == 0.0
    assert calculate_pass_at_k(10, 0, 5) == 0.0
    
    # Test 50% correct
    result = calculate_pass_at_k(10, 5, 1)
    assert 0.45 < result < 0.55, f"Pass@1 with 50% correct should be ~0.5, got {result}"
    
    # Test edge case k > n
    assert calculate_pass_at_k(5, 3, 10) == 0.0
    
    print("✓ Pass@k estimator working correctly")
    return True

def test_config_loading():
    """Test config loading without global state pollution"""
    print("\nTesting config loading...")
    from evaluate import load_config
    import yaml
    import io
    
    # Load actual config
    config = load_config('config/openrouter_config.yaml')
    assert 'models' in config, "Config missing models"
    assert 'xenorchestra' in config, "Config missing xenorchestra"
    
    # Test that YAML global state is not polluted
    test_yaml = 'test: ${TEST_VAR}'
    stream = io.StringIO(test_yaml)
    result = yaml.safe_load(stream)
    assert result['test'] == '${TEST_VAR}', "YAML global state polluted"
    
    print("✓ Config loading working without global pollution")
    return True

def test_csv_format():
    """Test that CSV has correct format"""
    print("\nTesting CSV dataset...")
    import csv
    
    with open('tasks/vm_provisioning_tasks.csv', 'r') as f:
        reader = csv.DictReader(f)
        tasks = list(reader)
    
    assert len(tasks) == 10, f"Expected 10 tasks, found {len(tasks)}"
    
    # Check for common bugs fixed
    for task in tasks:
        hcl = task.get('reference_hcl', '')
        
        # Check port typo is fixed
        assert ':808080' not in hcl, f"Task {task['task_id']} still has port typo :808080"
        
        # Check template name is consistent
        if 'xenorchestra_template' in hcl:
            assert 'Ubuntu-22' in hcl, f"Task {task['task_id']} uses wrong template name"
    
    # Check R1.2 expected_resources
    r12 = next(t for t in tasks if t['task_id'] == 'R1.2')
    assert 'xenorchestra_vms' in r12['expected_resources'], "R1.2 expected_resources not plural"
    
    print("✓ CSV dataset validated")
    return True

def test_async_functions():
    """Test async function signatures"""
    print("\nTesting async function signatures...")
    import inspect
    from eval_utils import execute_command, execute_terraform_apply
    
    # These should be async
    assert inspect.iscoroutinefunction(execute_command), "execute_command should be async"
    assert inspect.iscoroutinefunction(execute_terraform_apply), "execute_terraform_apply should be async"
    
    print("✓ Async functions properly defined")
    return True

def test_models_validation():
    """Test Pydantic models"""
    print("\nTesting Pydantic models...")
    from models import GlobalConfig, ModelConfig, XenOrchestraConfig
    
    # Test that GlobalConfig allows optional active_model_name
    try:
        config = GlobalConfig(
            models={
                'test': ModelConfig(
                    name='test',
                    display_name='Test',
                    folder_name='test',
                    id_prefix='t'
                )
            }
        )
        assert config.active_model_name is None, "active_model_name should be optional"
        print("✓ Pydantic models validated")
        return True
    except Exception as e:
        print(f"✗ Pydantic validation failed: {e}")
        return False

def main():
    """Run all tests"""
    print("="*60)
    print(" SLM EVALUATION FRAMEWORK - VERIFICATION")
    print("="*60)
    
    tests = [
        test_imports,
        test_extract_terraform_code,
        test_pass_at_k,
        test_config_loading,
        test_csv_format,
        test_async_functions,
        test_models_validation,
    ]
    
    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            print(f"✗ {test.__name__} failed with exception: {e}")
            results.append(False)
    
    print("\n" + "="*60)
    passed = sum(results)
    total = len(results)
    print(f" VERIFICATION COMPLETE: {passed}/{total} tests passed")
    print("="*60)
    
    if passed == total:
        print("\n✓ All verification tests PASSED")
        return 0
    else:
        print(f"\n✗ {total - passed} test(s) FAILED")
        return 1

if __name__ == "__main__":
    sys.exit(main())
