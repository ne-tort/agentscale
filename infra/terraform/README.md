# Terraform — cloud skeletons only

Local cluster is **not** provisioned by Terraform.  
Dev path: k3s + Argo — [`docs/07-infrastructure/runbook.md`](../../docs/07-infrastructure/runbook.md).

```text
infra/terraform/
├── environments/{dev,staging}/   # cloud (aspirational)
└── modules/{network,k3s-cluster,postgres,object-storage}
```

`environments/local` и `modules/k3s-local` удалены (были hint-only вокруг k3d).
