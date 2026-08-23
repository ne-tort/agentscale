# P0 k8s sketches (not Helm, not prod-hardened).
#
# Layout mirrors compose brokers from infra/docker-compose.stack.yml:
#   redis/    — C-CACHE + Celery broker (+ PVC)
#   minio/    — C-OBJECT-STORE (+ PVC + bucket-init Job)
#   kafka/    — C-EVENT-BUS Redpanda (+ PVC + topic-init Job)
#   celery/   — C-JOBS worker+beat
#   api/      — API Deployment/Service labels for NetworkPolicy
#   network/  — default-deny ingress + broker allow + API/Celery egress
#   cron/     — HTTP ops hooks (existing)
#
# Apply (dev cluster only):
#   kubectl apply -f deploy/k8s/redis -f deploy/k8s/minio -f deploy/k8s/kafka \
#     -f deploy/k8s/api -f deploy/k8s/celery \
#     -f deploy/k8s/network/brokers-allow.yaml \
#     -f deploy/k8s/network/egress-api-celery.yaml \
#     -f deploy/k8s/network/default-deny.yaml
#
# Order: allow/egress before (or with) default-deny.
# Holes: StorageClass sizing, TLS, Postgres/Keycloak egress CIDRs, multi-replica Kafka, Helm.
# Label API/Celery/cron pods with ``app.kubernetes.io/part-of: prodavan`` for NetworkPolicy.
