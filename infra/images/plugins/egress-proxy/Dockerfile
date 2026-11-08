# =============================================================================
# Базовый ИНФРАСТРУКТУРНЫЙ образ egress-proxy (Envoy) для плагинов
# (дизайн v4, §6.1 / §6.3 / §14 п.6; миграция, задача 16, п.5).
#
# КОНФИГ НЕ СТАТИЧЕСКИЙ И НЕ ЗАШИТ В ОБРАЗ. Никакого захардкоженного
# `webhooks.example.com`, никаких кластеров из краткоживущего PluginClaims/JWT.
# Envoy-кластеры и маршруты ГЕНЕРИРУЮТСЯ ЗАДАЧЕЙ 08 из `CapabilityGrant` в БД
# (`allowed_hosts` × `allowed_paths`, режимы none / webhooks-only / full):
# контроллер пишет ключ `envoy.yaml` в ConfigMap `egress-proxy-config`
# (infra/k3s/base/plugins/egress-proxy.yaml) и ОБЯЗАН вызвать reload/hot-restart
# Envoy, иначе новая политика не вступит в силу. Token несёт только `grant_id`:
# иначе истечение JWT пересобирало бы сетевые правила посреди запроса (§6.1).
#
# СТАРТОВОЕ СОСТОЯНИЕ = FAIL-CLOSED (deny all): ни одного upstream-кластера,
# единственный маршрут — `direct_response: 403`. Пока задача 08 не записала
# правила из grant, любой egress плагина ЯВНО отклоняется (а не «тихо» не
# работает) с событием `plugin_egress_request` в аудите.
#
# ВАЖНО ПО ПОСТАВКЕ. Собирается и пушится НАШИМ workflow
# `.github/workflows/plugin-base-images.yml` в ghcr (provenance + SBOM).
# В k3s НЕ тянется напрямую с Docker Hub: нода тянет наш ghcr-образ,
# запиненный по digest. Имя `ghcr.io/ne-tort/plugin-base-envoy` выбрано, чтобы
# НЕ затереть зеркало `ghcr.io/ne-tort/envoy:v1.31.2`.
# =============================================================================

# ЖЁСТКАЯ ФИКСАЦИЯ ВЕРСИИ И DIGEST (п.5 задачи: «уточни актуальную
# стабильную»). В ТЗ был `envoyproxy/envoy:v1.31-latest` — ЭТО ПЛАВАЮЩИЙ ТЕГ,
# использовать его ЗАПРЕЩЕНО (см. README.md, запрет :latest).
#
# Проверено 2026-11-08:
#   * https://raw.githubusercontent.com/envoyproxy/envoy/release/v1.31/VERSION.txt
#     → 1.31.10 (последний стабильный патч ветки v1.31, EOL-линия);
#   * Docker Hub API tag `v1.31.10` → multi-arch manifest-list
#     sha256:caa5b411be1633b90023592a34a7e010c933d6e60206c758f631485e53006865
#     (last_pushed 2025-07-18, amd64 + arm64);
#   * task 14 пока пинует `v1.31.2` (2024-09-19) — родитель перепинит
#     манифест на digest СРАЗУ после первой успешной сборки этого образа.
#
# ARG позволяет подставить ДРУГУЮ запиненную версию, но ТОЛЬКО вместе с
# @sha256:<digest>: плавающий тег сюда передавать нельзя.
ARG ENVOY_BASE_IMAGE=envoyproxy/envoy:v1.31.10@sha256:caa5b411be1633b90023592a34a7e010c933d6e60206c758f631485e53006865
FROM ${ENVOY_BASE_IMAGE}

LABEL org.opencontainers.image.title="agentscale-plugin-base-envoy" \
      org.opencontainers.image.description="Envoy 1.31.10 pinned by digest, non-root, fail-closed egress (config from CapabilityGrant)" \
      org.opencontainers.image.source="https://github.com/ne-tort/agentscale" \
      org.opencontainers.image.base.name="envoyproxy/envoy:v1.31.10@sha256:caa5b411be1633b90023592a34a7e010c933d6e60206c758f631485e53006865"

# -----------------------------------------------------------------------------
# NON-ROOT. Официальный образ envoyproxy/envoy (Ubuntu-based) создаёт
# системного пользователя `envoy` (adduser --group --system envoy) и его
# /docker-entrypoint.sh сам дропает привилегии через su-exec, БУДУЧИ root.
#
# Мы задаём `USER envoy` ЯВНО в НАШЕМ образе. Тогда entrypoint увидит, что
# процесс уже не root, и НЕ будет пытаться su-exec (этот путь требует root).
# Если пользователя `envoy` в базе нет — сборка УПАДЁТ на `id -u` (fail-closed),
# а не тихо уедет под root.
#
# :8080 (egress listener) и :9901 (admin) — НЕ привилегированные порты,
# поэтому non-root биндит их без CAP_NET_BIND_SERVICE. Admin-консоль слушает
# 0.0.0.0 (НЕ 127.0.0.1!): kubelet делает readiness GET <PodIP>:9901/ready
# с УЗЛА, loopback пода ему недоступен. :9901 НЕ вынесен отдельным Service
# и закрыт NetworkPolicy для внешних — доступен только пробам/контроллеру.
# -----------------------------------------------------------------------------
USER root

RUN set -eux; \
    id -u envoy >/dev/null; \
    id -g envoy >/dev/null; \
    envoy --version; \
    install -d -m 0755 /etc/envoy; \
    chown -R envoy:envoy /etc/envoy

# -----------------------------------------------------------------------------
# СТАРТОВЫЙ ЗАКРЫТЫЙ КОНФИГ (fail-closed по умолчанию).
#
# В k3s ЭТОТ файл ПЕРЕКРЫВАЕТ ConfigMap `egress-proxy-config` задачи 14,
# смонтированный КАТАЛОГОМ БЕЗ subPath в /etc/envoy (чтобы kubelet проецировал
# обновления задачи 08). Deployment явно запускает:
#   envoy -c /etc/envoy/envoy.yaml --log-level warn
#
# Конфиг в образе нужен как ЭТАЛОН и как fail-closed дефолт: даже без
# ConfigMap (или до первой генерации задачей 08) Envoy стартует и ОТВЕЧАЕТ
# 403 на любой запрос, а не открывает доступ «по умолчанию» и не падает.
# clusters: [] — ни одного внешнего upstream. НЕ добавлять сюда хосты: это
# ровно та ошибка v1/v3, которую исправляет §6.3.
#
# ВНИМАНИЕ ДЛЯ ЗАДАЧИ 15: ConfigMap правится контроллером ВНЕ Git → нужен
# `ignoreDifferences` на `/data` в Argo Application `plugin-platform`, иначе
# selfHeal вернёт конфиг к пустому deny-all и сотрёт выданные grant'ы.
# -----------------------------------------------------------------------------
COPY --chown=envoy:envoy envoy.yaml /etc/envoy/envoy.yaml

USER envoy:envoy

EXPOSE 8080

# HEALTHCHECK: admin /ready отвечает только когда конфиг загружен (совпадает
# с readinessProbe в egress-proxy.yaml). Образ envoyproxy/envoy основан на
# Ubuntu, /bin/bash с /dev/tcp присутствует (в Dockerfile это shell-форма CMD,
# НЕ JSON-массив с "CMD-SHELL" — тот синтаксис принадлежит Compose).
# 127.0.0.1 ЗДЕСЬ корректен: проверка идёт ИЗНУТРИ контейнера, а admin слушает
# 0.0.0.0 (покрывает и loopback). Не «исправляйте» конфиг обратно на 127.0.0.1
# по этой строке — kubelet пробует <PodIP> с узла, а не loopback пода.
# Отсутствие маршрутов НЕ делает под unhealthy — deny all это ШТАТНОЕ
# стартовое состояние, а не авария.
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=5 \
    CMD bash -c 'exec 3<>/dev/tcp/127.0.0.1/9901 && printf "GET /ready HTTP/1.0\r\nHost: localhost\r\n\r\n" >&3 && head -n 1 <&3 | grep -q " 200 "'

# ENTRYPOINT / CMD наследуются от официального образа:
#   ENTRYPOINT ["/docker-entrypoint.sh"], CMD ["envoy","-c","/etc/envoy/envoy.yaml"]
# Deployment задачи 14 переопределяет command полностью (envoy -c ... --log-level warn),
# поэтому CMD здесь не дублируем, чтобы не разойтись с манифестом.
