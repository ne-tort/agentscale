"""MIG-AR-BUNDLE-01 acceptance artifacts: assembly of the Agent Run host bundle.

Nothing in this package is executed during the migration freeze; every test is
written to be checkable against ``core/infra/agent_run_bundle.py`` and the
``AgentRunHostDependencies`` contract in ``core/infra/agent_run_worker_resource.py``.

The bundle is composed over a **fake** session factory on purpose: every
constructor in the composition stores its factory and opens a session only when a
method is called, so a full bundle can be assembled, installed and inspected
without a database, a network or the agent bootstrap path.
"""
