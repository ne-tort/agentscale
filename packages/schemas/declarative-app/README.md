# Declarative App Protocol

Versioned contract for platform-hosted declarative applications and isolated code plugins.

`v1/types.ts` is dependency-free TypeScript and mirrors the wire schemas:

- `schema.json` describes app manifests, declarative UI trees, isolated plugin artifacts, requested capabilities, quotas, and lifecycle policy.
- `protocol.json` describes capability grants, app lifecycle events, and request/response/error/notification RPC messages.
- `v1/index.ts` is the TypeScript export entrypoint.

The host must treat `requestedCapabilities` as untrusted input. Effective access is the intersection of requested capabilities and the issued `GrantSet`; a plugin must not receive a capability merely because it was declared in its manifest.
