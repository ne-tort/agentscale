"""Объектное хранилище плагинов: IAM-граница вместо Python-фильтра тегов.

Архитектура v4 §5.4, §4.5, §11 (критерий 19), §13.3.

As-built (что заменяем):
  * один бакет ``agentscale`` на все компании;
  * изоляция тегами ``tenant_infra`` / ``project:`` — это фильтр в
    ``TenantObjectsService._project_assets()``, а не IAM;
  * один service user с ``agentscale/*`` (``minio-api-policy.json``): баг в
    Python-коде = утечка между компаниями;
  * ``put(..., content: bytes)`` / ``get_bytes() -> bytes`` гонят payload через
    API (base64 в JSON) — узкое место по памяти, размеру запроса и логам.

Здесь (целевая модель):
  * bucket-per-tenant ``tenant-<company>`` + жёсткий префикс
    ``module-<plugin>/``, зафиксированный в IAM-политике (``policy.py``);
  * short-lived scoped STS credentials / scoped presigned URL, НИКОГДА root
    или service-креды платформы;
  * ни одного ``bytes`` в публичном контракте: плагин ходит в MinIO :9000
    напрямую (egress разрешён только plugin-namespace, §6.2);
  * ``TenantObjectsService`` остаётся фасадом аудита/квот/листинга, но НЕ
    единственной границей — граница обеспечивается IAM + этим сервисом;
  * fail-closed: недоступен STS / бакет / аудит → ЯВНАЯ ошибка, а не деградация
    до root-кредов, не «тихое» разрешение и не base64 через API.

Аудит: событие ``plugin_object_access`` (§4.4) содержит только канонические
поля — идентификаторы, операцию, решение, код, размер/digest. Ни bytes, ни
payload, ни токенов, ни секретов, ни PII (критерий 11).
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from prodavan.application.plugins.objects import errors as err
from prodavan.application.plugins.objects import policy as iam
from prodavan.application.plugins.objects import ports

AUDIT_EVENT = "plugin_object_access"


def _normalize_key(company_id: str, plugin_id: str, key: str, *, operation: str) -> str:
    """Привести пользовательский ключ к ``module-<plugin>/...`` ИЛИ отказать.

    Это САМАЯ ВАЖНАЯ проверка сервиса. Плагин не должен иметь возможности:
      * выйти из своего префикса через ``../`` (``../../other-company/secret``);
      * обратиться к чужому префиксу ``module-<other>/...``;
      * обратиться к чужому бакету ``tenant-<other>/...``;
      * подсунуть абсолютный путь, ``\\``, ``//``, NUL, wildcard или ``s3://``.

    Нормализация — не «почистить строку», а перестроить ключ из доверенных
    ``(company_id, plugin_id)``: даже если вызывающий уже передал правильный
    префикс, мы не доверяем ему, а отсекаем ТОЛЬКО свой собственный префикс и
    заново добавляем его. Любое расхождение → ``ObjectAccessDenied``.

    ``company_id`` здесь нужен для канонического сообщения об ошибке и для
    явной проверки, что ключ не пытается адресовать другой бакет; бакет в ключе
    не хранится и никогда из ключа не выводится.
    """
    # Валидация идентификаторов через общий генератор: те же правила, что и в
    # ARN политики. Нельзя, чтобы сервис и IAM расходились в понятии «свой».
    iam.bucket_name(company_id)
    prefix = iam.module_prefix(plugin_id)

    if not isinstance(key, str) or not key or "\x00" in key:
        raise err.InvalidObjectKey(f"пустой/некорректный ключ при {operation}")

    # Windows-разделитель, URL-схемы и ведущий '/' трактуем как попытку обхода,
    # а не как «приведём к виду».
    candidate = key.strip().replace("\\", "/")
    if candidate.startswith("/") or "//" in candidate or "://" in candidate:
        raise err.ObjectAccessDenied(
            f"ключ {key!r} использует абсолютный путь/схему при {operation}"
        )
    if any(ch in candidate for ch in ("*", "?", "..")):
        raise err.ObjectAccessDenied(
            f"ключ {key!r} содержит обход пути/wildcard при {operation}"
        )

    # Явная проверка на «чужую компанию» ДО какой-либо нормализации: строка
    # вида tenant-<other>/... не должна молча превратиться в валидный ключ
    # нашего префикса. Проверяем СТРУКТУРУ (префикс ``tenant-``), а не
    # совпадение с конкретным именем чужой компании — иначе политика зависела
    # бы от того, как «другую компанию» назвали в тесте.
    if candidate.lower().startswith("tenant-"):
        raise err.ObjectAccessDenied(
            f"ключ {key!r} адресует чужой бакет/компанию при {operation}"
        )

    # Если ключ уже начинается с нашего префикса — снимаем ровно его и
    # продолжаем проверку хвоста. Если начинается с ЛЮБОГО другого
    # ``module-``/``tenant-`` префикса — отказ, а не переписывание.
    if candidate.startswith(prefix):
        tail = candidate[len(prefix) :]
    elif candidate.startswith("module-") or candidate.startswith("tenant-"):
        raise err.ObjectAccessDenied(
            f"ключ {key!r} адресует чужой префикс при {operation}"
        )
    else:
        tail = candidate

    # Хвост не должен содержать сегментов пути, меняющих область (повторная
    # проверка после возможного снятия префикса), и не должен быть пустым.
    tail = tail.lstrip("/")
    if not tail or ".." in tail.split("/") or tail.startswith("module-"):
        raise err.ObjectAccessDenied(
            f"ключ {key!r} выходит за пределы {prefix!r} при {operation}"
        )

    return f"{prefix}{tail}"


def _digest(value: str) -> str:
    """Канонический digest для аудита вместо сырого значения (§4.5)."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class PluginObjectStore:
    """Единая точка выдачи прав на объекты плагину (capability ``objects``).

    Все зависимости — порты; реализация подключается родителем при интеграции.
    Ни один public-метод не возвращает и не принимает содержимое объекта.
    """

    def __init__(
        self,
        *,
        buckets: ports.BucketAdminPort,
        sts: ports.StsPort | None = None,
        presigner: ports.PresignPort | None = None,
        audit: ports.AuditPort,
        quota_bytes: int = 1024 * 1024 * 1024,
        clock: Any = time,
    ) -> None:
        self._buckets = buckets
        # ``sts``/``presigner`` опциональны на уровне КОНСТРУКТОРА (чтобы можно
        # было собрать сервис в среде без MinIO STS), но НЕ на уровне вызова:
        # отсутствие адаптера → ``StsUnavailable`` / ``BucketUnavailable``.
        self._sts = sts
        self._presigner = presigner
        self._audit = audit
        self._quota_bytes = int(quota_bytes)
        self._clock = clock

    # ------------------------------------------------------------------
    # Аудит — только канонические поля, без bytes/payload/токенов.
    # ------------------------------------------------------------------
    async def _emit(
        self,
        *,
        company_id: str,
        plugin_id: str,
        operation: str,
        decision: str,
        strict: bool = False,
        code: str | None = None,
        reason: str | None = None,
        key_digest: str | None = None,
        ttl_seconds: int | None = None,
        object_size: int | None = None,
    ) -> None:
        """Записать событие ``plugin_object_access``.

        ``strict=True`` для позитивных мутаций/выдачи прав: сбой append = deny
        (§4.5), операция не считается успешной. Для промежуточных deny-событий
        сбой аудита не маскирует исходный отказ в доступе.
        """
        event: dict[str, Any] = {
            "event": AUDIT_EVENT,
            "capability": "objects",
            "company_id": company_id,
            "plugin_id": plugin_id,
            "bucket": iam.bucket_name(company_id),
            "prefix": iam.module_prefix(plugin_id),
            "operation": operation,
            "decision": decision,
            "code": code,
            "reason": reason,
            "key_digest": key_digest,
            "ttl_seconds": ttl_seconds,
            "object_size": object_size,
            "ts": float(self._clock.time()),
        }
        # Страховка от «случайно» переданного адаптером/наследником payload:
        # в событии допустимы только примитивы перечисленных выше типов.
        for field, value in event.items():
            if isinstance(value, (bytes, bytearray, memoryview)):
                raise err.AuditUnavailable(
                    f"поле аудита {field!r} содержит bytes — запись отклонена"
                )
        try:
            await self._audit.record(event)
        except err.AuditUnavailable:
            raise
        except Exception as exc:
            if not strict:
                # Промежуточное deny-событие: исходный отказ важнее сбоя лога.
                return
            # Append failure для позитивной мутации = deny, а не «залогировали
            # потом»: вызывающий получит явную ошибку вместо молчаливого успеха.
            raise err.AuditUnavailable(
                f"не удалось записать {AUDIT_EVENT}: {type(exc).__name__}"
            ) from exc

    # ------------------------------------------------------------------
    # grant / revoke / uninstall (§5.5, критерий 15)
    # ------------------------------------------------------------------
    async def grant(self, company_id: str, plugin_id: str) -> dict[str, Any]:
        """Создать бакет компании, записать политику и квоту.

        Идемпотентно: ``ensure_bucket`` вызывается с КАЖДОЙ парой
        ``(company_id, plugin_id)``; политика всегда пересобирается из явных
        идентификаторов. Плагин НЕ получает право создавать бакеты — это делает
        доверенная платформа.
        """
        bucket = iam.bucket_name(company_id)
        bucket_policy = iam.bucket_policy(company_id, plugin_id)
        try:
            await self._buckets.ensure_bucket(
                bucket, policy=bucket_policy, quota_bytes=self._quota_bytes
            )
        except err.PluginObjectError:
            raise
        except Exception as exc:
            await self._emit(
                company_id=company_id,
                plugin_id=plugin_id,
                operation="grant",
                decision="deny",
                code=err.BucketUnavailable.CODE,
                reason=type(exc).__name__,
            )
            raise err.BucketUnavailable(
                f"ensure_bucket({bucket!r}) недоступен: {type(exc).__name__}"
            ) from exc
        await self._emit(
            company_id=company_id,
            plugin_id=plugin_id,
            operation="grant",
            decision="allow",
            strict=True,
        )
        return {
            "bucket": bucket,
            "prefix": iam.module_prefix(plugin_id),
            "quota_bytes": self._quota_bytes,
            "policy": bucket_policy,
        }

    async def revoke(self, company_id: str, plugin_id: str) -> dict[str, Any]:
        """Мгновенный отзыв capability.

        Долгоживущий ``CapabilityGrant`` деактивируется в gateway (вне зоны
        ответственности этого каталога); здесь фиксируем событие и возвращаем
        область, чьи STS-сессии считаются недействительными. Данные сохраняются
        (§5.5): ``revoke`` НЕ удаляет ни бакет, ни префикс.
        """
        await self._emit(
            company_id=company_id,
            plugin_id=plugin_id,
            operation="revoke",
            decision="allow",
            strict=True,
            reason="capability grant деактивирован; STS-сессии не продлеваются",
        )
        return {
            "bucket": iam.bucket_name(company_id),
            "prefix": iam.module_prefix(plugin_id),
            "bucket_deleted": False,
        }

    async def uninstall(self, company_id: str, plugin_id: str) -> dict[str, Any]:
        """Удалить ТОЛЬКО свой bucket / префикс, не трогая чужие (критерий 15).

        Политика удаления строится из ``company_id``: имя чужого бакета в этом
        вызове физически не может появиться. ``delete_bucket`` получает ровно
        ``tenant-<company>``. При ``delete_own_bucket=False`` адаптер удаляет
        только префикс (режим «префикс + STS», v1-компромисс §5.4), что также
        ограничено ``module-<plugin>/``.
        """
        bucket = iam.bucket_name(company_id)
        prefix = iam.module_prefix(plugin_id)
        owns_bucket = self._delete_own_bucket(company_id)

        # В режиме «несколько плагинов в одном бакете» удаление всего бакета
        # задело бы чужие префиксы: тогда ОБЯЗАТЕЛЬНО нужен delete_prefix,
        # иначе uninstall запрещён (fail-closed), а не «удалим всё».
        delete_prefix = getattr(self._buckets, "delete_prefix", None)
        if not owns_bucket and delete_prefix is None:
            raise err.BucketUnavailable(
                "режим префикса требует BucketAdminPort.delete_prefix"
            )

        # В bucket-per-tenant дополнительная чистка префикса не нужна: весь
        # бакет и так принадлежит этой компании и удаляется целиком.
        if not owns_bucket:
            try:
                await delete_prefix(bucket, prefix)
            except err.PluginObjectError:
                raise
            except Exception as exc:
                raise err.BucketUnavailable(
                    f"удаление префикса {prefix!r} в {bucket!r} недоступно: "
                    f"{type(exc).__name__}"
                ) from exc

        bucket_deleted = False
        if owns_bucket:
            try:
                await self._buckets.delete_bucket(bucket)
                bucket_deleted = True
            except err.PluginObjectError:
                raise
            except Exception as exc:
                raise err.BucketUnavailable(
                    f"delete_bucket({bucket!r}) недоступен: {type(exc).__name__}"
                ) from exc

        await self._emit(
            company_id=company_id,
            plugin_id=plugin_id,
            operation="uninstall",
            decision="allow",
            strict=True,
            object_size=0,
        )
        return {
            "bucket": bucket,
            "prefix": prefix,
            "bucket_deleted": bucket_deleted,
        }

    def _delete_own_bucket(self, company_id: str) -> bool:
        """Можно ли удалить весь бакет.

        По умолчанию ``True`` (bucket-per-tenant, §5.4). Если в адаптере
        включён режим «префикс + STS» (несколько плагинов в одном бакете), он
        выставляет ``owns_bucket=False`` — тогда удаляем только свой префикс
        через ``delete_prefix``; при его отсутствии удаление ЗАПРЕЩЕНО
        (fail-closed), а не «удалим весь бакет за компанию».
        Решение принимается адаптером/конфигурацией, НЕ из пользовательского
        ввода, поэтому подменить компанию здесь нельзя.
        """
        owns = getattr(self._buckets, "owns_bucket", True)
        if callable(owns):
            owns = owns(company_id)
        return bool(owns)

    # ------------------------------------------------------------------
    # Short-lived scoped credentials (критерий 19)
    # ------------------------------------------------------------------
    async def issue_credentials(
        self,
        company_id: str,
        plugin_id: str,
        ttl_seconds: int = ports.DEFAULT_TTL_SECONDS,
    ) -> ports.ScopedCredentials:
        """Выдать short-lived STS creds ТОЛЬКО на ``module-<plugin>/``.

        Гарантии:
          * ``ttl_seconds`` в ``[60, 3600]`` (иначе ``InvalidTtl``, не кламп);
          * ``session_policy`` — подмножество родительской ``bucket_policy``;
          * ``StsPort`` обязателен: нет адаптера/вызов упал → ``StsUnavailable``,
            а НЕ root-креды и НЕ presigned от service user;
          * результат адаптера перепроверяется: пустые ключи, отсутствие
            session_token, чужой bucket или чужой prefix → ``StsUnavailable``
            (fail-closed, адаптеру не доверяем вслепую);
          * «чужая компания не может получить creds на ``tenant-<other>``»:
            ожидаемый бакет вычисляется из своего ``company_id``, и любое
            расхождение отклоняется.
        """
        ttl = ports.validate_ttl(ttl_seconds)
        expected_bucket = iam.bucket_name(company_id)
        expected_prefix = iam.module_prefix(plugin_id)
        s_policy = iam.session_policy(company_id, plugin_id)

        # Session policy обязана быть подмножеством родительской: один генератор
        # в policy.py даёт это конструктивно, но проверяем и здесь (защита от
        # будущей «оптимизации» генератора).
        parent = iam.bucket_policy(company_id, plugin_id)
        if s_policy["Version"] != parent["Version"] or not _is_subset(
            s_policy["Statement"], parent["Statement"]
        ):
            raise err.StsUnavailable("session policy не является подмножеством родительской")

        if self._sts is None:
            await self._emit(
                company_id=company_id,
                plugin_id=plugin_id,
                operation="issue_credentials",
                decision="deny",
                code=err.StsUnavailable.CODE,
                reason="StsPort не сконфигурирован",
                ttl_seconds=ttl,
            )
            raise err.StsUnavailable(
                "STS не сконфигурирован: временные creds не выданы, "
                "root/service-креды запрещены"
            )

        try:
            creds = await self._sts.assume_role(
                company_id=company_id,
                plugin_id=plugin_id,
                ttl_seconds=ttl,
                session_policy=s_policy,
            )
        except err.PluginObjectError:
            raise
        except Exception as exc:
            await self._emit(
                company_id=company_id,
                plugin_id=plugin_id,
                operation="issue_credentials",
                decision="deny",
                code=err.StsUnavailable.CODE,
                reason=type(exc).__name__,
                ttl_seconds=ttl,
            )
            raise err.StsUnavailable(
                f"AssumeRole недоступен: {type(exc).__name__}; "
                "root-креды не выдаются"
            ) from exc

        # Перепроверка результата адаптера: даже «свой» STS не имеет права
        # вернуть creds на другой бакет/префикс или без session policy.
        if not isinstance(creds, ports.ScopedCredentials):
            await self._emit(
                company_id=company_id,
                plugin_id=plugin_id,
                operation="issue_credentials",
                decision="deny",
                code=err.StsUnavailable.CODE,
                reason="адаптер вернул не ScopedCredentials",
                ttl_seconds=ttl,
            )
            raise err.StsUnavailable("STS вернул некорректный тип credentials")
        if (
            not creds.access_key
            or not creds.secret_key
            or not creds.session_token
            or creds.bucket != expected_bucket
            or creds.prefix != expected_prefix
            or creds.policy != s_policy
            or creds.expires_at <= float(self._clock.time())
        ):
            await self._emit(
                company_id=company_id,
                plugin_id=plugin_id,
                operation="issue_credentials",
                decision="deny",
                code=err.StsUnavailable.CODE,
                reason="область/TTL creds не совпадает с ожидаемой",
                ttl_seconds=ttl,
            )
            raise err.StsUnavailable(
                "STS вернул creds вне своего bucket/prefix или без session policy"
            )

        await self._emit(
            company_id=company_id,
            plugin_id=plugin_id,
            operation="issue_credentials",
            decision="allow",
            strict=True,
            ttl_seconds=ttl,
        )
        return creds

    # ------------------------------------------------------------------
    # Presigned URL и листинг
    # ------------------------------------------------------------------
    async def presign(
        self,
        company_id: str,
        plugin_id: str,
        key: str,
        method: str,
        ttl: int = ports.DEFAULT_TTL_SECONDS,
    ) -> str:
        """Scoped presigned URL с ЖЁСТКОЙ нормализацией ключа.

        ``key`` ОБЯЗАТЕЛЬНО приводится к ``module-<plugin>/...``. Попытка
        ``../../other-company/secret``, чужого ``module-<other>/`` или чужого
        ``tenant-<other>/`` → ``ObjectAccessDenied`` (наследник также ловится
        как ``InvalidObjectKey``, см. ``errors.py``).

        URL подписывается на ТОЛЬКО ЧТО нормализованный ключ и на СВОЙ бакет;
        TTL проверяется той же ``validate_ttl`` (60..3600).
        """
        ttl = ports.validate_ttl(ttl)
        if method not in ("get", "put"):
            await self._emit(
                company_id=company_id,
                plugin_id=plugin_id,
                operation="presign",
                decision="deny",
                code=err.InvalidObjectMethod.CODE,
                reason=f"method={method!r}",
            )
            raise err.InvalidObjectMethod(f"недопустимый method: {method!r}")

        try:
            safe_key = _normalize_key(company_id, plugin_id, key, operation="presign")
        except err.ObjectAccessDenied as exc:
            await self._emit(
                company_id=company_id,
                plugin_id=plugin_id,
                operation="presign",
                decision="deny",
                code=exc.CODE,
                key_digest=_digest(str(key)),
                reason="ключ вне своего префикса",
            )
            raise

        if self._presigner is None:
            await self._emit(
                company_id=company_id,
                plugin_id=plugin_id,
                operation="presign",
                decision="deny",
                code=err.BucketUnavailable.CODE,
                key_digest=_digest(safe_key),
                reason="PresignPort не сконфигурирован",
                ttl_seconds=ttl,
            )
            raise err.BucketUnavailable(
                "PresignPort не сконфигурирован: URL не подписан"
            )

        bucket = iam.bucket_name(company_id)
        try:
            if method == "get":
                url = await self._presigner.presign_get(
                    bucket=bucket, key=safe_key, ttl_seconds=ttl
                )
            else:
                url = await self._presigner.presign_put(
                    bucket=bucket, key=safe_key, ttl_seconds=ttl
                )
        except err.PluginObjectError:
            raise
        except Exception as exc:
            await self._emit(
                company_id=company_id,
                plugin_id=plugin_id,
                operation="presign",
                decision="deny",
                code=err.BucketUnavailable.CODE,
                key_digest=_digest(safe_key),
                reason=type(exc).__name__,
                ttl_seconds=ttl,
            )
            raise err.BucketUnavailable(
                f"подпись URL недоступна: {type(exc).__name__}"
            ) from exc

        if not isinstance(url, str) or not url:
            raise err.BucketUnavailable("PresignPort вернул пустой URL")

        await self._emit(
            company_id=company_id,
            plugin_id=plugin_id,
            operation="presign",
            decision="allow",
            strict=True,
            key_digest=_digest(safe_key),
            ttl_seconds=ttl,
        )
        return url

    async def list(self, company_id: str, plugin_id: str) -> list[ports.ObjectInfo]:
        """Листинг ТОЛЬКО своего префикса.

        В листинг уходят только метаданные (``ObjectInfo``): как и весь сервис,
        он не возвращает ``bytes`` и не открывает доступ к содержимому через
        API. Жёсткий префикс передаётся в адаптер ЯВНО; результат
        перепроверяется на случай «широкого» list в реализации порта.
        """
        bucket = iam.bucket_name(company_id)
        prefix = iam.module_prefix(plugin_id)
        lister = getattr(self._buckets, "list_prefix", None)
        if lister is None:
            await self._emit(
                company_id=company_id,
                plugin_id=plugin_id,
                operation="list",
                decision="deny",
                code=err.BucketUnavailable.CODE,
                reason="list_prefix не реализован адаптером",
            )
            raise err.BucketUnavailable(
                "BucketAdminPort.list_prefix не реализован: листинг запрещён"
            )
        try:
            raw = await lister(bucket, prefix)
        except err.PluginObjectError:
            raise
        except Exception as exc:
            raise err.BucketUnavailable(
                f"листинг {prefix!r} недоступен: {type(exc).__name__}"
            ) from exc

        out: list[ports.ObjectInfo] = []
        for item in raw or []:
            if isinstance(item, ports.ObjectInfo):
                info = item
            elif isinstance(item, dict):
                info = ports.ObjectInfo(
                    key=str(item.get("key", "")),
                    size=int(item.get("size") or 0),
                    etag=item.get("etag"),
                )
            else:
                # Неизвестная форма = fail-closed, а не «пропустим и угадаем».
                raise err.BucketUnavailable(
                    f"неожиданный элемент листинга: {type(item).__name__}"
                )
            if not info.key.startswith(prefix) or ".." in info.key:
                # Адаптер вернул чужой ключ — не показываем его плагину.
                raise err.ObjectAccessDenied(
                    f"листинг вернул ключ вне {prefix!r}"
                )
            out.append(info)

        await self._emit(
            company_id=company_id,
            plugin_id=plugin_id,
            operation="list",
            decision="allow",
            object_size=sum(i.size for i in out),
        )
        return out

    async def usage(self, company_id: str) -> int:
        """Фактическое потребление бакета компании (фасад квот, не payload)."""
        bucket = iam.bucket_name(company_id)
        try:
            return int(await self._buckets.get_usage(bucket))
        except err.PluginObjectError:
            raise
        except Exception as exc:
            raise err.BucketUnavailable(
                f"get_usage({bucket!r}) недоступен: {type(exc).__name__}"
            ) from exc


def _is_subset(child: list[dict], parent: list[dict]) -> bool:
    """Проверка «session policy ⊂ родительская» для ``issue_credentials``.

    Сравниваем по канонической JSON-форме Statement: у дочерней политики не
    должно быть ни одного Statement, которого нет (или который шире) у
    родительской. Порядок не важен, лишние разрешения — запрещены.
    """
    import json

    canonical_parent = {
        json.dumps(s, sort_keys=True, separators=(",", ":")) for s in parent
    }
    for statement in child:
        if json.dumps(statement, sort_keys=True, separators=(",", ":")) not in canonical_parent:
            return False
    return True


__all__ = ["AUDIT_EVENT", "PluginObjectStore", "_normalize_key", "_is_subset"]
