"""Acceptance tests for the complete ``ShadowGatewayPorts`` assembly.

Task ``MIG-M06-PORTS-01``.  The statement under test is narrow and testable:
sixteen fields, each either constructed from a real adapter or named as absent,
and — the point of the whole exercise — ``build_shadow_gateway`` really builds a
shadow facade out of the result.
"""

from __future__ import annotations
