# Traefik on host ports — binds 0.0.0.0 for WSL localhostForwarding → Windows 127.0.0.1.
# web = plain HTTP (${http_port}), websecure = HTTPS (${https_port}) with a default
# self-signed cert (Traefik generates one when no TLS store cert matches).
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
%{ if https_port != 0 }
      websecure:
        port: ${https_port}
        exposedPort: ${https_port}
        hostPort: ${https_port}
        hostIP: "0.0.0.0"
        tls:
          enabled: true
%{ endif }
