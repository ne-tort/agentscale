# Project Containers — errors & ops

## Классы сбоев

| Class | Примеры | Default response |
|-------|---------|------------------|
| **Transient** | ImagePullBackOff (registry blip), Evicted | retry with backoff; surface `last_error` |
| **Config** | Image not found, invalid NetworkPolicy | leave `failed`; admin must fix profile |
| **Stuck** | Terminating > N min; finalizer hang | `force_kill` (grace 0) |
| **Zombie** | Orphan pod | reap via reconcile |
| **Drift** | Project paused, pod Running | reconcile → pause |

## Force kill

1. Admin / reconcile вызывает `ContainerRuntimePort.force_kill`.
2. Delete pod with `gracePeriodSeconds=0`.
3. Clear/update `runtime_ref`; status `paused` or `failed` per policy.
4. Audit `container.force_killed` with project_id, container_id, actor.
5. **Не** auto-resume Project.

## Admin actions (detail)

| Action | Maps to |
|--------|---------|
| Pause | Project.pause → Port.pause |
| Resume | Project.resume (key gate) → Port.start |
| Delete project | Project.delete → Port.delete |
| Force kill | Port.force_kill only |
| Reconcile | Port.reconcile(project_id) |

## Observability

- Platform events / audit for force-kill, zombie reap, ensure_created failures.
- `last_error` on entity for UI banner (severity warning, без «тупых» эссе).
- Logs: container-id + project-id correlation IDs.

## Safety

- Force kill не удаляет MinIO blobs сам по себе (только compute), кроме явного Project delete purge.
- Reconcile идемпотентен; не спамит delete на missing pods.
