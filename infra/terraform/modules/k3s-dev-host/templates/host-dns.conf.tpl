[Resolve]
# Пустое присваивание сбрасывает список: systemd НАКАПЛИВАЕТ значения list-опций
# из всех drop-in (лексикографически), иначе чужие/legacy servers (инцидент
# 2026-10-29: rate-limited 1.1.1.1 из ручного 99-dot.conf) встанут ПЕРЕД нашими.
DNS=
DNS=${dns_servers}
# FallbackDNS очищен осознанно (детерминизм): при недоступности явных upstream
# resolved не должен молча уходить на случайные fallback-серверы systemd —
# лучше явная ошибка резолва, чем непредсказуемый маршрут.
FallbackDNS=
DNSOverTLS=${dns_over_tls}
%{ if stub_extra != "" ~}
DNSStubListenerExtra=${stub_extra}
%{ endif ~}
