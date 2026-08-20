# Terraform (stub)

Спецификация: [`docs/07-infrastructure/terraform.md`](../../docs/07-infrastructure/terraform.md).

**Статус:** каталог зарезервирован; `.tf` файлы — в итерации **I7** (см. [`docs/10-implementation/roadmap.md`](../../docs/10-implementation/roadmap.md)).

Планируемая структура:

```text
infra/terraform/
├── environments/
│   ├── dev/
│   ├── staging/
│   └── prod/
├── modules/
│   ├── k3s-cluster/
│   ├── postgres/
│   ├── object-store/
│   └── dns/
└── README.md
```
