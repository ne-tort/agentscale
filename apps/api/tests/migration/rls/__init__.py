"""RLS acceptance package.

The verification freeze is lifted: everything here runs.  Most modules remain
static review surfaces - they inspect the registry, the rendered DDL or a model of
the predicate - but ``test_live_policy_installation`` reads a real PostgreSQL and
``test_policy_name_length`` gates the identifier limit before a name can reach a
server that would silently truncate it.  The live module skips (with a reason)
when ``PRODAVAN_TEST_DATABASE_URL`` is unset, so a database-free run stays green
without pretending to have verified a catalog.
"""
