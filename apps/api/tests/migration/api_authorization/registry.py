"""The reviewed authorization registry: what is public, and what is a known gap.

Two tables, both of which must match the running application exactly:

:data:`PUBLIC_ROUTES`
    every route that deliberately answers without an authenticated principal.
    Every entry carries the reason it is safe, and the reason is a *test
    assertion*: a public route whose justification no longer matches its code
    fails the contract, so a route cannot quietly become public because someone
    added it to this file.

:data:`UNVALIDATED_TENANT_SELECTORS`
    tenant selectors a non-admin handler takes from the request and uses without
    a guard from :mod:`route_inventory`. Each entry is a **finding** (see
    ``findings.md`` next to this package) — the route owner decides whether to
    close it. Listing an entry here does not make it correct; it records that
    the gap was reviewed and is still open, and it makes the gap disappear from
    this table (and so fail the test) the moment the handler starts validating.
"""

from __future__ import annotations

from dataclasses import dataclass

from .route_inventory import RouteRecord


@dataclass(frozen=True, slots=True)
class PublicRoute:
    """One reviewer-approved anonymous route.

    ``justification`` is prose for a human; ``expects`` is what the test checks
    mechanically, so the prose cannot drift away from the code:

    ``"exact"``
        the route key is listed verbatim (a fixed path);
    ``"prefix"``
        the entry covers every path equal to ``path`` or below it. ``path`` must
        therefore end where a segment ends: ``"/api/v1/auth"`` covers
        ``/api/v1/auth/login`` but not ``/api/v1/authentication``.
    """

    method: str
    path: str
    match: str
    justification: str
    route_id: str = ""

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"

    def covers(self, record: RouteRecord) -> bool:
        if record.method != self.method:
            return False
        if self.match == "prefix":
            return record.path == self.path or record.path.startswith(self.path + "/")
        return record.path == self.path


#: Routes allowed to answer without an authenticated principal.
#:
#: Rules a new entry must satisfy, in order of preference:
#:   1. it establishes a credential (login / refresh / broker callback), or
#:   2. it reports whether the process can serve traffic (probes), or
#:   3. it authenticates the *caller's own* request by another means (HMAC),
#:      and that means is verified on the server before any tenant row is read.
#:
#: Anything else — "the UI calls it before login", "it only returns metadata" —
#: is not a justification; such a route gets authenticated.
PUBLIC_ROUTES: tuple[PublicRoute, ...] = (
    # ---- platform / probe surface -------------------------------------------
    PublicRoute(
        method="GET",
        path="/health",
        match="prefix",
        justification=(
            "k3s + client liveness/smoke. Mounted twice on purpose: bare ``/health*`` "
            "for the probes and ``/api/v1/health*`` for versioned clients (see "
            "``main.create_app``). Returns build metadata and readiness only; it "
            "reads no tenant row, so it cannot leak one tenant to another."
        ),
    ),
    PublicRoute(
        method="GET",
        path="/api/v1/health",
        match="prefix",
        justification=(
            "The versioned twin of the probe surface (``v1.router`` mounts the same "
            "``health.router`` under ``settings.api_v1_prefix``). Same payload, same "
            "no-tenant guarantee."
        ),
    ),
    PublicRoute(
        method="GET",
        path="/docs",
        match="prefix",
        justification=(
            "FastAPI's own Swagger UI. It serves static HTML and the schema already "
            "published at ``/openapi.json``; no handler runs, so there is no tenant "
            "data behind it."
        ),
    ),
    PublicRoute(
        method="GET",
        path="/redoc",
        match="exact",
        justification=(
            "FastAPI's own ReDoc viewer — the same reasoning as ``/docs``: static "
            "HTML over the public schema."
        ),
    ),
    PublicRoute(
        method="GET",
        path="/openapi.json",
        match="exact",
        justification=(
            "The schema itself. Deliberately public: the OpenAPI document is the "
            "contract clients (and this audit) read, and it describes routes rather "
            "than their data. Note it is public by framework default — if the "
            "surface ever needs to be hidden, this entry must be removed *and* "
            "``docs_url``/``openapi_url`` disabled in ``create_app``."
        ),
    ),
    # ---- authentication surface --------------------------------------------
    PublicRoute(
        method="GET",
        path="/api/v1/auth",
        match="prefix",
        justification=(
            "Discovery and status half of the auth surface, reachable before the "
            "caller holds any credential — that is its purpose. ``config`` returns "
            "``AuthService.public_config`` (by construction no issuer/token/revoke "
            "URLs), ``health`` reports only whether the identity provider answers, "
            "and ``broker/{idp}/start`` plus ``broker/callback`` are the OIDC "
            "round trip itself. None of them reads a tenant row."
        ),
    ),
    PublicRoute(
        method="POST",
        path="/api/v1/auth",
        match="prefix",
        justification=(
            "Credential-establishing half of the auth surface: ``login``, "
            "``refresh`` and ``logout`` take the credential *in the body* and "
            "answer from the identity provider. They must work before a bearer "
            "token exists, and they read no tenant row — the session they return "
            "is the one the caller just proved they own."
        ),
    ),
    # ---- non-browser ingress with its own credential ------------------------
    PublicRoute(
        method="POST",
        path="/api/v1/projects/{project_id}/webhooks/http",
        match="exact",
        justification=(
            "External ``webhook.http`` trigger ingress. The caller authenticates "
            "with an HMAC-SHA256 over the raw body in ``X-Prodavan-Signature``, "
            "verified server-side (``domain.projects.webhook_hmac."
            "verify_webhook_signature`` via ``projects.signed_ingress."
            "enqueue_signed_trigger``) against the *company's* secret for the "
            "*routed* project before any row is written. A missing project and a "
            "missing secret collapse to one generic 404 (audit API-P2d), so the "
            "route cannot be used to enumerate project ids. The secret therefore "
            "is the tenant boundary and it is checked per request."
        ),
    ),
    PublicRoute(
        method="POST",
        path="/api/v1/projects/{project_id}/webhooks/telegram",
        match="exact",
        justification=(
            "Same transport as the HTTP webhook, with the company's "
            "``telegram_hmac_secret`` as the key. Telegram cannot present a bearer "
            "token, so an HMAC over the raw body is the only credential available; "
            "it is verified before the payload is queued."
        ),
    ),
    # ---- stub marker --------------------------------------------------------
    PublicRoute(
        method="GET",
        path="/api/v1/stub",
        match="exact",
        justification=(
            "``api/v1/stub.py`` returns a fixed marker string (``status: stub``) "
            "with no input, no session and no tenant read. Public so the smoke "
            "probe can tell a stub deployment from a real one. If a handler ever "
            "grows a payload here, this entry must be deleted with it."
        ),
    ),
)


#: How each public route's justification is checked against the code.
#:
#: Keyed by :attr:`PublicRoute.key`; the value is the ``module:function`` — or,
#: for a prefix entry, the ``module:`` — FastAPI reports for the handler. A
#: registered public route must still be served by that code: if the handler is
#: moved or renamed, the justification above describes something that no longer
#: exists and the test says so instead of silently trusting a stale comment.
PUBLIC_ROUTE_HANDLERS: dict[str, str] = {
    "GET /health": "prodavan.api.v1.health:",
    "GET /api/v1/health": "prodavan.api.v1.health:",
    "GET /docs": "fastapi.applications:swagger_ui_",
    "GET /redoc": "fastapi.applications:redoc_html",
    "GET /openapi.json": "fastapi.applications:openapi",
    "GET /api/v1/auth": "prodavan.api.v1.auth:",
    "POST /api/v1/auth": "prodavan.api.v1.auth:",    "POST /api/v1/projects/{project_id}/webhooks/http": (
        "prodavan.api.v1.projects:ingress_signed_webhook"
    ),
    "POST /api/v1/projects/{project_id}/webhooks/telegram": (
        "prodavan.api.v1.projects:ingress_signed_telegram"
    ),
    "GET /api/v1/stub": "prodavan.api.v1.stub:stub_info",
}


# --------------------------------------------------------------------------- #
# reviewed, still-open gaps in tenant validation
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TenantSelectorGap:
    """A tenant selector a handler accepts and uses without a reachable guard.

    Not an allow-list: the contract test fails the moment a *new* gap appears,
    and fails again the moment a listed one is closed (so the entry cannot
    outlive the defect). ``finding`` names the section in ``findings.md``.
    """

    method: str
    path: str
    selector: str
    finding: str

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"


#: Non-platform routes that take ``company_id``/``owner_company_id``/… from the
#: query string or a body model and act on it without a guard this audit can
#: find. Severity is in ``findings.md``.
UNVALIDATED_TENANT_SELECTORS: tuple[TenantSelectorGap, ...] = (
    # -- admin surface: authenticated by ``require_platform_admin`` ----------
    TenantSelectorGap("POST", "/api/v1/admin/ai-keys", "body.company_ids", "F-01"),
    # Different failure mode: a guard *is* called, but on a path that skips it.
    # Recorded as its own finding so the bypass is not averaged in with "the
    # admin may do this anyway".
    TenantSelectorGap(
        "PUT", "/api/v1/admin/ai-keys/{key_id}/companies", "body.company_ids", "F-05"
    ),
    TenantSelectorGap("POST", "/api/v1/admin/ai-models", "body.owner_company_id", "F-01"),
    TenantSelectorGap("POST", "/api/v1/admin/cabinets", "body.company_id", "F-01"),
    TenantSelectorGap("POST", "/api/v1/admin/cabinets", "body.company_ids", "F-01"),
    TenantSelectorGap("PATCH", "/api/v1/admin/cabinets/{cabinet_id}", "body.company_id", "F-01"),
    TenantSelectorGap(
        "PATCH", "/api/v1/admin/cabinets/{cabinet_id}", "body.company_ids", "F-01"
    ),
    TenantSelectorGap("PATCH", "/api/v1/admin/modules/{module_id}", "body.company_ids", "F-01"),
    TenantSelectorGap("GET", "/api/v1/admin/platform-events", "company_id", "F-01"),
    TenantSelectorGap(
        "POST", "/api/v1/admin/search-index/reconcile-equipment", "company_id", "F-01"
    ),
    # -- admin document-store / search-index: ``company_id`` scope argument ---
    TenantSelectorGap("POST", "/api/v1/admin/document-store/upsert", "body.company_id", "F-02"),
    TenantSelectorGap("POST", "/api/v1/admin/document-store/get", "body.company_id", "F-02"),
    TenantSelectorGap("POST", "/api/v1/admin/document-store/find", "body.company_id", "F-02"),
    TenantSelectorGap("POST", "/api/v1/admin/document-store/delete", "body.company_id", "F-02"),
    TenantSelectorGap(
        "POST", "/api/v1/admin/document-store/ensure-indexes", "body.company_id", "F-02"
    ),
    TenantSelectorGap("POST", "/api/v1/admin/search-index/indexes/ensure", "body.company_id", "F-02"),
    TenantSelectorGap("POST", "/api/v1/admin/search-index/indexes/delete", "body.company_id", "F-02"),
    TenantSelectorGap(
        "POST", "/api/v1/admin/search-index/documents/index", "body.company_id", "F-02"
    ),
    TenantSelectorGap(
        "POST", "/api/v1/admin/search-index/documents/bulk", "body.company_id", "F-02"
    ),
    TenantSelectorGap("POST", "/api/v1/admin/search-index/documents/get", "body.company_id", "F-02"),
    TenantSelectorGap(
        "POST", "/api/v1/admin/search-index/documents/delete", "body.company_id", "F-02"
    ),
    TenantSelectorGap("POST", "/api/v1/admin/search-index/search", "body.company_id", "F-02"),
)


#: Routes where a guard *is* reachable from the handler but does **not** validate
#: the selector — the reachability search cannot tell the difference, so the
#: caveat is written down instead of letting the route look protected.
#:
#: This is the honest limit of a syntactic audit: it sees that
#: ``require_access`` is called, not whether the call is reached or bypassed.
REACHABILITY_CAVEATS: dict[str, str] = {
    "PUT /api/v1/admin/ai-keys/{key_id}/companies": (
        "AiKeysService.set_companies -> cascade_company_key_revoked -> "
        "ProjectCommand.pause(..., skip_access=True). The ``require_access`` the "
        "index finds lives inside ``pause`` and is explicitly skipped at that call "
        "site; nothing validates the caller against ``body.company_ids``. The only "
        "check on each supplied id is existence (``_replace_bindings`` raises 422 "
        "for an unknown company), which is not authorization."
    ),
}


#: Project-scoped routes that reach a project access decision through a service
#: the audit follows *by hand* rather than by syntax.
#:
#: ``SourceIndex`` follows a call only when the callee receives the caller's
#: identity **and** the receiver resolves to a class it can see. Two legitimate
#: shapes stay invisible to it:
#:
#: * ``runtime.sessions.resolve(...)`` in ``web_stream.web_chat_stream`` — the
#:   runtime object is injected by ``install_web_stream_runtime``, so its type is
#:   not in the handler. The resolver implementation
#:   (``web_facade_adapter.sessions.SqlChatSessionResolver.resolve``) calls
#:   ``ProjectAccessPolicy.require_access`` before reading the session row.
#: * ``tenant: ResourceTenantDep`` in ``api/v1/resources.py`` — the tenant is a
#:   *dependency* (``resource_tenant``), not a call, so it is covered by
#:   ``TENANT_DEPENDENCIES`` rather than by the guard search.
#:
#: Every entry states where the check really happens. A route listed here is
#: still required to be authenticated; only the tenant proof is recorded as
#: indirect.
REVIEWED_INDIRECT_TENANT_PROOFS: dict[str, str] = {
    "POST /api/v1/web/projects/{project_id}/chat/sessions/{session_id}/stream": (
        "web_stream.web_chat_stream -> runtime.sessions.resolve -> "
        "SqlChatSessionResolver.resolve -> ProjectAccessPolicy.require_access "
        "(web_facade_adapter/sessions.py), before the session row is read"
    ),
}


__all__ = [
    "PUBLIC_ROUTES",
    "PUBLIC_ROUTE_HANDLERS",
    "PublicRoute",
    "REACHABILITY_CAVEATS",
    "REVIEWED_INDIRECT_TENANT_PROOFS",
    "TenantSelectorGap",
    "UNVALIDATED_TENANT_SELECTORS",
]
