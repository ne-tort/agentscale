"""Real AssumeRole, canary authorization and separate scoped-session acceptance."""
from __future__ import annotations

import asyncio
import time

import pytest
from botocore.exceptions import ClientError

from prodavan.application.plugins.objects import policy as iam
from prodavan.application.plugins.objects.errors import StsUnavailable

from .conftest import ObjectStack
from .support import (
    LeakSentinel,
    require,
    require_s3_access_denied,
    require_s3_head_zero,
    s3_status_and_code,
)

pytestmark = pytest.mark.minio_acceptance


async def test_assume_role_probe_and_fresh_scoped_session_enforce_exact_scope(
    object_stack: ObjectStack,
    caplog: pytest.LogCaptureFixture,
    isolated_transport_environment: dict[str, str],
) -> None:
    environment = object_stack.environment
    identity = environment.identity
    sts = object_stack.sts
    sentinel = LeakSentinel.for_environment(environment)

    require(sts.scoped_credentials_wired is False, "unprobed STS must start typed-unwired")
    with pytest.raises(StsUnavailable):
        await sts.assume_role(
            company_id=identity.company_id,
            plugin_id=identity.plugin_id,
            ttl_seconds=900,
            session_policy=iam.session_policy(identity.company_id, identity.plugin_id),
        )

    # Production probe performs real AssumeRole, HeadBucket, fixed zero-byte HEAD,
    # foreign HEAD and foreign ListBucket. Probe credentials are discarded internally.
    require(await sts.probe() is True, "real STS canary probe failed")
    require(sts.scoped_credentials_wired is True, "successful probe did not wire scoped credentials")
    require(not hasattr(sts, "_probe_credentials"), "probe credentials were retained")

    # This is a new AssumeRole after the discarded probe session, never a reuse of it.
    issued = await sts.assume_role(
        company_id=identity.company_id,
        plugin_id=identity.plugin_id,
        ttl_seconds=900,
        session_policy=iam.session_policy(identity.company_id, identity.plugin_id),
    )
    require(issued.bucket == identity.bucket, "scoped session bucket mismatch")
    require(issued.prefix == identity.prefix, "scoped session prefix mismatch")
    require(issued.policy == iam.session_policy(identity.company_id, identity.plugin_id), "scoped policy mismatch")
    require(bool(issued.access_key and issued.secret_key and issued.session_token), "incomplete scoped session")
    require(time.time() < issued.expires_at <= time.time() + 901, "scoped session expiry mismatch")
    require(
        issued.access_key not in environment.sensitive_values
        and issued.secret_key not in environment.sensitive_values,
        "STS returned a static/root credential instead of a fresh session",
    )

    scoped_s3 = environment.new_s3_client(
        access_key=issued.access_key,
        secret_key=issued.secret_key,
        session_token=issued.session_token,
    )
    try:
        with pytest.raises(ClientError) as unscoped_list:
            await asyncio.to_thread(
                scoped_s3.list_objects_v2,
                Bucket=identity.bucket,
                MaxKeys=10,
            )
        require_s3_access_denied(unscoped_list.value)

        with pytest.raises(ClientError) as canary_list:
            await asyncio.to_thread(
                scoped_s3.list_objects_v2,
                Bucket=identity.canary_bucket,
                Prefix=identity.prefix,
                MaxKeys=1,
            )
        require_s3_access_denied(canary_list.value)

        fixed_head = await asyncio.to_thread(
            scoped_s3.head_object,
            Bucket=identity.bucket,
            Key=identity.fixed_key,
        )
        require_s3_head_zero(fixed_head)

        own_list = await asyncio.to_thread(
            scoped_s3.list_objects_v2,
            Bucket=identity.bucket,
            Prefix=identity.prefix,
            MaxKeys=10,
        )
        require(s3_status_and_code(own_list)[0] == 200, "own-prefix ListBucket was not allowed")
        require(
            all(item.get("Key", "").startswith(identity.prefix) for item in own_list.get("Contents", [])),
            "own-prefix ListBucket leaked a foreign key",
        )

        # The foreign key and foreign bucket were both provisioned, so 404 cannot
        # be accepted as evidence of authorization denial.
        with pytest.raises(ClientError) as foreign_head:
            await asyncio.to_thread(
                scoped_s3.head_object,
                Bucket=identity.bucket,
                Key=identity.foreign_key,
            )
        require_s3_access_denied(foreign_head.value)

        with pytest.raises(ClientError) as foreign_list:
            await asyncio.to_thread(
                scoped_s3.list_objects_v2,
                Bucket=identity.foreign_bucket,
                Prefix=identity.prefix,
                MaxKeys=1,
            )
        require_s3_access_denied(foreign_list.value)
    finally:
        await asyncio.to_thread(scoped_s3.close)

    sentinel.assert_clean(caplog.text)
    for ambient in isolated_transport_environment.values():
        require(ambient not in caplog.text, "ambient proxy/application sentinel leaked")
