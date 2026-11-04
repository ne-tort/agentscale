"""MIG-AUTH-AUDIT-01 — static authorization contract for every HTTP route.

This package audits, without a database and without sending a request, two
properties that functional tests cannot see because they always call the API
with a valid token:

1. **every route is authenticated** unless it appears in an explicit, justified
   allow-list of public routes;
2. **the tenant is taken from the server context**, not from a client-supplied
   ``tenant_id`` / ``company_id`` selector that no reachable guard validates.

The analysis is static in the *structural* sense (it never executes a handler):
the route inventory comes from the live FastAPI object graph, and the guard
search reads the handler's syntax tree. That split is deliberate — the route
list must be the real one (a route registered through ``include_router`` with a
prefix is invisible to a file scan), while a guard must be provable by reading
code (an executed handler would need a database).
"""
