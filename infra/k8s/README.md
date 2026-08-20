# Kubernetes path stub (redirect)

**Канон манифестов: [`../k3s/`](../k3s/).**

Целевая платформа — **k3s** (см. [`docs/07-infrastructure/k3s-services.md`](../../docs/07-infrastructure/k3s-services.md)).
Каталог `infra/k8s/` оставлен только как указатель; не добавляйте сюда Deployment YAML.

```bash
kubectl apply -k infra/k3s/overlays/dev
```
