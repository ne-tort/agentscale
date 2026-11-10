"""Stateful IO-only boundaries for offline production composition tests.

No application service, repository, grant, publication, Deployment or quota
outcome is fabricated here. Tests intentionally remain unexecuted during the
migration verification freeze.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from sqlalchemy import DateTime, event
from sqlalchemy.dialects.sqlite.base import SQLiteCompiler
from sqlalchemy.types import TypeDecorator
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from prodavan.application.plugins.artifacts.publication import (
    ArtifactPublicationCommand,
    PublicationEvidence,
)
from prodavan.application.plugins.artifacts.verification import VerificationResult
from prodavan.application.plugins.sources.service import ModuleSourceProvisioningConfig
from prodavan.application.plugins.sources.types import ModuleSource, ModuleSourceStatus
from prodavan.application.plugins.tenantdb.service import ExecuteResult, StoredSecret
from prodavan.infrastructure.persistence.models import _sqlite_jsonb_deep_ne
from prodavan.infrastructure.persistence.models.ai_keys import (  # noqa: F401
    AiProviderKeyRow,
)
from prodavan.infrastructure.persistence.models.artifact_lifecycle import (  # noqa: F401
    PluginArtifactLifecycleRow,
)
from prodavan.infrastructure.persistence.models.base import Base
from prodavan.infrastructure.persistence.models.identity import (  # noqa: F401
    CompanyRow,
    EmployeeRow,
    IdentityLinkRow,
    MembershipRow,
)
from prodavan.infrastructure.persistence.models.plugin_platform import (  # noqa: F401
    CapabilityGrantRow,
    PluginArtifactRow,
    PluginAuditEventRow,
    PluginEventDeliveryRow,
    PluginFeatureFlagRow,
    PluginInstanceRow,
    PluginSourceReservationRow,
    ModuleSourceRow,
)
from prodavan.infrastructure.persistence.models.plugin_routes import (  # noqa: F401
    PluginRouteRow,
)
from prodavan.infrastructure.persistence.models.publication_approvals import (  # noqa: F401
    PluginPublicationApprovalRow,
)
from prodavan.infrastructure.plugins.runtime.k8s_scale import (
    K8sApiConflict,
    K8sApiError,
    K8sApiNotFound,
)
from prodavan.infrastructure.plugins.sources.github_app import GitHubAppClient
from prodavan.infrastructure.plugins.sources.models import (
    BranchProtectionPolicy,
    ManagedOrganization,
    RepositoryTemplate,
)

COMPANY_ID = "co_acceptance"
PLUGIN_ID = "hello_plugin"
VERSION = "0.1.0"
ORGANIZATION = "managed-plugins"
INSTALLATION_ID = 42
SOURCE_ID = "src_hello_plugin"
REPO_ID = 91001
COMMIT_SHA = "a" * 40
RUN_ID = "123456789"
RUN_ATTEMPT = "1"
BUNDLE_REPOSITORY = f"{ORGANIZATION}/{PLUGIN_ID}"
BACKEND_REPOSITORY = f"{ORGANIZATION}/{PLUGIN_ID}-backend"
BACKEND_DIGEST = "sha256:" + "b" * 64
BACKEND_REF = f"ghcr.io/{BACKEND_REPOSITORY}@{BACKEND_DIGEST}"
ISSUER = "https://token.actions.githubusercontent.com"
TRUSTED_WORKFLOW_REF = (
    "ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@" + "c" * 40
)
SIGNER_IDENTITY = f"https://github.com/{TRUSTED_WORKFLOW_REF}"
PRIVATE_KEY_REF = "secret://agentscale/github-app-private-key"
GATEWAY_URL = "http://plugin-gateway.agentscale-dev.svc:8080"
NAMESPACE = "agentscale-dev"
FIXED_TIME = 1_800_000_000.0
NOW = datetime.fromtimestamp(FIXED_TIME, tz=UTC)
QUOTAS = {
    "ops_per_minute": 17,
    "max_rows": 101,
    "max_bytes": 1_000_001,
    "max_keys": 303,
    "max_object_bytes": 5_000_001,
    "max_egress_bytes": 7_000_001,
}
CAPABILITIES = (
    "cache",
    "objects",
    "docs",
    "events",
    "jobs",
    "pods",
    "mcp",
    "secrets",
    "network",
)


def source_config() -> ModuleSourceProvisioningConfig:
    organization = ManagedOrganization(
        name=ORGANIZATION,
        installation_id=INSTALLATION_ID,
        template=RepositoryTemplate(
            owner=ORGANIZATION,
            name="private-plugin-template",
            agents_revision="d" * 40,
            security_revision="e" * 40,
        ),
    )
    from prodavan.application.plugins.sources.types import ModuleSourceTrustConfig

    return ModuleSourceProvisioningConfig(
        organization=organization,
        trust=ModuleSourceTrustConfig(
            installation_credential_ref=PRIVATE_KEY_REF,
            trusted_workflow_ref=TRUSTED_WORKFLOW_REF,
        ),
        branch_protection=BranchProtectionPolicy(
            required_status_checks=("plugin-verify", "plugin-security"),
            required_approving_review_count=1,
        ),
        default_branch="main",
    )


def ready_source() -> ModuleSource:
    config = source_config()
    return ModuleSource(
        id=SOURCE_ID,
        company_id=COMPANY_ID,
        plugin_id=PLUGIN_ID,
        provider="github_app",
        repo_id=REPO_ID,
        full_name=f"{ORGANIZATION}/{PLUGIN_ID}",
        private=True,
        default_branch="main",
        installation_credential_ref=config.trust.installation_credential_ref,
        trusted_workflow_ref=config.trust.trusted_workflow_ref,
        status=ModuleSourceStatus.READY,
        created_at=NOW,
        updated_at=NOW,
    )


def app_settings(*, source: bool = False, platform: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        app_version="production-composition-test",
        api_v1_prefix="/api/v1",
        cors_origin_list=[],
        plugin_platform_enabled=platform,
        plugin_module_source_enabled=source,
        plugin_claims_secret="composition-test-signing-key-not-a-live-secret",
        plugin_event_signing_secret_ref="vault://composition/plugin-event-signing",
        plugin_claims_ttl_seconds=300,
        plugin_claims_issuer="agentscale-plugins",
        plugin_claims_audience="capability-gateway",
        plugin_claims_previous_secrets="",
        plugin_static_origin="https://plugins.example.test",
        plugin_shell_origin="https://agentscale.example.test",
        plugin_tenant_db_admin_url=(
            "postgresql://tenant_admin:test-only@tenant-postgres:5432/postgres"
            "?sslmode=require"
        ),
        plugin_tenant_db_host="tenant-postgres",
        plugin_tenant_db_port=5432,
        plugin_egress_mode="none",
        plugin_egress_approved=False,
        plugin_idle_ttl_seconds=300,
        plugin_ready_timeout_seconds=120,
        plugin_max_concurrent_wakes=2,
        plugin_oci_registry=f"ghcr.io/{ORGANIZATION}",
        plugin_cosign_identity=SIGNER_IDENTITY,
        plugin_cosign_issuer=ISSUER,
        plugin_min_platform_version="4.0.0",
    )


class StatefulVaultBoundary:
    """One ref-keyed secret backend shared by tenant/webhook/GitHub adapters."""

    def __init__(self) -> None:
        self._values: dict[str, str] = {}
        self._refs: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []

    def seed(self, reference: str, value: str) -> None:
        self._values[reference] = value

    @staticmethod
    def _file_ref(key: str) -> str:
        return f"file://ai_keys/{key}.secret"

    def _read_reference(self, reference: str) -> str:
        if reference not in self._values:
            error = LookupError("secret not found")
            setattr(error, "code", "SECRET_NOT_FOUND")
            raise error
        return self._values[reference]

    async def read_secret(self, reference: str) -> str:
        self.calls.append(("read_secret", reference))
        return self._read_reference(reference)

    async def get(self, *, key: str) -> StoredSecret | None:
        self.calls.append(("get", key))
        reference = self._refs.get(key, self._file_ref(key))
        if reference not in self._values:
            return None
        return StoredSecret(secret_ref=reference, value=self._values[reference])

    async def put(self, *, key: str, value: str) -> str:
        self.calls.append(("put", key))
        reference = self._refs.get(key, self._file_ref(key))
        self._refs[key] = reference
        self._values[reference] = value
        return reference

    async def rotate(self, *, key: str, value: str) -> str:
        self.calls.append(("rotate", key))
        if not value:
            reference = self._refs.get(key, self._file_ref(key))
            self._values.pop(reference, None)
            return ""
        return await self.put(key=key, value=value)

    async def delete(self, *, secret_ref: str) -> None:
        self.calls.append(("delete", secret_ref))
        self._values.pop(secret_ref, None)

    def ref_for(self, key: str) -> str:
        return self._refs.get(key, self._file_ref(key))


class StatefulAsyncpgAdmin:
    """DDL-state boundary; raw SQL/passwords are never retained or exposed."""

    def __init__(self) -> None:
        self.databases: set[str] = set()
        self.roles: dict[str, bool] = {}
        self.connect_grants: set[tuple[str, str]] = set()
        self.schemas: set[tuple[str, str]] = set()
        self.operations: list[tuple[str, str]] = []
        self.opened = False

    @staticmethod
    def _ident(text: str) -> str:
        match = re.search(r'"([a-z0-9_]+)"', text)
        if match is None:
            raise AssertionError("DDL did not contain a safe quoted identifier")
        return match.group(1)

    def _record(self, operation: str, identifier: str = "") -> None:
        self.operations.append((operation, identifier))

    async def open(self) -> None:
        self.opened = True

    async def close(self) -> None:
        self.opened = False

    async def health(self) -> bool:
        return self.opened

    async def execute(self, text: str, params: Mapping[str, Any] | None = None, *, target: Any) -> ExecuteResult:
        values = dict(params or {})
        upper = " ".join(text.strip().upper().split())
        if "ACLEXPLODE" in upper:
            key = (str(values.get("name", "")), str(values.get("role", "")))
            self._record("connect_granted", ":".join(key))
            return ExecuteResult(rows=((1,),) if key in self.connect_grants else ())
        if "FROM PG_DATABASE" in upper and "DATNAME" in upper:
            name = str(values.get("name", ""))
            self._record("database_exists", name)
            return ExecuteResult(rows=((1,),) if name in self.databases else ())
        if "FROM PG_ROLES" in upper and "ROLCANLOGIN" in upper:
            name = str(values.get("rolname", ""))
            self._record("role_login_enabled", name)
            return ExecuteResult(rows=((1,),) if self.roles.get(name) is True else ())
        if "FROM PG_ROLES" in upper:
            name = str(values.get("rolname", ""))
            self._record("role_exists", name)
            return ExecuteResult(rows=((1,),) if name in self.roles else ())
        if "INFORMATION_SCHEMA.SCHEMATA" in upper:
            database = str(getattr(target, "database", ""))
            key = (database, str(values.get("schema", "")))
            self._record("schema_exists", ":".join(key))
            return ExecuteResult(rows=((1,),) if key in self.schemas else ())
        if upper.startswith("CREATE DATABASE"):
            name = self._ident(text)
            self.databases.add(name)
            self._record("create_database", name)
        elif upper.startswith("CREATE ROLE"):
            name = self._ident(text)
            self.roles[name] = True
            self._record("create_role", name)
        elif upper.startswith("ALTER ROLE") and " NOLOGIN" in upper:
            name = self._ident(text)
            self.roles[name] = False
            self._record("role_no_login", name)
        elif upper.startswith("ALTER ROLE") and " LOGIN" in upper:
            name = self._ident(text)
            self.roles[name] = True
            self._record("role_login", name)
        elif upper.startswith("GRANT CONNECT"):
            database = self._ident(text.split(" DATABASE ", 1)[1])
            role = self._ident(text.rsplit(" TO ", 1)[1])
            self.connect_grants.add((database, role))
            self._record("grant_connect", f"{database}:{role}")
        elif upper.startswith("REVOKE CONNECT"):
            database = self._ident(text.split(" DATABASE ", 1)[1])
            role = self._ident(text.rsplit(" FROM ", 1)[1])
            self.connect_grants.discard((database, role))
            self._record("revoke_connect", f"{database}:{role}")
        elif upper.startswith("CREATE SCHEMA"):
            database = str(getattr(target, "database", ""))
            schema = self._ident(text)
            self.schemas.add((database, schema))
            self._record("create_schema", f"{database}:{schema}")
        elif upper.startswith("DROP SCHEMA"):
            database = str(getattr(target, "database", ""))
            schema = self._ident(text)
            self.schemas.discard((database, schema))
            self._record("drop_schema", f"{database}:{schema}")
        elif upper.startswith("DROP ROLE"):
            name = self._ident(text)
            self.roles.pop(name, None)
            self.connect_grants = {item for item in self.connect_grants if item[1] != name}
            self._record("drop_role", name)
        elif upper.startswith("DROP DATABASE"):
            name = self._ident(text)
            self.databases.discard(name)
            self.schemas = {item for item in self.schemas if item[0] != name}
            self.connect_grants = {item for item in self.connect_grants if item[0] != name}
            self._record("drop_database", name)
        else:
            self._record("ddl_confirmed")
        return ExecuteResult()


class StatefulAsyncpgPool:
    """Driver-level pool used by the real AsyncpgAdminConnection."""

    def __init__(self, state: StatefulAsyncpgAdmin, database: str) -> None:
        self.state = state
        self.database = database
        self.closed = False

    def acquire(self, *, timeout: float) -> "StatefulAsyncpgPool":
        del timeout
        if self.closed:
            raise ConnectionError("pool is closed")
        return self

    async def __aenter__(self) -> "StatefulAsyncpgPool":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        return None

    def _params(self, query: str, args: tuple[Any, ...]) -> dict[str, Any]:
        upper = " ".join(query.strip().upper().split())
        if "ACLEXPLODE" in upper:
            return {"name": args[0], "role": args[1]}
        if "FROM PG_DATABASE" in upper:
            return {"name": args[0]}
        if "FROM PG_ROLES" in upper:
            return {"rolname": args[0]}
        if "INFORMATION_SCHEMA.SCHEMATA" in upper:
            return {"schema": args[0]}
        # DDL parameters are passwords. They are deliberately never retained.
        return {}

    async def fetch(self, query: str, *args: Any, timeout: float) -> list[tuple[Any, ...]]:
        del timeout
        result = await self.state.execute(
            query,
            self._params(query, args),
            target=SimpleNamespace(database=self.database),
        )
        return list(result.rows)

    async def execute(self, query: str, *args: Any, timeout: float) -> str:
        del timeout
        if query.strip().upper() == "SELECT 1":
            return "SELECT 1"
        await self.state.execute(
            query,
            self._params(query, args),
            target=SimpleNamespace(database=self.database),
        )
        return "OK"

    async def close(self) -> None:
        self.closed = True


class StatefulS3Transport:
    """Boto3 response boundary for the real MinioBucketAdmin implementation."""

    def __init__(self) -> None:
        self.buckets: dict[str, dict[str, Any]] = {}
        self.objects: dict[str, dict[str, dict[str, Any]]] = {}
        self.quota_enforced = False
        self.quota_calls: list[tuple[str, int]] = []

    def head_bucket(self, *, Bucket: str) -> dict[str, Any]:
        if Bucket not in self.buckets:
            raise LookupError("NoSuchBucket")
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def create_bucket(self, **kwargs: Any) -> dict[str, Any]:
        bucket = kwargs["Bucket"]
        self.buckets.setdefault(bucket, {})
        self.objects.setdefault(bucket, {})
        return {"Location": f"/{bucket}"}

    def get_bucket_policy(self, *, Bucket: str) -> dict[str, str]:
        if "policy" not in self.buckets.get(Bucket, {}):
            raise LookupError("NoSuchBucketPolicy")
        return {"Policy": self.buckets[Bucket]["policy"]}

    def put_bucket_policy(self, *, Bucket: str, Policy: str) -> None:
        self.buckets[Bucket]["policy"] = Policy

    def get_paginator(self, name: str) -> "StatefulS3Transport":
        if name != "list_objects_v2":
            raise ValueError(name)
        return self

    def paginate(self, **kwargs: Any) -> list[dict[str, Any]]:
        bucket = kwargs["Bucket"]
        prefix = kwargs.get("Prefix", "")
        contents = [
            {"Key": key, "Size": int(meta.get("Size", 0))}
            for key, meta in self.objects.get(bucket, {}).items()
            if key.startswith(prefix)
        ]
        return [{"Contents": contents}]

    def delete_objects(self, *, Bucket: str, Delete: dict[str, Any], **_: Any) -> None:
        for item in Delete.get("Objects", []):
            self.objects.get(Bucket, {}).pop(item["Key"], None)

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.objects.get(Bucket, {}).pop(Key, None)

    def delete_bucket(self, *, Bucket: str) -> None:
        self.buckets.pop(Bucket, None)
        self.objects.pop(Bucket, None)

    async def probe(self) -> None:
        if not self.quota_enforced:
            self.quota_enforced = True

    async def enable_quota_enforcement(self) -> None:
        self.quota_enforced = True

    async def set_hard_quota(self, bucket: str, quota_bytes: int) -> None:
        if not self.quota_enforced:
            raise RuntimeError("quota enforcement was not probed")
        self.buckets[bucket]["quota_bytes"] = quota_bytes
        self.quota_calls.append((bucket, quota_bytes))


class StatefulRedisBoundary:
    """Redis scripts as a stateful clocked store, including processing leases."""

    def __init__(self) -> None:
        self.enabled = True
        self.client = self
        self.counters: dict[str, int] = {}
        self.windows: dict[str, list[tuple[int, int]]] = {}
        self.values: dict[str, tuple[str, float | None, str]] = {}
        self.clock = time.time
        self.eval_calls: list[str] = []

    def _purge(self, key: str) -> None:
        current = self.values.get(key)
        if current is not None and current[1] is not None and current[1] <= self.clock():
            self.values.pop(key, None)

    async def eval(self, script: str, key_count: int, *args: Any) -> Any:
        if not self.enabled:
            raise ConnectionError("redis unavailable")
        del key_count
        key = str(args[0])
        self.eval_calls.append(script)
        if "ZREMRANGEBYSCORE" in script:
            now, window, amount, limit = (int(args[index]) for index in range(1, 5))
            events = [(ts, qty) for ts, qty in self.windows.get(key, []) if ts > now - window]
            used = sum(qty for _, qty in events)
            if used + amount > limit:
                retry = max(1, window - (now - events[0][0])) if events else window
                self.windows[key] = events
                return [used, 0, retry]
            events.append((now, amount))
            self.windows[key] = events
            return [used + amount, 1, 0]
        if "INCRBY" in script and "tonumber(ARGV[1])" in script:
            amount, limit = int(args[1]), int(args[2])
            used = self.counters.get(key, 0)
            if used + amount > limit:
                return [used, 0]
            self.counters[key] = used + amount
            return [self.counters[key], 1]
        if "PEXPIRE" in script and "INCR" in script and len(args) == 2:
            ttl = int(args[1])
            self.counters[key] = self.counters.get(key, 0) + 1
            self.values[key] = (str(self.counters[key]), self.clock() + ttl / 1000, "counter")
            return self.counters[key]
        if "local ttl_seconds = tonumber(ARGV[2])" in script:
            token, ttl = str(args[1]), int(args[2])
            self._purge(key)
            current = self.values.get(key)
            if current is not None and current[0] == token and current[2] == "done":
                self.values[key] = (token, self.clock() + ttl, "done")
                return 1
            if current is None or current[0] != token or current[2] != "processing":
                return 0
            self.values[key] = (token, self.clock() + ttl, "done")
            return 1
        if "return redis.call('DEL', KEYS[1])" in script:
            token = str(args[1])
            current = self.values.get(key)
            if current is not None and current[0] == token and current[2] == "processing":
                self.values.pop(key, None)
            return 1
        if "local lease_ms = tonumber(ARGV[2])" in script:
            self._purge(key)
            token, lease_ms = str(args[1]), int(args[2])
            current = self.values.get(key)
            if current is None:
                self.values[key] = (token, self.clock() + lease_ms / 1000, "processing")
                return ["first", "", 0]
            previous, _expires, state = current
            if state == "done":
                return ["duplicate", previous, 0]
            retry = max(1, math.ceil(_expires - self.clock())) if _expires else math.ceil(lease_ms / 1000)
            return ["in_progress", previous, retry]
        raise AssertionError(f"unmodelled Redis script: {script[:160]}")

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        self._purge(key)
        if nx and key in self.values:
            return False
        self.values[key] = (value, self.clock() + ex if ex else None, "done")
        return True

    async def get(self, key: str) -> Any:
        self._purge(key)
        current = self.values.get(key)
        if current is not None:
            return current[0]
        return self.counters.get(key, 0)

    async def zcard(self, key: str) -> int:
        return sum(qty for _, qty in self.windows.get(key, []))


@dataclass
class _K8sStore:
    objects: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    versions: dict[tuple[str, str], int] = field(default_factory=dict)
    token_requests: list[dict[str, Any]] = field(default_factory=list)


class StatefulK8sApi:
    """Small Kubernetes REST state machine with CAS and fault injection."""

    def __init__(self, *, namespace: str = NAMESPACE, scoped_identity: bool) -> None:
        self.namespace = namespace
        self.scoped_identity = scoped_identity
        self.store = _K8sStore()
        self.calls: list[tuple[str, str, str]] = []
        self.fail_next_deployment_create = False

    @staticmethod
    def _kind(path: str) -> str:
        if "/secrets" in path:
            return "secrets"
        if "/deployments" in path:
            return "deployments"
        if "/services" in path:
            return "services"
        if "/configmaps" in path:
            return "configmaps"
        if "/pods" in path:
            return "pods"
        return "other"

    @staticmethod
    def _name(path: str, kind: str) -> str:
        if kind == "other":
            return path
        tail = path.rsplit(f"/{kind}", 1)[1].strip("/")
        return tail.split("/", 1)[0] if tail else ""

    def _next_version(self, key: tuple[str, str]) -> str:
        version = self.store.versions.get(key, 0) + 1
        self.store.versions[key] = version
        return str(version)

    @staticmethod
    def _scale_body(deployment: dict[str, Any]) -> dict[str, Any]:
        return {
            "apiVersion": "autoscaling/v1",
            "kind": "Scale",
            "metadata": copy.deepcopy(deployment["metadata"]),
            "spec": {"replicas": deployment.get("spec", {}).get("replicas", 0)},
        }

    def _put(self, path: str, body: dict[str, Any], *, create: bool) -> dict[str, Any]:
        kind = self._kind(path)
        name = self._name(path, kind)
        if not name:
            # Collection POST identifies the object in metadata, not in the URL.
            name = str(body.get("metadata", {}).get("name", ""))
        key = (kind, name)
        if create and key in self.store.objects:
            raise K8sApiConflict("already exists")
        stored = copy.deepcopy(body)
        metadata = stored.setdefault("metadata", {})
        metadata.setdefault("name", name)
        metadata.setdefault("namespace", self.namespace)
        metadata["resourceVersion"] = self._next_version(key)
        self.store.objects[key] = stored
        return copy.deepcopy(stored)

    async def request(self, method: str, path: str, *, body: dict[str, Any] | None = None, content_type: str | None = None) -> Any:
        del content_type
        method = method.upper()
        self.calls.append((method, self._kind(path), path))
        if path.endswith("/serviceaccounts/plugin-deployment-admin/token"):
            if method != "POST" or body is None:
                raise K8sApiError("invalid TokenRequest")
            self.store.token_requests.append(copy.deepcopy(body))
            return SimpleNamespace(body={
                "apiVersion": "authentication.k8s.io/v1",
                "kind": "TokenRequest",
                "status": {
                    "token": "scoped-lifecycle-token-test",
                    "expirationTimestamp": datetime.fromtimestamp(FIXED_TIME + 3600, tz=UTC).isoformat().replace("+00:00", "Z"),
                },
            })
        kind = self._kind(path)
        name = self._name(path, kind)
        key = (kind, name)
        if method == "GET":
            if kind == "pods":
                items = [copy.deepcopy(value) for (item_kind, _), value in self.store.objects.items() if item_kind == "pods"]
                return SimpleNamespace(body={"apiVersion": "v1", "kind": "PodList", "items": items})
            current = self.store.objects.get(key)
            if current is None:
                raise K8sApiNotFound(path)
            if kind == "deployments" and path.endswith("/scale"):
                return SimpleNamespace(body=self._scale_body(current))
            return SimpleNamespace(body=copy.deepcopy(current))
        if method == "POST":
            if kind == "deployments" and self.fail_next_deployment_create:
                self.fail_next_deployment_create = False
                raise K8sApiError("injected deployment create failure")
            if body is None:
                raise K8sApiError("POST body is required")
            return SimpleNamespace(body=self._put(path, body, create=True))
        if method == "PATCH":
            current = self.store.objects.get(key)
            if current is None:
                raise K8sApiNotFound(path)
            if body is None:
                raise K8sApiError("PATCH body is required")
            expected = body.get("metadata", {}).get("resourceVersion")
            if expected is not None and str(current["metadata"]["resourceVersion"]) != str(expected):
                raise K8sApiConflict(path)
            merged = copy.deepcopy(current)
            for field_name, value in body.items():
                if field_name in {"metadata", "spec", "data", "status"} and isinstance(value, dict):
                    merged.setdefault(field_name, {}).update(copy.deepcopy(value))
                else:
                    merged[field_name] = copy.deepcopy(value)
            updated = self._put(path, merged, create=False)
            if kind == "deployments" and path.endswith("/scale"):
                return SimpleNamespace(body=self._scale_body(updated))
            return SimpleNamespace(body=updated)
        if method == "DELETE":
            if key not in self.store.objects:
                raise K8sApiNotFound(path)
            removed = self.store.objects.pop(key)
            return SimpleNamespace(body=copy.deepcopy(removed))
        raise K8sApiError(f"unsupported {method} {path}")

    def secret(self, name: str) -> dict[str, Any]:
        return self.store.objects[("secrets", name)]

    def deployment(self, name: str) -> dict[str, Any]:
        return self.store.objects[("deployments", name)]


@dataclass(frozen=True)
class RsaBoundary:
    private_pem: str
    public_jwk: dict[str, Any]

    @classmethod
    def create(cls) -> "RsaBoundary":
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")
        public = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
        public.update({"kid": "composition-rsa-key", "use": "sig", "alg": "RS256"})
        return cls(private_pem=pem, public_jwk=public)


class StatefulOidcJwks:
    def __init__(self, rsa_boundary: RsaBoundary) -> None:
        self.rsa = rsa_boundary
        self.calls = 0

    async def fetch(self, url: str) -> Mapping[str, object]:
        if not url.startswith("https://"):
            raise AssertionError("JWKS must use HTTPS")
        self.calls += 1
        return {"keys": [dict(self.rsa.public_jwk)]}

    def token(self, source: ModuleSource) -> str:
        now = int(FIXED_TIME)
        wall_now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": "agentscale-plugin-ci",
            # PyJWT uses wall time before the validator applies its injected
            # clock. Keep this test token valid under both, without disabling
            # either temporal validation path.
            "exp": max(now, wall_now) + 600,
            "iat": min(now, wall_now) - 30,
            "nbf": min(now, wall_now) - 60,
            "repository": source.full_name,
            "repository_id": str(source.repo_id),
            "ref": source.expected_ref,
            "sha": COMMIT_SHA,
            "event_name": "push",
            "job_workflow_ref": source.trusted_workflow_ref,
            "ref_protected": "true",
            "run_id": RUN_ID,
            "run_attempt": RUN_ATTEMPT,
        }
        return jwt.encode(
            claims,
            self.rsa.private_pem,
            algorithm="RS256",
            headers={"kid": self.rsa.public_jwk["kid"]},
        )


class StatefulGhcrBoundary:
    """Digest-addressed OCI responses and cosign trust response for one bundle."""

    def __init__(self) -> None:
        self.manifests: dict[str, bytes] = {}
        self.blobs: dict[str, bytes] = {}
        self.authorized_requests = 0
        self.cosign_calls: list[str] = []

    def client_factory(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(self._handle),
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(2.0),
        )

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.headers.get("Authorization"):
            self.authorized_requests += 1
        if "/manifests/" in path:
            digest = path.rsplit("/", 1)[-1]
            data = self.manifests.get(digest)
        elif "/blobs/" in path:
            digest = path.rsplit("/", 1)[-1]
            data = self.blobs.get(digest)
        else:
            data = None
        if data is None:
            return httpx.Response(404, content=b"not found")
        # Keep a raw async stream: content=data is already consumed when the
        # production loader enters response.aiter_raw() under MockTransport.
        return httpx.Response(
            200,
            stream=httpx.ByteStream(data),
            headers={"Content-Length": str(len(data))},
        )

    def verify(self, image_ref: str, identity: str, issuer: str) -> VerificationResult:
        digest = image_ref.rsplit("@", 1)[-1]
        self.cosign_calls.append(digest)
        verified = (
            digest in self.manifests
            and identity == SIGNER_IDENTITY
            and issuer == ISSUER
        )
        return VerificationResult(
            verified=verified,
            signed_digest=digest if verified else None,
            identity=identity if verified else None,
            issuer=issuer if verified else None,
            sbom_verified=verified,
            provenance_verified=verified,
        )


@dataclass(frozen=True)
class PublicationFixture:
    command: ArtifactPublicationCommand
    manifest_bytes: bytes
    canonical_bytes: bytes
    bundle_digest: str

    @property
    def bundle_ref(self) -> str:
        return self.command.bundle_ref


def publication_fixture(
    source: ModuleSource | None = None,
    *,
    public: bool = False,
    manifest_patch: Mapping[str, Any] | None = None,
) -> PublicationFixture:
    source = source or ready_source()
    manifest_path = (
        Path(__file__).resolve().parents[5]
        / "packages"
        / "plugins"
        / "hello-plugin"
        / "manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if public:
        manifest["public_pages"] = ["/"]
        manifest["rpc_operations"][0]["public"] = True
    if manifest_patch is not None:
        manifest.update(copy.deepcopy(dict(manifest_patch)))
    canonical = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    config_digest = "sha256:" + hashlib.sha256(canonical).hexdigest()
    frontend = b"hello frontend\n"
    migrations = b"hello migrations\n"
    frontend_digest = "sha256:" + hashlib.sha256(frontend).hexdigest()
    migrations_digest = "sha256:" + hashlib.sha256(migrations).hexdigest()
    backend_ref = BACKEND_REF
    annotations = {
        "plugin_id": PLUGIN_ID,
        "manifest_version": VERSION,
        "protocol": "plugin-rpc/1",
        "backend_digest": BACKEND_DIGEST,
        "com.agentscale.plugin.backend_image_ref": backend_ref,
        "com.agentscale.plugin.source_repository": source.full_name,
        "com.agentscale.plugin.source_sha": COMMIT_SHA,
        "com.agentscale.plugin.manifest_digest": config_digest,
        "requestedCapabilities": ",".join(CAPABILITIES),
        "min_platform_version": "4.0.0",
    }
    oci = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "artifactType": "application/vnd.agentscale.plugin.oci",
        "annotations": annotations,
        "config": {
            "mediaType": "application/vnd.agentscale.plugin.manifest.v1+json",
            "digest": config_digest,
            "size": len(canonical),
        },
        "layers": [
            {
                "mediaType": "application/vnd.agentscale.plugin.manifest.v1+json",
                "digest": config_digest,
                "size": len(canonical),
                "annotations": {"com.agentscale.plugin.layer.role": "canonical-manifest"},
            },
            {
                "mediaType": "application/octet-stream",
                "digest": frontend_digest,
                "size": len(frontend),
                "annotations": {"com.agentscale.plugin.layer.role": "frontend-dist"},
            },
            {
                "mediaType": "application/octet-stream",
                "digest": migrations_digest,
                "size": len(migrations),
                "annotations": {"com.agentscale.plugin.layer.role": "migrations"},
            },
        ],
    }
    manifest_bytes = json.dumps(oci, sort_keys=True, separators=(",", ":")).encode("utf-8")
    bundle_digest = "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
    bundle_ref = f"ghcr.io/{BUNDLE_REPOSITORY}@{bundle_digest}"
    command = ArtifactPublicationCommand(
        source_id=source.id,
        plugin_id=PLUGIN_ID,
        version=VERSION,
        bundle_ref=bundle_ref,
        bundle_digest=bundle_digest,
        backend_ref=backend_ref,
        backend_digest=BACKEND_DIGEST,
        manifest=oci,
        commit_sha=COMMIT_SHA,
        ref=source.expected_ref,
        run_url=(
            f"https://github.com/{source.full_name}/actions/runs/{RUN_ID}"
            f"/attempts/{RUN_ATTEMPT}"
        ),
        evidence=PublicationEvidence(
            scan_verified=True,
            csp_verified=True,
            sbom_digest="sha256:" + "1" * 64,
            provenance_digest="sha256:" + "2" * 64,
        ),
    )
    return PublicationFixture(
        command=command,
        manifest_bytes=manifest_bytes,
        canonical_bytes=canonical,
        bundle_digest=bundle_digest,
    )


def register_publication(fixture: PublicationFixture, ghcr: StatefulGhcrBoundary) -> None:
    ghcr.manifests[fixture.bundle_digest] = fixture.manifest_bytes
    manifest = fixture.command.manifest
    ghcr.blobs[manifest["config"]["digest"]] = fixture.canonical_bytes
    for layer in manifest["layers"]:
        role = layer["annotations"]["com.agentscale.plugin.layer.role"]
        if role == "frontend-dist":
            ghcr.blobs[layer["digest"]] = b"hello frontend\n"
        elif role == "migrations":
            ghcr.blobs[layer["digest"]] = b"hello migrations\n"


def github_client(vault: StatefulVaultBoundary, rsa_boundary: RsaBoundary) -> GitHubAppClient:
    config = source_config()
    vault.seed(PRIVATE_KEY_REF, rsa_boundary.private_pem)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/app/installations/{INSTALLATION_ID}/access_tokens":
            requested_permissions = json.loads(request.content)["permissions"]
            return httpx.Response(
                201,
                json={
                    "token": "github-installation-token-test-only",
                    "expires_at": "2030-01-01T00:00:00Z",
                    # Mirror the exact profile requested by the real client;
                    # registry_read intentionally differs from authoring scopes.
                    "permissions": requested_permissions,
                    "repository_selection": "all",
                },
            )
        return httpx.Response(404, json={"message": "not found"})

    return GitHubAppClient(
        app_id=1001,
        private_key_secret_ref=PRIVATE_KEY_REF,
        secret_provider=vault,
        organizations=(config.organization,),
        transport=httpx.MockTransport(handler),
        clock=lambda: FIXED_TIME,
        timeout_seconds=2.0,
    )


class StatefulKafkaProducer:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.flushes = 0
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    async def send_and_wait(self, topic: str, **kwargs: Any) -> None:
        if not self.started:
            raise RuntimeError("producer is not started")
        self.messages.append({"topic": topic, **kwargs})

    async def flush(self) -> None:
        if not self.started:
            raise RuntimeError("producer is not started")
        self.flushes += 1


class StatefulKafkaConsumer:
    def __init__(self) -> None:
        self.records: list[Any] = []
        self.commits: list[Any] = []
        self.seeks: list[tuple[Any, int]] = []
        self.stopped = False

    async def getone(self, *, timeout_ms: int) -> Any:
        del timeout_ms
        if not self.records:
            raise asyncio.TimeoutError
        return self.records.pop(0)

    async def commit(self, assignment: Any) -> None:
        self.commits.append(assignment)

    def seek(self, partition: Any, offset: int) -> None:
        self.seeks.append((partition, offset))

    async def stop(self) -> None:
        self.stopped = True


class StatefulKafkaAdmin:
    def __init__(self) -> None:
        self.topics: set[str] = set()
        self.closed = False

    async def list_topics(self) -> set[str]:
        return set(self.topics)

    async def create_topics(self, topics: list[Any]) -> None:
        for topic in topics:
            self.topics.add(str(topic.name))

    async def close(self) -> None:
        self.closed = True


class _SqliteAwareDateTime(TypeDecorator):
    """Mirror PostgreSQL TIMESTAMPTZ results for domain contracts on SQLite."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_result_value(
        self, value: datetime | None, dialect: Any
    ) -> datetime | None:
        del dialect
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class _ScalarRows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def first(self) -> Any:
        return self._rows[0][0] if self._rows else None

    def all(self) -> list[Any]:
        return [row[0] for row in self._rows]


class _RowcountReturningResult:
    """Expose real RETURNING rows plus PostgreSQL-style affected row count."""

    def __init__(self, result: Any) -> None:
        self._result = result
        self._rows = list(result.fetchall())
        self.rowcount = len(self._rows)

    def scalars(self) -> _ScalarRows:
        return _ScalarRows(self._rows)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._result, name)


class _ReturningResultSession(AsyncSession):
    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        result = await super().execute(statement, *args, **kwargs)
        sql = " ".join(str(statement).upper().split())
        if (
            result.__class__.__name__ == "ChunkedIteratorResult"
            and "RETURNING" in sql
            and sql.startswith(("INSERT", "UPDATE", "DELETE"))
        ):
            return _RowcountReturningResult(result)
        return result


class RelationalBoundary:
    """No-network relational state for real SQL repositories (not row fakes)."""

    TABLE_NAMES = (
        "companies",
        "cabinet_instances",
        "projects",
        "plugin_installation_scopes",
        "employees",
        "ai_provider_keys",
        "memberships",
        "identity_links",
        "plugin_capability_grants",
        "plugin_routes",
        "plugin_instances",
        "plugin_artifacts",
        "plugin_artifact_lifecycle",
        "plugin_module_sources",
        "plugin_source_reservations",
        "plugin_publication_approvals",
        "plugin_audit_events",
        "plugin_event_deliveries",
        "plugin_feature_flags",
    )

    def __init__(self) -> None:
        self.engine: AsyncEngine | None = None
        self.session_factory: Any = None
        self._source_datetime_columns: list[tuple[Any, Any]] = []

    async def open(self) -> "RelationalBoundary":
        # SQLite drops tzinfo on SELECT, unlike production TIMESTAMPTZ. Limit
        # the compatibility adapter to ModuleSource's strict domain timestamps.
        for name in ("created_at", "updated_at"):
            column = ModuleSourceRow.__table__.c[name]
            self._source_datetime_columns.append((column, column.type))
            column.type = _SqliteAwareDateTime()
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
        )

        @event.listens_for(self.engine.sync_engine, "connect")
        def _foreign_keys(dbapi: Any, _: Any) -> None:
            dbapi.execute("PRAGMA foreign_keys=ON")
            dbapi.create_function(
                "regexp",
                2,
                lambda pattern, value: bool(re.search(pattern, str(value))),
            )
            dbapi.create_function(
                "regexp_replace",
                3,
                lambda value, pattern, replacement: re.sub(
                    pattern, replacement, str(value), count=1
                ),
            )
            dbapi.create_function("jsonb_deep_ne", 2, _sqlite_jsonb_deep_ne)

        tables = [Base.metadata.tables[name] for name in self.TABLE_NAMES]
        original = getattr(SQLiteCompiler, "visit_JSONB", None)
        SQLiteCompiler.visit_JSONB = lambda self, type_, **kw: "JSON"  # type: ignore[attr-defined]
        try:
            async with self.engine.begin() as connection:
                await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
                # Match PostgreSQL's partial current-artifact uniqueness on SQLite.
                await connection.exec_driver_sql(
                    "DROP INDEX uq_plugin_artifact_current"
                )
                await connection.exec_driver_sql(
                    "CREATE UNIQUE INDEX uq_plugin_artifact_current "
                    "ON plugin_artifacts (plugin_id) WHERE is_current = true"
                )
        finally:
            if original is None:
                delattr(SQLiteCompiler, "visit_JSONB")
            else:
                SQLiteCompiler.visit_JSONB = original  # type: ignore[attr-defined]
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=_ReturningResultSession,
            expire_on_commit=False,
        )
        return self

    async def close(self) -> None:
        try:
            if self.engine is not None:
                await self.engine.dispose()
        finally:
            for column, original_type in self._source_datetime_columns:
                column.type = original_type
            self._source_datetime_columns.clear()


__all__ = [
    "ArtifactPublicationCommand",
    "BACKEND_DIGEST",
    "BACKEND_REF",
    "CAPABILITIES",
    "COMMIT_SHA",
    "COMPANY_ID",
    "FIXED_TIME",
    "GATEWAY_URL",
    "ISSUER",
    "NAMESPACE",
    "NOW",
    "PLUGIN_ID",
    "PRIVATE_KEY_REF",
    "PublicationFixture",
    "QUOTAS",
    "RelationalBoundary",
    "RsaBoundary",
    "RUN_ATTEMPT",
    "RUN_ID",
    "SIGNER_IDENTITY",
    "StatefulAsyncpgAdmin",
    "StatefulAsyncpgPool",
    "StatefulGhcrBoundary",
    "StatefulK8sApi",
    "StatefulKafkaAdmin",
    "StatefulKafkaConsumer",
    "StatefulKafkaProducer",
    "StatefulOidcJwks",
    "StatefulRedisBoundary",
    "StatefulS3Transport",
    "StatefulVaultBoundary",
    "VERSION",
    "app_settings",
    "github_client",
    "publication_fixture",
    "ready_source",
    "register_publication",
    "source_config",
]
