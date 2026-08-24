## Summary

<!-- What changed and why (not a file list). -->

## Test plan

- [ ] `CI Gate` green (infra + api + flutter + schemas)
- [ ] If images changed: `CI Images` on main pushed `:latest` to GHCR
- [ ] If cluster deploy: smoke `http://prodavan.local:8088/` (`Host: prodavan.local`)
