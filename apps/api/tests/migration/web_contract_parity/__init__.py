"""Cross-layer contract parity between the web client and the live HTTP API.

vitest mocks ``fetch`` and pytest never loads a browser, so neither side alone can
notice that a path, a field name or an error status drifted. These tests take both
sides from their real sources:

* the server side from the registered FastAPI routes (``app.openapi()``) and from
  the serializers/pydantic models that actually produce the bytes;
* the web side from the TypeScript sources under ``apps/web/src`` (text parsing —
  the browser bundle is not built here).

See ``README.md`` for the divergences this suite found and which side was fixed.
"""
