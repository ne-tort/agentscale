# Dynamic app React host

`index.ts` exports a router-independent React integration surface:

- `DynamicRouteHost` resolves `pathname` through the existing `DynamicRegistryController` and signed snapshot verifier, then renders the resolved root from the verified route. The caller owns route registration and supplies an untrusted `snapshotHint` only for route-pointer lookup.
- `DynamicReactHost` renders a supplied `RegistryClientResolution`. It revalidates the closed manifest, requires a matching `CapabilityBoundary`, and binds table/action controls only to host-owned operation mappings.
- `DynamicCodePluginHost` creates an opaque sandboxed iframe for a registry-selected immutable plugin artifact and mediates calls through the supplied broker policy.

Example caller-owned composition:

```tsx
<DynamicRouteHost
  registry={registryController}
  pathname={location.pathname}
  snapshotHint={signedSnapshotHint}
  context={requestContext}
  boundary={capabilityBoundary}
  gateway={capabilityGateway}
  bindings={{ tables: hostQueries, actions: hostCommands }}
  enabled={!dynamicKillSwitch}
  onRecover={openDynamicAppDiagnostics}
/>
```

Bindings are trusted host configuration, not manifest data. `DeclarativeTableBinding` provides one registered query method, visible columns, and a context-to-params builder. The renderer bounds params/results, rejects duplicate row IDs, searches and pages the bounded returned rows locally, exposes refresh/retry, and aborts a query when its component unmounts. `DeclarativeActionBinding` maps one button to a registered method/capability and a specific list of input node IDs. Required inputs are checked before invocation; cancellation aborts its signal and preserves the form draft. The capability gateway and backend remain authoritative on every call.

The manifest's requested capabilities are not grants. The host requires the capability to be declared, present in the matching effective boundary, unexpired, and permitted for the exact method. The gateway rechecks authorization. Missing bindings or grants render explicit unavailable states and send no operation.

## Plugin limitations

The iframe uses `sandbox="allow-scripts"`, `referrerpolicy="no-referrer"`, a registry-selected HTTPS origin, an opaque-origin WindowProxy/nonce/identity handshake, closed protocol envelopes, bounded message size and concurrency, and broker-mediated RPC. `plugin.ready` must match plugin id, version, digest, instance and nonce. The kill switch aborts in-flight operations and destroys the iframe.

The host does not fetch or compile plugin source. `artifact.uri` must already be a signed, immutable registry URL with an allowlisted deployment origin, and its digest must match the resolved registry digest. The browser cannot independently prove that an HTTP server returns the same bytes on a later iframe navigation; deployment immutability, artifact verification and CSP are upstream responsibilities. RPC capability versions must be supplied by the host and match each method's declared single required capability. Incompatible/missing declarations fail closed.

## Integration blockers

- This host is an importable surface; the shared `AppRouter` was deliberately not changed. Callers choose where to mount it.
- The backend code inspected exposes service/port contracts, but no registered browser route for registry snapshots or plugin invocation was found in the API source. No HTTP endpoint was invented. `RegistryFetcher` and `CapabilityGatewayTransport` remain caller-provided adapters to the eventual registered transport.
- The API plugin gateway's method declaration (`method/capability/operation/resource/domain`) does not match the shared declarative protocol's `PluginMethod` (`name/requiredCapabilities`). The React host supports only a host-supplied explicit mapping where each method has one required capability; multi-capability methods remain unavailable pending a canonical contract.
- The plugin deployment must support the documented handshake at its approved isolated origin. The host rejects unexpected origin, identity, protocol, navigation and oversized messages.
- Test fixtures are authored under this zone. No tests, builds, compilation, lint, format, or Git commands were run due to migration-stage verification freeze.
