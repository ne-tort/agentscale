"""``PluginInfraController`` — fail-closed синхронизация controller-managed /data.

Алгоритм ``sync()`` (СТРОГО в этом порядке, без «частичного успеха»)
--------------------------------------------------------------------
1. Прочитать активные ``CapabilityGrant`` из ``GrantStorePort``.
2. ЧИСТО отрендерить три артефакта (``renderer.py``):
   ``tenant_hba.conf`` / ``userlist.txt`` / ``envoy.yaml``.
3. Прочитать текущие значения ``/data`` трёх объектов (ConfigMapPort /
   SecretPort). Сравнить с рендером: если совпадает — reload НЕ дёргаем.
4. Для КАЖДОГО шага: patch → reload в порядке
   **tenant-PG → PgBouncer → Envoy**.
   Порядок обоснован: pg_hba должен разрешить роль ДО того, как PgBouncer
   начнёт пускать её соединения; Envoy перезагружается последним, чтобы новые
   egress-правила не открылись раньше, чем БД готова принять плагин.
5. Audit (append-only, без секретов/DSN) — строго ПОСЛЕ всех reload.

Fail-closed
-----------
ЛЮБАЯ ошибка (store / render / read / patch / reload / audit) →
``SyncError`` с указанием шага. ``SyncReceipt`` возвращается ТОЛЬКО когда все
шаги подтверждены. Не бывает «2 из 3 применилось, вернём успех»: синхронизация
— одна логическая транзакция, и caller не должен считать grant действующим,
если хотя бы один компонент не перечитал конфиг.

Идемпотентность
---------------
Повторный ``sync()`` без изменений НЕ вызывает ни patch, ни reload: сравнение
идёт по отрендеренной строке целиком (детерминированные рендеры, см.
``renderer.py``). При revoke grant исчезает из ``list_active_grants()`` →
пара/секрет/кластер исчезают из рендера → следующий sync пишет урезанный
артефакт и перезагружает ровно те компоненты, чьё содержимое изменилось.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping

from prodavan.infrastructure.plugins.controllers.errors import (
    STEP_AUDIT,
    STEP_ENVOY_PATCH,
    STEP_ENVOY_RELOAD,
    STEP_PGBOUNCER_RELOAD,
    STEP_TENANT_HBA_PATCH,
    STEP_TENANT_PG_RELOAD,
    STEP_USERLIST_PATCH,
    RenderError,
    SyncError,
)
from prodavan.infrastructure.plugins.controllers.ports import (
    EGRESS_PROXY_CONFIGMAP,
    ENVOY_CONFIG_KEY,
    PGBOUNCER_CREDENTIALS_SECRET,
    PGBOUNCER_USERLIST_KEY,
    TENANT_HBA_KEY,
    TENANT_POSTGRES_CONFIGMAP,
    ActiveGrant,
    AuditPort,
    ConfigMapPort,
    EnvoyReloadPort,
    GrantStorePort,
    PgBouncerReloadPort,
    PostgresReloadPort,
    SecretPort,
    SyncReceipt,
)
from prodavan.infrastructure.plugins.controllers.renderer import (
    build_envoy_policies,
    render_envoy_config,
    render_envoy_yaml,
    render_pgbouncer_userlist_preserving_admin,
    render_tenant_hba,
)

# Имя audit-события (§4.4, единый реестр имён). Одно событие на успешный sync,
# одно на отказ (обе записи — ТОЛЬКО канонические поля, без secret-материала).
AUDIT_EVENT_SYNC = "plugin_infra_sync"
AUDIT_EVENT_SYNC_FAILED = "plugin_infra_sync_failed"


@dataclass(frozen=True, slots=True)
class ControllerPorts:
    """Все зависимости контроллера ОДНИМ объектом.

    Ни один порт не может быть ``None``: собрать «полуподключённый» контроллер
    запрещено (правило §0 плана миграции: отсутствие интеграции = явная ошибка).
    """

    grants: GrantStorePort
    configmaps: ConfigMapPort
    secrets: SecretPort
    postgres: PostgresReloadPort
    pgbouncer: PgBouncerReloadPort
    envoy: EnvoyReloadPort
    audit: AuditPort

    def __post_init__(self) -> None:
        for name in (
            "grants",
            "configmaps",
            "secrets",
            "postgres",
            "pgbouncer",
            "envoy",
            "audit",
        ):
            if getattr(self, name) is None:
                raise SyncError(step=STEP_TENANT_HBA_PATCH, reason="adapter_missing")


@dataclass(frozen=True, slots=True)
class _Artifact:
    """Один шаг patch+reload: имя объекта, ключ, новое и текущее содержимое."""

    patch_step: str
    reload_step: str
    object_name: str
    key: str
    desired: str
    current: str

    @property
    def changed(self) -> bool:
        return self.desired != self.current


class PluginInfraController:
    """Синхронизация трёх controller-managed артефактов plugin-платформы.

    Экземпляр не держит состояния между вызовами: решение «patch или нет»
    принимается ИСКЛЮЧИТЕЛЬНО сравнением рендера с живым ``/data``. Это важно
    при рестарте контроллера и при ручной правке ConfigMap/Secret: состояние
    кластера, а не память процесса, является источником истины.
    """

    def __init__(self, ports: ControllerPorts, *, now: Any = None) -> None:
        if ports is None:
            raise SyncError(step=STEP_TENANT_HBA_PATCH, reason="adapter_missing")
        self._ports = ports
        # ``now`` — фабрика timezone-aware datetime для детерминированных тестов.
        # НЕ путать с ``ClockPort`` (монотонные секунды дебаунса watcher'а).
        self._now_factory = now

    # ------------------------------------------------------------------
    # Публичная точка входа
    # ------------------------------------------------------------------

    def sync(self) -> SyncReceipt:
        """Выполнить полную синхронизацию. Вернуть ``SyncReceipt`` ИЛИ поднять ``SyncError``.

        Никакого «частичного успеха»: если хотя бы один patch/reload/audit упал,
        исключение содержит ``step`` и вызывающий НЕ получает квитанцию.
        """
        ports = self._ports
        if ports is None:  # pragma: no cover - defense in depth
            raise SyncError(step=STEP_TENANT_HBA_PATCH, reason="adapter_missing")

        # --- 1) активные grant'ы -------------------------------------------------
        try:
            raw = ports.grants.list_active_grants()
        except SyncError:
            raise
        except Exception as exc:
            raise SyncError(step=STEP_TENANT_HBA_PATCH, reason="store_failed") from exc
        if raw is None:
            raise SyncError(step=STEP_TENANT_HBA_PATCH, reason="store_failed")
        # Материализуем ОДИН РАЗ: адаптер вправе вернуть генератор, и повторное
        # чтение дало бы пустую выборку (молчаливый revoke всех пар).
        all_grants = tuple(raw)
        grants = tuple(g for g in all_grants if isinstance(g, ActiveGrant) and g.active)
        if len(grants) != len(all_grants):
            # Неактивные/битые записи в выборке активных — нарушение контракта
            # адаптера. Не «пропустим молча», а явно откажем, иначе revoke мог
            # бы не сработать из-за расхождения фильтров.
            raise SyncError(step=STEP_TENANT_HBA_PATCH, reason="store_contract_violation")

        # --- 2) прочитать текущие /data ДО рендера userlist ----------------------
        # userlist требует ``current`` для сохранения строки pgbouncer_admin.
        try:
            tenant_data = ports.configmaps.read_data(name=TENANT_POSTGRES_CONFIGMAP)
            pgb_data = ports.secrets.read_data(name=PGBOUNCER_CREDENTIALS_SECRET)
            envoy_data = ports.configmaps.read_data(name=EGRESS_PROXY_CONFIGMAP)
        except Exception as exc:
            raise SyncError(step=STEP_TENANT_HBA_PATCH, reason="read_failed") from exc
        if not all(isinstance(d, dict) for d in (tenant_data, pgb_data, envoy_data)):
            raise SyncError(step=STEP_TENANT_HBA_PATCH, reason="read_contract_violation")

        # --- 3) чистый рендер трёх артефактов (каждый со своим шагом) -----------
        try:
            desired_hba = render_tenant_hba(grants)
        except RenderError as exc:
            raise SyncError(step=STEP_TENANT_HBA_PATCH, reason=exc.reason) from exc
        except Exception as exc:
            raise SyncError(step=STEP_TENANT_HBA_PATCH, reason="render_failed") from exc

        try:
            policies = build_envoy_policies(grants)
            desired_envoy = render_envoy_yaml(render_envoy_config(policies))
        except RenderError as exc:
            raise SyncError(step=STEP_ENVOY_PATCH, reason=exc.reason) from exc
        except Exception as exc:
            raise SyncError(step=STEP_ENVOY_PATCH, reason="render_failed") from exc

        current_userlist = pgb_data.get(PGBOUNCER_USERLIST_KEY, "")
        try:
            desired_userlist = render_pgbouncer_userlist_preserving_admin(
                grants, current=current_userlist
            )
        except RenderError as exc:
            raise SyncError(step=STEP_USERLIST_PATCH, reason=exc.reason) from exc
        except Exception as exc:
            raise SyncError(step=STEP_USERLIST_PATCH, reason="render_failed") from exc

        artifacts = (
            _Artifact(
                patch_step=STEP_TENANT_HBA_PATCH,
                reload_step=STEP_TENANT_PG_RELOAD,
                object_name=TENANT_POSTGRES_CONFIGMAP,
                key=TENANT_HBA_KEY,
                desired=desired_hba,
                current=tenant_data.get(TENANT_HBA_KEY, ""),
            ),
            _Artifact(
                patch_step=STEP_USERLIST_PATCH,
                reload_step=STEP_PGBOUNCER_RELOAD,
                object_name=PGBOUNCER_CREDENTIALS_SECRET,
                key=PGBOUNCER_USERLIST_KEY,
                desired=desired_userlist,
                current=current_userlist,
            ),
            _Artifact(
                patch_step=STEP_ENVOY_PATCH,
                reload_step=STEP_ENVOY_RELOAD,
                object_name=EGRESS_PROXY_CONFIGMAP,
                key=ENVOY_CONFIG_KEY,
                desired=desired_envoy,
                current=envoy_data.get(ENVOY_CONFIG_KEY, ""),
            ),
        )

        # --- 4) patch → reload СТРОГО в порядке tenant-PG → PgBouncer → Envoy --
        changed: list[str] = []
        reloaded: list[str] = []
        for artifact in artifacts:
            if not artifact.changed:
                # Идемпотентность: совпадает с живым /data → НЕ дёргаем ни patch,
                # ни reload. Рестарт контроллера не перезагружает БД зря.
                continue
            self._patch(artifact, grant_count=len(grants), changed=changed)
            changed.append(artifact.patch_step)
            self._reload(artifact, grant_count=len(grants), changed=changed)
            reloaded.append(artifact.reload_step)

        # --- 5) audit строго ПОСЛЕ подтверждённых reload ------------------------
        occurred_at = self._now()
        event = self._safe_audit_event(
            decision="allow",
            grant_count=len(grants),
            changed_steps=changed,
            reloaded_steps=reloaded,
            occurred_at=occurred_at,
        )
        try:
            ports.audit.record(event)
        except Exception as exc:
            # Аудит — часть транзакции: «успех без следа» = SyncError, а не
            # квитанция. НЕ пытаемся записать событие отказа второй раз: порт
            # аудита уже доказал свою недоступность.
            raise SyncError(step=STEP_AUDIT, reason="audit_failed") from exc

        return SyncReceipt(
            grant_count=len(grants),
            changed_steps=tuple(changed),
            reloaded_steps=tuple(reloaded),
            audit_events=(event,),
            occurred_at=occurred_at,
            details={
                "objects": tuple(a.object_name for a in artifacts),
                "keys": tuple(a.key for a in artifacts),
                "egress_policy_count": len(policies),
            },
        )

    # ------------------------------------------------------------------
    # Внутренние шаги
    # ------------------------------------------------------------------

    def _patch(self, artifact: _Artifact, *, grant_count: int, changed: list[str]) -> None:
        """Точечный patch ОДНОГО ключа /data. Ошибка → ``SyncError`` с шагом."""
        try:
            if artifact.patch_step == STEP_USERLIST_PATCH:
                self._ports.secrets.patch_data(
                    name=artifact.object_name, key=artifact.key, value=artifact.desired
                )
            else:
                self._ports.configmaps.patch_data(
                    name=artifact.object_name, key=artifact.key, value=artifact.desired
                )
        except SyncError:
            raise
        except Exception as exc:
            self._record_failure(
                step=artifact.patch_step,
                reason="patch_failed",
                grant_count=grant_count,
                changed_steps=changed,
            )
            # Текст исключения адаптера НЕ подставляется: там может быть DSN.
            raise SyncError(step=artifact.patch_step, reason="patch_failed") from exc

    def _reload(self, artifact: _Artifact, *, grant_count: int, changed: list[str]) -> None:
        """Reload компонента ПОСЛЕ успешного patch. Ошибка → ``SyncError`` с шагом."""
        try:
            if artifact.reload_step == STEP_TENANT_PG_RELOAD:
                self._ports.postgres.pg_reload_conf()
            elif artifact.reload_step == STEP_PGBOUNCER_RELOAD:
                # НЕ SIGHUP: PgBouncer читает auth_file по ``RELOAD;`` консоли.
                self._ports.pgbouncer.reload()
            else:
                # STEP_ENVOY_RELOAD: hot-reload Envoy + подтверждение /ready.
                self._ports.envoy.hot_reload()
        except SyncError:
            raise
        except Exception as exc:
            self._record_failure(
                step=artifact.reload_step,
                reason="reload_failed",
                grant_count=grant_count,
                changed_steps=changed,
            )
            raise SyncError(step=artifact.reload_step, reason="reload_failed") from exc

    def _record_failure(
        self,
        *,
        step: str,
        reason: str,
        grant_count: int,
        changed_steps: list[str],
    ) -> None:
        """Лучшее усилие записать отказ в аудит (без секретов/DSN/текста исключения).

        Ошибка самой записи НЕ маскирует исходный ``SyncError``: вызывающий всё
        равно получит исключение с исходным шагом. Аудит отказа — диагностика,
        а не ещё один источник «частичного успеха».
        """
        event = self._safe_audit_event(
            decision="deny",
            grant_count=grant_count,
            changed_steps=changed_steps,
            reloaded_steps=(),
            occurred_at=self._now(),
            step=step,
            reason=reason,
        )
        try:
            self._ports.audit.record(event)
        except Exception:
            # Намеренно глушим: текст исключения адаптера может содержать DSN.
            return

    @staticmethod
    def _safe_audit_event(
        *,
        decision: str,
        grant_count: int,
        changed_steps: tuple[str, ...] | list[str],
        reloaded_steps: tuple[str, ...] | list[str],
        occurred_at: datetime,
        step: str | None = None,
        reason: str | None = None,
    ) -> Mapping[str, Any]:
        """Собрать audit-событие ИСКЛЮЧИТЕЛЬНО из канонических полей.

        ЗАПРЕЩЕНО класть сюда: SCRAM-секреты, plaintext-пароли, DSN,
        содержимое ``userlist.txt`` / ``tenant_hba.conf`` / ``envoy.yaml``,
        списки ``grant_id``, тела исключений, строки подключения. Разрешены:
        имя события, решение, шаг, код причины, ИМЕНА объектов/ключей,
        счётчики и время. Тест проверяет отсутствие секретов в ``repr(event)``.
        """
        event: dict[str, Any] = {
            "event": AUDIT_EVENT_SYNC if decision == "allow" else AUDIT_EVENT_SYNC_FAILED,
            "occurred_at": occurred_at.isoformat(),
            "decision": decision,
            "grant_count": grant_count,
            "changed_steps": tuple(changed_steps),
            "reloaded_steps": tuple(reloaded_steps),
            "objects": (
                TENANT_POSTGRES_CONFIGMAP,
                PGBOUNCER_CREDENTIALS_SECRET,
                EGRESS_PROXY_CONFIGMAP,
            ),
            "keys": (TENANT_HBA_KEY, PGBOUNCER_USERLIST_KEY, ENVOY_CONFIG_KEY),
        }
        if step is not None:
            event["step"] = step
        if reason is not None:
            event["reason"] = reason
        return event

    def _now(self) -> datetime:
        if self._now_factory is not None and hasattr(self._now_factory, "now"):
            return self._now_factory.now()
        return datetime.now(UTC)


__all__ = ["AUDIT_EVENT_SYNC", "AUDIT_EVENT_SYNC_FAILED", "ControllerPorts", "PluginInfraController"]
