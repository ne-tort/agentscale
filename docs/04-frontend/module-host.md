# Flutter Module Host

Platform shell hosts cabinet UI modules declared in `cabinet-profile.json` → `GET …/manifest`.

## Shell responsibilities

- Auth, profile, admin vs operator split
- Cabinet switch / project switch
- Load `manifest.ui.navigation` and `manifest.ui.projectTabs`
- Provide `AppScope` + `FeatureGate` from capabilities
- Route to module screens by `route` / tab id

## Shell must not

- Hard-code profile id (`electronics-procurement`)
- Hard-code labels «КП», «S4B» without capability/tab from manifest
- Embed domain widgets that another cabinet cannot replace

## Manifest UI contract (v1)

```json
{
  "navigation": [
    { "id": "projects", "label": "Проекты", "icon": "folder", "route": "/projects" }
  ],
  "projectTabs": ["chat", "specs", "variants", "kp"],
  "modules": {
    "kp_export": { "type": "kp-export", "enabled": true }
  },
  "theme": "system"
}
```

### Built-in tab handlers (platform or cabinet)

| Tab id | Owner | Widget |
|--------|-------|--------|
| `chat` | Platform (agent) | Agent chat placeholder / stream |
| `specs` | Cabinet (if capability) | Specs / inbox / runs |
| `variants` | Cabinet | Variants list |
| `kp` | Cabinet | KP export |
| `equipment` | Cabinet | Equipment cards |
| `catalogs` | Cabinet or platform catalogs facade | Catalog upload |

Unknown tabs → empty state «модуль не зарегистрирован».

## Profile picker

Creating a cabinet: `GET /cabinet-profiles` → user picks profile → `POST /cabinets` with chosen `profile_id`.
