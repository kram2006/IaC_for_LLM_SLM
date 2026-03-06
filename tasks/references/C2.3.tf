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

variable "names" {
  default = ["web-01", "web-02", "web-03"]
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
  count        = length(var.names)
  name_label   = var.names[count.index]
  memory_max   = 4294967296
  cpus         = 2
  template  = data.xenorchestra_template.template.id
  auto_poweron = true

  disk {
    sr_id      = data.xenorchestra_sr.sr.id
    name_label = "${var.names[count.index]}-disk"
    size       = 53687091200
  }

  network {
    network_id = data.xenorchestra_network.net.id
  }

  cdrom {
    id = "286a9f23-133c-4cdf-a247-4de9ef4b17e9"
  }

  tags = ["iso-install", "ubuntu-22.04"]
}