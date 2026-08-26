# ProjectContainer — domain

## Сущность

`ProjectContainer` — учётная запись **изолированного runtime** одного Project.

| Поле | Смысл |
|------|--------|
| `id` | `pctr_*` |
| `project_id` | FK UNIQUE → Project (1:1 MVP) |
| `status` | `pending` \| `running` \| `paused` \| `failed` \| `terminating` \| `deleted` |
| `workspace_key` | = Project.workspace_key |
| `runtime_ref` | opaque: pod name/uid (когда Pod есть) |
| `last_error` | последняя ошибка оркестратора |

Project хранит только opaque `container_id` / `container_ref` — без парсинга k8s вне модуля 14.

## Статусы ↔ Project

| Project.status | Container (желаемое) |
|----------------|----------------------|
| `active` | `running` (Pod up) |
| `paused` | `paused` (**нет** Pod; blobs в MinIO) |
| `deleted` | `deleted` (нет Pod; wipe blobs) |

## Port `ContainerRuntimePort`

Единственный контракт наружу (вызывает ProjectService / reconcile):

| Method | Эффект |
|--------|--------|
| `ensure` | row + workspace ready |
| `start` | создать/запустить Pod + hydrate |
| `pause` | остановить compute: **удалить Pod**; MinIO keep |
| `delete` | удалить Pod + optional wipe MinIO |
| `force_kill` | grace=0 delete Pod |
| `get` / `get_metrics` | status + k8s metrics |
| `list_orphans` / `reconcile` | zombies / drift |

Запрещено: AiKeys → Port; UI → kubectl; размазывать client по ProjectService.
