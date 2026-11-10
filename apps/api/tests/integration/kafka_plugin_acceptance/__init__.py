"""Real-service Kafka plugin event acceptance harness.

This package must never be reduced to a generic conftest: topic, consumer-group,
SQL schema, Redis and backend resources are owned and cleaned up per test by the
explicit helpers in this directory.
"""
