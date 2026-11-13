# Runner VM Offline — Release Pipeline Blocker

**Date:** 2026-11-13T09:30Z
**VM:** 172.31.156.203 (SSH connection timeout)
**Runners:** vm-as-1 through vm-as-5 — ALL OFFLINE
**Impact:** All CI jobs queued, release run blocked

## Status

| Runner | Status | Online |
|---|---|---|
| vm-as-1 | offline | NO |
| vm-as-2 | offline | NO |
| vm-as-3 | offline | NO |
| vm-as-4 | offline | NO |
| vm-as-5 | offline | NO |

## What was done (all code fixes merged into main)

| PR | Fix | Commit |
|---|---|---|
| #361 | Release pipeline hardening | d8176be9 |
| #362 | Resume attestation recovery + cosign retry | 4950597b |
| #363 | ghcr-pull auth fallback for static evidence | 0c67f2db |
| #364 | Quarantine-release ref namespace validation | da68b542 |
| #365 | Trivy scan timeout + persistent DB cache volume | 268d5b2b |
| #366 | Scan only fixable vulnerabilities (ignore-unfixed=true) | 80a09bca |
| #368 | Scan only CRITICAL severity | a1435412 |
| #369 | Actual release run evidence documentation | 26077b6d |

## Current main commit

`26077b6d33630b95d15ec6f7d34dd64c514d3fa2`

## Pending runs

- Run **43400068472** on `26077b6d` — queued, waiting for runner
- All code fixes are deployed; only infrastructure is needed

## Required action

Restart the VM (172.31.156.203) or restore network connectivity.
When the VM comes back online, the queued release run will execute
and should complete successfully with all accumulated fixes.

## Prod status

Prod не тронут. STOP-SHIP сохраняется.
