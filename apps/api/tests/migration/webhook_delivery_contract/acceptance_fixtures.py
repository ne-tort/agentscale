"""MIG-WEBHOOK-01 acceptance cases; kept unexecuted during verification freeze."""

CASES = (
    "delivery listing exposes subscription_enabled so paused state is visible in the UI",
    "paused subscriptions keep pending delivery state visible after a worker claim is released",
    "removed subscriptions produce exhausted delivery state with released lease evidence",
    "exhausted delivery retains bounded retry history including every attempt",
    "manual retry resets delivery attempt state and appends a manual_retry history marker",
)
