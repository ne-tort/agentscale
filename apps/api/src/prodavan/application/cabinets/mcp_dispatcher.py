"""Dispatch platform cabinet.* MCP tools to application services (L06)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.cabinets.access import CabinetAccessService
from prodavan.application.cabinets.bundle_service import CabinetBundleService
from prodavan.application.cabinets.instance_service import CabinetInstanceService
from prodavan.application.cabinets.meta_service import CabinetMetaService
from prodavan.application.cabinets.packages_service import CabinetPackagesService
from prodavan.application.cabinets.rows_service import CabinetRowsService
from prodavan.domain.cabinets.mcp_tools import BANNED_TOOL_PREFIXES, PLATFORM_TOOLS, TOOL_BY_NAME
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.identity import EmployeeRow


def list_platform_tools() -> list[dict]:
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
            "implemented": t.implemented,
        }
        for t in PLATFORM_TOOLS
    ]


class CabinetMcpDispatcher:
    """Single entry for agent/runtime: tool name + JSON args → service call.

    No raw SQL path. Unknown / banned tools fail closed.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._access = CabinetAccessService(session)
        self._instances = CabinetInstanceService(session)
        self._meta = CabinetMetaService(session)
        self._rows = CabinetRowsService(session)
        self._bundles = CabinetBundleService(session)
        self._packages = CabinetPackagesService(session)

    async def call(
        self,
        *,
        cabinet_id: str,
        tool: str,
        arguments: dict[str, Any] | None,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        args = arguments or {}
        if not isinstance(args, dict):
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="arguments must be object",
            )

        self._reject_banned(tool)
        spec = TOOL_BY_NAME.get(tool)
        if spec is None:
            raise AppError(
                code="MCP_UNKNOWN_TOOL",
                title="Unknown tool",
                status=404,
                detail=f"unknown tool: {tool}",
            )
        if not spec.implemented:
            raise AppError(
                code="MCP_NOT_IMPLEMENTED",
                title="Tool not implemented",
                status=501,
                detail=f"{tool} not implemented yet",
            )

        # Ensure ACL before any work (write tools still re-check in services)
        await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=False
        )
        self._validate_required(spec.input_schema, args)

        if tool == "cabinet.info":
            return await self._instances.get(cabinet_id=cabinet_id, principal=principal, employee=employee)

        if tool == "cabinet.tables.list":
            return {
                "tables": await self._meta.list_tables(
                    cabinet_id=cabinet_id, principal=principal, employee=employee
                )
            }

        if tool == "cabinet.tables.create":
            return await self._meta.create_table(
                cabinet_id=cabinet_id,
                slug=str(args["slug"]),
                label=str(args["label"]),
                storage_kind=str(args.get("storage_kind") or "physical"),
                columns=list(args["columns"]),
                principal=principal,
                employee=employee,
            )

        if tool == "cabinet.tables.archive":
            return await self._meta.archive_table(
                cabinet_id=cabinet_id,
                table_slug=str(args["table_slug"]),
                principal=principal,
                employee=employee,
            )

        if tool == "cabinet.tabs.list":
            return {
                "tabs": await self._meta.list_tabs(
                    cabinet_id=cabinet_id, principal=principal, employee=employee
                )
            }

        if tool == "cabinet.rows.query":
            return await self._rows.query_rows(
                cabinet_id=cabinet_id,
                table_slug=str(args["table_slug"]),
                principal=principal,
                employee=employee,
                limit=int(args.get("limit") or 50),
                offset=int(args.get("offset") or 0),
            )

        if tool == "cabinet.rows.upsert":
            return await self._rows.upsert_row(
                cabinet_id=cabinet_id,
                table_slug=str(args["table_slug"]),
                values=dict(args.get("values") or {}),
                row_id=args.get("id"),
                principal=principal,
                employee=employee,
            )

        if tool == "cabinet.rows.delete":
            await self._rows.delete_row(
                cabinet_id=cabinet_id,
                table_slug=str(args["table_slug"]),
                row_id=str(args["id"]),
                principal=principal,
                employee=employee,
            )
            return {"deleted": True, "id": args["id"]}

        if tool == "cabinet.bundle.export":
            return await self._bundles.export_base64(
                cabinet_id=cabinet_id,
                principal=principal,
                employee=employee,
                include_data=bool(args.get("include_data", True)),
            )

        if tool == "cabinet.bundle.import":
            if employee is None:
                raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")
            return await self._bundles.import_base64(
                zip_base64=str(args["zip_base64"]),
                company_id=str(args["company_id"]),
                employee=employee,
                name_override=args.get("name"),
            )

        if tool == "cabinet.mcp_packages.deploy":
            return await self._packages.deploy_base64(
                cabinet_id=cabinet_id,
                zip_base64=str(args["zip_base64"]),
                principal=principal,
                employee=employee,
                replace_if_name=bool(args.get("replace_if_name", False)),
            )

        if tool == "cabinet.mcp_packages.list":
            return {
                "packages": await self._packages.list_packages(
                    cabinet_id=cabinet_id, principal=principal, employee=employee
                )
            }

        if tool == "cabinet.mcp_packages.disable":
            return await self._packages.disable(
                cabinet_id=cabinet_id,
                name=str(args["name"]),
                principal=principal,
                employee=employee,
            )

        if tool == "cabinet.mcp_packages.export":
            return await self._packages.export_base64(
                cabinet_id=cabinet_id,
                name=str(args["name"]),
                principal=principal,
                employee=employee,
            )

        raise AppError(
            code="MCP_NOT_IMPLEMENTED",
            title="Tool not implemented",
            status=501,
            detail=f"{tool} dispatch missing",
        )

    @staticmethod
    def _reject_banned(tool: str) -> None:
        lowered = tool.lower().strip()
        for prefix in BANNED_TOOL_PREFIXES:
            if lowered == prefix or lowered.startswith(prefix + "."):
                raise AppError(
                    code="MCP_FORBIDDEN_TOOL",
                    title="Forbidden tool",
                    status=403,
                    detail="raw SQL / execute tools are not allowed",
                )
        if "sql" in lowered.split(".") or lowered.endswith(".sql"):
            raise AppError(
                code="MCP_FORBIDDEN_TOOL",
                title="Forbidden tool",
                status=403,
                detail="raw SQL / execute tools are not allowed",
            )

    @staticmethod
    def _validate_required(schema: dict[str, Any], args: dict[str, Any]) -> None:
        required = schema.get("required") or []
        missing = [k for k in required if k not in args]
        if missing:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=f"missing required: {', '.join(missing)}",
            )
        if schema.get("additionalProperties") is False:
            allowed = set((schema.get("properties") or {}).keys())
            extra = set(args) - allowed
            if extra:
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail=f"unknown arguments: {', '.join(sorted(extra))}",
                )
