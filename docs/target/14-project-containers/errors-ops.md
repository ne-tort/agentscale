# ProjectContainer — errors & ops

| Class | Примеры | Response |
|-------|---------|----------|
| Transient | ImagePull blip, Evicted | retry; `last_error` |
| Config | bad image / NetworkPolicy | `failed`; fix profile |
| Stuck | Terminating > N min | `force_kill` |
| Zombie | orphan Pod | reconcile reap |
| Drift | Project paused, Pod Running | Port.pause |

**Force kill:** grace=0; audit `container.force_killed`; не auto-resume Project.  
Blobs в MinIO force-kill сам не трогает (только compute), кроме явного Project delete wipe.
