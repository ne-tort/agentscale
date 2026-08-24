# Traefik on host port ${http_port} — binds 0.0.0.0 for WSL localhostForwarding → Windows 127.0.0.1
apiVersion: helm.cattle.io/v1
kind: HelmChartConfig
metadata:
  name: traefik
  namespace: kube-system
spec:
  valuesContent: |-
    hostNetwork: true
    dnsPolicy: ClusterFirstWithHostNet
    service:
      type: ClusterIP
    ports:
      web:
        port: ${http_port}
        exposedPort: ${http_port}
        hostPort: ${http_port}
        hostIP: "0.0.0.0"
