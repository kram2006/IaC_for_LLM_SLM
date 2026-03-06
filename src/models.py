from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Any

class XenOrchestraConfig(BaseModel):
    url: str = "ws://localhost:8080/api/"
    username: str = "admin@admin.net"
    password: str = "admin"
    total_ram_gb: int = 24
    total_cpu_cores: int = 32
    usable_ram_gb: int = 20
    pool_name: str = "DAO-Agentic-Infra"
    network_name: str = "Pool-wide network associated with eth0"
    sr_name: str = "Local storage"
    template_name: str = "Ubuntu-22"

class ModelConfig(BaseModel):
    name: str
    display_name: str
    folder_name: str
    id_prefix: str
    temperature: float = 0.2
    max_tokens: int = 4096
    seed: Optional[int] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    system_prompt: Optional[str] = None
    local: bool = False

class GlobalConfig(BaseModel):
    active_model_name: Optional[str] = None
    baseline_system_prompt: Optional[str] = None
    multi_turn_system_prompt: Optional[str] = None
    xenorchestra: XenOrchestraConfig = Field(default_factory=XenOrchestraConfig)
    openrouter: Dict[str, Any] = Field(default_factory=dict)
    models: Dict[str, ModelConfig]

class TaskSpec(BaseModel):
    task_id: str
    category: str
    prompt: str
    resource_requirements: Dict[str, Any] = Field(default_factory=dict)
    expected_resources: List[str] = Field(default_factory=list)
    reference_hcl: Optional[str] = None
    complexity_level: int = 1
