"""Unit tests — trigger/idle worker enable flags."""

from __future__ import annotations

from unittest.mock import patch

from prodavan.application.agent.trigger_worker import _worker_wanted


def test_worker_wanted_when_trigger_enabled() -> None:
    with patch("prodavan.application.agent.trigger_worker.settings") as settings:
        settings.trigger_worker_enabled = True
        settings.idle_pause_worker_enabled = False
        assert _worker_wanted() is True


def test_worker_wanted_when_idle_only() -> None:
    with patch("prodavan.application.agent.trigger_worker.settings") as settings:
        settings.trigger_worker_enabled = False
        settings.idle_pause_worker_enabled = True
        assert _worker_wanted() is True


def test_worker_wanted_when_both_off() -> None:
    with patch("prodavan.application.agent.trigger_worker.settings") as settings:
        settings.trigger_worker_enabled = False
        settings.idle_pause_worker_enabled = False
        assert _worker_wanted() is False
