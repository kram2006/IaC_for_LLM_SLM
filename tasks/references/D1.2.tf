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