# Alias resolve flow

```text
GET /api/v1/content/aliases/{slug}/resolve
  1. AliasService.get_by_slug(slug)
  2. AccessPolicyService.require_read_alias(principal, alias)
  3. AliasBindingService.get_current(alias_id)
       → if no binding: 404 Not Ready
  4. AccessPolicyService.require_read_asset(principal, asset)
  5. AssetService.resolve_blob_version(asset, pinned_version)
  6. FileStoreManager.presign_get(storage_key, ttl=15m)
  7. 302 Redirect
```

Alias ACL и Asset ACL проверяются **раздельно** — grant на alias не даёт автоматически read на blob.

Presign TTL по умолчанию 900s; настраивается на begin_version.
