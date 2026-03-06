#!/usr/bin/env python3
"""
Minimal OLLAMA model evaluation script for IAC tasks.
"""
# IMPORTS
import math
import pandas as pd
import requests
import json
import logging
import warnings
import subprocess
import time
from pathlib import Path
from typing import Dict, List
from collections import Counter
from dotenv import load_dotenv 
import os 
load_dotenv()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

import nltk
from nltk.translate.bleu_score import sentence_bleu, SmoothingFunction
from nltk.translate.meteor_score import meteor_score
from rouge_score import rouge_scorer
try:
    from code_bert_score import score as code_bert_score
except Exception:  # pragma: no cover - optional dependency
    code_bert_score = None

try:
    from tree_sitter import Parser
    import tree_sitter_hcl
except Exception:  # pragma: no cover - optional dependency
    Parser = None
    tree_sitter_hcl = None

# LOGGING SETUP
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Reduce noisy third-party logs (Hugging Face Hub / httpx / transformers)
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("transformers").setLevel(logging.ERROR)
logging.getLogger("tokenizers").setLevel(logging.ERROR)

# Suppress common HF Hub warning about unauthenticated requests
warnings.filterwarnings(
    "ignore",
    message=r"You are sending unauthenticated requests to the HF Hub.*",
)

# CONFIGURATION
OLLAMA_API_URL = "http://localhost:11434/api/generate"
OLLAMA_MODELS = [
    "codellama:7b",
    "codellama:13b",
]

OPENROUTER_MODELS = [

]


# Prompt Template : To be refined. 
SYSTEM_PROMPT = """You are TerraformAI, an expert in Infrastructure as Code (IaC) and Terraform. 
Generate valid Terraform configuration code based on the user's requirements."""


# MODEL CALLING
def call_ollama(model: str, prompt: str, system_prompt: str = SYSTEM_PROMPT, 
                temperature: float = 0.7, max_tokens: int = 20000) -> str:
    """Call OLLAMA API and return the response."""
    try:
        payload = {
            "model": model,
            "prompt": f"{system_prompt}\n\n{prompt}",
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            }
        }
        
        response = requests.post(
            OLLAMA_API_URL,
            json=payload,
            timeout=6000
        )
        response.raise_for_status()
        
        result = response.json()
        return result.get("response", "").strip()
        
    except requests.exceptions.Timeout:
        logger.error(f"Timeout calling {model}")
        return ""
    except requests.exceptions.RequestException as e:
        logger.error(f"API error calling {model}: {e}")
        return ""
    except Exception as e:
        logger.error(f"Error calling {model}: {e}")
        return ""

# FOR OPENROUTER 
def call_openrouter(model:str, prompt: str, system_prompt: str = SYSTEM_PROMPT, temperature: float = 0.7, max_tokens: int =20000) ->str:
    response = requests.post(
        url = "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}"
        }, 
        data = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False
        })
    )

    return response.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()

# There might be code explanations or other text before the actual code. 
# This function extracts only the generated Terraform code. 
def extract_terraform_code(response: str) -> str:
    """Extract Terraform code from model response."""
    delimiters = ["```"]
    
    for delim in delimiters:
        if delim in response.lower():
            parts = response.split(delim) 
            if len(parts) >= 2:
                code = parts[1]
                # Remove language identifier
                if code.startswith(("hcl", "terraform", "HCL", "Terraform")):
                    code = code.split("\n", 1)[1] if "\n" in code else code
                return code.strip()
    
    return response.strip()


# Validates terraform code I guess? I will have to look into it further.
def validate_terraform(code: str, output_dir: Path) -> dict:
    """Validate Terraform code using terraform validate."""
    tf_file = output_dir / "main.tf"
    
    try:
        # Write code to file
        tf_file.write_text(code)
        
        # Initialize terraform
        subprocess.run(
            ["terraform", "init"],
            cwd=output_dir,
            capture_output=True,
            timeout=30
        )
        
        # Validate
        result = subprocess.run(
            ["terraform", "validate", "-json"],
            cwd=output_dir,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        validation = json.loads(result.stdout)
        return {
            "valid": validation.get("valid", False),
            "error_count": validation.get("error_count", 0),
            "errors": validation.get("diagnostics", [])
        }
    except Exception as e:
        logger.error(f"Validation error: {e}")
        return {"valid": False, "error_count": 1, "errors": [str(e)]}
    finally:
        # Cleanup
        if tf_file.exists():
            tf_file.unlink()

# really simple tokenizer lol 
def _tokenize(text: str) -> List[str]:
    return text.split()

#
def get_hcl_keywords(): 
    return {"resource", "provider", "variable", "output", "module", "data", "locals", "terraform", "backend", "provisioner", "connection"}

def compute_weighted_bleu(cd_tokens, ref_tokens, keywords, keyword_weight = 5): 

    # compute weighted precision: 
    cd_count = Counter(cd_tokens)
    ref_count = Counter(ref_tokens)

    numerator = 0
    denominator = 0 

    for token, count in cd_count.items(): 
        weight = keyword_weight if token in keywords else 1
        numerator += weight * min(count, ref_count.get(token,0)) # min because we're only checking for the overlap. 
        denominator += weight * count

    p1 = numerator / denominator if denominator > 0 else 0 
    
    # Brevity Penalty
    c, r = len(cd_tokens), len(ref_tokens)
    bp = 1 if c > r else math.exp(1-r/c)

    weighted_bleu = bp * p1 
    return weighted_bleu

def calculate_codebleu(candidate, reference, weights = (0.25, 0.25, 0.25, 0.25)):

    # Weighted average of 4 scores:
    # 1. BLEU
    ref_tokens = _tokenize(reference)
    hyp_tokens = _tokenize(candidate)
    score_bleu = sentence_bleu([ref_tokens], hyp_tokens, smoothing_function = SmoothingFunction().method1)

    # 2. Weighted N-Gram match
    keywords = get_hcl_keywords()
    weighted_bleu = compute_weighted_bleu(hyp_tokens, ref_tokens, keywords)

    # 3. Syntactic AST 
    def _get_parser():
        if Parser is None or tree_sitter_hcl is None:
            return None
        parser = Parser()
        parser.set_language(tree_sitter_hcl.language())
        return parser

    def _collect_node_types(node):
        types = []
        stack = [node]
        while stack:
            current = stack.pop()
            types.append(current.type)
            for child in current.children:
                stack.append(child)
        return types

    def _collect_identifiers(node, source_bytes):
        identifiers = []
        stack = [node]
        while stack:
            current = stack.pop()
            if current.type == "identifier":
                text = source_bytes[current.start_byte:current.end_byte].decode("utf-8", errors="ignore")
                if text:
                    identifiers.append(text)
            for child in current.children:
                stack.append(child)
        return identifiers

    def _f1_overlap(items_a, items_b):
        if not items_a and not items_b:
            return 1.0
        if not items_a or not items_b:
            return 0.0
        count_a = Counter(items_a)
        count_b = Counter(items_b)
        overlap = sum(min(count_a[k], count_b.get(k, 0)) for k in count_a)
        precision = overlap / sum(count_a.values()) if count_a else 0.0
        recall = overlap / sum(count_b.values()) if count_b else 0.0
        if precision + recall == 0:
            return 0.0
        return 2 * precision * recall / (precision + recall)

    parser = _get_parser()
    syntax_score = 0.0
    dataflow_score = 0.0
    if parser is not None:
        try:
            ref_bytes = (reference or "").encode("utf-8")
            hyp_bytes = (candidate or "").encode("utf-8")
            ref_tree = parser.parse(ref_bytes)
            hyp_tree = parser.parse(hyp_bytes)

            ref_types = _collect_node_types(ref_tree.root_node)
            hyp_types = _collect_node_types(hyp_tree.root_node)
            syntax_score = _f1_overlap(hyp_types, ref_types)

            ref_ids = _collect_identifiers(ref_tree.root_node, ref_bytes)
            hyp_ids = _collect_identifiers(hyp_tree.root_node, hyp_bytes)
            dataflow_score = _f1_overlap(hyp_ids, ref_ids)
        except Exception as e:
            logger.warning(f"Tree-sitter HCL parse failed: {e}")
            syntax_score = 0.0
            dataflow_score = 0.0

    # 4. Data Flow Match (approximated with identifier overlap for HCL)
    w1, w2, w3, w4 = weights
    return w1 * score_bleu + w2 * weighted_bleu + w3 * syntax_score + w4 * dataflow_score

# Computing text metrics: BLEU, ROUGE, METEOR, CodeBERTScore, CodeBLEU.
def compute_text_metrics(reference: str, candidate: str) -> Dict[str, float]:
    """Compute BLEU, ROUGE-3 (F1), METEOR, CodeBERTScore, and CodeBLEU for a single example."""
    reference = reference or ""
    candidate = candidate or ""

    smoothie = SmoothingFunction().method1 #? What is this?
    bleu = sentence_bleu([_tokenize(reference)], _tokenize(candidate), smoothing_function=smoothie) #? Should I even be using sentencebleu for this? Is there a better way? 

    scorer = rouge_scorer.RougeScorer(["rouge3"], use_stemmer=True)
    rouge3_f = scorer.score(reference, candidate)["rouge3"].fmeasure

    meteor = (
        meteor_score([_tokenize(reference)], _tokenize(candidate))
        if reference or candidate
        else 0.0
    )

    metrics = {
        "bleu": float(bleu),
        "rouge3_f": float(rouge3_f),
        "meteor": float(meteor),
        "codebleu": float(calculate_codebleu(candidate, reference)),
    }

    # Only runs if codebertscore was successfully imported. 
    if code_bert_score is not None:
        try:
            score_outputs = code_bert_score([candidate], [reference], lang="en") #? Can I put terraform here instead of en?
            # code_bert_score may return (P, R, F1) or (P, R, F1, hashcode)
            if isinstance(score_outputs, (list, tuple)):
                f1 = score_outputs[2]
            else:
                f1 = score_outputs
            metrics["codebertscore_f1"] = float(f1.mean().item())
        except Exception as e:
            logger.warning(f"CodeBERTScore failed: {e}")
            metrics["codebertscore_f1"] = 0.0
    else:
        metrics["codebertscore_f1"] = 0.0

    return metrics

def ensure_nltk_resources():
    """Ensure required NLTK resources for METEOR are available."""
    for resource in ["wordnet", "omw-1.4"]:
        try:
            nltk.data.find(f"corpora/{resource}")
        except LookupError:
            nltk.download(resource, quiet=True)


def evaluate_models(csv_path: str, models: list, max_samples: int = 10, openrouter = False):
    """Evaluate OLLAMA models on dataset."""
    ensure_nltk_resources()

    # Load data
    df = pd.read_csv(csv_path)
    logger.info(f"Loaded {len(df)} samples from {csv_path}")
    
    # Limit samples
    df = df.head(max_samples)
    
    # Create output directory
    output_dir = Path("./ollama_output_openrouter_ollama")
    output_dir.mkdir(exist_ok=True)
    
    results = []
    
    for idx, row in df.iterrows():
        prompt = row["Prompt"]
        intent = row["Intent"]
        reference = row.get("Reference output", "")
        # Normalize potential NaN/float values to strings for safe slicing/logging.
        if pd.isna(prompt):
            prompt = ""
        if pd.isna(intent):
            intent = ""
        if pd.isna(reference):
            reference = ""
        prompt = str(prompt)
        intent = str(intent)
        reference = str(reference)
        
        logger.info(f"\n[{idx+1}/{len(df)}] Evaluating: {intent[:60]}...")
        
        for model in models:
            logger.info(f"  Testing model: {model}")
            
            try:
                # Generate response
                start_time = time.perf_counter()
                if openrouter:
                    response = call_openrouter(model, prompt)
                else:
                    response = call_ollama(model, prompt)
                duration_s = time.perf_counter() - start_time

                if not response:
                    logger.warning(f"    No response from {model}")
                    results.append({
                        "sample_id": idx,
                        "model": model,
                        "prompt": prompt,
                        "intent": intent,
                        "reference": reference,
                        "response": "",
                        "code": "",
                        "bleu": 0.0,
                        "rouge3_f": 0.0,
                        "meteor": 0.0,
                        "codebleu": 0.0,
                        "codebertscore_f1": 0.0,
                        "time_s": float(duration_s),
                        "error": "empty_response",
                    })
                    continue

                # Extract code
                code = extract_terraform_code(response)
                logger.info(f"    Generated {len(code)} chars of code")

                # Validate (optional - requires terraform installed)
                # validation = validate_terraform(code, output_dir)

                metrics = compute_text_metrics(reference, code)
                results.append({
                    "sample_id": idx,
                    "model": model,
                    "prompt": prompt,
                    "intent": intent,
                    "reference": reference,
                    "response": response,
                    "code": code,
                    "bleu": metrics["bleu"],
                    "rouge3_f": metrics["rouge3_f"],
                    "meteor": metrics["meteor"],
                    "codebleu": metrics["codebleu"],
                    "codebertscore_f1": metrics["codebertscore_f1"],
                    "time_s": float(duration_s),
                    "error": "",
                    # "valid": validation["valid"],
                    # "error_count": validation["error_count"]
                })
            except Exception as e:
                logger.exception(f"    Error evaluating {model} on sample {idx}: {e}")
                results.append({
                    "sample_id": idx,
                    "model": model,
                    "prompt": prompt,
                    "intent": intent,
                    "reference": reference,
                    "response": "",
                    "code": "",
                    "bleu": 0.0,
                    "rouge3_f": 0.0,
                    "meteor": 0.0,
                    "codebleu": 0.0,
                    "codebertscore_f1": 0.0,
                    "time_s": 0.0,
                    "error": str(e),
                })
    
    # Save per-sample results
    results_df = pd.DataFrame(results)
    output_file = output_dir / "ollama_results.csv"
    results_df.to_csv(output_file, index=False)
    logger.info(f"\n✓ Results saved to {output_file}")

    # Aggregate metrics per model.
    # This is done by taking the result dump and combining all results by averaging metric values. 
    # ! Metrics are averaged, so there is no danger of getting better values just by going through more of the dataset. 
    metrics_table = (
        results_df
        .groupby("model", as_index=False)
        .agg({
            "bleu": "mean",
            "rouge3_f": "mean",
            "meteor": "mean",
            "codebleu": "mean",
            "codebertscore_f1": "mean",
            "time_s": "mean",
        })
        .rename(columns={"rouge3_f": "rouge3"})
    )
    metrics_file = output_dir / "ollama_metrics.csv"
    metrics_table.to_csv(metrics_file, index=False)
    logger.info(f"✓ Metrics table saved to {metrics_file}")
    
    # Print summary
    print("\n" + "=" * 60)
    print("EVALUATION SUMMARY")
    print("=" * 60)
    for _, row in metrics_table.iterrows():
        print(
            f"{row['model']:30s} - BLEU: {row['bleu']:.4f}, "
            f"ROUGE-3: {row['rouge3']:.4f}, METEOR: {row['meteor']:.4f}, "
            f"CodeBLEU: {row['codebleu']:.4f}, "
            f"CodeBERTScore-F1: {row['codebertscore_f1']:.4f}, "
            f"Avg Time (s): {row['time_s']:.2f}"
        )
    print("=" * 60)





def evaluate_models_mixed(csv_path: str, ollama_models: list, openrouter_models: list, max_samples: int = 10):
    """Evaluate both OLLAMA and OpenRouter models in a single pass over the dataset."""
    ensure_nltk_resources()

    df = pd.read_csv(csv_path)
    logger.info(f"Loaded {len(df)} samples from {csv_path}")
    df = df.head(max_samples)

    output_dir = Path("./experiments/ollama_output_codebleu_mixed_2")
    output_dir.mkdir(exist_ok=True)

    results = []

    for idx, row in df.iterrows():
        prompt = row["Prompt"]
        intent = row["Intent"]
        reference = row.get("Reference output", "")
        # Normalize potential NaN/float values to strings for safe slicing/logging.
        if pd.isna(prompt):
            prompt = ""
        if pd.isna(intent):
            intent = ""
        if pd.isna(reference):
            reference = ""
        prompt = str(prompt)
        intent = str(intent)
        reference = str(reference)

        logger.info(f"\n[{idx+1}/{len(df)}] Evaluating: {intent[:60]}...")

        for model in ollama_models:
            logger.info(f"  Testing OLLAMA model: {model}")
            try:
                start_time = time.perf_counter()
                response = call_ollama(model, prompt)
                duration_s = time.perf_counter() - start_time
                results.extend(_collect_model_result(idx, model, prompt, intent, reference, response, duration_s))
            except Exception as e:
                logger.exception(f"    Error evaluating {model} on sample {idx}: {e}")
                results.append({
                    "sample_id": idx,
                    "model": model,
                    "prompt": prompt,
                    "intent": intent,
                    "reference": reference,
                    "response": "",
                    "code": "",
                    "bleu": 0.0,
                    "rouge3_f": 0.0,
                    "meteor": 0.0,
                    "codebleu": 0.0,
                    "codebertscore_f1": 0.0,
                    "time_s": 0.0,
                    "error": str(e),
                })

        for model in openrouter_models:
            logger.info(f"  Testing OpenRouter model: {model}")
            try:
                start_time = time.perf_counter()
                response = call_openrouter(model, prompt)
                duration_s = time.perf_counter() - start_time
                results.extend(_collect_model_result(idx, model, prompt, intent, reference, response, duration_s))
            except Exception as e:
                logger.exception(f"    Error evaluating {model} on sample {idx}: {e}")
                results.append({
                    "sample_id": idx,
                    "model": model,
                    "prompt": prompt,
                    "intent": intent,
                    "reference": reference,
                    "response": "",
                    "code": "",
                    "bleu": 0.0,
                    "rouge3_f": 0.0,
                    "meteor": 0.0,
                    "codebleu": 0.0,
                    "codebertscore_f1": 0.0,
                    "time_s": 0.0,
                    "error": str(e),
                })

    _finalize_and_report(results, output_dir)


def _collect_model_result(sample_id, model, prompt, intent, reference, response, duration_s):
    try:
        if not response:
            logger.warning(f"    No response from {model}")
            return [{
                "sample_id": sample_id,
                "model": model,
                "prompt": prompt,
                "intent": intent,
                "reference": reference,
                "response": "",
                "code": "",
                "bleu": 0.0,
                "rouge3_f": 0.0,
                "meteor": 0.0,
                "codebleu": 0.0,
                "codebertscore_f1": 0.0,
                "time_s": float(duration_s),
                "error": "empty_response",
            }]

        code = extract_terraform_code(response)
        logger.info(f"    Generated {len(code)} chars of code")
        metrics = compute_text_metrics(reference, code)
        return [{
            "sample_id": sample_id,
            "model": model,
            "prompt": prompt,
            "intent": intent,
            "reference": reference,
            "response": response,
            "code": code,
            "bleu": metrics["bleu"],
            "rouge3_f": metrics["rouge3_f"],
            "meteor": metrics["meteor"],
            "codebleu": metrics["codebleu"],
            "codebertscore_f1": metrics["codebertscore_f1"],
            "time_s": float(duration_s),
            "error": "",
        }]
    except Exception as e:
        logger.exception(f"    Error processing {model} result for sample {sample_id}: {e}")
        return [{
            "sample_id": sample_id,
            "model": model,
            "prompt": prompt,
            "intent": intent,
            "reference": reference,
            "response": "",
            "code": "",
            "bleu": 0.0,
            "rouge3_f": 0.0,
            "meteor": 0.0,
            "codebleu": 0.0,
            "codebertscore_f1": 0.0,
            "time_s": float(duration_s),
            "error": str(e),
        }]


def _finalize_and_report(results, output_dir: Path):
    results_df = pd.DataFrame(results)
    output_file = output_dir / "ollama_results.csv"
    results_df.to_csv(output_file, index=False)
    logger.info(f"\n✓ Results saved to {output_file}")

    metrics_table = (
        results_df
        .groupby("model", as_index=False)
        .agg({
            "bleu": "mean",
            "rouge3_f": "mean",
            "meteor": "mean",
            "codebleu": "mean",
            "codebertscore_f1": "mean",
            "time_s": "mean",
        })
        .rename(columns={"rouge3_f": "rouge3"})
    )
    metrics_file = output_dir / "ollama_metrics.csv"
    metrics_table.to_csv(metrics_file, index=False)
    logger.info(f"✓ Metrics table saved to {metrics_file}")

    print("\n" + "=" * 60)
    print("EVALUATION SUMMARY")
    print("=" * 60)
    for _, row in metrics_table.iterrows():
        print(
            f"{row['model']:30s} - BLEU: {row['bleu']:.4f}, "
            f"ROUGE-3: {row['rouge3']:.4f}, METEOR: {row['meteor']:.4f}, "
            f"CodeBLEU: {row['codebleu']:.4f}, "
            f"CodeBERTScore-F1: {row['codebertscore_f1']:.4f}, "
            f"Avg Time (s): {row['time_s']:.2f}"
        )
    print("=" * 60)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Evaluate OLLAMA models on IAC tasks")
    parser.add_argument("--csv", default="data.csv", help="Path to CSV data file")
    # parser.add_argument("--models", nargs="+", default=OPENROUTER_MODELS, help="OpenRouter models to test")
    parser.add_argument("--samples", type=int, default=100, help="Number of samples to evaluate")
    # parser.add_argument("--openrouter", action="store_true", help="Use OpenRouter models instead of OLLAMA")
    
    args = parser.parse_args()
    
    # evaluate_models(args.csv, args.models, args.samples, args.openrouter)
    evaluate_models_mixed(args.csv, OLLAMA_MODELS, OPENROUTER_MODELS, args.samples)