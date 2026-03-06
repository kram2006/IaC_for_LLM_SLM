import os
import time
import requests
import json
import logging
from huggingface_hub import InferenceClient
from eval_utils import extract_terraform_code

class OpenRouterClient:
    def __init__(self, api_key=None, model_name=None, temperature=0.2, max_tokens=4096, base_url="https://openrouter.ai/api/v1/chat/completions", timeout=300, seed=None):
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        # Try HF_TOKEN if base_url is Hugging Face
        if "huggingface.co" in base_url and not api_key:
            self.api_key = os.environ.get("HF_TOKEN") or self.api_key
            
        if not self.api_key:
            raise ValueError("API Key (OPENROUTER_API_KEY or HF_TOKEN) not found")
            
        self.model_name = model_name
        if not self.model_name:
            # We don't hardcode a default anymore, evaluate.py must provide it from config
            raise ValueError("Model name must be provided to OpenRouterClient")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.base_url = base_url
        self.max_retries = 3
        self.timeout = timeout
        self.seed = seed  # FIX D3: Seed for reproducibility
        
    def chat_completion(self, messages):
        """
        Send a chat completion request.
        Supports OpenRouter/OpenAI-compatible APIs and Hugging Face Inference API.
        """
        if "huggingface.co" in self.base_url:
            return self._chat_completion_hf(messages)
        return self._chat_completion_standard(messages)

    def _chat_completion_hf(self, messages):
        try:
            # Use model short name from name or the full path from self.model_name
            client = InferenceClient(api_key=self.api_key)
            kwargs = {
                "messages": messages,
                "model": self.model_name,
                "temperature": self.temperature if self.temperature > 0 else 0.01,
                "max_tokens": self.max_tokens,
                "stream": False
            }
            if self.seed is not None:
                kwargs["seed"] = self.seed
                
            response = client.chat_completion(**kwargs)
            return response.choices[0].message.content
        except Exception as e:
            logging.error(f"Hugging Face Inference failed: {str(e)}")
            return None

    def _chat_completion_standard(self, messages):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/google-deepmind/iac-eval",
            "X-Title": "IaC-Eval-XCPNG"
        }
        
        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens 
        }
        # FIX D3: Add seed for reproducibility if set
        if self.seed is not None:
            payload["seed"] = self.seed
        
        for attempt in range(self.max_retries):
            try:
                logging.debug(f"Sending payload to {self.base_url}")
                # logging.debug(f"Payload: {json.dumps(payload, indent=2)}")
                response = requests.post(
                    self.base_url, 
                    headers=headers, 
                    json=payload, 
                    timeout=self.timeout
                )
                if response.status_code != 200:
                    logging.error(f"API Error {response.status_code}. Model: {self.model_name}")
                
                if response.status_code == 200:
                    data = response.json()
                    choices = data.get('choices')
                    if not isinstance(choices, list) or not choices:
                        logging.error(f"Malformed API response: missing choices list ({data})")
                        return None

                    message = choices[0].get('message', {}) if isinstance(choices[0], dict) else {}
                    content = message.get('content')
                    if content is None:
                        logging.error(f"Malformed API response: missing message.content ({choices[0]})")
                        return None
                    return content
                elif response.status_code == 429:
                    wait_time = (2 ** attempt) * 2
                    logging.warning(f"Rate limited. Waiting {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                else:
                    logging.error(f"API Error {response.status_code}: {response.text}")
                    if attempt < self.max_retries - 1:
                        time.sleep(2)  # Brief wait before retry on non-429 errors
                        continue
                    return None
                    
            except Exception as e:
                logging.error(f"Standard Request failed: {str(e)}")
                if attempt == self.max_retries - 1:
                    return None
                time.sleep(2)
                
        return None

    def generate_terraform_code(self, prompt, system_prompt):
        """Legacy wrapper - use chat_completion for conversation loops"""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
        content = self.chat_completion(messages)
        if content:
             return self.extract_terraform_code(content)
        return None
    
    def extract_terraform_code(self, response_text):
        return extract_terraform_code(response_text)

class LocalTransformersClient:
    def __init__(self, model_name, temperature=0.2, max_tokens=4096, seed=None):
        import torch
        from transformers import AutoTokenizer, AutoModelForCausalLM
        
        logging.info(f"Loading local model: {model_name}")
        self.model_name = model_name
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        # Using device_map="auto" and torch_dtype="auto" to handle memory/precision automatically
        # trust_remote_code is needed for many Qwen variants
        self.model = AutoModelForCausalLM.from_pretrained(
            model_name, 
            device_map="auto", 
            torch_dtype="auto", 
            trust_remote_code=True
        )
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
            
        self.temperature = temperature if temperature > 0 else 0.01
        self.max_tokens = max_tokens
        self.seed = seed

    def chat_completion(self, messages):
        import torch
        if self.seed is not None:
            torch.manual_seed(self.seed)
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(self.seed)

        inputs = self.tokenizer.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
        ).to(self.model.device)

        with torch.no_grad():
            outputs = self.model.generate(
                **inputs, 
                max_new_tokens=self.max_tokens,
                temperature=self.temperature,
                do_sample=True if self.temperature > 0 else False,
                pad_token_id=self.tokenizer.eos_token_id
            )
        
        # Decode only the generated part
        response = self.tokenizer.decode(outputs[0][inputs["input_ids"].shape[-1]:], skip_special_tokens=True)
        return response

    def extract_terraform_code(self, response_text):
        return extract_terraform_code(response_text)
