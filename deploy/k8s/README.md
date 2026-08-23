# P0 k8s sketches (not Helm, not prod-hardened).
#
# Layout mirrors compose brokers from infra/docker-compose.stack.yml:
#   redis/   — C-CACHE + Celery broker
#   minio/   — C-OBJECT-STORE
#   kafka/   — C-EVENT-BUS (Redpanda)
#   celery/  — C-JOBS worker+beat
#   cron/    — HTTP ops hooks (existing)
#
# Apply (dev cluster only):
#   kubectl apply -f deploy/k8s/redis -f deploy/k8s/minio -f deploy/k8s/kafka -f deploy/k8s/celery
#
# Holes: PVC, NetworkPolicy, TLS, multi-replica Kafka, image pull secrets, bucket init Job.
