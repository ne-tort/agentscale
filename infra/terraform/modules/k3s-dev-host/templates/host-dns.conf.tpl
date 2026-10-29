[Resolve]
DNS=${dns_servers}
FallbackDNS=
DNSOverTLS=${dns_over_tls}
%{ if stub_extra != "" ~}
DNSStubListenerExtra=${stub_extra}
%{ endif ~}
