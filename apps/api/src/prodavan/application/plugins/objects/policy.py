"""Генератор IAM-политик MinIO для объектной capability плагинов (v4 §5.4).

As-built: один бакет ``agentscale`` и теги ``tenant_infra`` / ``project:`` —
это Python-фильтр ``_project_assets()`` в ``objects_service.py``, а не IAM;
service user имеет ``agentscale/*`` целиком (``minio-api-policy.json``). Баг в
Python-коде = утечка между компаниями.

Целевая модель: bucket-per-tenant ``tenant-<company>`` и жёсткий префикс
``module-<plugin>/``. Плагин получает short-lived STS-креденшелы (или scoped
presigned), а НЕ root/service-креды платформы.

ВАЖНО — про policy variables (открытый вопрос v4 §12.8, blocker плана §5.1):
MinIO умеет подставлять ``${aws:username}`` и (для JWT/OIDC) ``${jwt:*}`` в
Resource/Condition политики. Это ОПЦИЯ MinIO, а не гарантированный контракт
AWS IAM; конкретный набор переменных, поведение ``AssumeRole`` с session policy
и допустимость ``StringLike`` по ``s3:prefix`` ЗАВИСЯТ ОТ ВЕРСИИ MinIO и требуют
подтверждения ОФИЦИАЛЬНОЙ документацией MinIO. В этом ревью страница была
недоступна. Поэтому по умолчанию здесь НЕ используются никакие переменные:
«динамизм» достигается явной генерацией политики под конкретную пару
``(company_id, plugin_id)`` на стороне доверенной платформы. Каждая пара
получает собственный стабильный объект политики с уже подставленными
литералами. Если STS в deployment отсутствует — используется только
``presign_get/presign_put`` с тем же самым явным префиксом.
"""

from __future__ import annotations

import re
from typing import Any

from prodavan.application.plugins.objects.errors import InvalidObjectKey

POLICY_VERSION = "2012-10-17"

# Единственный разрешённый набор S3-действий. Ни s3:* , ни s3:CreateBucket,
# ни s3:DeleteBucket, ни s3:PutBucketPolicy плагину недоступны: бакет и policy
# создаёт доверенная платформа через BucketAdminPort, а не principal плагина.
OBJECT_ACTIONS: tuple[str, ...] = (
    "s3:GetObject",
    "s3:PutObject",
    "s3:DeleteObject",
    "s3:ListBucket",
)

# ``s3:ListBucket`` применяется к самому бакету, остальные — к объектам.
# Разделяем их в разные Statement, потому что ARN бакета и ARN объектов —
# разные ресурсы; общий «*» здесь был бы дырой.
_BUCKET_ACTIONS: tuple[str, ...] = ("s3:ListBucket",)
_PREFIX_CONDITION_KEY = "s3:prefix"

# Только lowercase: имя S3-бакета обязано быть в нижнем регистре. Намеренно
# НЕ приводим ``company_id`` к lower(): ``co_A`` и ``co_a`` слились бы в один
# бакет — это утечка между компаниями. Некорректный идентификатор = отказ.
_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


def _validate_identifier(value: str, *, kind: str) -> str:
    """Идентификатор идёт в имя бакета и в ARN — валидируем ЖЁСТКО.

    Fail-closed: без ``[a-z0-9_-]`` невозможно образование ``../``, ``//``,
    ``*``, ``?``, ``arn:``-инъекций, разделителей пути и подстановки другого
    ресурса. Пустое значение, не-строка и верхний регистр — тоже отказ, а не
    «подставим дефолт» / «приведём к нижнему регистру».
    """
    if not isinstance(value, str) or not value:
        raise InvalidObjectKey(f"пустой или нестроковый {kind}")
    if not _IDENTIFIER_RE.fullmatch(value):
        raise InvalidObjectKey(f"недопустимый {kind}: {value!r}")
    return value


def bucket_name(company_id: str) -> str:
    """Имя бакета компании: ``tenant-<company>`` (v4 §5.4)."""
    return f"tenant-{_validate_identifier(company_id, kind='company_id')}"


def module_prefix(plugin_id: str) -> str:
    """Префикс плагина внутри бакета: ``module-<plugin>/``."""
    return f"module-{_validate_identifier(plugin_id, kind='plugin_id')}/"


def _bucket_arn(company_id: str) -> str:
    return f"arn:aws:s3:::{bucket_name(company_id)}"


def _object_arn(company_id: str, plugin_id: str) -> str:
    return f"{_bucket_arn(company_id)}/{module_prefix(plugin_id)}*"


def _statements(company_id: str, plugin_id: str) -> list[dict[str, Any]]:
    """Общий набор Statement для родительской и session-политики.

    Session policy ДОЛЖНА быть подмножеством родительской (иначе ``AssumeRole``
    её всё равно обрежет, но мы не хотим расхождений между кодом и IAM). Один
    генератор на обе политики — гарантия подмножества конструктивно, а не
    «проверкой в тесте».
    """
    prefix = module_prefix(plugin_id)
    return [
        {
            "Sid": "ListOwnModulePrefix",
            "Effect": "Allow",
            "Action": list(_BUCKET_ACTIONS),
            "Resource": [_bucket_arn(company_id)],
            # ListBucket без Condition на prefix позволил бы перечислить ВСЕ
            # объекты бакета компании, включая соседние плагины. Жёсткое
            # условие — обязательная часть изоляции, а не «улучшение».
            "Condition": {
                "StringLike": {_PREFIX_CONDITION_KEY: [f"{prefix}*"]}
            },
        },
        {
            "Sid": "ReadWriteOwnModuleObjects",
            "Effect": "Allow",
            "Action": [a for a in OBJECT_ACTIONS if a not in _BUCKET_ACTIONS],
            "Resource": [_object_arn(company_id, plugin_id)],
            # s3:prefix допустим только для ListBucket выше. Object-level
            # действия ограничены самим exact tenant/module prefix ARN.
        },
    ]


def bucket_policy(company_id: str, plugin_id: str) -> dict[str, Any]:
    """Родительская IAM-политика для ``(company_id, plugin_id)``.

    Возвращает JSON-совместимый ``dict``::

        {
          "Version": "2012-10-17",
          "Statement": [
            {"Action": ["s3:ListBucket"],
             "Resource": ["arn:aws:s3:::tenant-<co>"],
             "Condition": {"StringLike": {"s3:prefix": ["module-<p>/*"]}}},
            {"Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
             "Resource": ["arn:aws:s3:::tenant-<co>/module-<p>/*"]}
          ]
        }

    В политике НЕТ и не может быть ``arn:aws:s3:::agentscale/*``: имя бакета
    строится только из ``tenant-`` + провалидированный ``company_id``.

    Политика НЕ содержит ``${aws:username}`` / ``${jwt:*}``: см. docstring
    модуля — variables являются опцией MinIO, требующей подтверждения
    официальной документацией; по умолчанию используем явный префикс.
    """
    return {
        "Version": POLICY_VERSION,
        "Statement": _statements(company_id, plugin_id),
    }


def session_policy(company_id: str, plugin_id: str) -> dict[str, Any]:
    """Session policy для MinIO STS ``AssumeRole``.

    Передаётся как ``Policy=json.dumps(...)`` при ``AssumeRole``. Эффективные
    права сессии = пересечение родительской политики роли и этой session policy;
    здесь она СТРОГО равна подмножеству (фактически тому же самому набору
    Statement, что и ``bucket_policy``), поэтому:

    * плагин не может получить прав БОЛЬШЕ, чем ``module-<plugin>/*``;
    * при отсутствии/отключении STS адаптер не может «тихо» выдать root-креды —
      см. ``StsUnavailable`` в ``service.py``;
    * права ограничены ещё и TTL (``ttl_seconds`` в ``issue_credentials``).

    ВНИМАНИЕ: сам факт поддержки ``AssumeRole`` + session policy конкретной
    версией MinIO (``RELEASE.2024-10-02T17-50-41Z`` в ``minio.yaml``) требует
    подтверждения официальной документацией MinIO (v4 §12.8, план §5.1).
    До подтверждения реализация остаётся fail-closed: ``StsPort`` — это Port,
    а не «гарантированно работающий вызов», и при его недоступности операция
    падает, а не деградирует до presigned-URL от root-ключей или до base64-байт
    через API.
    """
    return {
        "Version": POLICY_VERSION,
        "Statement": _statements(company_id, plugin_id),
    }


__all__ = [
    "POLICY_VERSION",
    "OBJECT_ACTIONS",
    "bucket_name",
    "module_prefix",
    "bucket_policy",
    "session_policy",
]
