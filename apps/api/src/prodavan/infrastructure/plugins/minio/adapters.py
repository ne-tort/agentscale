"""MinIO-адаптеры портов объектной capability плагинов (задача 06 → F3, v4 §5.4).

Реализуются контракты ``application/plugins/objects/ports.py``:
``BucketAdminPort``, ``StsPort``, ``PresignPort``, ``AuditPort``.

Ключевые правила (fail-closed, без ``bytes``):

* Ни один метод НЕ принимает и НЕ возвращает ``bytes`` / payload: только имена
  бакетов, префиксы, ключи, TTL, политики и метаданные. Плагин ходит в MinIO
  напрямую по short-lived scoped creds / presigned URL (§5.4).
* Бакет — ТОЛЬКО ``tenant-<company>`` (генератор ``policy.bucket_name``). Имя
  ``agentscale`` (as-built) и любые «не tenant-*» имена отвергаются: адаптер не
  должен дать одному service user'у с ``agentscale/*`` снова стать общей дырой.
* ``ensure_bucket`` **МЕРДЖИТ** политики, а не перезаписывает: у компании может
  быть несколько плагинов, каждый вызывает grant со своей ``bucket_policy``;
  перезапись отозвала бы доступ у уже установленных плагинов (важный контракт
  из отчёта задачи 06). Мердж идемпотентный и дедуплицирует по каноническому
  JSON Statement (НЕ по Sid — Sid у всех плагинов одинаковые).
* ``MinioPresigner`` — **ОСНОВНОЙ** путь выдачи доступа (вердикт родителя: STS
  не подтверждён), подписывает ОТДЕЛЬНЫМ principal ``plugin-object-presigner``
  с узкой политикой только на ``tenant-*/module-*``. As-built
  ``agentscale-api`` имеет ``agentscale/*`` — подпись им запрещена.
* Любая ошибка MinIO/Admin API → ``BucketUnavailable`` (или ``StsUnavailable``
  для STS), а не «и так сойдёт» и не тихое разрешение.
* Ключи нормализуются в ``module-<plugin>/...``. Адаптер НЕ доверяет сервису и
  перепроверяет жёсткий префикс сам: ``../``, чужой ``module-*``/``tenant-*``,
  абсолютный путь, ``//``, ``\\``, wildcard, NUL → ``ObjectAccessDenied``.
* При отключённом/недоступном STS — ``StsUnavailable``, НИКОГДА root-креды и
  НИКОГДА постоянные креды service user'а. ``StsPort``/``ScopedCredentials``
  сохранены как opt-in за ``PLUGIN_MINIO_STS_ENABLED``.
* Presigned URL и ``X-Amz-Signature`` НЕ логируются (аудит — только
  ``key_digest``). Отзыв URL не мгновенный: они живут до ``X-Amz-Expires``
  ≤ 3600 с; ``revoke`` = запрет НОВЫХ URL + деактивация grant, мгновенный
  отзыв = ротация ключа подписанта (чужой ``service.py`` задачи 06 НЕ правлен).
* In-cluster host не резолвится снаружи: для внешних клиентов подпись СРАЗУ на
  внешний host (``PLUGIN_MINIO_PUBLIC_ENDPOINT``) либо ``proxy=1``; перепись
  Host ПОСЛЕ подписи инвалидирует SigV4.

Технически: boto3 уже используется в ``infrastructure/files/minio_store.py``,
новых зависимостей НЕ добавляем. Синхронные вызовы boto3 выносятся в
``asyncio.to_thread`` (как ``infrastructure/files/manager.py``), чтобы не блокировать
event loop. Клиент создаётся лениво и инжектируется (``client=...``) — тесты
работают с in-memory fake без сети/MinIO.

ВАЖНО — НЕ ПОДТВЕРЖДЕНО ОФИЦИАЛЬНОЙ ДОКУМЕНТАЦИЕЙ MINIO
(в этой сессии документация недоступна, образ
``RELEASE.2024-10-02T17-50-41Z`` в ``minio.yaml``):
  1. MinIO STS ``AssumeRole`` + session policy (``Policy=json.dumps(...)``),
     ``DurationSeconds`` и ``StringLike`` по ``s3:prefix`` (v4 §12.8, план §5.1).
     Сеть недоступна, документ НЕ ПОДТВЕРЖДЁН (вердикт родителя): scoped presigned
     по умолчанию, STS opt-in; ``MinioSts`` отвечает ``StsUnavailable`` ДО вызова.
  2. Метод MinIO Admin API для **жёсткой** квоты (``SetBucketQuota`` /
     ``mc admin bucket quota ... --hard``). boto3 S3-клиент таких операций не
     имеет, поэтому ``set_quota`` делегируется инжектированному
     ``QuotaAdminPort`` (реализация через mc/Admin API — зона родителя), либо
     fail-closed ``BucketUnavailable``. ``get_usage`` считает сумму ``Size``
     через ``list_objects_v2`` — это observability, НЕ hard limit, но и НЕ
     «тихий успех»: квота без администратора явно недоступна.

Все комментарии — на русском, идентификаторы — английские.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Protocol, runtime_checkable
from urllib.parse import unquote, urlsplit

from prodavan.application.plugins.objects import errors as err
from prodavan.application.plugins.objects import policy as iam
from prodavan.application.plugins.objects import ports
from prodavan.application.plugins.objects.service import AUDIT_EVENT
from prodavan.infrastructure.plugins.minio.config import (
    DEFAULT_CHECKSUM_MAX_BYTES,
    MAX_CHECKSUM_MAX_BYTES,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Общие константы и проверки имён (fail-closed)
# ---------------------------------------------------------------------------

#: Префикс имён бакетов компаний. Любой другой бакет адаптеру запрещён.
_TENANT_BUCKET_PREFIX = "tenant-"

#: Имя as-built общего бакета. Явный запрет (НЕ строка из настроек, НЕ ввод).
_FORBIDDEN_BUCKETS = frozenset({"agentscale", "agentscale-api", "prodavan"})

#: Допустимые S3-действия в политике бакета. Плагину нельзя CreateBucket,
#: DeleteBucket, PutBucketPolicy, s3:* — это прерогатива доверенной платформы.
_ALLOWED_POLICY_ACTIONS = frozenset(iam.OBJECT_ACTIONS)

#: Разрешённые ключи верхнего уровня IAM-политики (защита от инъекции).
_ALLOWED_POLICY_TOP_KEYS = frozenset({"Version", "Statement"})
_ALLOWED_STATEMENT_KEYS = frozenset(
    {"Sid", "Effect", "Action", "Resource", "Condition", "Principal"}
)

# Local guards remain mandatory alongside botocore's restored model validation.
# MinIO STS has a separate 900..3600-second contract; presign stays 60..3600.
_STS_ROLE_ARN_RE = re.compile(r"^arn:aws:iam::[0-9]{12}:role/plugin-object-access$")
_STS_ROLE_SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9_+=,.@-]{2,64}$")
_ASSUME_ROLE_PARAMETER_NAMES = frozenset(
    {"RoleArn", "RoleSessionName", "DurationSeconds", "Policy"}
)


def _validate_sts_role_arn(value: Any) -> str:
    if not isinstance(value, str) or _STS_ROLE_ARN_RE.fullmatch(value) is None:
        raise err.StsUnavailable("STS RoleArn failed typed validation")
    return value


def _validate_sts_role_session_name(value: Any) -> str:
    if not isinstance(value, str) or _STS_ROLE_SESSION_NAME_RE.fullmatch(value) is None:
        raise err.StsUnavailable("STS RoleSessionName failed typed validation")
    return value


def _validate_bucket(bucket: str) -> str:
    """Разрешить ТОЛЬКО ``tenant-<company>`` из валидного идентификатора.

    Сравниваем с каноническим ``bucket_name`` после обратной проверки: имя
    ``tenant-co_other`` не должно случайно совпасть с чьим-то «почти таким же».
    Строим ожидаемое имя из ``company_id``, извлечённого из строки, и требуем
    точного равенства — подмена регистра/символов исключена генератором задачи 06.
    """
    if not isinstance(bucket, str) or not bucket:
        raise err.ObjectAccessDenied("пустой/нестроковый bucket")
    if bucket in _FORBIDDEN_BUCKETS or not bucket.startswith(_TENANT_BUCKET_PREFIX):
        raise err.ObjectAccessDenied(
            f"бакет {bucket!r} не является tenant-<company>: доступ к as-built запрещён"
        )
    company_id = bucket[len(_TENANT_BUCKET_PREFIX) :]
    try:
        expected = iam.bucket_name(company_id)
    except err.PluginObjectError as exc:
        raise err.ObjectAccessDenied(
            f"некорректное имя бакета {bucket!r}"
        ) from exc
    if bucket != expected:
        raise err.ObjectAccessDenied(f"имя бакета {bucket!r} не канонично")
    return bucket


def _module_prefix(plugin_id: str) -> str:
    """Жёсткий префикс плагина через генератор задачи 06."""
    try:
        return iam.module_prefix(plugin_id)
    except err.PluginObjectError as exc:
        raise err.ObjectAccessDenied(
            f"некорректный plugin_id: {plugin_id!r}"
        ) from exc


def _normalize_prefix(prefix: str) -> str:
    """Проверить, что префикс — ровно ``module-<plugin>/`` (не шире, не чужой).

    ``prefix`` приходит от доверенного сервиса, но адаптер не имеет права
    расширять его: ``delete_prefix``/``list_prefix`` с ``prefix=""`` удалили бы
    или показали бы чужие плагины компании.
    """
    if not isinstance(prefix, str) or not prefix:
        raise err.ObjectAccessDenied("пустой префикс: операция запрещена")
    candidate = prefix.replace("\\", "/")
    if not candidate.endswith("/") or not candidate.startswith("module-"):
        raise err.ObjectAccessDenied(
            f"префикс {prefix!r} не является module-<plugin>/"
        )
    plugin_id = candidate[len("module-") : -1]
    if "/" in plugin_id:
        raise err.ObjectAccessDenied(f"префикс {prefix!r} содержит путь")
    try:
        expected = _module_prefix(plugin_id)
    except err.ObjectAccessDenied as exc:
        raise err.ObjectAccessDenied(
            f"префикс {prefix!r} не каноничен"
        ) from exc
    if candidate != expected:
        raise err.ObjectAccessDenied(f"префикс {prefix!r} не каноничен")
    return candidate


def _normalize_key(key: str, *, expected_prefix: str | None = None) -> str:
    """Перепроверка ключа НА УРОВНЕ АДАПТЕРА (сервис уже нормализовал его).

    Возвращает канонический ``module-<plugin>/...``. Отвергает:
      * ``../`` / ``..\\`` / сегмент ``..`` (обход пути);
      * абсолютный путь, ``//``, ``s3://``, ``://``, ``\\``;
      * ``tenant-*`` (чужой бакет), ``module-*`` в ХВОСТЕ (чужой плагин);
      * NUL, ``*``/``?`` (wildcard), пустой хвост;
      * ключ БЕЗ префикса ``module-<plugin>/`` (адаптер не «достраивает» его
        сам: сервис обязан был это сделать, расхождение = отказ, а не угадывание);
      * при заданном ``expected_prefix`` — ключ ЛЮБОГО другого ``module-<plugin>/``
        («начинается с какого-нибудь module-*» само по себе недостаточно).

    Это дублирует ``service._normalize_key`` намеренно: граница IAM + адаптер,
    а не «доверие одному слою» (ports.py: «порт НЕ доверяет входу»).

    ``expected_prefix`` — канонический ``module-<plugin>/`` доверенного
    вызывающего (сервис задачи 06 / фабрика). Если он задан, адаптер обязан
    сверить, что ключ лежит ИМЕННО в этом префиксе: проверка «начинается с
    любого ``module-*``» недостаточна, иначе ``module-other/secret`` (чужой
    плагин той же компании) был бы подписан. Без ``expected_prefix`` (прямой
    вызов адаптера) действует структурная проверка: префикс каноничен, хвост
    не адресует другой ``module-*``; привязка к конкретному плагину тогда
    обеспечивается вызывающим (``service._normalize_key``).
    """
    if not isinstance(key, str) or not key or "\x00" in key:
        raise err.InvalidObjectKey("пустой/некорректный ключ объекта")

    candidate = key.strip().replace("\\", "/")
    if expected_prefix is not None:
        # Жёсткая привязка к СВОЕМУ плагину ДО любой другой нормализации:
        # «начинается с любого module-*» НЕ означает «начинается с моего».
        # Ключ ``module-other/secret`` (чужой плагин той же компании) обязан
        # быть отклонён здесь, а не подписан (v4 §5.4, fail-closed).
        try:
            canonical_expected = _normalize_prefix(expected_prefix)
        except err.ObjectAccessDenied as exc:
            raise err.ObjectAccessDenied(
                "ожидаемый префикс плагина некорректен — подпись запрещена"
            ) from exc
        if not candidate.startswith(canonical_expected):
            raise err.ObjectAccessDenied(
                f"ключ {key!r} вне префикса {canonical_expected!r}: "
                "подпись чужого module-* запрещена"
            )
        tail_expected = candidate[len(canonical_expected) :]
        if not tail_expected or tail_expected.startswith("/"):
            raise err.ObjectAccessDenied(
                f"ключ {key!r} не указывает объект внутри {canonical_expected!r}"
            )
    if candidate.startswith("/") or "//" in candidate or "://" in candidate:
        raise err.ObjectAccessDenied(
            f"ключ {key!r} использует абсолютный путь/схему"
        )
    if any(ch in candidate for ch in ("*", "?", "..")):
        raise err.ObjectAccessDenied(f"ключ {key!r} содержит обход пути/wildcard")
    if candidate.lower().startswith("tenant-"):
        raise err.ObjectAccessDenied(f"ключ {key!r} адресует чужой бакет")

    # Ключ ОБЯЗАН уже лежать в своёмmodule-префиксе. Определяем его структуру,
    # не принимая «голый» ключ: иначе адаптер начал бы сам решать, к какому
    # плагину отнести объект, что противоречит контракту задачи 06.
    if not candidate.startswith("module-"):
        raise err.ObjectAccessDenied(
            f"ключ {key!r} не нормализован в module-<plugin>/..."
        )
    slash = candidate.find("/")
    if slash <= len("module-"):
        raise err.InvalidObjectKey(f"ключ {key!r} не содержит префикса плагина")
    prefix = candidate[: slash + 1]
    tail = candidate[slash + 1 :]
    canonical_prefix = _normalize_prefix(prefix)
    if not tail or tail.startswith("/") or ".." in PurePosixPath(tail).parts:
        raise err.ObjectAccessDenied(f"ключ {key!r} выходит за пределы {prefix!r}")
    # Хвост не должен начинаться с «чужого» префикса плагина (как в service):
    # ``module-mod_a/module-mod_b/...`` — попытка выйти из своей области.
    if tail.startswith("module-") or tail.lower().startswith("tenant-"):
        raise err.ObjectAccessDenied(f"ключ {key!r} адресует чужой префикс/бакет")
    return f"{canonical_prefix}{tail}"


def _validate_bucket_policy(policy: Mapping[str, Any], bucket: str) -> dict[str, Any]:
    """Проверить политику перед записью: только tenant-* ARN, только свои actions.

    Защита от «широкой» политики: ``Resource: ["*"]``, ``s3:*``,
    ``arn:aws:s3:::agentscale/*`` или чужой ``tenant-*`` не должны попасть в
    бакет компании через ``ensure_bucket``.
    """
    if not isinstance(policy, Mapping):
        raise err.BucketUnavailable("политика бакета должна быть mapping")
    unknown_top = set(policy) - _ALLOWED_POLICY_TOP_KEYS
    if unknown_top:
        raise err.BucketUnavailable(
            f"недопустимые ключи политики: {sorted(unknown_top)!r}"
        )
    if policy.get("Version") != iam.POLICY_VERSION:
        raise err.BucketUnavailable("некорректная Version политики")
    statements = policy.get("Statement")
    if not isinstance(statements, Sequence) or isinstance(statements, (str, bytes)) or not statements:
        raise err.BucketUnavailable("политика бакета без Statement")

    bucket_arn = f"arn:aws:s3:::{bucket}"
    checked: list[dict[str, Any]] = []
    for raw in statements:
        if not isinstance(raw, Mapping):
            raise err.BucketUnavailable("Statement политики не mapping")
        unknown = set(raw) - _ALLOWED_STATEMENT_KEYS
        if unknown:
            raise err.BucketUnavailable(f"недопустимые поля Statement: {sorted(unknown)!r}")
        if raw.get("Effect") != "Allow":
            # Deny-правила в РОДИТЕЛЬСКОЙ политике не нужны: fail-closed
            # обеспечивается отсутствием Allow. Явный запрет не должен тихо
            # переопределяться мерджем.
            raise err.BucketUnavailable("Statement политики должен быть Allow")
        actions = raw.get("Action")
        actions = [actions] if isinstance(actions, str) else actions
        if not isinstance(actions, Sequence) or isinstance(actions, (str, bytes)) or not actions:
            raise err.BucketUnavailable("Statement политики без Action")
        for action in actions:
            if action not in _ALLOWED_POLICY_ACTIONS:
                raise err.BucketUnavailable(f"недопустимое действие политики: {action!r}")
        resources = raw.get("Resource")
        resources = [resources] if isinstance(resources, str) else resources
        if not isinstance(resources, Sequence) or isinstance(resources, (str, bytes)) or not resources:
            raise err.BucketUnavailable("Statement политики без Resource")
        expected_prefix = f"{bucket_arn}/module-"
        for resource in resources:
            if not isinstance(resource, str) or resource == "*":
                raise err.BucketUnavailable("Resource '*' в политике запрещён")
            if resource != bucket_arn and not resource.startswith(expected_prefix):
                raise err.BucketUnavailable(
                    f"Resource {resource!r} вне бакета {bucket!r}/module-*"
                )
        checked.append(dict(raw))
    return {"Version": iam.POLICY_VERSION, "Statement": checked}


def _statement_key(statement: Mapping[str, Any]) -> str:
    """Стабильный канонический ключ Statement для дедупликации при мердже.

    ВАЖНО: НЕ используем ``Sid`` как ключ. Генератор задачи 06 даёт КАЖДОМУ
    плагину одни и те же Sid (``ListOwnModulePrefix``,
    ``ReadWriteOwnModuleObjects``), различаются только ``Resource``/``Condition``
    с ``module-<plugin>/``. Дедуп по Sid перезаписал бы Statement предыдущего
    плагина — ровно тот баг, который требует исправить «мердж, а не
    перезапись». Ключ = канонический JSON ВСЕГО Statement: повторный grant того
    же плагина даёт тот же ключ (идемпотентно), другой плагин — другой ключ.
    """
    return json.dumps(statement, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def merge_bucket_policies(
    existing: Mapping[str, Any] | None,
    incoming: Mapping[str, Any],
    *,
    bucket: str,
) -> dict[str, Any]:
    """СМЕРДЖИТЬ политики нескольких плагинов одной компании (НЕ перезаписать).

    Контракт из отчёта задачи 06: ``grant`` вызывается для КАЖДОЙ пары
    ``(company_id, plugin_id)``; ``ensure_bucket`` не должен отзывать доступ у
    уже установленных плагинов. Поэтому:

      * берём Statement из текущей ``get_bucket_policy`` (если бакет уже есть);
      * добавляем Statement из новой (провалидированной) политики;
      * дедуплицируем по каноническому JSON Statement — НЕ по ``Sid``: Sid у
        всех плагинов одинаковые (``policy.py``), а различаются ARN/префиксы.
        Повторный grant того же плагина идемпотентен, grant другого плагина
        добавляет НОВЫЕ Statement, не затирая предыдущие;
      * НИКОГДА не удаляем существующие Statement (кроме точного дубля) —
        удаление префикса делается только явным ``revoke/uninstall``;
      * результат снова прогоняется через валидацию (широкая/чужая политика
        из «прошлого» состояния не может легализоваться мерджем).

    Функция чистая и экспортируется: юнит-тесты проверяют мердж без MinIO.
    """
    incoming_checked = _validate_bucket_policy(incoming, bucket)
    merged: dict[str, dict[str, Any]] = {}

    if existing is not None:
        if not isinstance(existing, Mapping):
            raise err.BucketUnavailable("существующая политика бакета не mapping")
        raw_statements = existing.get("Statement") or []
        if isinstance(raw_statements, Mapping):
            # MinIO/AWS иногда отдают один Statement объектом, а не списком.
            raw_statements = [raw_statements]
        if isinstance(raw_statements, Sequence) and not isinstance(raw_statements, (str, bytes)):
            for statement in raw_statements:
                if not isinstance(statement, Mapping):
                    raise err.BucketUnavailable(
                        "существующая политика содержит не-mapping Statement"
                    )
                checked = _validate_bucket_policy(
                    {"Version": existing.get("Version", iam.POLICY_VERSION),
                     "Statement": [statement]},
                    bucket,
                )["Statement"][0]
                merged[_statement_key(checked)] = checked

    for statement in incoming_checked["Statement"]:
        # Тот же плагин (тот же канонический JSON при повторном grant) →
        # идемпотентное обновление; Statement других плагинов НЕ трогаем.
        merged[_statement_key(statement)] = statement

    result = {
        "Version": iam.POLICY_VERSION,
        "Statement": [merged[k] for k in sorted(merged)],
    }
    return _validate_bucket_policy(result, bucket)


# ---------------------------------------------------------------------------
# boto3-клиент: создание и обёртка вызовов
# ---------------------------------------------------------------------------


def _build_client(
    config: Any,
    *,
    access_key: str | None = None,
    secret_key: str | None = None,
    endpoint: str | None = None,
) -> Any:
    """Создать S3-клиент boto3 из ``MinioPluginConfig`` (зависимость уже есть).

    По умолчанию — ADMIN-креды (``BucketAdminPort``). ``MinioPresigner``
    ОБЯЗАТЕЛЬНО передаёт ``access_key``/``secret_key`` подписывающего principal
    и, при внешних клиентах, ``endpoint=config.presign_endpoint``: подпись от
    as-built ``agentscale-api`` (у того ``agentscale/*``) или от admin-ключа
    унаследовала бы слишком широкие права (вердикт родителя).

    Повторяем настройки ``minio_store.MinioFileStore``: ``signature_version``
    s3v4 и ``request_checksum_calculation="when_required"`` (MinIO отвечает
    ``MissingContentMD5`` на DeleteObjects при новых botocore).
    """
    import boto3
    from botocore.client import Config

    config_kwargs: dict[str, Any] = {"signature_version": "s3v4"}
    try:
        Config(**{**config_kwargs, "request_checksum_calculation": "when_required"})
        config_kwargs["request_checksum_calculation"] = "when_required"
    except TypeError:
        # Старый botocore: параметра нет, Content-MD5 считается сам.
        pass
    return boto3.client(
        "s3",
        endpoint_url=endpoint or config.endpoint,
        aws_access_key_id=access_key or config.admin_access_key,
        aws_secret_access_key=secret_key or config.admin_secret_key,
        region_name=config.region,
        config=Config(**config_kwargs),
    )


async def _call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Синхронный boto3-вызов в потоке; любое исключение → caller мапит в ошибку.

    Намеренно НЕ глотаем и НЕ логируем значения аргументов: среди них могут
    быть политики, но не секреты (секреты живут только в конфигурации клиента).
    """
    return await asyncio.to_thread(fn, *args, **kwargs)


class _ChecksumStreamFailure(RuntimeError):
    """Internal fixed-code failure; never carries backend text or object bytes."""


def _head_sha256(raw: Mapping[str, Any]) -> str | None:
    """Read only a genuine SHA-256 field, never an S3 ETag."""
    metadata = raw.get("Metadata") or {}
    if isinstance(metadata, Mapping):
        for name, candidate in metadata.items():
            if str(name).lower() not in {"sha256", "x-amz-meta-sha256"}:
                continue
            if not isinstance(candidate, str):
                continue
            value = candidate.strip().lower()
            if value.startswith("sha256:"):
                value = value[len("sha256:"):]
            if len(value) == 64 and all(ch in "0123456789abcdef" for ch in value):
                return f"sha256:{value}"
    checksum = raw.get("ChecksumSHA256")
    if isinstance(checksum, str):
        try:
            decoded = base64.b64decode(checksum.strip(), validate=True)
        except (ValueError, TypeError):
            decoded = b""
        if len(decoded) == 32:
            return f"sha256:{decoded.hex()}"
    return None


def _stream_sha256(
    body: Any,
    *,
    max_bytes: int,
    chunk_size: int,
    expected_size: int | None,
) -> str:
    """Hash a boto body with a hard byte cap and close it exactly once."""
    digest = hashlib.sha256()
    total = 0
    chunks: Any = None
    try:
        if expected_size is not None and expected_size > max_bytes:
            raise _ChecksumStreamFailure("object_checksum_size_cap_exceeded")
        iter_chunks = getattr(body, "iter_chunks", None)
        read = getattr(body, "read", None)
        if not callable(iter_chunks) and not callable(read):
            raise _ChecksumStreamFailure("object_checksum_body_invalid")
        chunks = iter_chunks(chunk_size=chunk_size) if callable(iter_chunks) else None
        while True:
            if chunks is not None:
                try:
                    chunk = next(chunks)
                except StopIteration:
                    break
            else:
                chunk = read(chunk_size)
                if chunk == b"":
                    break
            if not isinstance(chunk, bytes):
                raise _ChecksumStreamFailure("object_checksum_chunk_invalid")
            total += len(chunk)
            if total > max_bytes:
                raise _ChecksumStreamFailure("object_checksum_size_cap_exceeded")
            digest.update(chunk)
        if expected_size is not None and total != expected_size:
            raise _ChecksumStreamFailure("object_checksum_size_mismatch")
        return f"sha256:{digest.hexdigest()}"
    finally:
        close_chunks = getattr(chunks, "close", None)
        try:
            if callable(close_chunks):
                close_chunks()
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()


# ---------------------------------------------------------------------------
# Порт квот (MinIO Admin API, НЕ входит в boto3 S3-клиент)
# ---------------------------------------------------------------------------


@runtime_checkable
class QuotaAdminPort(Protocol):
    """Мини-порт для жёсткой квоты бакета (реализуется через ``mc admin``).

    Вынесен отдельно, потому что boto3 S3 API не умеет ``SetBucketQuota``.
    Пока официальная документация MinIO Admin API недоступна, фабрика НЕ
    подключает реализацию и ``MinioBucketAdmin.set_quota`` fail-closed.
    """

    async def set_hard_quota(self, bucket: str, quota_bytes: int) -> None: ...

    async def enable_quota_enforcement(self) -> None: ...

    async def probe(self) -> None:
        """Проверить доступность Admin API ДО мутации (fail-fast).

        Реализация должна поднять исключение, если квоты не включены; тогда
        ``ensure_bucket`` откажет ДО создания бакета, не оставляя частичного
        состояния (бакет есть, а квоты нет = безлимит).
        """
        ...


class UnavailableQuotaAdmin:
    """Fail-closed заглушка: квоты НЕ включены → явный отказ, не «безлимит»."""

    async def set_hard_quota(self, bucket: str, quota_bytes: int) -> None:
        raise err.BucketUnavailable(
            "MinIO Admin API квот не сконфигурирован: жёсткая квота недоступна "
            "(НЕ деградация до безлимита)"
        )

    async def enable_quota_enforcement(self) -> None:
        raise err.BucketUnavailable("MinIO Admin API квот не сконфигурирован")

    async def probe(self) -> None:
        raise err.BucketUnavailable("MinIO Admin API квот не сконфигурирован")


# ---------------------------------------------------------------------------
# BucketAdminPort
# ---------------------------------------------------------------------------


@dataclass
class MinioBucketAdmin:
    """``BucketAdminPort`` через boto3 (доверенная платформа, НЕ плагин).

    ``owns_bucket=True`` — bucket-per-tenant (§5.4): при uninstall удаляется
    весь СВОЙ бакет ``tenant-<company>`` и только он. Значение читается
    сервисом задачи 06 через ``getattr(..., "owns_bucket", True)``.

    ``client`` инжектируется (тесты → fake); ``config`` нужен только для
    ленивого построения реального boto3-клиента. ``clock`` — для observability
    событий, не для Trust.
    """

    config: Any = None
    client: Any = None
    quota_admin: Any = None
    #: bucket-per-tenant по умолчанию. Не из пользовательского ввода.
    owns_bucket: bool = True
    clock: Any = time
    checksum_max_bytes: int | None = None
    checksum_chunk_size: int = 64 * 1024

    def __post_init__(self) -> None:
        if self.checksum_max_bytes is None:
            self.checksum_max_bytes = getattr(
                self.config, "checksum_max_bytes", DEFAULT_CHECKSUM_MAX_BYTES
            )
        if (
            not isinstance(self.checksum_max_bytes, int)
            or isinstance(self.checksum_max_bytes, bool)
            or not 1 <= self.checksum_max_bytes <= MAX_CHECKSUM_MAX_BYTES
        ):
            raise err.BucketUnavailable("invalid SHA-256 streaming size cap")
        if (
            not isinstance(self.checksum_chunk_size, int)
            or isinstance(self.checksum_chunk_size, bool)
            or not 1 <= self.checksum_chunk_size <= 1024 * 1024
        ):
            raise err.BucketUnavailable("invalid SHA-256 streaming chunk size")
        if self.quota_admin is None:
            self.quota_admin = UnavailableQuotaAdmin()
        if self.client is None and self.config is None:
            raise err.BucketUnavailable(
                "MinioBucketAdmin требует config или инжектированный client"
            )
        if self.client is None:
            try:
                self.client = _build_client(self.config)
            except Exception as exc:  # noqa: BLE001 - мапим в fail-closed ошибку
                raise err.BucketUnavailable(
                    f"не удалось создать S3-клиент: {type(exc).__name__}"
                ) from exc

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _policy_json(policy: Mapping[str, Any]) -> str:
        """Канонический JSON политики (детерминированный, для get/put/сравнения)."""
        return json.dumps(policy, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    async def _bucket_exists(self, bucket: str) -> bool:
        try:
            await _call(self.client.head_bucket, Bucket=bucket)
            return True
        except Exception:  # noqa: BLE001 - 404/403/сеть = «не подтверждено»
            return False

    async def _read_policy(self, bucket: str) -> dict[str, Any] | None:
        """Прочитать текущую политику. Отсутствие политики → ``None`` (не ошибка)."""
        try:
            raw = await _call(self.client.get_bucket_policy, Bucket=bucket)
        except Exception:  # noqa: BLE001 - NoSuchBucketPolicy и прочее = нет политики
            return None
        text = raw.get("Policy") if isinstance(raw, Mapping) else None
        if not isinstance(text, str) or not text.strip():
            return None
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError) as exc:
            # Битая политика в хранилище = fail-closed, а не «перезапишем всё».
            raise err.BucketUnavailable(
                f"политика бакета {bucket!r} не распарсена — мердж запрещён"
            ) from exc
        if not isinstance(parsed, Mapping):
            raise err.BucketUnavailable("политика бакета имеет некорректную форму")
        return dict(parsed)

    # -- BucketAdminPort ---------------------------------------------------
    async def ensure_bucket(
        self, bucket: str, *, policy: dict, quota_bytes: int
    ) -> None:
        """Создать ``tenant-<company>`` при grant и МЕРДЖИТЬ политику.

        Порядок важен: сначала проверяем имя, потом создаём бакет, затем читаем
        существующую политику и объединяем её с новой. Перезаписи НЕТ: при
        установке второго плагина компании Statement первого остаётся в силе.
        Квота вызывается последней; её недоступность = отказ grant (fail-closed).
        """
        safe_bucket = _validate_bucket(bucket)
        incoming = _validate_bucket_policy(policy, safe_bucket)
        try:
            limit = int(quota_bytes)
        except (TypeError, ValueError) as exc:
            raise err.BucketUnavailable("некорректное значение квоты") from exc
        if limit <= 0:
            raise err.BucketUnavailable("квота бакета должна быть положительной")

        # Fail-fast: если Admin API квот недоступен, отказываем ДО create_bucket.
        # Иначе бакет появился бы без hard limit (молчаливый безлимит), а
        # исключение пришло бы только после записи политики.
        probe = getattr(self.quota_admin, "probe", None)
        if probe is not None:
            try:
                result = probe()
                if isinstance(result, Awaitable):
                    await result
            except err.PluginObjectError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise err.BucketUnavailable(
                    f"Admin API квот недоступен: {type(exc).__name__}"
                ) from exc

        try:
            exists = await self._bucket_exists(safe_bucket)
            if not exists:
                # CreateBucketConfiguration нужен для регионов, отличных от
                # us-east-1; MinIO в as-built использует us-east-1.
                create_kwargs: dict[str, Any] = {"Bucket": safe_bucket}
                region = getattr(self.config, "region", None) if self.config else None
                if region and region != "us-east-1":
                    create_kwargs["CreateBucketConfiguration"] = {
                        "LocationConstraint": region
                    }
                await _call(self.client.create_bucket, **create_kwargs)

            existing = await self._read_policy(safe_bucket)
            merged = merge_bucket_policies(existing, incoming, bucket=safe_bucket)
            await _call(
                self.client.put_bucket_policy,
                Bucket=safe_bucket,
                Policy=self._policy_json(merged),
            )
        except err.PluginObjectError:
            raise
        except Exception as exc:  # noqa: BLE001 - любой сбой MinIO = явная ошибка
            raise err.BucketUnavailable(
                f"ensure_bucket({safe_bucket!r}) недоступен: {type(exc).__name__}"
            ) from exc

        # Квота — hard limit на стороне MinIO (§5.4). НЕ observability-счётчик.
        try:
            await self.quota_admin.set_hard_quota(safe_bucket, limit)
        except err.PluginObjectError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise err.BucketUnavailable(
                f"set_quota({safe_bucket!r}) недоступен: {type(exc).__name__}"
            ) from exc

    async def delete_bucket(self, bucket: str) -> None:
        """Удалить ТОЛЬКО СВОЙ ``tenant-<company>`` (uninstall, критерий 15).

        Перед удалением чистим объекты своим же клиентом, но ТОЛЬКО этого
        бакета: ``head_bucket`` подтверждает, что имя осталось каноническим.
        Никаких ``list_buckets`` + цикл по всем — чужой бакет в вызов не попадает.
        """
        safe_bucket = _validate_bucket(bucket)
        try:
            paginator = self.client.get_paginator("list_objects_v2")
            # Пагинатор синхронный: итерируем в потоке, не блокируя loop.
            def _purge() -> None:
                for page in paginator.paginate(Bucket=safe_bucket):
                    objects = [
                        {"Key": str(o["Key"])}
                        for o in (page.get("Contents") or [])
                        if isinstance(o, Mapping) and o.get("Key")
                    ]
                    for i in range(0, len(objects), 1000):
                        self.client.delete_objects(
                            Bucket=safe_bucket,
                            Delete={"Objects": objects[i : i + 1000], "Quiet": True},
                        )

            await _call(_purge)
            await _call(self.client.delete_bucket, Bucket=safe_bucket)
        except err.PluginObjectError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise err.BucketUnavailable(
                f"delete_bucket({safe_bucket!r}) недоступен: {type(exc).__name__}"
            ) from exc

    async def delete_prefix(self, bucket: str, prefix: str) -> int:
        """Удалить ТОЛЬКО ``module-<plugin>/`` (режим «префикс + STS»).

        В bucket-per-tenant ``owns_bucket=True`` и сервис этот метод не зовёт,
        но порт обязан существовать (задача 06 проверяет ``getattr``). Возвращает
        число удалённых объектов — метаданные, НЕ payload.
        """
        safe_bucket = _validate_bucket(bucket)
        safe_prefix = _normalize_prefix(prefix)
        try:
            paginator = self.client.get_paginator("list_objects_v2")

            def _purge() -> int:
                deleted = 0
                for page in paginator.paginate(Bucket=safe_bucket, Prefix=safe_prefix):
                    objects = [
                        {"Key": str(o["Key"])}
                        for o in (page.get("Contents") or [])
                        if isinstance(o, Mapping)
                        and o.get("Key")
                        and str(o["Key"]).startswith(safe_prefix)
                    ]
                    for i in range(0, len(objects), 1000):
                        chunk = objects[i : i + 1000]
                        # Как в minio_store: при отказе батча (MissingContentMD5)
                        # чистим по одному, иначе останутся протухшие объекты.
                        try:
                            self.client.delete_objects(
                                Bucket=safe_bucket,
                                Delete={"Objects": chunk, "Quiet": True},
                            )
                            deleted += len(chunk)
                        except Exception:  # noqa: BLE001
                            logger.warning(
                                "batch delete failed for prefix %s — per-object fallback",
                                safe_prefix,
                            )
                            for obj in chunk:
                                try:
                                    self.client.delete_object(
                                        Bucket=safe_bucket, Key=obj["Key"]
                                    )
                                    deleted += 1
                                except Exception:  # noqa: BLE001
                                    logger.exception(
                                        "delete_object failed for key under %s",
                                        safe_prefix,
                                    )
                return deleted

            return int(await _call(_purge))
        except err.PluginObjectError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise err.BucketUnavailable(
                f"delete_prefix({safe_prefix!r}) недоступен: {type(exc).__name__}"
            ) from exc

    async def set_quota(self, bucket: str, quota_bytes: int) -> None:
        """Жёсткая квота СВОЕГО бакета (НЕ счётчик в БД, §5.4)."""
        safe_bucket = _validate_bucket(bucket)
        try:
            limit = int(quota_bytes)
        except (TypeError, ValueError) as exc:
            raise err.BucketUnavailable("некорректное значение квоты") from exc
        if limit <= 0:
            raise err.BucketUnavailable("квота бакета должна быть положительной")
        try:
            await self.quota_admin.set_hard_quota(safe_bucket, limit)
        except err.PluginObjectError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise err.BucketUnavailable(
                f"set_quota({safe_bucket!r}) недоступен: {type(exc).__name__}"
            ) from exc

    async def get_usage(self, bucket: str) -> int:
        """Сумма ``Size`` объектов СВОЕГО бакета (observability, не payload)."""
        safe_bucket = _validate_bucket(bucket)
        try:
            paginator = self.client.get_paginator("list_objects_v2")

            def _size() -> int:
                total = 0
                for page in paginator.paginate(Bucket=safe_bucket):
                    for obj in page.get("Contents") or []:
                        if isinstance(obj, Mapping):
                            total += int(obj.get("Size") or 0)
                return total

            return int(await _call(_size))
        except err.PluginObjectError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise err.BucketUnavailable(
                f"get_usage({safe_bucket!r}) недоступен: {type(exc).__name__}"
            ) from exc

    async def list_prefix(self, bucket: str, prefix: str) -> list[ports.ObjectInfo]:
        """Листинг ТОЛЬКО своего префикса, метаданные без содержимого.

        Возвращаемые ключи перепроверяются: «широкий» list из MinIO не должен
        показать соседний ``module-*`` этой же компании.
        """
        safe_bucket = _validate_bucket(bucket)
        safe_prefix = _normalize_prefix(prefix)
        try:
            paginator = self.client.get_paginator("list_objects_v2")

            def _list() -> list[dict[str, Any]]:
                out: list[dict[str, Any]] = []
                for page in paginator.paginate(
                    Bucket=safe_bucket, Prefix=safe_prefix
                ):
                    for obj in page.get("Contents") or []:
                        if isinstance(obj, Mapping):
                            out.append(dict(obj))
                return out

            raw = await _call(_list)
        except err.PluginObjectError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise err.BucketUnavailable(
                f"list_prefix({safe_prefix!r}) недоступен: {type(exc).__name__}"
            ) from exc

        out: list[ports.ObjectInfo] = []
        for obj in raw:
            key = str(obj.get("Key") or "")
            if not key.startswith(safe_prefix) or ".." in key:
                raise err.ObjectAccessDenied(
                    f"листинг вернул ключ вне {safe_prefix!r}"
                )
            etag = obj.get("ETag")
            out.append(
                ports.ObjectInfo(
                    key=key,
                    size=int(obj.get("Size") or 0),
                    etag=str(etag).strip('"') if etag else None,
                )
            )

        return out

    async def list_prefix_page(
        self,
        *,
        bucket: str,
        prefix: str,
        limit: int,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Return one bounded S3 page without loading the whole prefix."""
        safe_bucket = _validate_bucket(bucket)
        safe_prefix = _normalize_prefix(prefix)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise err.BucketUnavailable("list page limit must be within 1..1000")
        kwargs: dict[str, Any] = {
            "Bucket": safe_bucket,
            "Prefix": safe_prefix,
            "MaxKeys": limit,
        }
        if cursor is not None:
            if not cursor or len(cursor) > 4096 or any(ord(ch) < 32 for ch in cursor):
                raise err.ObjectAccessDenied("invalid S3 continuation cursor")
            kwargs["ContinuationToken"] = cursor
        try:
            raw = await _call(self.client.list_objects_v2, **kwargs)
        except Exception as exc:  # noqa: BLE001
            raise err.BucketUnavailable(
                f"list_prefix_page({safe_prefix!r}) failed: {type(exc).__name__}"
            ) from exc
        if not isinstance(raw, Mapping):
            raise err.BucketUnavailable("S3 list page is not a mapping")
        objects: list[ports.ObjectInfo] = []
        for item in raw.get("Contents") or []:
            if not isinstance(item, Mapping):
                raise err.BucketUnavailable("S3 list item is invalid")
            key = str(item.get("Key") or "")
            if not key.startswith(safe_prefix) or ".." in key:
                raise err.ObjectAccessDenied("S3 list escaped the plugin prefix")
            etag = item.get("ETag")
            objects.append(
                ports.ObjectInfo(
                    key=key,
                    size=int(item.get("Size") or 0),
                    etag=str(etag).strip('"') if etag else None,
                )
            )
        if len(objects) > limit:
            raise err.BucketUnavailable("S3 list exceeded MaxKeys")
        next_cursor = raw.get("NextContinuationToken")
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise err.BucketUnavailable("S3 continuation cursor is invalid")
        return {"objects": objects, "next_cursor": next_cursor}

    async def content_sha256(
        self, *, bucket: str, key: str, expected_size: int | None = None
    ) -> str:
        """Return metadata SHA-256 or hash a bounded GET; ETag is never a digest."""
        safe_bucket = _validate_bucket(bucket)
        safe_key = _normalize_key(key)
        if expected_size is not None and (
            not isinstance(expected_size, int)
            or isinstance(expected_size, bool)
            or expected_size < 0
        ):
            raise err.BucketUnavailable("expected object size is invalid")
        try:
            raw = await _call(
                self.client.head_object,
                Bucket=safe_bucket,
                Key=safe_key,
                ChecksumMode="ENABLED",
            )
        except Exception as exc:  # noqa: BLE001
            raise err.BucketUnavailable(
                f"head_object checksum failed: {type(exc).__name__}"
            ) from None
        if not isinstance(raw, Mapping):
            raise err.BucketUnavailable("head_object returned invalid metadata")
        declared = raw.get("ContentLength")
        if declared is not None and (
            not isinstance(declared, int) or isinstance(declared, bool) or declared < 0
        ):
            raise err.BucketUnavailable("object ContentLength is invalid")
        if expected_size is not None and declared != expected_size:
            raise err.BucketUnavailable("object_list_size_mismatch")
        metadata_digest = _head_sha256(raw)
        if metadata_digest is not None:
            return metadata_digest
        if declared is not None and declared > int(self.checksum_max_bytes):
            raise err.BucketUnavailable("object_checksum_size_cap_exceeded")
        try:
            response = await _call(
                self.client.get_object,
                Bucket=safe_bucket,
                Key=safe_key,
            )
        except Exception as exc:  # noqa: BLE001
            raise err.BucketUnavailable(
                f"get_object checksum failed: {type(exc).__name__}"
            ) from None
        if not isinstance(response, Mapping) or "Body" not in response:
            raise err.BucketUnavailable("get_object returned no checksum body")
        stream_size = declared if declared is not None else expected_size
        if stream_size is None:
            response_size = response.get("ContentLength")
            if response_size is not None and (
                not isinstance(response_size, int)
                or isinstance(response_size, bool)
                or response_size < 0
            ):
                close = getattr(response["Body"], "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:  # noqa: BLE001 - preserve the sanitized size error
                        pass
                raise err.BucketUnavailable("object ContentLength is invalid")
            stream_size = response_size
        try:
            return await _call(
                _stream_sha256,
                response["Body"],
                max_bytes=int(self.checksum_max_bytes),
                chunk_size=self.checksum_chunk_size,
                expected_size=stream_size,
            )
        except _ChecksumStreamFailure as exc:
            raise err.BucketUnavailable(str(exc)) from None
        except Exception as exc:  # noqa: BLE001
            raise err.BucketUnavailable(
                f"object SHA-256 streaming failed: {type(exc).__name__}"
            ) from None

    async def delete_object(self, *, bucket: str, key: str) -> bool:
        """Delete exactly one plugin object and report prior existence truthfully."""
        safe_bucket = _validate_bucket(bucket)
        safe_key = _normalize_key(key)
        try:
            await _call(self.client.head_object, Bucket=safe_bucket, Key=safe_key)
        except Exception as exc:  # noqa: BLE001
            response = getattr(exc, "response", None)
            status = int((response or {}).get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
            code = str((response or {}).get("Error", {}).get("Code", ""))
            if status == 404 or code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise err.BucketUnavailable(
                f"head_object before delete failed: {type(exc).__name__}"
            ) from exc
        try:
            await _call(self.client.delete_object, Bucket=safe_bucket, Key=safe_key)
        except Exception as exc:  # noqa: BLE001
            raise err.BucketUnavailable(
                f"delete_object failed: {type(exc).__name__}"
            ) from exc
        return True

    async def aclose(self) -> None:
        """Close an owned boto client once; shared clients are not passed here."""
        if getattr(self, "_closed", False):
            return
        self._closed = True
        close = getattr(self.client, "close", None)
        if callable(close):
            await _call(close)


# ---------------------------------------------------------------------------
# StsPort
# ---------------------------------------------------------------------------


@dataclass
class MinioSts:
    """``StsPort``: MinIO STS ``AssumeRole`` с session policy (v4 §5.4, §12.8).

    Контракт fail-closed:
      * до успешного async post-probe и после expiry/error/close →
        ``StsUnavailable`` НЕМЕДЛЕННО, без сетевого вызова;
      * ``sts_client`` отсутствует/упал/вернул мусор → ``StsUnavailable``;
      * НИКОГДА не возвращаем ``admin_access_key``/``admin_secret_key``, root
        или постоянные service-креды. В ``ScopedCredentials`` уходят ТОЛЬКО
        ``Credentials.AccessKeyId`` / ``SecretAccessKey`` / ``SessionToken``
        из ответа ``AssumeRole``;
      * перепроверяем, что сессия выдана ровно на ожидаемые ``bucket`` и
        ``prefix`` и что ``session_policy`` та же, что передал сервис;
      * expires_at принимается только из проверяемого Expiration ответа;
        отсутствующий, истёкший или превышающий TTL срок является ошибкой.

    ВАЖНО: поддержка ``AssumeRole`` + ``Policy=`` + ``DurationSeconds`` и
    ``StringLike`` по ``s3:prefix`` конкретным образом
    ``RELEASE.2024-10-02T17-50-41Z`` ТРЕБУЕТ ПОДТВЕРЖДЕНИЯ официальной
    документацией MinIO. До подтверждения фабрика создаёт только typed-unwired
    вариант: сетевой AssumeRole запрещён, static credentials не подставляются.
    """

    config: Any = None
    sts_client: Any = None
    clock: Any = time
    #: Optional injected S3 canary IO factory. It never bypasses AssumeRole or
    #: authorization checks; production builds a client from issued temp credentials.
    canary_client_factory: Any = None
    _probe_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _probe_invoked: bool = field(default=False, init=False, repr=False)
    _probe_succeeded_at: float | None = field(default=None, init=False, repr=False)
    _last_probe_failure_at: float | None = field(default=None, init=False, repr=False)
    _probe_attempt: int = field(default=0, init=False, repr=False)

    def _probe_fresh(self, now: float | None = None) -> bool:
        if self._probe_succeeded_at is None:
            return False
        current = float(self.clock.time()) if now is None else now
        max_age = int(getattr(self.config, "sts_probe_revalidate_seconds", 300))
        age = current - self._probe_succeeded_at
        return 0 <= age < max_age

    @property
    def scoped_credentials_wired(self) -> bool:
        """True only for fresh successful AssumeRole plus S3 canary evidence."""
        if (
            self.config is None
            or not bool(getattr(self.config, "sts_enabled", False))
            or getattr(self, "_closed", False)
        ):
            return False
        return bool(getattr(self.config, "sts_probe_enabled", False)) and self._probe_fresh()

    @property
    def sts_available(self) -> bool:
        """Read-only compatibility projection; it cannot be used as proof input."""
        return self.scoped_credentials_wired

    def invalidate(self) -> None:
        """Drop availability without retaining any probe response or credentials."""
        self._probe_succeeded_at = None

    def _probe_session_policy(self, company_id: str, plugin_id: str) -> dict[str, Any]:
        """Canary-only policy: no tenant data, writes, or foreign buckets."""
        del company_id, plugin_id  # The configured dedicated target is authoritative.
        bucket = self.config.sts_probe_bucket
        key = self.config.sts_probe_key
        return {
            "Version": iam.POLICY_VERSION,
            "Statement": [
                {
                    "Sid": "ListDedicatedStsCanaryBucket",
                    "Effect": "Allow",
                    "Action": ["s3:ListBucket"],
                    "Resource": [f"arn:aws:s3:::{bucket}"],
                },
                {
                    "Sid": "GetDedicatedStsCanaryObject",
                    "Effect": "Allow",
                    "Action": ["s3:GetObject"],
                    "Resource": [f"arn:aws:s3:::{bucket}/{key}"],
                },
            ],
        }

    @staticmethod
    def _s3_status_and_code(value: Any) -> tuple[int | None, str]:
        response = value if isinstance(value, Mapping) else getattr(value, "response", None)
        if not isinstance(response, Mapping):
            return None, ""
        metadata = response.get("ResponseMetadata") or {}
        raw_status = metadata.get("HTTPStatusCode") if isinstance(metadata, Mapping) else None
        try:
            status = int(raw_status) if raw_status is not None else None
        except (TypeError, ValueError):
            status = None
        error = response.get("Error") or {}
        code = str(error.get("Code") or "") if isinstance(error, Mapping) else ""
        return status, code

    def _make_canary_client(
        self, credentials: ports.ScopedCredentials, timeout_seconds: int,
    ) -> Any:
        if self.canary_client_factory is not None:
            return self.canary_client_factory(
                credentials=credentials, timeout_seconds=timeout_seconds,
            )
        import boto3
        from botocore.client import Config

        # Only the just-issued temporary session is used for authorization IO.
        # Admin/static credentials never leave the STS client configuration.
        return boto3.client(
            "s3",
            endpoint_url=self.config.endpoint,
            aws_access_key_id=credentials.access_key,
            aws_secret_access_key=credentials.secret_key,
            aws_session_token=credentials.session_token,
            region_name=self.config.region,
            config=Config(
                signature_version="s3v4",
                connect_timeout=timeout_seconds,
                read_timeout=timeout_seconds,
                retries={"max_attempts": 0},
            ),
        )

    def _probe_identity(self) -> tuple[str, str, str, str, str]:
        bucket = self.config.sts_probe_bucket
        key = self.config.sts_probe_key
        company_id = bucket[len("tenant-") :]
        prefix = key.split("/", 1)[0] + "/"
        # Here prefix already ends in the separator, so drop exactly that slash.
        plugin_id = prefix[len("module-") : -1]
        return company_id, plugin_id, bucket, key, prefix

    async def _canary_bucket_and_object(self, client: Any, bucket: str, key: str) -> None:
        # A missing synthetic bucket is NOT an authorization success. Require an
        # actual provisioned bucket and its fixed zero-byte object, both HTTP 200.
        try:
            bucket_result = await _call(client.head_bucket, Bucket=bucket)
        except Exception as exc:
            status, code = self._s3_status_and_code(exc)
            if status == 404 and code.lower() == "nosuchbucket":
                raise err.StsUnavailable("STS canary bucket is not provisioned") from None
            raise err.StsUnavailable("STS canary bucket is missing or unauthorized") from None
        bucket_status, _ = self._s3_status_and_code(bucket_result)
        if not isinstance(bucket_result, Mapping) or bucket_status != 200:
            raise err.StsUnavailable("STS canary bucket HEAD is ambiguous")
        try:
            object_result = await _call(client.head_object, Bucket=bucket, Key=key)
        except Exception as exc:
            # NoSuchKey after a proven existing bucket is distinguished from
            # NoSuchBucket, but the required provisioned object is still missing.
            status, code = self._s3_status_and_code(exc)
            if status == 404 and code.lower() == "nosuchkey":
                raise err.StsUnavailable("STS canary fixed object is not provisioned") from None
            raise err.StsUnavailable("STS canary fixed object is missing or unauthorized") from None
        object_status, _ = self._s3_status_and_code(object_result)
        if not isinstance(object_result, Mapping) or object_status != 200:
            raise err.StsUnavailable("STS canary fixed object HEAD is ambiguous")
        if object_result.get("ContentLength") != 0:
            raise err.StsUnavailable("STS canary fixed object must be zero-byte")

    async def _canary_denied(self, call: Any, **kwargs: Any) -> None:
        """Accept only an unambiguous HTTP 403 with Error.Code=AccessDenied."""
        try:
            result = await _call(call, **kwargs)
        except Exception as exc:
            status, code = self._s3_status_and_code(exc)
            if status == 403 and code.lower() in {"accessdenied", "403"}:
                return
            raise err.StsUnavailable("STS canary denial returned an ambiguous error") from None
        status, code = self._s3_status_and_code(result)
        if status == 403 and code.lower() in {"accessdenied", "403"}:
            return
        raise err.StsUnavailable("STS canary foreign/list operation was not denied")

    async def _run_scoped_canary(
        self, credentials: ports.ScopedCredentials, timeout_seconds: int,
    ) -> None:
        """Authorize exact GetObject scope, then require foreign/ListBucket 403."""
        timeout = timeout_seconds
        client = await _call(self._make_canary_client, credentials, timeout)
        if client is None:
            raise err.StsUnavailable("STS canary client is unavailable")
        _company, plugin, bucket, key, prefix = self._probe_identity()
        try:
            await self._canary_bucket_and_object(client, bucket, key)
            await self._canary_denied(
                client.head_object,
                Bucket=bucket,
                Key=f"module-{plugin}-foreign/__authorization_canary__",
            )
            # HeadBucket requires s3:ListBucket on the dedicated bucket. A
            # foreign-bucket ListBucket must still be denied by the scoped policy.
            await self._canary_denied(
                client.list_objects_v2,
                Bucket=f"{bucket}-foreign",
                Prefix=prefix,
                MaxKeys=1,
            )
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    await _call(close)
                except Exception:
                    raise err.StsUnavailable("STS canary client close failed") from None

    async def probe(self, *, force: bool = False) -> bool:
        """Validate MinIO STS once, discarding the short-lived probe credentials.

        No caller receives the probe session. A bounded 900-second canonical
        AssumeRole is shared by concurrent callers. Failures are typed-unwired,
        use bounded retry backoff, and never fall back to static/admin credentials.
        """
        self._probe_invoked = True
        if (
            self.config is None
            or not bool(getattr(self.config, "sts_enabled", False))
            or not bool(getattr(self.config, "sts_probe_enabled", False))
            or getattr(self, "_closed", False)
        ):
            self.invalidate()
            return False
        attempt = self._probe_attempt
        async with self._probe_lock:
            now = float(self.clock.time())
            if getattr(self, "_closed", False):
                self.invalidate()
                return False
            if force and attempt != self._probe_attempt:
                return self._probe_fresh(now)
            if not force and self._probe_fresh(now):
                return True
            if (
                not force
                and self._last_probe_failure_at is not None
                and 0 <= now - self._last_probe_failure_at < int(
                    getattr(self.config, "sts_probe_retry_seconds", 30)
                )
            ):
                return False

            company_id, plugin_id, _bucket, _key, _prefix = self._probe_identity()
            policy = self._probe_session_policy(company_id, plugin_id)
            ttl = ports.MIN_STS_TTL_SECONDS
            try:
                async with asyncio.timeout(int(getattr(self.config, "sts_probe_timeout_seconds", 5))):
                    issued = await self._issue_scoped_credentials(
                        company_id=company_id,
                        plugin_id=plugin_id,
                        ttl_seconds=ttl,
                        session_policy=policy,
                        client_timeout_seconds=int(
                            getattr(self.config, "sts_probe_timeout_seconds", 5)
                        ),
                        probe=True,
                    )
                    # Server-side authorization, not typed fields populated by this
                    # adapter, proves the actual session scope before availability.
                    await self._run_scoped_canary(
                        issued,
                        int(getattr(self.config, "sts_probe_timeout_seconds", 5)),
                    )
                now = float(self.clock.time())
                if (
                    issued.expires_at <= now
                    or issued.expires_at > now + ttl + 1
                    or not all((issued.access_key, issued.secret_key, issued.session_token))
                ):
                    raise err.StsUnavailable("STS probe expiration or credentials mismatch")
                if (
                    issued.access_key == getattr(self.config, "admin_access_key", None)
                    or issued.secret_key == getattr(self.config, "admin_secret_key", None)
                ):
                    raise err.StsUnavailable("STS probe returned admin credentials")
            except Exception:
                self.invalidate()
                self._last_probe_failure_at = float(self.clock.time())
                self._probe_attempt += 1
                return False

            # Do not store, return, log, or reuse the probe session. Only the
            # validation timestamp becomes availability evidence.
            del issued
            self._probe_succeeded_at = now
            self._last_probe_failure_at = None
            self._probe_attempt += 1
            return True

    async def assume_role(
        self,
        *,
        company_id: str,
        plugin_id: str,
        ttl_seconds: int,
        session_policy: dict,
    ) -> ports.ScopedCredentials:
        # Startup/lifespan must attempt the first probe. Before that, and after
        # stale/failed evidence, the operation remains typed-unwired. A stale
        # initialized adapter may revalidate asynchronously under singleflight.
        if not self.scoped_credentials_wired:
            if self._probe_invoked:
                await self.probe()
            if not self.scoped_credentials_wired:
                raise err.StsUnavailable(
                    "MinIO STS is disabled or has no fresh successful post-probe"
                )
        try:
            return await self._issue_scoped_credentials(
                company_id=company_id,
                plugin_id=plugin_id,
                ttl_seconds=ttl_seconds,
                session_policy=session_policy,
            )
        except Exception:
            self.invalidate()
            self._last_probe_failure_at = float(self.clock.time())
            raise

    async def _issue_scoped_credentials(
        self,
        *,
        company_id: str,
        plugin_id: str,
        ttl_seconds: int,
        session_policy: dict,
        client_timeout_seconds: int | None = None,
        probe: bool = False,
    ) -> ports.ScopedCredentials:
        if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool):
            raise err.InvalidTtl("STS DurationSeconds must be an integer")
        ttl = ports.validate_sts_ttl(ttl_seconds)
        expected_bucket = self.config.sts_probe_bucket if probe else iam.bucket_name(company_id)
        expected_prefix = _module_prefix(plugin_id)
        role_arn = _validate_sts_role_arn(self.config.sts_role_arn)
        role_session_name = _validate_sts_role_session_name(
            f"plugin-{plugin_id}-{company_id}"[:64]
        )

        # Проверяем session policy ДО любого сетевого вызова: она обязана быть
        # ровно той, что сгенерировал доверенный сервис (один генератор в
        # policy.py), и не содержать ничего шире своего бакета/префикса.
        canonical = (
            self._probe_session_policy(company_id, plugin_id)
            if probe
            else iam.session_policy(company_id, plugin_id)
        )
        if not isinstance(session_policy, Mapping):
            raise err.StsUnavailable("session policy is not a mapping")
        canonical_by_sid = {
            statement.get("Sid"): statement for statement in canonical["Statement"]
        }
        statements = session_policy.get("Statement")
        if not isinstance(statements, list) or not statements:
            raise err.StsUnavailable("session policy is empty")
        for statement in statements:
            if not isinstance(statement, Mapping):
                raise err.StsUnavailable("session policy statement is invalid")
            parent = canonical_by_sid.get(statement.get("Sid"))
            if parent is None:
                raise err.StsUnavailable("session policy statement is not canonical")
            parent_actions = set(parent.get("Action", []))
            requested_actions = set(statement.get("Action", []))
            if not requested_actions or not requested_actions.issubset(parent_actions):
                raise err.StsUnavailable("session policy exceeds canonical actions")
            if dict(statement) != {**dict(parent), "Action": statement.get("Action")}:
                raise err.StsUnavailable("session policy broadens resources or conditions")
        _validate_bucket_policy(session_policy, expected_bucket)

        if getattr(self, "_closed", False):
            raise err.StsUnavailable("MinIO STS adapter is closed")
        configured_duration = getattr(self.config, "sts_session_duration_seconds", 900)
        if ttl > configured_duration:
            raise err.StsUnavailable("requested STS session exceeds the configured cap")
        if self.sts_client is None:
            try:
                self.sts_client = self._build_sts_client(client_timeout_seconds)
            except Exception as exc:  # noqa: BLE001
                raise err.StsUnavailable(
                    f"STS-клиент недоступен: {type(exc).__name__}"
                ) from None
        if self.sts_client is None:
            raise err.StsUnavailable("STS-клиент не сконфигурирован")

        try:
            # SessionPolicy передаётся КАК ЕСТЬ (канонический JSON), без
            # ``${aws:username}`` / ``${jwt:*}`` — policy variables являются
            # опцией MinIO и не подтверждены (policy.py, §12.8).
            request = {
                "RoleArn": role_arn,
                "RoleSessionName": role_session_name,
                "DurationSeconds": ttl,
                "Policy": json.dumps(session_policy, sort_keys=True, separators=(",", ":")),
            }
            if set(request) != _ASSUME_ROLE_PARAMETER_NAMES:
                raise err.StsUnavailable("STS AssumeRole request shape invariant failed")
            response = await _call(self.sts_client.assume_role, **request)
        except err.PluginObjectError:
            raise
        except Exception as exc:  # noqa: BLE001
            # Ни текста исключения, ни ответа в лог: там могут быть токены.
            raise err.StsUnavailable(
                f"AssumeRole недоступен: {type(exc).__name__}; root-креды не выдаются"
            ) from None

        if not isinstance(response, Mapping):
            raise err.StsUnavailable("STS вернул некорректный ответ")
        creds = response.get("Credentials")
        if not isinstance(creds, Mapping):
            raise err.StsUnavailable("STS не вернул Credentials")

        access_key = str(creds.get("AccessKeyId") or "")
        secret_key = str(creds.get("SecretAccessKey") or "")
        session_token = str(creds.get("SessionToken") or "")
        if not access_key or not secret_key or not session_token:
            raise err.StsUnavailable(
                "STS вернул неполные временные creds (нет session token)"
            )
        # Страховка от «адаптер случайно пробросил admin-креды»: сравниваем с
        # конфигурацией и явно запрещаем совпадение.
        cfg = self.config
        if cfg is not None and (
            access_key == getattr(cfg, "admin_access_key", None)
            or secret_key == getattr(cfg, "admin_secret_key", None)
        ):
            raise err.StsUnavailable(
                "STS вернул admin/service-креды вместо временных — отказ"
            )
        # Запрещаем известные root/as-built идентификаторы.
        if access_key.lower() in {"agentscale", "agentscale-api", "minio", "root"}:
            raise err.StsUnavailable("STS вернул root/API access key — отказ")

        expiration = creds.get("Expiration")
        now = float(self.clock.time())
        if not hasattr(expiration, "timestamp"):
            raise err.StsUnavailable("STS returned no verifiable expiration")
        expires_at = float(expiration.timestamp())
        if expires_at <= now or expires_at > now + ttl + 1:
            raise err.StsUnavailable("STS expiration exceeds the requested session")

        return ports.ScopedCredentials(
            access_key=access_key,
            secret_key=secret_key,
            session_token=session_token,
            bucket=expected_bucket,
            prefix=expected_prefix,
            expires_at=expires_at,
            policy=dict(session_policy),
        )

    def _build_sts_client(self, timeout_seconds: int | None = None) -> Any:
        """Build STS IO with optional hard connect/read timeout and no retries."""
        if self.config is None:
            return None
        import boto3

        kwargs: dict[str, Any] = {}
        from botocore.client import Config

        options: dict[str, Any] = {
            # The 900-second STS minimum satisfies the standard botocore model.
            # No validation bypass remains; local guards also validate every field.
            "retries": {"max_attempts": 0},
        }
        if timeout_seconds is not None:
            options["connect_timeout"] = timeout_seconds
            options["read_timeout"] = timeout_seconds
        kwargs["config"] = Config(**options)
        return boto3.client(
            "sts",
            endpoint_url=self.config.sts_endpoint_url,
            aws_access_key_id=self.config.admin_access_key,
            aws_secret_access_key=self.config.admin_secret_key,
            region_name=self.config.region,
            **kwargs,
        )

    async def aclose(self) -> None:
        """Invalidate availability, then close the owned STS client once."""
        async with self._probe_lock:
            if getattr(self, "_closed", False):
                return
            self._closed = True
            self.invalidate()
            close = getattr(self.sts_client, "close", None)
            if callable(close):
                try:
                    await _call(close)
                except Exception as exc:  # noqa: BLE001
                    raise err.StsUnavailable(
                        f"STS client close failed: {type(exc).__name__}"
                    ) from None


# ---------------------------------------------------------------------------
# PresignPort
# ---------------------------------------------------------------------------


@dataclass
class MinioPresigner:
    """``PresignPort``: scoped presigned URL, TTL 60..3600, жёсткий префикс.

    **ОСНОВНОЙ путь выдачи доступа** (вердикт родителя: сеть недоступна,
    MinIO STS документально не подтверждён → ``PLUGIN_MINIO_STS_ENABLED=false``,
    STS opt-in). Подписывает ТОЛЬКО нормализованный ключ
    ``module-<plugin>/...`` в ``tenant-<company>``.

    Подпись делается ОТДЕЛЬНЫМ principal ``plugin-object-presigner`` с узкой
    IAM-политикой (только Get/Put на ``tenant-*/module-*``), НЕ admin-ключом и
    НЕ as-built ``agentscale-api`` (у того ``agentscale/*`` по
    ``minio-api-policy.json``). Иначе «политика на префикс» осталась бы только
    на бумаге: presigned URL наследует права подписанта.

    URL и ``X-Amz-Signature`` НИКОГДА не логируются и не уходят в аудит —
    только ``key_digest`` (§4.5, кр. 11). In-cluster host не резолвится
    снаружи: для внешних клиентов задаётся ``PLUGIN_MINIO_PUBLIC_ENDPOINT``
    (подпись СРАЗУ на внешний host) либо используется ``proxy=1`` — перепись
    Host ПОСЛЕ подписи инвалидирует SigV4.

    Отзыв (к чужому ``service.py`` задачи 06 НЕ прикасаемся): выданный URL
    живёт до ``X-Amz-Expires`` ≤ 3600 с и НЕ отзывается мгновенно. ``revoke``
    = запрет НОВЫХ URL + деактивация ``CapabilityGrant``; мгновенный отзыв —
    РОТАЦИЯ ключа ``plugin-object-presigner``.
    """

    config: Any = None
    client: Any = None
    #: Доверенный ``module-<plugin>/``, в котором адаптеру разрешено подписывать
    #: ключи. ``PresignPort`` не передаёт ``plugin_id`` (контракт задачи 06),
    #: поэтому «свой» префикс задаётся ЯВНО интегратором, который знает пару
    #: (company, plugin). ``None`` = адаптер вызван напрямую (юнит-тесты):
    #: проверяется только структура ключа, без привязки к конкретному плагину.
    expected_prefix: str | None = None

    def __post_init__(self) -> None:
        if self.client is None and self.config is None:
            raise err.BucketUnavailable(
                "MinioPresigner требует config или инжектированный client"
            )
        if self.client is None:
            try:
                # ЯВНО другой principal + (опционально) внешний endpoint.
                self.client = _build_client(
                    self.config,
                    access_key=self.config.presign_access_key,
                    secret_key=self.config.presign_secret_key,
                    endpoint=self.config.presign_endpoint,
                )
            except Exception as exc:  # noqa: BLE001
                raise err.BucketUnavailable(
                    f"не удалось создать S3-клиент подписанта: {type(exc).__name__}"
                ) from exc

    async def presign_get(self, *, bucket: str, key: str, ttl_seconds: int) -> str:
        return await self._sign("get_object", bucket, key, ttl_seconds)

    async def presign_put(self, *, bucket: str, key: str, ttl_seconds: int) -> str:
        return await self._sign("put_object", bucket, key, ttl_seconds)

    async def presign_head(self, *, bucket: str, key: str, ttl_seconds: int) -> str:
        # S3 HeadObject is authorized by s3:GetObject; it does not grant listing.
        return await self._sign("head_object", bucket, key, ttl_seconds)

    async def _sign(
        self, operation: str, bucket: str, key: str, ttl_seconds: int
    ) -> str:
        if getattr(self, "_closed", False):
            raise err.BucketUnavailable("MinIO presigner is closed")
        # Единый валидатор задачи 06: вне [60, 3600] → InvalidTtl, НЕ кламп
        # (as-built minio_store делает только max(60, ...) без верхней границы).
        ttl = ports.validate_ttl(ttl_seconds)
        safe_bucket = _validate_bucket(bucket)
        safe_key = _normalize_key(key, expected_prefix=self.expected_prefix)
        # Дополнительная страховка: ключ не должен адресовать ДРУГОЙ бакет,
        # даже если тот тоже начинается с tenant-.
        if safe_key.lower().startswith("tenant-"):
            raise err.ObjectAccessDenied("ключ адресует чужой бакет")

        try:
            url = await _call(
                self.client.generate_presigned_url,
                operation,
                Params={"Bucket": safe_bucket, "Key": safe_key},
                ExpiresIn=ttl,
                HttpMethod={"get_object": "GET", "put_object": "PUT", "head_object": "HEAD"}[operation],
            )
        except err.PluginObjectError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise err.BucketUnavailable(
                f"подпись URL недоступна: {type(exc).__name__}"
            ) from exc
        if not isinstance(url, str) or not url:
            raise err.BucketUnavailable("PresignPort вернул пустой URL")
        # The signer must never serialize admin material or its own secret key.
        # X-Amz-Credential (the scoped access key id) and X-Amz-Signature are the
        # standard presign fields; secret/static admin values are never legitimate.
        if self.config is not None:
            forbidden_values = (
                getattr(self.config, "admin_access_key", None),
                getattr(self.config, "admin_secret_key", None),
                getattr(self.config, "presign_secret_key", None),
            )
            decoded_url = unquote(url)
            if any(
                isinstance(value, str) and value and (value in url or value in decoded_url)
                for value in forbidden_values
            ):
                raise err.BucketUnavailable("presigned URL contains credential material — отказ")

        # Подпись обязана быть на СКОНФИГУРИРОВАННЫЙ endpoint подписанта
        # (``presign_endpoint`` = внешний host или in-cluster endpoint), а не
        # куда угодно. Явно заданный внешний host подписывается СРАЗУ: перепись
        # Host ПОСЛЕ подписи инвалидировала бы SigV4 (вердикт родителя).
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise err.BucketUnavailable("presigned URL имеет некорректную форму")
        cfg_endpoint = getattr(self.config, "presign_endpoint", None)
        if cfg_endpoint is None and self.config is not None:
            cfg_endpoint = getattr(self.config, "endpoint", None)
        if isinstance(cfg_endpoint, str) and cfg_endpoint:
            expected = urlsplit(cfg_endpoint)
            if parsed.netloc != expected.netloc or parsed.scheme != expected.scheme:
                raise err.BucketUnavailable(
                    "presigned URL подписан на другой endpoint — отказ"
                )
        return url

    async def aclose(self) -> None:
        """Close the dedicated signing client once; never close the admin client."""
        if getattr(self, "_closed", False):
            return
        self._closed = True
        close = getattr(self.client, "close", None)
        if callable(close):
            try:
                await _call(close)
            except Exception as exc:  # noqa: BLE001
                raise err.BucketUnavailable(
                    f"presign client close failed: {type(exc).__name__}"
                ) from None


# ---------------------------------------------------------------------------
# AuditPort
# ---------------------------------------------------------------------------


#: Поля канонического события ``plugin_object_access`` (§4.4/§4.5, задача 06).
#: Строятся в ``service._emit``; здесь — БЕЛЫЙ список для append-only записи.
_ALLOWED_AUDIT_FIELDS: frozenset[str] = frozenset(
    {
        "event",
        "capability",
        "company_id",
        "plugin_id",
        "bucket",
        "prefix",
        "operation",
        "decision",
        "code",
        "reason",
        "key_digest",
        "ttl_seconds",
        "object_size",
        "ts",
    }
)

#: Значения, которые нельзя писать даже под разрешённым ключом.
_FORBIDDEN_AUDIT_VALUE_TOKENS: tuple[str, ...] = (
    "x-amz-credential", "x-amz-signature", "x-amz-security-token",
    "accesskeyid", "secretaccesskey", "sessiontoken", "session_token",
    "password", "secret", "bearer ", "authorization:", "http://", "https://",
)

_ALLOWED_DECISIONS = frozenset({"allow", "deny", "error"})


def _redact_audit_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Вернуть БЕЗОПАСНУЮ проекцию события (без bytes/payload/секретов/PII).

    Отличается от ``capabilities.audit.redact`` тем, что событие задачи 06 уже
    канонично; задача адаптера — НЕ дать «случайно» протащить в append-only
    лог URL с подписью, токен, ответ MinIO или сырой ключ. Поэтому:

      * неизвестные ключи → отбрасываются (fail-closed, а не «сохраним всё»);
      * ``bytes``/``bytearray``/``memoryview`` → ``AuditUnavailable``;
      * вложенные mapping/list/tuple/set → ``AuditUnavailable`` (в каноническом
        событии их быть не должно; это верный признак попытки пронести payload);
      * строковые значения с подозрительными токенами → заменяются на digest;
      * ``reason`` — короткий канонический код/тип исключения, без данных.
    """
    if not isinstance(event, Mapping):
        raise err.AuditUnavailable("событие аудита должно быть mapping")
    if event.get("event") != AUDIT_EVENT:
        raise err.AuditUnavailable(
            "ожидается событие plugin_object_access, получено другое"
        )

    clean: dict[str, Any] = {}
    for raw_key, value in event.items():
        key = str(raw_key)
        if key not in _ALLOWED_AUDIT_FIELDS:
            # Не «молча выкинем и забудем», а явно зафиксируем факт изъятия
            # БЕЗ значения: адаптер не доверяет вызывающему.
            clean[f"{key}_redacted"] = True
            continue
        if isinstance(value, (bytes, bytearray, memoryview)):
            raise err.AuditUnavailable(
                f"поле аудита {key!r} содержит bytes — запись отклонена"
            )
        if isinstance(value, (Mapping, list, tuple, set, frozenset)):
            raise err.AuditUnavailable(
                f"поле аудита {key!r} содержит вложенную структуру — запись отклонена"
            )
        if isinstance(value, str):
            lowered = value.lower()
            if any(token in lowered for token in _FORBIDDEN_AUDIT_VALUE_TOKENS):
                clean[key] = "sha256:" + hashlib.sha256(
                    value.encode("utf-8")
                ).hexdigest()
                clean[f"{key}_redacted"] = True
                continue
            if key == "reason" and len(value) > 200:
                # Длинный свободный текст — почти всегда raw данные/PII.
                clean[key] = "sha256:" + hashlib.sha256(
                    value.encode("utf-8")
                ).hexdigest()
                clean[f"{key}_redacted"] = True
                continue
        elif value is not None and not isinstance(value, (int, float, bool)):
            # Неизвестный объект: только тип, без repr/полей.
            clean[key] = type(value).__name__
            clean[f"{key}_redacted"] = True
            continue
        clean[key] = value

    # Обязательные поля канонического события: неполная запись = отказ.
    for required in ("event", "company_id", "plugin_id", "operation", "decision", "ts"):
        if required not in clean:
            raise err.AuditUnavailable(
                f"событие аудита без обязательного поля {required!r}"
            )
    if str(clean.get("decision")) not in _ALLOWED_DECISIONS:
        raise err.AuditUnavailable("некорректное решение в событии аудита")
    return clean


@runtime_checkable
class AuditSinkPort(Protocol):
    """Инжектируемый append-only приёмник (БД/журнал», зона родителя)."""

    async def append(self, event: Mapping[str, Any]) -> None: ...


class InMemoryPluginAuditSink:
    """Тестовый append-only sink: хранит ТОЛЬКО ``redacted`` проекции."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def append(self, event: Mapping[str, Any]) -> None:
        self.rows.append(dict(event))


@dataclass
class MinioAuditAdapter:
    """``AuditPort``: append-only событие ``plugin_object_access`` с redaction.

    Никаких bytes/payload/секретов/токенов/PII. Запись идёт в инжектируемый
    ``sink`` (реальная реализация — append-only таблица/журнал родителя). При
    отсутствии sink или ошибке append → ``AuditUnavailable`` (§4.5: сломанный
    аудит для позитивной мутации = deny, сервис задачи 06 это обеспечивает
    через ``strict=True``).
    """

    sink: Any = None
    clock: Any = time

    async def record(self, event: dict) -> None:
        clean = _redact_audit_event(event)
        if self.sink is None:
            raise err.AuditUnavailable(
                "append-only sink аудита не сконфигурирован"
            )
        # Добавляем ТОЛЬКО технические поля (без какого-либо значения извне).
        row = {
            **clean,
            "redaction_version": "plugin-object-audit-v1",
        }
        try:
            append = getattr(self.sink, "append", None)
            if append is None:
                raise TypeError("у sink нет append()")
            result = append(row)
            if isinstance(result, Awaitable):
                await result
        except err.PluginObjectError:
            raise
        except Exception as exc:  # noqa: BLE001 - append failure = deny
            raise err.AuditUnavailable(
                f"не удалось записать {AUDIT_EVENT}: {type(exc).__name__}"
            ) from exc


__all__ = [
    "QuotaAdminPort",
    "UnavailableQuotaAdmin",
    "AuditSinkPort",
    "InMemoryPluginAuditSink",
    "MinioBucketAdmin",
    "MinioSts",
    "MinioPresigner",
    "MinioAuditAdapter",
    "merge_bucket_policies",
    "_validate_bucket",
    "_normalize_key",
    "_normalize_prefix",
    "_validate_bucket_policy",
    "_redact_audit_event",
]
