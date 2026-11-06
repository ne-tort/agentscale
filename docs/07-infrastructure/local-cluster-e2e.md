# Local cluster e2e (GitOps)

Единственный путь: **k3s + Argo CD + kustomize**. Кластер поднимается на VM
`www@172.31.156.203` через **Terraform** ([runbook.md §2](runbook.md); полный цикл
«снести → поставить с нуля» — §2.2). Ручные `kubectl apply -k infra/argocd/...` —
не операторский шаг: их выполняет terraform-bootstrap (provisioner `gitops_bootstrap`,
включая секреты `ghcr-pull`/`repo-agentscale` из `TF_VAR_ghcr_token`).

1. `terraform apply` на VM — k3s + Argo CD + sealed-secrets + root-app + `ghcr-pull`
2. На VM: `export KUBECONFIG=~/.kube/prodavan-dev.yaml`
3. `cd infra/ops && poetry install && poetry run prodavan-ops validate && poetry run prodavan-ops wait && poetry run prodavan-ops smoke`
4. Прогоны: `poetry run prodavan-ops e2e run` / `e2e cleanup` — см. [e2e.md](e2e.md)

Day-2: merge to `main` → Argo sync.

**Pytest e2e (канон):** [`e2e.md`](e2e.md) — L2/L3a/L3b, CI opt-in. Legacy-скрипты (`tools/_live_containers_e2e.py`, `tools/generated/_e2e_*`) **удалены**.
