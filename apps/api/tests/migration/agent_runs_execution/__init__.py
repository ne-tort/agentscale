"""Acceptance artifacts for the SQL ``RunLeaseStore`` and the execution adapter.

The zone owns no production schema (the lease table, its CHECK constraints and
its Alembic revision belong to the persistence slice), so these tests verify the
two things it does own: the exact SQL predicates of every lease statement, and
the fenced composition of the execution port.  See ``acceptance.md`` for the
case matrix and ``fakes.py`` for the doubles.
"""
