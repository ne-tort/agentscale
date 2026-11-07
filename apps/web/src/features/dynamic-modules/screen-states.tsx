import { useId, type ReactNode } from "react";
import type { CapabilityName } from "../../capabilities/types";
import { useI18n } from "../../i18n/useT";
import { Button, EmptyState, InlineError, LoadingState } from "../../shared/ui";
import { DYNAMIC_MODULE_CAPABILITIES, type DynamicModuleOperation } from "./capability";
import type { DynamicModuleServiceError } from "./service";

export function DynamicModulesLoading({ label }: { label?: string }) {
  const { t } = useI18n();
  return <LoadingState className="dynamic-modules__loading" label={label ?? t("dynamicModules.loadingModules")} />;
}

/**
 * Capability refusal. Rendered instead of the screen body: the shell hides what it
 * cannot do, and the gateway still re-checks the grant on every call.
 */
export function CapabilityDeniedNotice({
  operation,
  missing,
  children,
}: {
  readonly operation: DynamicModuleOperation;
  readonly missing?: readonly CapabilityName[];
  readonly children?: ReactNode;
}) {
  const { t } = useI18n();
  const titleId = useId();
  const required = DYNAMIC_MODULE_CAPABILITIES[operation];
  const missingList = (missing && missing.length ? missing : [required]).join(", ");
  return <section aria-labelledby={titleId} className="dynamic-modules__blocked" role="alert">
    <h2 id={titleId}>{t("dynamicModules.capabilityRequiredHeading")}</h2>
    <p>
      {operation === "read"
        ? t("dynamicModules.capabilityRequiredRead", { capability: required })
        : t("dynamicModules.capabilityRequiredManage", { capability: required })}
    </p>
    <p className="dynamic-modules__blocked-detail">
      {t("dynamicModules.capabilityMissing", { list: missingList })}
    </p>
    {children}
  </section>;
}

export function DynamicModulesApiNotice({ missing }: { readonly missing: readonly string[] }) {
  const { t } = useI18n();
  const titleId = useId();
  return <section aria-labelledby={titleId} className="dynamic-modules__blocked">
    <h2 id={titleId}>{t("dynamicModules.apiUnavailableHeading")}</h2>
    <p>
      {t("dynamicModules.apiUnavailableBody")}
    </p>
    <p className="dynamic-modules__blocked-detail">
      {t("dynamicModules.apiMissing", { list: missing.join(", ") || "dynamicModules" })}
    </p>
  </section>;
}

export interface ErrorNoticeProps {
  readonly error: DynamicModuleServiceError;
  readonly onRetry?: () => void;
  readonly retryLabel?: string;
}

export function DynamicModuleErrorNotice({ error, onRetry, retryLabel }: ErrorNoticeProps) {
  const { t } = useI18n();
  // The code, status and trace id stay: they are what a support request needs, and
  // the message above them is already in plain language.
  const detail = [
    t("dynamicModules.errorCode", { code: error.code }),
    error.status === undefined ? "" : t("dynamicModules.errorHttp", { status: error.status }),
    error.traceId ? t("dynamicModules.errorTrace", { trace: error.traceId }) : "",
  ].filter(Boolean).join(" · ");
  return <InlineError
    className="dynamic-modules__error"
    message={<>
      <span className="dynamic-modules__error-message">{error.message}</span>
      <span className="dynamic-modules__error-detail">{detail}</span>
    </>}
    action={onRetry ? <Button onClick={onRetry} size="small" variant="secondary">{retryLabel ?? t("common.retry")}</Button> : undefined}
  />;
}

export function DynamicModulesEmpty({
  title,
  description,
  action,
}: {
  readonly title: string;
  readonly description?: ReactNode;
  readonly action?: ReactNode;
}) {
  // Пустой реестр — это результат загрузки, а не просто разметка: `role="status"`
  // объявляет его ассистивным технологиям так же, как ошибку/загрузку рядом.
  return <div role="status">
    <EmptyState action={action} className="dynamic-modules__empty" description={description} title={title} />
  </div>;
}
