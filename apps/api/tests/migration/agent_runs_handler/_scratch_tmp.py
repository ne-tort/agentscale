"""Acceptance: every way the handler must refuse instead of pretending to deliver.

Each case asserts two things at once: the intent is *not* reported delivered, and
the run state is left exactly as it was.  The negative paths are the reason the
handler exists at all — a handler that degraded to "best effort" here would
consume a handoff without executing anything.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from prodavan.application.agent_runs import handler as handler_module
from prodavan.application.agent_runs.dispatcher import (
    DispatchContext,
    IntentDispatchAction,
    IntentDispatchReason,
    IntentKind,
)
from prodavan.application.agent_runs.handler import (
    BLOCKER_COMPOSITION_MISSING,
    BLOCKER_EXECUTION_FACTORY_FAILED,
    BLOCKER_EXECUTION_FACTORY_UNWIRED,
    BLOCKER_JOURNAL_UNWIRED,
    BLOCKER_LEASE_STORE_UNWIRED,
    BLOCKER_RUN_SOURCE_UNWIRED,
    BLOCKER_TURN_RUNNER_UNWIRED,
    IntentExecutionPipeline,
    build_run_intent_handler,
    build_terminal_refusal_handlers,
)
from prodavan.application.agent_runs.worker import WorkerTick, WorkerTickStatus
from prodavan.domain.agent.control_plane import AgentRunStatus

from .acceptance_fixtures import (
    MEMBER_NAMES if False else None,  # placeholder removed below
)
