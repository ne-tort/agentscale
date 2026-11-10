"""Scoped lifecycle identity acceptance tests; not executed during freeze."""
from __future__ import annotations

from typing import Any

import pytest

import prodavan.infrastructure.plugins.runtime.k8s_scale as k8s_runtime
from prodavan.infrastructure.plugins.runtime.errors import RuntimeConfigError
from prodavan.infrastructure.plugins.runtime.k8s_scale import (
    ConfigMapStateStore,
    K8sApiClient,
    K8sScalePort,
)
from tests.unit.plugins.runtime.conftest import FakeK8sApi, FakeProbe


class Provider:
    async def get_token(self) -> str:
        return "scoped-test-token"


def test_missing_scoped_client_never_constructs_default_mounted_api(
    config: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_default(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("mounted K8sApiClient must not be constructed")

    monkeypatch.setattr(k8s_runtime, "K8sApiClient", forbidden_default)
    constructors = (
        lambda **kwargs: K8sScalePort(config=config, probe=FakeProbe(), **kwargs),
        lambda **kwargs: ConfigMapStateStore(config=config, **kwargs),
    )
    for constructor in constructors:
        for kwargs in ({}, {"client": object()}):
            with pytest.raises(
                RuntimeConfigError,
                match="default_mounted_service_account_forbidden",
            ):
                constructor(**kwargs)


def test_explicit_token_provider_is_the_only_client_less_identity_override(
    config: Any,
) -> None:
    provider = Provider()
    scale = K8sScalePort(
        config=config,
        probe=FakeProbe(),
        token_provider=provider,
    )
    state = ConfigMapStateStore(config=config, token_provider=provider)

    assert scale.scoped_identity is True
    assert state.scoped_identity is True
    assert scale.api.scoped_identity is True  # type: ignore[attr-defined]
    assert state._api.scoped_identity is True  # type: ignore[attr-defined]
    assert scale.api._token_provider is provider  # type: ignore[attr-defined]
    assert state._api._token_provider is provider  # type: ignore[attr-defined]


def test_mounted_real_client_and_unmarked_custom_api_are_denied(config: Any) -> None:
    mounted = K8sApiClient()
    assert mounted.scoped_identity is False

    class UnmarkedApi:
        async def request(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("not called")

    for api in (mounted, UnmarkedApi()):
        with pytest.raises(
            RuntimeConfigError,
            match="default_mounted_service_account_forbidden",
        ):
            K8sScalePort(config=config, api=api, probe=FakeProbe())
        with pytest.raises(
            RuntimeConfigError,
            match="default_mounted_service_account_forbidden",
        ):
            ConfigMapStateStore(config=config, api=api)


def test_scoped_custom_api_and_matching_provider_are_explicitly_allowed(
    config: Any,
) -> None:
    provider = Provider()
    scoped = K8sApiClient(token_provider=provider)
    scale = K8sScalePort(
        config=config,
        api=scoped,
        token_provider=provider,
        probe=FakeProbe(),
    )
    state = ConfigMapStateStore(
        config=config,
        api=scoped,
        token_provider=provider,
    )
    custom = FakeK8sApi()
    custom_scale = K8sScalePort(config=config, api=custom, probe=FakeProbe())

    assert scale.api is scoped
    assert state._api is scoped
    assert custom_scale.api is custom


def test_real_scoped_client_cannot_be_combined_with_another_provider(
    config: Any,
) -> None:
    api = K8sApiClient(token_provider=Provider())
    for constructor in (
        lambda: K8sScalePort(
            config=config,
            api=api,
            token_provider=Provider(),
            probe=FakeProbe(),
        ),
        lambda: ConfigMapStateStore(
            config=config,
            api=api,
            token_provider=Provider(),
        ),
    ):
        with pytest.raises(RuntimeConfigError, match="identity mismatch"):
            constructor()
