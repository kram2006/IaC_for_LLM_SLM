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

data "xenorchestra_template" "template" {
  pool_id    = data.xenorchestra_pool.pool.id
  name_label = "Ubuntu-22"
}

data "xenorchestra_sr" "sr" {
  pool_id    = data.xenorchestra_pool.pool.id
  name_label = "Local storage"
}

data "xenorchestra_network" "net" {
  pool_id    = data.xenorchestra_pool.pool.id
  name_label = "Pool-wide network associated with eth0"
}

resource "xenorchestra_vm" "vm" {
  name_label   = "vm-01"
  memory_max   = 2147483648
  cpus         = 1
  template  = data.xenorchestra_template.template.id
  auto_poweron = true

  disk {
    sr_id      = data.xenorchestra_sr.sr.id
    name_label = "disk"
    size       = 10737418240
  }

  network {
    network_id = data.xenorchestra_network.net.id
  }
}