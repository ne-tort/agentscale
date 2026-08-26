# ADR — Project Containers

## Status

Accepted. Реализация Pod — впереди; канон = Pod, не object-ws-as-container.

## Decisions

1. BC **ProjectContainer** + `ContainerRuntimePort` — единственный k8s writer для project sandboxes.
2. Runtime = **per-project Pod** + NetworkPolicy (internet egress only).
3. Workspace SoT = **MinIO**; Pod получает hydrate при start.
4. **Pause = удалить Pod**, файлы в MinIO; **Resume = новый Pod + hydrate**. Не держать paused Pod ради экономии RAM.
5. Product cascade: всегда через **Project**, затем Port.
6. Cabinet bundle / starter catalog ≠ Container.
7. Отставание кода (только object-ws) = долг, не «канон transitional».

## Consequences

- ORM `project_containers`, Port, overlay NetworkPolicy, sandbox image.
- `pause_container` no-op в коде — заменить реализацией Port.pause.
- Admin UI уже может list/proxy Project; metrics/force-kill — после Pod.
