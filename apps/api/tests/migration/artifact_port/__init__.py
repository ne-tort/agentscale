"""Acceptance tests for ``ArtifactContentPort`` (task ``MIG-ARTIFACT-PORT-01``).

The contract under test lives in
``application/migration/m04e_definition_version/artifact.py``::

    async def resolve_artifact_uri(...) -> str | None
    async def read_artifact(...) -> bytes | None   # ``None`` only when *confirmed* absent

and the M04E backfill additionally hashes what it gets and compares it with the
``artifact_digest`` persisted on the version row.  Every test below pins one of the five
claims the task states:

1. a matching artifact is read (and its digest is proven, not assumed);
2. a digest mismatch is refused -- the store is never believed;
3. an absent artifact is a *typed* refusal, never a silent ``None``;
4. a foreign tenant is refused, not answered with an empty result;
5. ``build_partition_chain`` no longer reports ``partition_artifact_content_port_unavailable``.
"""
