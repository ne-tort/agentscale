"""Opt-in real-Kubernetes acceptance harness for plugin lifecycle adapters.

Scope: TokenRequest identity, DeploymentAdmin, SecretProjection against a
DISPOSABLE non-production cluster only. The harness is skipped unless
PLUGIN_ACCEPTANCE_KUBECONFIG points at an explicit disposable kubeconfig.

Out of scope (never faked here): SqlPluginUpdateLock (requires PostgreSQL),
plugin platform/prod wiring, and running plugin backend pods.

Written during the migration verification freeze: this package has never been
executed (no tests/build/lint/compile/kubectl/git verification runs).
"""
