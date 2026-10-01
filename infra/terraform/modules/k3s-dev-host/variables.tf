variable "cluster_name" {
  type    = string
  default = "prodavan-dev"
}

variable "host_profile" {
  type        = string
  default     = "wsl"
  description = "Target host kind. wsl: Kali WSL (custom sshd on 2222, disable Docker Engine — it fights CNI inside WSL). vm: dedicated Linux VM (system sshd, Docker Engine stays for CI runners)."

  validation {
    condition     = contains(["wsl", "vm"], var.host_profile)
    error_message = "host_profile must be wsl or vm."
  }
}

variable "k3s_tls_sans" {
  type        = list(string)
  default     = ["127.0.0.1", "host.docker.internal"]
  description = "Extra TLS SANs for the k3s API cert (vm profile: 127.0.0.1 + the VM IP)."
}

variable "runner_kubeconfig_path" {
  type        = string
  default     = ""
  description = "vm profile: also place a kubeconfig for the CI `runner` user (GitHub Actions runners on the same host)."
}

variable "k3s_version" {
  type    = string
  default = "v1.29.5+k3s1"
}

variable "http_port" {
  type        = number
  default     = 8088
  description = "Host port for Traefik web entrypoint (smoke/ingress)."
}

variable "https_port" {
  type        = number
  default     = 8443
  description = "Host port for Traefik websecure entrypoint (HTTPS ingress). 0 disables hostPort."
}

variable "api_port" {
  type    = number
  default = 6443
}

variable "ssh_host" {
  type    = string
  default = "127.0.0.1"
}

variable "ssh_port" {
  type    = number
  default = 2222
}

variable "ssh_user" {
  type    = string
  default = "www"
}

variable "ssh_private_key_path" {
  type = string
}

variable "remote_repo_path" {
  type        = string
  description = "Absolute path to prodavan checkout on the SSH host (WSL)."
}

variable "kubeconfig_path" {
  type        = string
  description = "kubeconfig path on SSH host for operator/runner."
  default     = ""
}

variable "bootstrap_gitops" {
  type        = bool
  default     = true
  description = "Apply Argo CD install + root-app after k3s is Ready."
}

variable "ghcr_token" {
  type        = string
  default     = ""
  sensitive   = true
  description = "Optional GHCR pull token for one-time ghcr-pull secret (not stored in git)."
}

variable "ghcr_username" {
  type    = string
  default = ""
}

variable "windows_kubeconfig_path" {
  type        = string
  default     = ""
  description = "Optional path under /mnt/c/... for Docker Desktop CI runners. Empty = derive from remote_repo_path when export_docker_kubeconfig=true."
}

variable "export_docker_kubeconfig" {
  type        = bool
  default     = false
  description = "CI-only: write Docker-ready kubeconfig to Windows home. Not part of SSH+Terraform bootstrap / UI access."
}
