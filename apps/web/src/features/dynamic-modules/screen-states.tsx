import { useId, type ReactNode } from "react";
import { Button, EmptyState, InlineError, LoadingState } from "../../shared/ui";
import type { CapabilityName } from "../../capabilities/types";
import { DYNAMIC_MODULE_CAPABILITIES, type DynamicModuleOperation } from "./capability";
import type { DynamicModuleServiceError } from "./service";

export function DynamicModulesLoading({ label = "Loading dynamic modules…" }: { label?: string }) {
  return <LoadingState className="dynamic-modules__loading" label={label} />;
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
  const titleId = useId();
  const required = DYNAMIC_MODULE_CAPABILITIES[operation];
  const missingList = (missing && missing.length ? missing : [required]).join(", ");
  return <section aria-labelledby={titleId} className="dynamic-modules__blocked" role="alert">
    <h2 id={titleId}>Capability required</h2>
    <p>
      This {operation === "read" ? "registry view" : "registry command"} needs the
      {" "}<code>{required}</code> capability, which is not part of your effective grant.
      Nothing was requested from the server.
    </p>
    <p className="dynamic-modules__blocked-detail">
      Missing: <code>{missingList}</code>. Requested capabilities and effective grants are
      separate: a manifest request is not a grant.
    </p>
    {children}
  </section>;
}

export function DynamicModulesApiNotice({ missing }: { readonly missing: readonly string[] }) {
  const titleId = useId();
  return <section aria-labelledby={titleId} className="dynamic-modules__blocked">
    <h2 id={titleId}>Dynamic module API is not wired in this shell build</h2>
    <p>
      The typed client contract is in place, but the authenticated API facade does not expose the
      registry transport yet. No request was sent and no state was changed.
    </p>
    <p className="dynamic-modules__blocked-detail">
      Missing facade methods: <code>{missing.join(", ") || "dynamicModules"}</code>
    </p>
  </section>;
}

export interface ErrorNoticeProps {
  readonly error: DynamicModuleServiceError;
  readonly onRetry?: () => void;
  readonly retryLabel?: string;
}

export function DynamicModuleErrorNotice({ error, onRetry, retryLabel = "Retry" }: ErrorNoticeProps) {
  const detail = [
    `Code: ${error.code}`,
    error.status === undefined ? "" : `HTTP ${error.status}`,
    error.traceId ? `Trace: ${error.traceId}` : "",
  ].filter(Boolean).join(" · ");
  return <InlineError
    className="dynamic-modules__error"
    message={<>
      <span className="dynamic-modules__error-message">{error.message}</span>
      <span className="dynamic-modules__error-detail">{detail}</span>
    </>}
    action={onRetry ? <Button onClick={onRetry} size="small" variant="secondary">{retryLabel}</Button> : undefined}
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
