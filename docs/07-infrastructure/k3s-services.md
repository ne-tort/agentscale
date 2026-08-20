# k3s Services

Kubernetes manifests и Helm values для сервисов Prodavan в k3s. Расположение: `infra/k3s/`.

---

## Directory layout

```text
infra/k3s/
├── base/
│   ├── namespace.yaml
│   ├── prodavan-api/
│   │   ├── deployment.yaml
│   │   ├── service.yaml
│   │   ├── hpa.yaml
│   │   └── configmap.yaml
│   ├── prodavan-ws/
│   ├── mcp-gateway/
│   ├── redis/
│   └── ingress.yaml
├── overlays/
│   ├── dev/
│   ├── staging/
│   └── prod/
└── workers/
    ├── agent-worker-podtemplate.yaml
    ├── networkpolicy.yaml
    └── rbac.yaml
```

Kustomize overlays patch replicas, resources, domains.

---

## prodavan-api

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: prodavan-api
  namespace: prodavan
spec:
  replicas: 2
  selector:
    matchLabels:
      app: prodavan-api
  template:
    metadata:
      labels:
        app: prodavan-api
    spec:
      containers:
        - name: api
          image: ghcr.io/org/prodavan-api:1.0.0
          ports:
            - containerPort: 8000
          envFrom:
            - configMapRef:
                name: prodavan-config
            - secretRef:
                name: prodavan-api-secrets
          readinessProbe:
            httpGet:
              path: /health/ready
              port: 8000
          livenessProbe:
            httpGet:
              path: /health/live
              port: 8000
          resources:
            requests:
              cpu: 250m
              memory: 512Mi
            limits:
              cpu: "1"
              memory: 1Gi
```

Service: ClusterIP :8000

HPA: CPU 70%, min 2, max 10.

---

## prodavan-ws (SSE gateway)

Option A: same deployment as api with route split.
Option B: dedicated deployment for long-lived connections.

```yaml
# Dedicated WS/SSE deployment
spec:
  replicas: 2
  template:
    spec:
      containers:
        - name: ws
          image: ghcr.io/org/prodavan-api:1.0.0
          args: ["uvicorn", "prodavan.main:app", "--workers", "1"]
          env:
            - name: SSE_MODE
              value: "true"
```

Ingress annotation:

```yaml
nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"
nginx.ingress.kubernetes.io/proxy-send-timeout: "3600"
```

---

## mcp-gateway

```yaml
spec:
  replicas: 2
  template:
    spec:
      containers:
        - name: gateway
          image: ghcr.io/org/prodavan-mcp-gateway:1.0.0
          ports:
            - containerPort: 8080
          env:
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: prodavan-api-secrets
                  key: DATABASE_URL
            - name: REDIS_URL
              valueFrom:
                secretKeyRef:
                  name: prodavan-redis
                  key: REDIS_URL
```

Internal only — no public ingress.

---

## redis

```yaml
# Bitnami Redis chart values (staging/prod)
architecture: replication
auth:
  enabled: true
master:
  persistence:
    enabled: true
    size: 8Gi
replica:
  replicaCount: 2
```

Used for: rate limits (M05), policy cache, session pub/sub.

---

## agent-worker (dynamic pods)

Not a Deployment — pods created by api orchestrator.

```yaml
# infra/k3s/workers/agent-worker-podtemplate.yaml
# Used as template by K8s Python client
apiVersion: v1
kind: Pod
metadata:
  generateName: agent-
  namespace: prodavan-workers
spec:
  restartPolicy: Never
  serviceAccountName: agent-worker
  # ... see worker-isolation.md
```

RBAC:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  namespace: prodavan-workers
  name: agent-worker
rules:
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get"]  # self only via downward API
```

Orchestrator ServiceAccount can `create/delete pods` in `prodavan-workers`.

---

## ingress

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: prodavan
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
spec:
  tls:
    - hosts: [api.prodavan.ru]
      secretName: prodavan-tls
  rules:
    - host: api.prodavan.ru
      http:
        paths:
          - path: /api
            pathType: Prefix
            backend:
              service:
                name: prodavan-api
                port:
                  number: 8000
          - path: /stream
            pathType: Prefix
            backend:
              service:
                name: prodavan-ws
                port:
                  number: 8000
```

---

## postgres (in-cluster dev only)

```yaml
# dev overlay — CNPG or simple StatefulSet
# staging/prod → managed DB outside k3s (terraform)
```

---

## minio (dev)

```yaml
# dev overlay
apiVersion: apps/v1
kind: Deployment
metadata:
  name: minio
  namespace: prodavan-data
spec:
  template:
    spec:
      containers:
        - name: minio
          image: minio/minio:latest
          args: ["server", "/data", "--console-address", ":9001"]
```

---

## Monitoring hooks

Pod annotations:

```yaml
prometheus.io/scrape: "true"
prometheus.io/port: "8000"
prometheus.io/path: "/metrics"
```

---

## Resource quotas (prodavan-workers)

```yaml
apiVersion: v1
kind: ResourceQuota
metadata:
  name: worker-quota
  namespace: prodavan-workers
spec:
  hard:
    pods: "100"
    requests.cpu: "50"
    requests.memory: 100Gi
```

Per-tenant limits enforced in orchestrator before pod create.

---

## Связанные документы

- [topology.md](topology.md)
- [argocd.md](argocd.md)
- [../06-agent-runtime/worker-isolation.md](../06-agent-runtime/worker-isolation.md)
- [env-matrix.md](env-matrix.md)
