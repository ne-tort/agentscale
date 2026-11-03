"""Typed, replayable retry decisions for automation outbox claims.

The policy only emits fixed error codes and redacted diagnostics. It never keeps
or formats exception messages, response bodies, payloads, or secret references.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import TypeAlias

from .durable import (
    ClockPort,
    DeliveryClaim,
    DeliveryOutcome,
    DeliveryState,
    OutboxBlockedError,
    OutboxClaim,
    OutboxState,
    OutboxStateRecord,
)


RetryAfter: TypeAlias = str | datetime | timedelta | None

_MAX_CONFIGURED_DELAY_SECONDS = 365 * 24 * 60 * 60
_MICROSECONDS_PER_SECOND = 1_000_000


class AutomationFailureCategory(StrEnum):
    TRANSIENT = "transient"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    SERVER_ERROR = "server_error"
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    INVALID_REQUEST = "invalid_request"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    PERMANENT = "permanent"
    UNKNOWN = "unknown"


class AutomationFailureReason(StrEnum):
    TRANSIENT = "transient"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    SERVER_ERROR = "server_error"
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    INVALID_REQUEST = "invalid_request"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    PERMANENT = "permanent"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class AutomationFailure:
    """Safe failure inputs; intentionally contains no message or response body."""

    category: AutomationFailureCategory
    http_status: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.category, AutomationFailureCategory):
            raise ValueError("category must be an AutomationFailureCategory")
        if self.http_status is not None and (
            not isinstance(self.http_status, int) or isinstance(self.http_status, bool)
            or not 100 <= self.http_status <= 599
        ):
            raise ValueError("http_status must be an integer from 100 through 599")


@dataclass(frozen=True, slots=True)
class FailureClassification:
    category: AutomationFailureCategory
    reason: AutomationFailureReason
    retryable: bool
    error_code: str

    def __post_init__(self) -> None:
        if not isinstance(self.category, AutomationFailureCategory):
            raise ValueError("category must be an AutomationFailureCategory")
        if not isinstance(self.reason, AutomationFailureReason) or self.reason.value != self.category.value:
            raise ValueError("reason must match the classified category")
        retryable_categories = {
            AutomationFailureCategory.TRANSIENT,
            AutomationFailureCategory.RATE_LIMITED,
            AutomationFailureCategory.TIMEOUT,
            AutomationFailureCategory.CONNECTION,
            AutomationFailureCategory.SERVER_ERROR,
            AutomationFailureCategory.UNKNOWN,
        }
        if self.retryable != (self.category in retryable_categories):
            raise ValueError("retryable must match the fixed failure classification")
        expected = f"automation.error.{self.reason.value}"
        if self.error_code != expected:
            raise ValueError("error_code must be the fixed redacted code for its reason")

    @property
    def redacted_diagnostic(self) -> str:
        return "Automation failure details were redacted."


@dataclass(frozen=True, slots=True)
class RetryDecision:
    classification: FailureClassification
    attempt: int
    failed_at: datetime
    next_attempt_at: datetime | None

    def __post_init__(self) -> None:
        _aware(self.failed_at, "failed_at")
        if self.attempt < 1:
            raise ValueError("attempt must be positive")
        if self.next_attempt_at is not None:
            _aware(self.next_attempt_at, "next_attempt_at")
            if not self.classification.retryable or self.next_attempt_at < self.failed_at:
                raise ValueError("only retryable failures may have a non-past next attempt")

    @property
    def dead_letter(self) -> bool:
        return self.next_attempt_at is None

    @property
    def error_code(self) -> str:
        return self.classification.error_code

    def to_delivery_outcome(self, *, claim: DeliveryClaim) -> DeliveryOutcome:
        _check_delivery_claim(claim, attempt=self.attempt, failed_at=self.failed_at)
        state = DeliveryState.DEAD_LETTER if self.dead_letter else DeliveryState.RETRY_WAIT
        retry_at = self.next_attempt_at
        if state is DeliveryState.RETRY_WAIT and retry_at is None:
            raise ValueError("retry outcome requires next_attempt_at")
        if retry_at is not None and retry_at < claim.lease_until:
            raise ValueError("retry outcome cannot precede the claim lease expiry")
        return DeliveryOutcome(
            delivery_id=claim.intent.delivery_id,
            attempt=claim.attempt,
            lease_generation=claim.lease_generation,
            claim_token=claim.claim_token,
            state=state,
            completed_at=self.failed_at,
            retry_at=retry_at,
            error_code=self.error_code,
        )

    def to_outbox_state(self, *, claim: OutboxClaim) -> OutboxStateRecord:
        _check_outbox_claim(claim, attempt=self.attempt, failed_at=self.failed_at)
        current = claim.state
        if self.dead_letter:
            return OutboxStateRecord(
                event_id=claim.event.event_id,
                partition=claim.event.partition,
                state=OutboxState.DEAD_LETTER,
                available_at=current.available_at,
                attempts=current.attempts,
                lease_generation=current.lease_generation,
                last_error_code=self.error_code,
                dead_lettered_at=self.failed_at,
                revision=current.revision + 1,
            )
        retry_at = self.next_attempt_at
        if retry_at is None:
            raise ValueError("retry outcome requires next_attempt_at")
        lease_until = current.lease_until
        if lease_until is None:
            raise OutboxBlockedError("outbox failure is outside its live claim lease")
        if retry_at < lease_until:
            raise ValueError("retry outcome cannot precede the claim lease expiry")
        return OutboxStateRecord(
            event_id=claim.event.event_id,
            partition=claim.event.partition,
            state=OutboxState.RETRY_WAIT,
            available_at=retry_at,
            attempts=current.attempts,
            lease_generation=current.lease_generation,
            last_error_code=self.error_code,
            revision=current.revision + 1,
        )


def classify_failure(failure: AutomationFailure) -> FailureClassification:
    """Classify a failure without accepting or retaining free-form diagnostics."""
    category = failure.category
    status = failure.http_status
    if status is not None:
        if status == 429:
            category = AutomationFailureCategory.RATE_LIMITED
        elif status == 408:
            category = AutomationFailureCategory.TIMEOUT
        elif status == 425:
            category = AutomationFailureCategory.TRANSIENT
        elif status >= 500:
            category = AutomationFailureCategory.SERVER_ERROR
        elif 400 <= status < 500:
            category = {
                401: AutomationFailureCategory.AUTHENTICATION,
                403: AutomationFailureCategory.AUTHORIZATION,
                404: AutomationFailureCategory.NOT_FOUND,
                409: AutomationFailureCategory.CONFLICT,
            }.get(status, AutomationFailureCategory.INVALID_REQUEST)
        elif status < 200 or status >= 300:
            category = AutomationFailureCategory.UNKNOWN

    reason = AutomationFailureReason(category.value)
    retryable = category in {
        AutomationFailureCategory.TRANSIENT,
        AutomationFailureCategory.RATE_LIMITED,
        AutomationFailureCategory.TIMEOUT,
        AutomationFailureCategory.CONNECTION,
        AutomationFailureCategory.SERVER_ERROR,
        AutomationFailureCategory.UNKNOWN,
    }
    return FailureClassification(
        category=category,
        reason=reason,
        retryable=retryable,
        error_code=f"automation.error.{reason.value}",
    )


class AutomationRetryPolicy:
    """Compute deterministic jittered retry times for existing typed claims."""

    def __init__(
        self,
        *,
        clock: ClockPort,
        max_attempts: int = 8,
        initial_delay_seconds: int = 5,
        max_delay_seconds: int = 3600,
        jitter_basis_points: int = 2000,
    ) -> None:
        if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if (not isinstance(initial_delay_seconds, int) or isinstance(initial_delay_seconds, bool)
                or initial_delay_seconds < 1
                or not isinstance(max_delay_seconds, int)
                or isinstance(max_delay_seconds, bool)
                or max_delay_seconds < initial_delay_seconds
                or max_delay_seconds > _MAX_CONFIGURED_DELAY_SECONDS):
            raise ValueError("retry delay bounds are invalid")
        if (not isinstance(jitter_basis_points, int) or isinstance(jitter_basis_points, bool)
                or not 0 <= jitter_basis_points <= 10_000):
            raise ValueError("jitter_basis_points must be between 0 and 10000")
        self._clock = clock
        self._max_attempts = max_attempts
        self._initial_delay_seconds = initial_delay_seconds
        self._max_delay_seconds = max_delay_seconds
        self._jitter_basis_points = jitter_basis_points

    def evaluate_delivery(
        self,
        *,
        claim: DeliveryClaim,
        failure: AutomationFailure,
        retry_after: RetryAfter = None,
    ) -> RetryDecision:
        failed_at = self._checked_now()
        _check_delivery_claim(claim, attempt=claim.attempt, failed_at=failed_at)
        return self._evaluate(
            stable_key=claim.intent.idempotency_key,
            attempt=claim.attempt,
            lease_until=claim.lease_until,
            failed_at=failed_at,
            failure=failure,
            retry_after=retry_after,
        )

    def evaluate_outbox(
        self,
        *,
        claim: OutboxClaim,
        failure: AutomationFailure,
        retry_after: RetryAfter = None,
    ) -> RetryDecision:
        failed_at = self._checked_now()
        _check_outbox_claim(claim, attempt=claim.state.attempts, failed_at=failed_at)
        lease_until = claim.state.lease_until
        if lease_until is None:
            raise OutboxBlockedError("outbox failure is outside its live claim lease")
        return self._evaluate(
            stable_key=claim.event.idempotency_key,
            attempt=claim.state.attempts,
            lease_until=lease_until,
            failed_at=failed_at,
            failure=failure,
            retry_after=retry_after,
        )

    def _checked_now(self) -> datetime:
        now = self._clock.now()
        _aware(now, "clock.now()")
        return now

    def _evaluate(
        self,
        *,
        stable_key: str,
        attempt: int,
        lease_until: datetime,
        failed_at: datetime,
        failure: AutomationFailure,
        retry_after: RetryAfter,
    ) -> RetryDecision:
        classification = classify_failure(failure)
        if not classification.retryable or attempt >= self._max_attempts:
            return RetryDecision(classification, attempt, failed_at, None)

        delay = self._jittered_delay(stable_key=stable_key, attempt=attempt)
        server_delay = self._bounded_retry_after(retry_after, failed_at)
        if server_delay is not None and server_delay > delay:
            delay = server_delay
        candidate = _add_delay(failed_at, delay)
        return RetryDecision(classification, attempt, failed_at, max(candidate, lease_until))

    def _jittered_delay(self, *, stable_key: str, attempt: int) -> timedelta:
        if attempt - 1 >= self._max_delay_seconds.bit_length():
            base_seconds = self._max_delay_seconds
        else:
            base_seconds = min(
                self._max_delay_seconds,
                self._initial_delay_seconds * (1 << (attempt - 1)),
            )
        base_microseconds = base_seconds * _MICROSECONDS_PER_SECOND
        spread = base_microseconds * self._jitter_basis_points // 10_000
        if spread:
            material = f"automation-retry-v1\0{stable_key}\0{attempt}".encode("utf-8")
            sample = int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
            offset = sample % (2 * spread + 1) - spread
        else:
            offset = 0
        jittered = min(
            self._max_delay_seconds * _MICROSECONDS_PER_SECOND,
            max(0, base_microseconds + offset),
        )
        return _timedelta_from_microseconds(jittered)

    def _bounded_retry_after(self, value: RetryAfter, now: datetime) -> timedelta | None:
        if value is None:
            return None
        if isinstance(value, timedelta):
            delay = value
        elif isinstance(value, datetime):
            _aware(value, "Retry-After date")
            delay = value - now
        else:
            raw = value.strip()
            if not raw:
                return None
            if raw.isascii() and raw.isdecimal():
                if len(raw) > 20:
                    seconds = self._max_delay_seconds
                else:
                    seconds = min(int(raw), self._max_delay_seconds)
                return timedelta(seconds=seconds)
            try:
                parsed = parsedate_to_datetime(raw)
            except (TypeError, ValueError, OverflowError):
                return None
            if parsed is None or parsed.tzinfo is None or parsed.utcoffset() is None:
                return None
            delay = parsed - now
        if delay <= timedelta(0):
            return timedelta(0)
        maximum = timedelta(seconds=self._max_delay_seconds)
        return min(delay, maximum)


def _check_delivery_claim(claim: DeliveryClaim, *, attempt: int, failed_at: datetime) -> None:
    _aware(failed_at, "failed_at")
    if (claim.attempt != attempt or failed_at < claim.claimed_at or failed_at >= claim.lease_until
            or not claim.lease.valid_at(failed_at)):
        raise OutboxBlockedError("delivery failure is outside its live claim lease")


def _check_outbox_claim(claim: OutboxClaim, *, attempt: int, failed_at: datetime) -> None:
    _aware(failed_at, "failed_at")
    lease_until = claim.state.lease_until
    if (claim.state.state is not OutboxState.CLAIMED or claim.state.attempts != attempt
            or lease_until is None or failed_at < claim.claimed_at or failed_at >= lease_until
            or not claim.writer_lease.valid_at(failed_at)):
        raise OutboxBlockedError("outbox failure is outside its live claim lease")


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _timedelta_from_microseconds(value: int) -> timedelta:
    return timedelta(
        seconds=value // _MICROSECONDS_PER_SECOND,
        microseconds=value % _MICROSECONDS_PER_SECOND,
    )


def _add_delay(value: datetime, delay: timedelta) -> datetime:
    try:
        return value + delay
    except OverflowError as exc:
        raise ValueError("retry time exceeds the supported datetime range") from exc


__all__ = [
    "AutomationFailure",
    "AutomationFailureCategory",
    "AutomationFailureReason",
    "AutomationRetryPolicy",
    "FailureClassification",
    "RetryAfter",
    "RetryDecision",
    "classify_failure",
]