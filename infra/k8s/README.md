# Kubernetes manifests (stub)

Спецификация: [`docs/07-infrastructure/k3s-services.md`](../../docs/07-infrastructure/k3s-services.md).

**Статус:** манифесты — в итерации **I7** после scaffold API и worker.

Планируемые overlays:

```text
infra/k8s/
├── base/
│   ├── api/
│   ├── agent-worker/
│   ├── mcp-gateway/
│   └── postgres/
└── overlays/
    ├── dev/
    └── staging/
```
