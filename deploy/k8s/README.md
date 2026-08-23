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
# Holes: StorageClass sizing, NetworkPolicy, TLS, multi-replica Kafka, Helm.
# PVC sketches: redis/minio/kafka Deployments bind PersistentVolumeClaims.
# Bucket init: ``deploy/k8s/minio/minio.yaml`` includes Job ``prodavan-minio-init``.
