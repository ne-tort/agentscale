"""Sanitized errors for the GitHub App source-control adapter."""

from __future__ import annotations


class GitHubSourceError(RuntimeError):
    """Base error which never contains credentials or HTTP payloads."""


class GitHubConfigurationError(ValueError):
    """The adapter composition is incomplete or unsafe."""


class GitHubValidationError(ValueError):
    """A source-control value does not match the canonical grammar."""


class GitHubOrganizationNotAllowed(GitHubValidationError):
    """The requested owner is not an explicitly managed organization."""


class GitHubClientClosed(GitHubSourceError):
    """An operation was attempted after client shutdown started."""


class GitHubAuthenticationError(GitHubSourceError):
    """The private key could not be loaded or an App JWT could not be signed."""


class GitHubTransportError(GitHubSourceError):
    """GitHub was unreachable; transport details are deliberately suppressed."""


class GitHubConflictError(GitHubSourceError):
    """A pinned base or exclusive branch-creation invariant conflicted."""

    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__(f"GitHub operation {operation!r} conflicted with the pinned repository state")


class GitHubAPIError(GitHubSourceError):
    """GitHub returned a non-success status.

    Only a constant operation name and numeric status are retained. Response
    bodies, request bodies, URLs, headers and transport messages are excluded.
    """

    def __init__(self, operation: str, status_code: int) -> None:
        self.operation = operation
        self.status_code = status_code
        super().__init__(f"GitHub operation {operation!r} failed with HTTP {status_code}")


class GitHubResponseError(GitHubSourceError):
    """A successful response was malformed or violated a security invariant."""

    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__(f"GitHub operation {operation!r} returned an invalid response")


class GitHubResponseTooLarge(GitHubResponseError):
    """A response exceeded the adapter's fixed decoded-byte budget."""

    def __init__(self, operation: str) -> None:
        self.operation = operation
        GitHubSourceError.__init__(
            self, f"GitHub operation {operation!r} exceeded the response size limit"
        )


__all__ = [
    "GitHubAPIError",
    "GitHubAuthenticationError",
    "GitHubClientClosed",
    "GitHubConfigurationError",
    "GitHubConflictError",
    "GitHubOrganizationNotAllowed",
    "GitHubResponseError",
    "GitHubResponseTooLarge",
    "GitHubSourceError",
    "GitHubTransportError",
    "GitHubValidationError",
]
