# React company/admin contours

This feature folder contains the React replacement surfaces for the company and platform-admin contours. It deliberately owns its API adapter and UI primitives so the shared client, context, router and styles stay unchanged.

## Confirmed API coverage

- Company: employees, contact email/password updates, company cabinet list, cabinet assignments, company AI keys and company AI models.
- Admin: companies, cabinet instances, modules, the `ai.http_providers` reference catalog, company metrics, metric series, counter rebuild and recycle-bin restore/purge.
- Destructive actions use an inline confirmation. Platform provider entries are archived through the confirmed catalog delete endpoint; seeded entries are not given a separate imaginary provider endpoint.
- Unavailable workflows are omitted from forms. The admin company form edits only the confirmed patch fields and cabinet quota form sends only the confirmed quota body.

`createContourService` accepts an injected fetcher for tests and uses `/api/v1` by default. Mounting these surfaces into the application router is intentionally left to the owning routing task.
