# P0 k8s sketches (not Helm, not prod-hardened).
#
# Layout mirrors compose brokers from infra/docker-compose.stack.yml:
#   redis/    — C-CACHE + Celery broker (+ PVC)
#   minio/    — C-OBJECT-STORE (+ PVC + bucket-init Job)
#   kafka/    — C-EVENT-BUS Redpanda (+ PVC + topic-init Job)
#   celery/   — C-JOBS worker+beat
#   network/  — NetworkPolicy allow ingress to brokers from part-of=prodavan
#   cron/     — HTTP ops hooks (existing)
#
# Apply (dev cluster only):
#   kubectl apply -f deploy/k8s/redis -f deploy/k8s/minio -f deploy/k8s/kafka \
#     -f deploy/k8s/celery -f deploy/k8s/network
#
# Holes: StorageClass sizing, TLS, multi-replica Kafka, Helm, default-deny namespace.
# Label API/Celery pods with ``app.kubernetes.io/part-of: prodavan`` for NetworkPolicy.
