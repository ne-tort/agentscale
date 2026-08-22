# STUB — состояние кодовой базы

`apps/api` и `apps/flutter` намеренно очищены до **болванки**.

| Слой | Статус |
|------|--------|
| Infra (k3s, Terraform, Argo, CI images) | Рабочий, не трогать без нужды |
| Product canon | Только [`docs/target/`](docs/target/) |
| Legacy docs | [`docs/LEGACY.md`](docs/LEGACY.md) — справочно, не следовать |
| API / Flutter / DB schema | Stub: health + empty UI + `stub_meta` |

Агентам: **не** копировать удалённую S4B/pipeline/password-JWT логику из истории коммитов.
Новая реализация — по `docs/target/` + план [`11-implementation-plan/`](docs/target/11-implementation-plan/) + as-built [`12-layer-docs/`](docs/target/12-layer-docs/) + [AGENTS.md](AGENTS.md) + Alembic с чистого bootstrap (см. [alembic.md](docs/07-infrastructure/alembic.md)).
Слой нельзя закрывать «минимальным прототипом» — см. DoD/veto в плане; карточку as-built обновлять в том же PR, что и код.
