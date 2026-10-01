# Traefik on host ports — binds 0.0.0.0 (VM: reachable from the Windows host by IP).
# web = plain HTTP (${http_port}), websecure = HTTPS (${https_port}); the default
# cert comes from the TLSStore (prodavan-tls.sh.tpl) when https_tls_sans is set,
# otherwise Traefik generates a self-signed one.
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
%{ if https_port != 0 && https_port < 1024 }
    # Traefik runs non-root: ports <1024 need NET_BIND_SERVICE.
    securityContext:
      capabilities:
        add:
          - NET_BIND_SERVICE
%{ endif }
