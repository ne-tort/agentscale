"""Random resource provisioning and exact-owner cleanup for real MinIO.

The S3 data path is boto3. Only MinIO IAM user/policy lifecycle is delegated to
the project-confirmed mc executable because boto3 has no MinIO Admin API.
No subprocess output, credentials or signed URLs are logged or persisted.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import uuid4

from prodavan.application.plugins.objects import policy as iam
from prodavan.infrastructure.plugins.minio.adapters import MinioSts
from prodavan.infrastructure.plugins.minio.config import MinioPluginConfig

from .config import AcceptanceConfig

_ALIAS = "acceptance"
_FIXED_OBJECT_NAME = "__authorization_canary__"
_NO_AMBIENT_PROXIES = {"http": "", "https": "", "all": ""}


class MinioProvisioningError(RuntimeError):
    """Sanitized provisioning/cleanup failure."""


@dataclass(frozen=True, slots=True)
class AcceptanceIdentity:
    run_id: str
    company_id: str
    plugin_id: str
    foreign_plugin_id: str
    missing_company_id: str
    bucket: str
    foreign_bucket: str
    missing_bucket: str
    canary_bucket: str
    prefix: str
    fixed_key: str
    foreign_key: str
    missing_key: str
    upload_key: str
    admin_user: str
    admin_secret: str = field(repr=False)
    presign_user: str = ""
    presign_secret: str = field(default="", repr=False)
    admin_policy: str = ""
    presign_policy: str = ""
    canary_policy: str = ""

    @classmethod
    def random(cls, *, admin_secret: str, presign_secret: str) -> "AcceptanceIdentity":
        run = f"a{uuid4().hex[:20]}"
        company = f"acc{run}"
        plugin = f"objects{run}"
        foreign_plugin = f"{plugin}-foreign"
        missing_company = f"missing{run}"
        prefix = iam.module_prefix(plugin)
        return cls(
            run_id=run,
            company_id=company,
            plugin_id=plugin,
            foreign_plugin_id=foreign_plugin,
            missing_company_id=missing_company,
            bucket=iam.bucket_name(company),
            foreign_bucket=f"{iam.bucket_name(company)}-foreign",
            missing_bucket=iam.bucket_name(missing_company),
            canary_bucket=iam.bucket_name(f"{company}-canary"),
            prefix=prefix,
            fixed_key=f"{prefix}{_FIXED_OBJECT_NAME}",
            foreign_key=f"{iam.module_prefix(foreign_plugin)}{_FIXED_OBJECT_NAME}",
            missing_key=f"{prefix}missing-{run}",
            upload_key=f"{prefix}upload-{run}.bin",
            admin_user=f"acc-admin-{run}",
            admin_secret=admin_secret,
            presign_user=f"acc-presign-{run}",
            presign_secret=presign_secret,
            admin_policy=f"acc-admin-policy-{run}",
            presign_policy=f"acc-presign-policy-{run}",
            canary_policy=f"acc-canary-policy-{run}",
        )


@dataclass(slots=True)
class MinioAcceptanceEnvironment:
    config: AcceptanceConfig = field(repr=False)
    plugin_config: MinioPluginConfig = field(repr=False)
    identity: AcceptanceIdentity
    root_s3: Any = field(repr=False)

    @property
    def sensitive_values(self) -> tuple[str, ...]:
        return (
            self.config.root_access_key,
            self.config.root_secret_key,
            self.identity.admin_user,
            self.identity.admin_secret,
            self.identity.presign_user,
            self.identity.presign_secret,
        )

    def new_s3_client(
        self,
        *,
        access_key: str,
        secret_key: str,
        session_token: str | None = None,
        endpoint: str | None = None,
        timeout_seconds: int | None = None,
    ) -> Any:
        return new_s3_client(
            endpoint=endpoint or self.config.endpoint,
            access_key=access_key,
            secret_key=secret_key,
            session_token=session_token,
            region=self.config.region,
            timeout_seconds=timeout_seconds,
        )


def new_s3_client(
    *,
    endpoint: str,
    access_key: str,
    secret_key: str,
    region: str,
    session_token: str | None = None,
    timeout_seconds: int | None = None,
) -> Any:
    """Build a bounded real boto3 client without emitting its configuration."""
    import boto3
    from botocore.client import Config

    options: dict[str, Any] = {
        "signature_version": "s3v4",
        "connect_timeout": timeout_seconds or 10,
        "read_timeout": timeout_seconds or 15,
        "retries": {"max_attempts": 0},
        # Empty explicit mappings bypass ambient HTTP(S)_PROXY/ALL_PROXY.
        "proxies": dict(_NO_AMBIENT_PROXIES),
    }
    try:
        Config(**{**options, "request_checksum_calculation": "when_required"})
        options["request_checksum_calculation"] = "when_required"
    except TypeError:
        pass
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        aws_session_token=session_token,
        region_name=region,
        config=Config(**options),
    )


def new_sts_client(
    *,
    endpoint: str,
    access_key: str,
    secret_key: str,
    region: str,
) -> Any:
    """Build real STS IO with explicit credentials and no ambient proxy."""
    import boto3
    from botocore.client import Config

    return boto3.client(
        "sts",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        config=Config(
            connect_timeout=10,
            read_timeout=15,
            retries={"max_attempts": 0},
            proxies=dict(_NO_AMBIENT_PROXIES),
        ),
    )


def new_minio_sts(
    environment: MinioAcceptanceEnvironment,
    config: MinioPluginConfig,
    *,
    canary_client_factory: Any = None,
) -> MinioSts:
    """Keep production MinioSts while isolating every owned boto3 transport."""
    if canary_client_factory is None:
        def canary_client_factory(*, credentials: Any, timeout_seconds: int) -> Any:
            return environment.new_s3_client(
                access_key=credentials.access_key,
                secret_key=credentials.secret_key,
                session_token=credentials.session_token,
                timeout_seconds=timeout_seconds,
            )

    return MinioSts(
        config=config,
        sts_client=new_sts_client(
            endpoint=config.sts_endpoint_url,
            access_key=config.admin_access_key,
            secret_key=config.admin_secret_key,
            region=config.region,
        ),
        canary_client_factory=canary_client_factory,
    )


def _force_delete_objects_content_md5(params: Any, **kwargs: Any) -> None:
    """MinIO 2024 requires Content-MD5 even with newer botocore checksums."""
    from botocore.utils import calculate_md5

    params["headers"]["Content-MD5"] = calculate_md5(params["body"])


def _status(response: Any) -> int | None:
    if not isinstance(response, dict):
        return None
    metadata = response.get("ResponseMetadata")
    if not isinstance(metadata, dict):
        return None
    try:
        return int(metadata.get("HTTPStatusCode"))
    except (TypeError, ValueError):
        return None


class McAdmin:
    """Invoke only exact mc admin operations with isolated configuration."""

    def __init__(self, config: AcceptanceConfig, config_dir: Path) -> None:
        self._binary = config.mc_binary
        self._config_dir = config_dir
        parsed = urlsplit(config.endpoint)
        credential_netloc = (
            f"{quote(config.root_access_key, safe='')}:{quote(config.root_secret_key, safe='')}"
            f"@{parsed.netloc}"
        )
        alias_url = urlunsplit((parsed.scheme, credential_netloc, "", "", ""))
        self._env: dict[str, str] = {}
        # mc is a native/static binary. PATH locates it; SystemRoot is required by
        # Windows process/loader internals. No app/AWS/MinIO/proxy env is inherited.
        for name in ("PATH", "SYSTEMROOT"):
            value = os.environ.get(name)
            if value:
                self._env[name] = value
        self._env["MC_CONFIG_DIR"] = str(config_dir)
        self._env[f"MC_HOST_{_ALIAS}"] = alias_url

    def environment_snapshot(self) -> dict[str, str]:
        """Return the exact child env for isolated assertions; never log its values."""
        return dict(self._env)

    def run(self, operation: str, *arguments: str) -> None:
        try:
            completed = subprocess.run(
                [self._binary, "--no-color", *arguments],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self._env,
                timeout=20,
                check=False,
                shell=False,
            )
        except Exception:
            raise MinioProvisioningError(f"mc {operation} could not be executed") from None
        if completed.returncode != 0:
            # Deliberately discard both streams: Admin API output can contain principals.
            raise MinioProvisioningError(f"mc {operation} failed")


class ResourceProvisioner:
    """Create and later remove only resources successfully created by this run."""

    def __init__(self, config: AcceptanceConfig, workspace: Path) -> None:
        self.config = config
        self.workspace = workspace
        self.identity = AcceptanceIdentity.random(
            admin_secret=config.admin_secret_key,
            presign_secret=config.presign_secret_key,
        )
        self.root_s3 = new_s3_client(
            endpoint=config.endpoint,
            access_key=config.root_access_key,
            secret_key=config.root_secret_key,
            region=config.region,
        )
        self.root_s3.meta.events.register(
            "before-call.s3.DeleteObjects",
            _force_delete_objects_content_md5,
        )
        self.mc = McAdmin(config, workspace)
        self._buckets: set[str] = set()
        self._users: set[str] = set()
        self._policies: set[str] = set()

    def provision(self) -> MinioAcceptanceEnvironment:
        identity = self.identity
        try:
            self._create_bucket(identity.bucket)
            self._create_bucket(identity.foreign_bucket)
            self._create_bucket(identity.canary_bucket)
            # Foreign objects really exist: a 404 can never be mistaken for a denial.
            self._put_zero(identity.bucket, identity.fixed_key)
            self._put_zero(identity.bucket, identity.foreign_key)
            self._put_zero(identity.foreign_bucket, identity.fixed_key)
            self._put_zero(identity.canary_bucket, identity.fixed_key)
            self._put_zero(identity.canary_bucket, identity.foreign_key)

            self._create_policy(identity.admin_policy, self._admin_policy())
            self._create_policy(identity.presign_policy, self._presign_policy())
            self._create_policy(identity.canary_policy, self._canary_policy())
            self._create_user(identity.admin_user, identity.admin_secret)
            self._create_user(identity.presign_user, identity.presign_secret)
            self.mc.run(
                "admin policy attach",
                "admin", "policy", "attach", _ALIAS, identity.admin_policy,
                "--user", identity.admin_user,
            )
            self.mc.run(
                "canary policy attach",
                "admin", "policy", "attach", _ALIAS, identity.canary_policy,
                "--user", identity.admin_user,
            )
            self.mc.run(
                "presign policy attach",
                "admin", "policy", "attach", _ALIAS, identity.presign_policy,
                "--user", identity.presign_user,
            )
        except Exception:
            try:
                self.cleanup()
            finally:
                self.root_s3.close()
            raise

        plugin_config = MinioPluginConfig(
            endpoint=self.config.endpoint,
            admin_access_key=identity.admin_user,
            admin_secret_key=identity.admin_secret,
            presign_access_key=identity.presign_user,
            presign_secret_key=identity.presign_secret,
            sts_enabled=True,
            sts_session_duration_seconds=900,
            sts_probe_enabled=True,
            sts_probe_timeout_seconds=15,
            sts_probe_revalidate_seconds=300,
            sts_probe_retry_seconds=1,
            sts_probe_bucket=identity.canary_bucket,
            sts_probe_key=identity.fixed_key,
            region=self.config.region,
        )
        return MinioAcceptanceEnvironment(
            config=self.config,
            plugin_config=plugin_config,
            identity=identity,
            root_s3=self.root_s3,
        )

    def _create_bucket(self, bucket: str) -> None:
        kwargs: dict[str, Any] = {"Bucket": bucket}
        if self.config.region != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": self.config.region}
        try:
            response = self.root_s3.create_bucket(**kwargs)
        except Exception:
            raise MinioProvisioningError("owned bucket creation failed") from None
        self._buckets.add(bucket)
        if _status(response) != 200:
            raise MinioProvisioningError("owned bucket creation returned an ambiguous response")

    def _put_zero(self, bucket: str, key: str) -> None:
        try:
            response = self.root_s3.put_object(Bucket=bucket, Key=key, Body=b"")
            head = self.root_s3.head_object(Bucket=bucket, Key=key)
        except Exception:
            raise MinioProvisioningError("fixed canary object provisioning failed") from None
        if _status(response) != 200 or _status(head) != 200 or head.get("ContentLength") != 0:
            raise MinioProvisioningError("fixed canary object did not have verified zero length")

    def _create_policy(self, name: str, policy: dict[str, Any]) -> None:
        path = self.workspace / f"{name}.json"
        path.write_text(json.dumps(policy, sort_keys=True), encoding="utf-8")
        self.mc.run("policy create", "admin", "policy", "create", _ALIAS, name, str(path))
        self._policies.add(name)

    def _create_user(self, access_key: str, secret_key: str) -> None:
        self.mc.run("user add", "admin", "user", "add", _ALIAS, access_key, secret_key)
        self._users.add(access_key)

    def _admin_policy(self) -> dict[str, Any]:
        statements: list[dict[str, Any]] = []
        for company in (self.identity.company_id, self.identity.missing_company_id):
            for raw in iam.bucket_policy(company, self.identity.plugin_id)["Statement"]:
                statement = dict(raw)
                statement["Sid"] = f"Acceptance{len(statements)}"
                statements.append(statement)
        return {"Version": iam.POLICY_VERSION, "Statement": statements}

    def _canary_policy(self) -> dict[str, Any]:
        return {
            "Version": iam.POLICY_VERSION,
            "Statement": [
                {
                    "Sid": "ListDedicatedStsCanaryBucket",
                    "Effect": "Allow",
                    "Action": ["s3:ListBucket"],
                    "Resource": [f"arn:aws:s3:::{self.identity.canary_bucket}"],
                },
                {
                    "Sid": "GetDedicatedStsCanaryObject",
                    "Effect": "Allow",
                    "Action": ["s3:GetObject"],
                    "Resource": [
                        f"arn:aws:s3:::{self.identity.canary_bucket}/{self.identity.fixed_key}"
                    ],
                },
            ],
        }

    def _presign_policy(self) -> dict[str, Any]:
        statements = []
        for raw in iam.bucket_policy(self.identity.company_id, self.identity.plugin_id)["Statement"]:
            actions = [action for action in raw["Action"] if action in {"s3:GetObject", "s3:PutObject"}]
            if actions:
                statement = dict(raw)
                statement["Action"] = actions
                statements.append(statement)
        if len(statements) != 1:
            raise MinioProvisioningError("could not derive the scoped presign policy")
        return {"Version": iam.POLICY_VERSION, "Statement": statements}

    def cleanup(self) -> None:
        """Remove only tracked random names; never list or purge global IAM state."""
        failures: list[str] = []
        for user in sorted(self._users):
            try:
                self.mc.run("user remove", "admin", "user", "remove", _ALIAS, user)
                self._users.remove(user)
            except MinioProvisioningError:
                failures.append("user")
        for policy in sorted(self._policies):
            try:
                self.mc.run("policy remove", "admin", "policy", "remove", _ALIAS, policy)
                self._policies.remove(policy)
            except MinioProvisioningError:
                failures.append("policy")
        for bucket in sorted(self._buckets):
            try:
                self._delete_owned_bucket(bucket)
                self._buckets.remove(bucket)
            except Exception:
                failures.append("bucket")
        if failures:
            raise MinioProvisioningError(
                "cleanup failed for owned resource types: " + ",".join(sorted(set(failures)))
            )

    def _delete_owned_bucket(self, bucket: str) -> None:
        if bucket not in self._buckets:
            raise MinioProvisioningError("refusing to delete an untracked bucket")
        paginator = self.root_s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            objects = [{"Key": item["Key"]} for item in page.get("Contents", []) if item.get("Key")]
            for offset in range(0, len(objects), 1000):
                response = self.root_s3.delete_objects(
                    Bucket=bucket,
                    Delete={"Objects": objects[offset:offset + 1000], "Quiet": True},
                )
                if _status(response) != 200 or response.get("Errors"):
                    raise MinioProvisioningError("owned object cleanup was not unambiguous")
        response = self.root_s3.delete_bucket(Bucket=bucket)
        if _status(response) != 204:
            raise MinioProvisioningError("owned bucket deletion was not unambiguous")

    def close(self) -> None:
        self.root_s3.close()
