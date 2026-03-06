terraform {
  required_providers {
    xenorchestra = {
      source  = "terra-farm/xenorchestra"
      version = "~> 0.26.0"
    }
  }
}

provider "xenorchestra" {
  url      = "ws://localhost:8080"
  username = "admin@admin.net"
  password = "admin"
  insecure = true
}

data "xenorchestra_pool" "pool" {
  name_label = "DAO-Agentic-Infra"
}

data "xenorchestra_vms" "all_vms" {
  pool_id = data.xenorchestra_pool.pool.id
}

output "vm_memory" {
  value = { for vm in data.xenorchestra_vms.all_vms.vms : vm.name_label => vm.memory_max }
}