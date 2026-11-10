# Declarative renderer

`renderer.ts` is the preserved, framework-free renderer for the accepted
`declarative-app/v1` schema. It validates the closed manifest through
`../schema.ts`, renders only allowlisted nodes as DOM text and keeps buttons
disabled until an accepted host supplies an operation path. Its focused source
fixtures live in `renderer.test.ts`.

The unused React host/barrel and the old registry-artifact iframe host/broker
were removed after a whole-web-source reference audit. They were not imported
by the shell router and implemented a handshake/envelope different from the
current plugin contract. Do not treat their former documentation or test
fixtures as the current integration surface.

## Current plugin bootstrap/RPC path

The current shell path is
[`main.tsx`](../../main.tsx) → [`app/router.tsx`](../../app/router.tsx) →
[`app/plugin-routes.tsx`](../../app/plugin-routes.tsx) →
[`PluginPage`](../../features/plugins/PluginPage.tsx). The page obtains signed
bootstrap data through [`pluginInfo.ts`](../../features/plugins/pluginInfo.ts),
builds the frontend URL with [`bootstrap.ts`](../../features/plugins/bootstrap.ts),
and mounts [`PluginFrame`](../../features/plugins/PluginFrame.tsx) with the
current [`rpc.ts`](../../features/plugins/rpc.ts) envelope.

[`legacy-host-import-graph.test.ts`](../legacy-host-import-graph.test.ts)
checks this path statically from `main.tsx` and rejects imports of the removed
legacy modules. This is a source-graph guard, not live plugin acceptance; the
platform remains subject to the STOP-SHIP status in
[`docs/migration/plugin-migration-status.md`](../../../../../docs/migration/plugin-migration-status.md).

No tests, builds, compilation, lint, formatting or Git checks were run for this
cleanup under the migration-stage verification freeze.
