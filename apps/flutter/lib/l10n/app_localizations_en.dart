// ignore: unused_import
import 'package:intl/intl.dart' as intl;

import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for English (`en`).
class AppLocalizationsEn extends AppLocalizations {
  AppLocalizationsEn([String locale = 'en']) : super(locale);

  @override
  String get adminActiveEmployees => 'Active employees';

  @override
  String get adminEmployeesOnline => 'Employees online';

  @override
  String get adminAdminEmail => 'Admin email';

  @override
  String get adminAgentMessages => 'AI requests';

  @override
  String get adminAgentPolicySaved => 'Agent policy saved';

  @override
  String get adminAgentRuntimePolicy => 'Policy';

  @override
  String get adminAiKeysBound => 'API keys';

  @override
  String adminAlertHighUsageTitle(String companyName) {
    return '$companyName: high agent token usage';
  }

  @override
  String adminAlertKeyRenewalTitle(String companyName) {
    return '$companyName: AI key renewal soon';
  }

  @override
  String adminAlertKeysRenewingSoon(String count) {
    return '$count key(s) renewing soon';
  }

  @override
  String adminAlertKeysRenewingSoonNext(String count, String next) {
    return '$count key(s) renewing soon · next $next';
  }

  @override
  String get adminAlertNoKeysSubtitle =>
      'Agent sessions will fail with NO_AI_KEY';

  @override
  String adminAlertNoKeysTitle(String companyName) {
    return '$companyName: no AI keys bound';
  }

  @override
  String adminAlertSubExpiredTitle(String companyName) {
    return '$companyName: subscription expired';
  }

  @override
  String adminAlertSubExpiringTitle(String companyName) {
    return '$companyName: subscription expiring';
  }

  @override
  String get adminAlertSubscriptionEnded => 'Subscription ended';

  @override
  String adminAlertSubscriptionEndedAt(String ends) {
    return 'Subscription ended · $ends';
  }

  @override
  String get adminAlertSubscriptionEndsSoon => 'Subscription ends soon';

  @override
  String adminAlertSubscriptionEndsSoonAt(String ends) {
    return 'Subscription ends soon · $ends';
  }

  @override
  String adminAlertTokensAboveThreshold(String tokens) {
    return 'Agent tokens $tokens above platform threshold';
  }

  @override
  String get adminAlerts => 'Alerts';

  @override
  String get adminApiKind => 'API kind';

  @override
  String adminApiKindValue(String api_kind) {
    return 'API kind: $api_kind';
  }

  @override
  String get adminBindCompanies => 'Bind companies';

  @override
  String adminBindingsSelected(String count) {
    return '$count selected';
  }

  @override
  String get adminBundle => 'Bundle';

  @override
  String get adminCabinetQuotas => 'Quotas';

  @override
  String get adminCabinetQuotasMustBePositive =>
      'Cabinet quotas must be positive integers';

  @override
  String adminCabinetsProjectsSubtitle(String cabinets, String projects) {
    return '$cabinets cabinets · $projects projects';
  }

  @override
  String adminCabinetsQuotaCell(String active, String max) {
    return '$active / $max cabinets';
  }

  @override
  String get adminCompanyAdminInvite => 'Company admin invite';

  @override
  String get adminCompanyBindings => 'Company bindings';

  @override
  String get adminCompanyName => 'Company name';

  @override
  String get adminAddAiKey => 'Add key';

  @override
  String get adminAddCompany => 'Add company';

  @override
  String get adminCompanyGeneral => 'General';

  @override
  String get adminEvents => 'Events';

  @override
  String get adminInviteAdmin => 'Admin';

  @override
  String get adminPolicy => 'Policy';

  @override
  String get adminAgentLimits => 'Agent limits';

  @override
  String get adminQuotas => 'Quotas';

  @override
  String adminQuotasSummary(String cabinets, String packages, String bundleMb) {
    return '$cabinets · $packages · $bundleMb';
  }

  @override
  String get adminCreateAiKey => 'Create AI key';

  @override
  String get adminCreateCompany => 'Create company';

  @override
  String get adminCreateCompanyAndInviteAdmin =>
      'Create a company and invite company.admin';

  @override
  String get adminCreateCompanyToSeeMetrics =>
      'Create a company to see platform metrics';

  @override
  String get adminCreateKey => 'Create key';

  @override
  String get adminCreateRuntimeKeyHint => 'Add a key and configure on its page';

  @override
  String get adminDisableAiKey => 'Pause AI key';

  @override
  String get adminDisableKey => 'Pause key';

  @override
  String get adminResumeKey => 'Resume';

  @override
  String adminDisableKeyConfirm(String keyName) {
    return 'Pause key? Related projects will be paused';
  }

  @override
  String get adminDrainProjectTriggers => 'Triggers';

  @override
  String adminDrainedTriggers(String count) {
    return 'Drained $count trigger(s)';
  }

  @override
  String get adminDraining => 'Draining…';

  @override
  String get adminEditBindings => 'Edit bindings';

  @override
  String get adminEndsAt => 'Subscription';

  @override
  String get adminDateFormatHint => 'DD.MM.YY';

  @override
  String get adminInvalidDate => 'Format: DD.MM.YY or DD.MM.YYYY';

  @override
  String get adminEndsAtIfNotLifetime => 'Subscription (if not lifetime)';

  @override
  String get adminSubscription => 'Subscription';

  @override
  String adminEnterIntegerMin(String min) {
    return 'Enter integer ≥ $min';
  }

  @override
  String get adminIdlePauseAfterHours => 'Idle pause';

  @override
  String adminIdlePausedProjectsInCompany(String count) {
    return 'Idle-paused $count project(s) in this company';
  }

  @override
  String get adminInviteCompanyAdminViaKeycloak =>
      'Invite company.admin via Keycloak — password is not accepted.';

  @override
  String get adminKey => 'Key';

  @override
  String get adminKeyInfo => 'Key info';

  @override
  String adminKeyListSubtitle(String type, String provider) {
    return '$type · $provider';
  }

  @override
  String get adminIntegrationType => 'Type';

  @override
  String get adminTypeCursorSdk => 'Cursor SDK';

  @override
  String get adminTypeCodexSdk => 'Codex SDK';

  @override
  String get adminTypeClaudeSdk => 'Claude Agent SDK';

  @override
  String get adminTypeApiKey => 'API key';

  @override
  String get adminNextRenewal => 'Subscription';

  @override
  String get adminAddHttpProvider => 'Add HTTP endpoint';

  @override
  String get adminHttpEndpoint => 'HTTP endpoint';

  @override
  String get adminHttpBaseUrl => 'Base URL';

  @override
  String get adminHttpApiKind => 'API kind';

  @override
  String get adminHttpApiKindOpenai => 'OpenAI API';

  @override
  String get adminHttpApiKindAnthropic => 'Anthropic API';

  @override
  String get adminHttpApiKindOpenrouter => 'OpenRouter';

  @override
  String get adminHttpApiKindCustom => 'Custom / OpenAPI';

  @override
  String get adminHttpOpenaiCompatible => 'OpenAI-compatible';

  @override
  String get adminHttpAuthScheme => 'Auth';

  @override
  String get adminHttpAuthBearer => 'Bearer';

  @override
  String get adminHttpAuthXApiKey => 'x-api-key';

  @override
  String get adminHttpAuthNone => 'No auth';

  @override
  String get adminHttpChatPath => 'Chat completions path';

  @override
  String get adminHttpModelsPath => 'Models path';

  @override
  String get adminToolPresetChatReadonly => 'Chat (read-only)';

  @override
  String get adminToolPresetWorkspaceDev => 'Development';

  @override
  String get adminToolPresetWorkspaceFull => 'Full access';

  @override
  String get adminIdlePauseNever => 'Never pause';

  @override
  String adminIdlePauseHours(String hours) {
    return '$hours h';
  }

  @override
  String get adminAlertTag => 'Tag';

  @override
  String get adminAlertTagNoKeys => 'no keys';

  @override
  String adminAlertTagKeyRenewal(int count) {
    return 'renewal · $count';
  }

  @override
  String get adminAlertTagHighUsage => 'high usage';

  @override
  String get adminAlertTagSubExpiring => 'subscription';

  @override
  String get adminAlertTagSubExpired => 'expired';

  @override
  String get adminAlertTagIdentityUnbound => 'IDENTITY_UNBOUND';

  @override
  String adminAlertTagEmployeesUnbound(int count) {
    return 'EMPLOYEES_UNBOUND · $count';
  }

  @override
  String get adminAlertTagCascadeIncomplete => 'CASCADE_INCOMPLETE';

  @override
  String get commonPhone => 'Phone';

  @override
  String get adminKeyMetadata => 'Key metadata';

  @override
  String get adminLifecycle => 'Lifecycle';

  @override
  String get adminLifetimeSubscription => 'Lifetime subscription';

  @override
  String get adminMaxAgentTokensMonth => 'Agent tokens / month';

  @override
  String get adminMaxBundleImportMb => 'Bundle';

  @override
  String get adminMaxCabinets => 'Cabinets';

  @override
  String get adminMaxChatAttachmentMb => 'Chat attachment';

  @override
  String get adminMaxPackagesPerCabinet => 'Packages';

  @override
  String get adminMaxTokensPerRun => 'Tokens per run';

  @override
  String get adminMaxUsdCostMonth => 'USD cost / month';

  @override
  String get adminMetadataOnly => 'metadata only';

  @override
  String get adminMetrics => 'Metrics';

  @override
  String adminMetricsAiKeysRenewSoon(String count) {
    return '$count AI key(s) renew soon';
  }

  @override
  String adminMetricsAiKeysRenewSoonNext(String count, String next) {
    return '$count AI key(s) renew soon · next $next';
  }

  @override
  String adminMetricsHighTokenUsage(String tokens) {
    return 'High agent token usage ($tokens tokens)';
  }

  @override
  String get adminMetricsNoAiKeysBound =>
      'No AI keys bound — agent will return NO_AI_KEY';

  @override
  String get adminMetricsSubscriptionExpired => 'Subscription expired';

  @override
  String adminMetricsSubscriptionExpiring(String ends) {
    return 'Subscription expiring · $ends';
  }

  @override
  String get adminMetricsIdentityUnbound =>
      'Waiting for authentication service';

  @override
  String adminMetricsEmployeesUnbound(String count) {
    return 'Waiting for authentication service · $count employee(s)';
  }

  @override
  String get adminModelAllowlist => 'Model allowlist';

  @override
  String get adminNewSecret => 'New secret';

  @override
  String adminNextRenewalValue(String next_renewal_at) {
    return 'Next renewal: $next_renewal_at';
  }

  @override
  String get adminNoAiKeys => 'No AI keys';

  @override
  String get adminNoCompanies => 'No companies';

  @override
  String get adminNoContainers => 'No projects';

  @override
  String get adminNoCabinets => 'No cabinets';

  @override
  String get adminAddCabinet => 'Add cabinet';

  @override
  String get adminNoModules => 'No modules';

  @override
  String get adminAddModule => 'Add module';

  @override
  String adminDeleteModuleConfirm(String name) {
    return 'Delete module \"$name\"? Meta and bindings will be removed; cabinets and projects are not affected.';
  }

  @override
  String get adminModuleCopied => 'Module ID copied';

  @override
  String adminModuleCabinetsCount(int count) {
    return '$count cabinets';
  }

  @override
  String get adminModuleJson => 'JSON';

  @override
  String get adminModuleJsonConfigured => 'Configured';

  @override
  String get adminModulePreview => 'Seed data';

  @override
  String get adminModulePreviewEmpty => 'Add module JSON to edit seed data';

  @override
  String get adminModuleJsonInvalid => 'Invalid JSON';

  @override
  String get adminMetaInvalid => 'Metadata';

  @override
  String metaAddNew(String item) {
    return 'Add new $item';
  }

  @override
  String get adminModulePreviewMode => 'Seed data';

  @override
  String get adminModulePreviewShellNav => 'Shell nav (preview)';

  @override
  String get adminModulePreviewCabinetTabs => 'Cabinet tabs';

  @override
  String get adminModuleSeedEmpty => 'No seed rows yet';

  @override
  String get adminSelectCabinetsForModule => 'Select cabinets for module';

  @override
  String get adminSelectModulesForCabinet => 'Select modules for cabinet';

  @override
  String get adminGrantAllCompanies => 'All companies';

  @override
  String get adminPromptProfiles => 'Prompt profiles';

  @override
  String get adminSelectCompaniesForModule => 'Select companies for module';

  @override
  String adminDeleteCabinetConfirm(String name) {
    return 'Delete cabinet \"$name\"? All projects and the cabinet schema will be removed.';
  }

  @override
  String get adminCabinetCopied => 'Cabinet ID copied';

  @override
  String get adminCabinetOwnerScope => 'Owner scope';

  @override
  String adminCabinetCompaniesCount(int count) {
    return '$count companies';
  }

  @override
  String get adminSelectCompanyForCabinet => 'Select company';

  @override
  String get cabinetOpenedPlaceholder =>
      'Cabinet is open. Meta UI will return in a later iteration.';

  @override
  String get adminContainerColProject => 'Project';

  @override
  String get adminContainerColStatus => 'Status';

  @override
  String get adminContainerColCompany => 'Company';

  @override
  String get adminContainerColEmployee => 'Employee';

  @override
  String get adminContainerColProvider => 'Provider';

  @override
  String get adminContainerColCabinet => 'Cabinet';

  @override
  String get adminContainerStatusActive => 'Active';

  @override
  String get adminContainerStatusPaused => 'Paused';

  @override
  String get adminContainerStatusDraft => 'Draft';

  @override
  String get adminContainerRuntimeRef => 'Runtime ref';

  @override
  String get adminContainerMetricsHole => 'K8s metrics';

  @override
  String get adminContainerK8sPhase => 'K8s phase';

  @override
  String get adminContainerPodStatus => 'Pod status';

  @override
  String get adminContainerDesiredState => 'Desired state';

  @override
  String get adminContainerPodReadyTrue => 'Pod ready';

  @override
  String get adminContainerPodReadyFalse => 'Pod not ready';

  @override
  String get adminContainerPodRestarts => 'Restarts';

  @override
  String get adminContainerMetricsCpu => 'CPU';

  @override
  String get adminContainerMetricsMemory => 'Memory';

  @override
  String get adminContainerMetricsUnavailable =>
      'Metrics not yet received or unavailable';

  @override
  String get adminContainerLastError => 'Error';

  @override
  String get adminContainerRuntimeNotStarted =>
      'Pod is not running. It starts on first agent use or after Resume.';

  @override
  String get adminContainerRuntimeNotStartedShort => 'Not running';

  @override
  String get adminContainerRuntimePaused => 'Pod stopped — project is paused.';

  @override
  String get adminContainerRuntimePausedShort => 'Stopped';

  @override
  String get adminContainerRuntimeAttention =>
      'Project is active but no k8s pod yet. Open the agent or tap Resume.';

  @override
  String get adminContainerColK8s => 'K8s';

  @override
  String get containerObservedStateLabel => 'State';

  @override
  String get containerStateLabel => 'State';

  @override
  String get containerCreatedAt => 'Created';

  @override
  String get containerLastLaunch => 'Last launch';

  @override
  String get containerPodServiceId => 'Pod ID';

  @override
  String get containerPodIdCopied => 'Pod ID copied';

  @override
  String get containerKubId => 'Kub ID';

  @override
  String get containerKubIdCopied => 'Kub ID copied';

  @override
  String get projectBudgetLabel => 'Budget';

  @override
  String get containerStartedAt => 'Last launch';

  @override
  String get containerUptime => 'Uptime';

  @override
  String get containerRestarts => 'Restarts';

  @override
  String containerDurationDays(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count d',
      one: '$count d',
    );
    return '$_temp0';
  }

  @override
  String containerDurationHours(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count h',
      one: '$count h',
    );
    return '$_temp0';
  }

  @override
  String containerDurationMinutes(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count min',
      one: '$count min',
    );
    return '$_temp0';
  }

  @override
  String get containerObservedPreparing => 'Preparing';

  @override
  String get containerObservedProvisioning => 'Provisioning';

  @override
  String get containerObservedHydrating => 'Hydrating';

  @override
  String get containerObservedStarting => 'Starting';

  @override
  String get containerObservedRunning => 'Running';

  @override
  String get containerObservedDegraded => 'Degraded';

  @override
  String get containerObservedFailed => 'Failed';

  @override
  String get containerObservedPaused => 'Paused';

  @override
  String get containerObservedAbsent => 'Not created';

  @override
  String get containerObservedUnknown => 'Unknown';

  @override
  String get containerMetricsAwaiting =>
      'CPU/RAM: awaiting first metrics sample';

  @override
  String get containerErrorCopied => 'Error copied to clipboard';

  @override
  String get adminContainerOrchestratorStatus => 'Orchestrator status';

  @override
  String adminDeleteContainerConfirm(String name) {
    return 'Delete project \"$name\"? Workspace will be wiped.';
  }

  @override
  String adminDeleteCompanyConfirm(String name) {
    return 'Delete company \"$name\"? This disables employees, pauses and deletes projects (workspace wipe), and hard-deletes cabinets.';
  }

  @override
  String adminDisableAiKeyConfirm(String name) {
    return 'Pause key? Related projects will be paused';
  }

  @override
  String get adminNoCompaniesBound => 'No companies bound';

  @override
  String get adminNoCompaniesYet => 'No companies yet';

  @override
  String get adminNoPlatformEventsYet => 'No platform events yet';

  @override
  String get adminNoStarterBundles => 'No starter bundles';

  @override
  String get adminPlatformEvents => 'Events';

  @override
  String get adminPlatformFallback => 'Platform fallback';

  @override
  String adminPlatformIdleSweep(String count, String companies) {
    return 'Platform idle sweep: $count project(s) ($companies companies with policy)';
  }

  @override
  String get adminPlatformTotals => 'Metrics';

  @override
  String adminCompanyCabinetsRunning(String running, String quota) {
    return '$running / $quota';
  }

  @override
  String get adminPreferredProvider => 'Provider';

  @override
  String get adminPreferredProviderOptional => 'Provider';

  @override
  String get adminProdavanSubscription => 'Prodavan subscription';

  @override
  String get adminProdavanSubscriptionOptional => 'Prodavan subscription';

  @override
  String adminProviderValue(String provider) {
    return 'Provider: $provider';
  }

  @override
  String get adminQuotasSaved => 'Quotas saved';

  @override
  String get adminRenewPlusOneMonth => 'Renew +1 month';

  @override
  String get adminRenewing => 'Renewing…';

  @override
  String adminRotateKeyTitle(String keyName) {
    return 'Rotate $keyName';
  }

  @override
  String get adminRotateSecret => 'Rotate secret';

  @override
  String get adminRotateSecretHint =>
      'New secret replaces the stored value. Old secret is deleted from the file store.';

  @override
  String get adminRotating => 'Rotating…';

  @override
  String get adminSaveAgentPolicy => 'Save agent policy';

  @override
  String get adminSaveQuotas => 'Save quotas';

  @override
  String get adminSaveSubscription => 'Save subscription';

  @override
  String adminSecretRefValue(String secret_ref_prefix) {
    return 'Secret ref: $secret_ref_prefix';
  }

  @override
  String get adminSecretRequired => 'Secret required';

  @override
  String get adminSecretStoredServerSide => 'Secret is stored server-side only';

  @override
  String get adminSetEndDateOrLifetime =>
      'Set end date or enable lifetime subscription';

  @override
  String get adminShipped => 'shipped';

  @override
  String get adminStarterBundles => 'Starter bundles';

  @override
  String get adminStarterBundlesHint =>
      'Catalog entries appear when shipped under data/starter_bundles/';

  @override
  String adminStatusValue(String status) {
    return 'Status: $status';
  }

  @override
  String get adminSubscriptionSaved => 'Subscription saved';

  @override
  String get adminSweepIdlePause => 'Idle pause';

  @override
  String get adminSweepIdlePauseAll => 'Idle pause · all';

  @override
  String get adminSweeping => 'Sweeping…';

  @override
  String get adminTelegramHmacConfigured =>
      'Telegram HMAC: configured (leave blank to keep)';

  @override
  String get adminTelegramHmacNotSet => 'Telegram HMAC: not set';

  @override
  String get adminTelegramHmacSecret => 'Telegram HMAC';

  @override
  String get adminTokens => 'Tokens';

  @override
  String get adminToolPreset => 'Tool preset';

  @override
  String get adminWebhookHmacConfigured =>
      'Webhook HMAC: configured (leave blank to keep)';

  @override
  String get adminWebhookHmacNotSet => 'Webhook HMAC: not set';

  @override
  String get adminWebhookHmacSecret => 'Webhook HMAC';

  @override
  String get authAdvanced => 'Advanced';

  @override
  String get authBearerAccessToken => 'Bearer access token';

  @override
  String get authBearerAccessTokenPaste => 'Bearer access token (paste)';

  @override
  String authConfigUnavailableTestMode(String e) {
    return 'Auth config unavailable — using test mode. $e';
  }

  @override
  String get authConnecting => 'Connecting…';

  @override
  String get authContinueAsDemoEmployee => 'Continue as Demo Employee';

  @override
  String get authContinueAsPlatformAdmin => 'Continue as Platform Admin';

  @override
  String get authContinueWithToken => 'Continue with token';

  @override
  String get authDevTestModeHint =>
      'Dev test mode — one-click persona (no Keycloak)';

  @override
  String get authHideAdvanced => 'Hide advanced';

  @override
  String get authNoAccessTokenInTestLogin =>
      'No access_token in test login response';

  @override
  String get authOidcModeHint =>
      'OIDC — PKCE via Keycloak (mobile AppAuth, desktop browser loopback)';

  @override
  String get authOpeningLogin => 'Opening login…';

  @override
  String get authReloadAuthConfig => 'Reload auth config';

  @override
  String get authSignIn => 'Sign in';

  @override
  String get authLogin => 'Login';

  @override
  String get authPassword => 'Password';

  @override
  String get authShowPassword => 'Show password';

  @override
  String get authHidePassword => 'Hide password';

  @override
  String get authSignInWithKeycloak => 'Sign in with Keycloak';

  @override
  String get authSigningIn => 'Signing in…';

  @override
  String get authSignOut => 'Sign out';

  @override
  String get cabinetAddAtLeastOneColumn => 'Add at least one column';

  @override
  String get cabinetAddColumn => 'Add column';

  @override
  String get cabinetAddRow => 'Add row';

  @override
  String get cabinetAgentsInstructions => 'Agents instructions';

  @override
  String get cabinetAgentsMd => 'AGENTS.md';

  @override
  String get cabinetAgentsMdHint =>
      'Written into project workspace on materialize (AGENTS.md + CLAUDE.md). Re-materialize projects to apply.';

  @override
  String cabinetModuleRematerializeScheduled(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count project workspaces',
      one: '1 project workspace',
    );
    return 'Updating $_temp0 in the background…';
  }

  @override
  String cabinetModuleRematerializeDone(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count project workspaces',
      one: '1 project workspace',
    );
    return 'Updated $_temp0';
  }

  @override
  String get cabinetArchiveTable => 'Archive table';

  @override
  String get cabinetArchiveTableConfirm => 'Archive table?';

  @override
  String cabinetArchiveTableMessage(String tableSlug) {
    return 'Archive \"$tableSlug\". Rows stay in DB but table hides from lists. Remove views referencing this table first.';
  }

  @override
  String get cabinetArchivedBanner =>
      'Archived — you can delete permanently or go back.';

  @override
  String get cabinetArchiving => 'Archiving…';

  @override
  String get cabinetAuditLog => 'Audit log';

  @override
  String get cabinetAuditRecent => 'Audit (recent)';

  @override
  String get cabinetCabinetName => 'Cabinet name';

  @override
  String get cabinetChooseZipFile => 'Choose .zip file';

  @override
  String get cabinetColumnName => 'Column name';

  @override
  String get cabinetColumnSettings => 'Column settings';

  @override
  String cabinetColumnTypeChip(String name, String type) {
    return '$name · $type';
  }

  @override
  String get cabinetColumns => 'Columns';

  @override
  String get cabinetContextHint =>
      'Use the Projects tab to open an agent workspace. Tables and Tools tabs expose cabinet runtime data.';

  @override
  String cabinetContextStatusLine(String status, String companyId) {
    return 'Status: $status · Company $companyId';
  }

  @override
  String get cabinetCouldNotReadZipBytes => 'Could not read zip bytes';

  @override
  String get cabinetCreateBaseCabinetHint => 'Create a Base cabinet to start';

  @override
  String get cabinetCreateCabinet => 'Create cabinet';

  @override
  String get cabinetCreateMetaTableFirst =>
      'Create a meta table first (Tables tab → New table).';

  @override
  String get cabinetCreateTab => 'Create tab';

  @override
  String get cabinetCreateTable => 'Create table';

  @override
  String get cabinetCustomTabs => 'Custom tabs';

  @override
  String get cabinetCustomTabsHint =>
      'Custom tabs appear in the cabinet shell after creation. System tabs cannot be removed here.';

  @override
  String get cabinetDeleteColumn => 'Delete column';

  @override
  String get cabinetDeleteColumnConfirm => 'Delete column?';

  @override
  String get cabinetDeletePermanently => 'Delete permanently';

  @override
  String get cabinetDeleteRow => 'Delete row?';

  @override
  String cabinetDeleteRowPermanently(String rowId) {
    return 'Delete row $rowId permanently.';
  }

  @override
  String get cabinetDeleteTab => 'Delete tab?';

  @override
  String get cabinetDeleteTablePermanently => 'Delete table permanently?';

  @override
  String cabinetDropAllDataConfirm(String tableSlug) {
    return 'Drop all data for \"$tableSlug\". This cannot be undone.';
  }

  @override
  String get cabinetEditAgentsMd => 'Edit AGENTS.md';

  @override
  String get cabinetEditRow => 'Edit row';

  @override
  String get cabinetEmptyBundleExport => 'Empty bundle export';

  @override
  String get cabinetEnterAtLeastOneField => 'Enter at least one field value';

  @override
  String get cabinetExportCabinetBundle => 'Export cabinet bundle';

  @override
  String cabinetExportReady(String bytes) {
    return 'Export ready ($bytes bytes)';
  }

  @override
  String get cabinetExporting => 'Exporting…';

  @override
  String get cabinetFromFile => 'From file';

  @override
  String get cabinetImportBundleIntro =>
      'Import a cabinet.bundle zip exported from another cabinet. Creates a new cabinet instance with a fresh schema.';

  @override
  String get cabinetImportBundleTooltip => 'Import bundle';

  @override
  String get cabinetImportCabinetBundle => 'Import cabinet bundle';

  @override
  String get cabinetImportedCabinetDefault => 'Imported cabinet';

  @override
  String get cabinetImporting => 'Importing…';

  @override
  String get cabinetLettersDigitsUnderscore => 'Letters, digits, underscore';

  @override
  String get cabinetLowercaseSlugRule =>
      'Lowercase letters, digits, underscore';

  @override
  String get cabinetManageCustomTabs => 'Manage custom tabs';

  @override
  String get cabinetMcpTools => 'MCP tools';

  @override
  String get cabinetMetaTables => 'Meta tables';

  @override
  String get cabinetMyCabinetDefault => 'My cabinet';

  @override
  String get cabinetNewCabinet => 'New cabinet';

  @override
  String get cabinetNewCustomTab => 'New custom tab';

  @override
  String get cabinetNewTab => 'New tab';

  @override
  String get cabinetNewTable => 'New table';

  @override
  String get cabinetNoAuditEventsYet => 'No audit events yet.';

  @override
  String get cabinetNoCompanyIdFromMe => 'No company_id from /me memberships';

  @override
  String get cabinetNoCustomTabsYet => 'No custom tabs yet.';

  @override
  String cabinetNoInterpreterForView(String slug) {
    return 'No interpreter registered for view \"$slug\".';
  }

  @override
  String get cabinetNoMcpTools => 'No MCP tools exposed for this cabinet.';

  @override
  String get cabinetNoMetaTablesYet => 'No meta tables in this cabinet yet.';

  @override
  String get cabinetNoRows => 'No rows';

  @override
  String get cabinetNotShipped => 'not shipped';

  @override
  String get cabinetOfficialStarterBundles => 'Official starter bundles';

  @override
  String cabinetRemoveColumnData(String columnName) {
    return 'Remove column \"$columnName\" and its data.';
  }

  @override
  String cabinetRemoveTabAndView(String title) {
    return 'Remove tab \"$title\" and its view.';
  }

  @override
  String get cabinetReservedName => 'Reserved name';

  @override
  String cabinetRowFallback(String index) {
    return 'Row $index';
  }

  @override
  String get cabinetSaveLabel => 'Save label';

  @override
  String get cabinetSaveRow => 'Save row';

  @override
  String get cabinetSaveView => 'Save view';

  @override
  String cabinetSavedTo(String path) {
    return 'Saved to $path';
  }

  @override
  String get cabinetSelectATable => 'Select a table';

  @override
  String get cabinetSelectCabinetBundleZip =>
      'Select a cabinet.bundle zip file';

  @override
  String get cabinetSelectTableToPreview => 'Select a table to preview rows';

  @override
  String get cabinetSlug => 'Slug';

  @override
  String cabinetStatusCompanyLine(String status, String companyId) {
    return 'Status: $status · Company $companyId';
  }

  @override
  String get cabinetStorage => 'Storage';

  @override
  String get cabinetStorageJsonDocument => 'json_document';

  @override
  String get cabinetStoragePhysical => 'physical';

  @override
  String get cabinetSystemColumnReadOnly => 'System column — read only.';

  @override
  String get cabinetTabFallback => 'Tab';

  @override
  String get cabinetTabOrder => 'Tab order';

  @override
  String cabinetTabSubtitle(String viewSlug, String tableSlug) {
    return 'view: $viewSlug · table: $tableSlug';
  }

  @override
  String get cabinetTabTitle => 'Tab title';

  @override
  String get cabinetTableSettings => 'Table settings';

  @override
  String get cabinetTitleFieldColumnName => 'Title field (column name)';

  @override
  String get cabinetType => 'Type';

  @override
  String get cabinetUnique => 'Unique';

  @override
  String get cabinetUnknownTabNoViewSlug =>
      'Unknown tab — no view_slug from meta.';

  @override
  String get cabinetViewNotFound => 'View not found';

  @override
  String get cabinetViewSlug => 'View slug';

  @override
  String cabinetViewTitle(String viewSlug) {
    return 'View $viewSlug';
  }

  @override
  String get commonAdd => 'Add';

  @override
  String get commonAdding => 'Adding…';

  @override
  String get commonAgentTokens => 'Tokens';

  @override
  String get commonApiBaseUrl => 'API base URL';

  @override
  String get commonArchive => 'Archive';

  @override
  String get commonCabinets => 'Cabinets';

  @override
  String get commonCancel => 'Cancel';

  @override
  String get commonRetry => 'Retry';

  @override
  String get commonCompany => 'Company';

  @override
  String get commonCompanies => 'Companies';

  @override
  String get commonDescription => 'Description';

  @override
  String get commonEntity => 'Name';

  @override
  String get commonContinueAction => 'Continue';

  @override
  String get commonCreate => 'Create';

  @override
  String get commonCreating => 'Creating…';

  @override
  String get commonDelete => 'Delete';

  @override
  String get commonRemove => 'Remove';

  @override
  String get commonCopy => 'Copy';

  @override
  String get commonEdit => 'Edit';

  @override
  String get commonDeleting => 'Deleting…';

  @override
  String get commonDisable => 'Disable';

  @override
  String get commonDisplayNameOptional => 'Display name';

  @override
  String get commonMbUnit => 'MB';

  @override
  String get commonNotSet => 'Not set';

  @override
  String get commonOff => 'Off';

  @override
  String get commonOnline => 'Online';

  @override
  String get commonOffline => 'Offline';

  @override
  String get commonUnlimited => 'Unlimited';

  @override
  String adminBindingsCount(int count) {
    return '$count companies';
  }

  @override
  String get commonDone => 'Done';

  @override
  String get commonEmDash => '—';

  @override
  String get commonEmail => 'Email';

  @override
  String get commonEmployees => 'Employees';

  @override
  String get commonEmpty => 'Empty';

  @override
  String get commonFilter => 'Filter';

  @override
  String get commonImport => 'Import';

  @override
  String get commonInvite => 'Invite';

  @override
  String get commonLabel => 'Label';

  @override
  String get commonLastActivity => 'Last activity';

  @override
  String get commonList => 'List';

  @override
  String get commonName => 'Name';

  @override
  String get commonNameRequired => 'Name required';

  @override
  String get commonNone => 'None';

  @override
  String get commonNothingFound => 'Nothing found';

  @override
  String get commonOverview => 'Overview';

  @override
  String get commonPositiveInteger => 'Positive integer';

  @override
  String get commonProjects => 'Projects';

  @override
  String get commonProvider => 'Provider';

  @override
  String get commonReload => 'Reload';

  @override
  String get commonRequired => 'Required';

  @override
  String get commonResume => 'Resume';

  @override
  String get commonSave => 'Save';

  @override
  String get commonSaving => 'Saving…';

  @override
  String get commonSearch => 'Search';

  @override
  String get commonSecret => 'Secret';

  @override
  String get commonSelect => 'Select';

  @override
  String get commonSelectCompany => 'Select company';

  @override
  String get commonStatus => 'Status';

  @override
  String get commonStorageBytes => 'Storage';

  @override
  String get commonTable => 'Table';

  @override
  String get commonTitle => 'Title';

  @override
  String get commonUnknown => 'unknown';

  @override
  String get companyActive => 'Active';

  @override
  String get companyCabinet => 'Cabinet';

  @override
  String get companyCabinetsEmptyHint =>
      'Employees create cabinets in Employee contour';

  @override
  String get companyDisableEmployee => 'Disable employee';

  @override
  String companyDisableEmployeeConfirm(String email) {
    return 'Disable $email? They will lose access.';
  }

  @override
  String get companyAddEmployee => 'Add employee';

  @override
  String get companyAddCabinet => 'Add cabinet';

  @override
  String get companyPauseEmployee => 'Pause employee';

  @override
  String companyPauseEmployeeConfirm(String login) {
    return 'Pause $login? They will lose access until re-enabled.';
  }

  @override
  String get companyEnableEmployee => 'Enable employee';

  @override
  String companyEnableEmployeeConfirm(String login) {
    return 'Enable $login? They will regain access.';
  }

  @override
  String get companyInviteEmployee => 'Invite employee';

  @override
  String get companyInviteViaKeycloakNoPassword =>
      'Invite via Keycloak — no password field';

  @override
  String get companyInviteViaKeycloakPasswordNotAccepted =>
      'Invite via Keycloak — password is not accepted here.';

  @override
  String get companyInviting => 'Inviting…';

  @override
  String get companyNoCabinets => 'No cabinets';

  @override
  String get companyAssignEmployeeToCabinet => 'Assign employee';

  @override
  String get companyAssignedEmployees => 'Assigned';

  @override
  String get companyNoAssignedEmployees => 'No employees assigned';

  @override
  String get companyAssignCabinetsToEmployee => 'Cabinet access';

  @override
  String get companyNoEmployees => 'No employees';

  @override
  String get companyOrgMetrics => 'Org metrics';

  @override
  String get companyLoginId => 'Company ID';

  @override
  String get companyLogin => 'Login';

  @override
  String get credentialsInClipboard => 'Login and password copied to clipboard';

  @override
  String get companyIdCopied => 'Company ID copied';

  @override
  String get companyPassword => 'Password';

  @override
  String get companyPasswordHint => 'Minimum 8 characters';

  @override
  String get companyPasswordChanged => 'Password changed successfully';

  @override
  String get companyAddAiKey => 'Add key';

  @override
  String get companyNoAiKeys => 'No AI keys';

  @override
  String get companyKeyPlatformBound => 'From platform';

  @override
  String get companyKeySourceLocal => 'Local';

  @override
  String get companyAddModule => 'Add module';

  @override
  String get companyNoModules => 'No modules';

  @override
  String get companyModulesEmptyHint =>
      'Create a local module or wait for platform assignment';

  @override
  String get companyCreateRuntimeKeyHint =>
      'Add a company-owned key for your employees\' projects';

  @override
  String get errorConflict => 'Data conflict. Refresh and try again.';

  @override
  String get errorForbidden => 'You do not have permission for this action.';

  @override
  String get errorGateway =>
      'Server temporarily unavailable. Please try again.';

  @override
  String errorHttpStatus(int status) {
    return 'Request failed (HTTP $status)';
  }

  @override
  String get errorIdentityProvider =>
      'Could not update credentials. Please try again.';

  @override
  String get errorNetwork => 'No connection to the server. Check your network.';

  @override
  String get errorNotFound => 'The requested item was not found.';

  @override
  String get errorRateLimited => 'Too many requests. Please wait a moment.';

  @override
  String get errorServer => 'Internal server error. Please try again later.';

  @override
  String get errorUnauthorized => 'Session expired. Please sign in again.';

  @override
  String get errorInvalidCredentials => 'Incorrect username or password';

  @override
  String get errorUnexpected => 'Something went wrong.';

  @override
  String get errorValidation => 'Please check the entered data.';

  @override
  String get errorProjectPaused => 'Project is paused. Resume to continue.';

  @override
  String get errorCabinetArchived => 'Cabinet is not available for changes.';

  @override
  String get errorCompanySuspended =>
      'Company subscription is suspended or expired.';

  @override
  String get errorNoAiKey => 'No AI key available to run.';

  @override
  String get errorSessionClosed => 'Agent session is closed.';

  @override
  String get errorAgentBudget => 'Agent token budget exhausted.';

  @override
  String get errorCascadeIncomplete =>
      'Delete cascade is still in progress. Wait or retry later.';

  @override
  String companyCredentialsCreated(String companyId, String password) {
    return 'Login: $companyId · password: $password';
  }

  @override
  String get companyOwner => 'Owner';

  @override
  String get companyRole => 'Role';

  @override
  String get companyValidEmailRequired => 'Valid email required';

  @override
  String get devAdminSession => 'Admin session';

  @override
  String get devBearerTokenCompanyAdmin => 'Bearer token (company.admin JWT)';

  @override
  String get devBearerTokenPlatformAdmin => 'Bearer token (platform_admin JWT)';

  @override
  String get devBearerTokenTestJwt => 'Bearer token (AUTH_MODE=test JWT)';

  @override
  String get devCompanyAdminMembershipRequired =>
      'company.admin membership required';

  @override
  String get devCompanySession => 'Company session';

  @override
  String get devDevSession => 'Dev session';

  @override
  String get devNoEmployeeMemberships => 'No employee memberships';

  @override
  String get devTokenMustHaveCompanyContour =>
      'Token must have company contour (company.admin membership)';

  @override
  String get devTokenMustHavePlatformAdmin =>
      'Token must have platform_admin contour';

  @override
  String get galleryAlpha => 'Alpha';

  @override
  String get galleryBeta => 'Beta';

  @override
  String get galleryButtons => 'Buttons';

  @override
  String get galleryCheck => 'Check';

  @override
  String get galleryCoreGallery => 'Core gallery';

  @override
  String get galleryCount => 'Count';

  @override
  String get galleryDanger => 'Danger';

  @override
  String get galleryDemo => 'demo';

  @override
  String get galleryDemoConfirm => 'Demo confirm';

  @override
  String get galleryEntityCollection => 'EntityCollection';

  @override
  String get galleryListItem => 'List item';

  @override
  String get galleryOne => 'One';

  @override
  String get galleryPick => 'Pick';

  @override
  String get galleryRadio => 'Radio';

  @override
  String get gallerySampleRow => 'Sample row';

  @override
  String get gallerySelection => 'Selection';

  @override
  String get gallerySelector => 'Selector';

  @override
  String get gallerySubtitle => 'subtitle';

  @override
  String get galleryTwo => 'Two';

  @override
  String get navAiKeys => 'AI Keys';

  @override
  String get navBundles => 'Bundles';

  @override
  String get navContainers => 'Projects';

  @override
  String get navCabinets => 'Cabinets';

  @override
  String get navModules => 'Modules';

  @override
  String get navCompanies => 'Companies';

  @override
  String get navEmployees => 'Employees';

  @override
  String get navOverview => 'Overview';

  @override
  String get navManagement => 'Management';

  @override
  String get navProdavan => 'Prodavan';

  @override
  String get projectAgentError => 'Agent error';

  @override
  String get projectApproveAndContinue => 'Approve and continue';

  @override
  String get projectApproveTool => 'Approve tool';

  @override
  String projectApproveToolPrompt(String name) {
    return 'Approve $name?';
  }

  @override
  String get projectAttachFile => 'Attach file';

  @override
  String get projectAttachmentFallback => 'attachment';

  @override
  String get projectCancelledMarker => '(cancelled)';

  @override
  String projectCancelledWithText(String text) {
    return '$text\n(cancelled)';
  }

  @override
  String get projectCompanyDefault => 'Company default';

  @override
  String get projectCouldNotReadFileBytes => 'Could not read file bytes';

  @override
  String get projectCreateAndOpenChat => 'Create and open chat';

  @override
  String get projectCreateProject => 'Create project';

  @override
  String get projectCreateProjectHint =>
      'Create a project to open chat workspace';

  @override
  String get projectDeny => 'Deny';

  @override
  String get projectEmptyChatHint =>
      'Send a message to start the agent session';

  @override
  String get projectFileFallback => 'file';

  @override
  String projectInbox(String count) {
    return 'Inbox ($count)';
  }

  @override
  String get projectMessageHint => 'Message…';

  @override
  String get projectNewProject => 'New project';

  @override
  String get projectNoAttachmentData => 'No attachment data';

  @override
  String projectNoPreviewForType(String contentType, String bytes) {
    return 'No preview for $contentType ($bytes bytes)';
  }

  @override
  String get projectNoProjects => 'No projects';

  @override
  String get projectPauseProject => 'Pause project';

  @override
  String get projectProjectManagement => 'Project management';

  @override
  String get projectPausedBanner =>
      'Project is paused — chat, uploads and agent runs are disabled';

  @override
  String get projectPausedDisabledHint =>
      'Chat, uploads and agent runs are disabled while paused';

  @override
  String get projectPausedListSubtitle =>
      'Paused — chat, uploads and agent runs disabled';

  @override
  String get projectPausing => 'Pausing…';

  @override
  String projectPdfPreviewUnavailable(String bytes, String contentType) {
    return 'PDF inline preview is not available yet.\n$bytes bytes · $contentType';
  }

  @override
  String get projectPreferredAgentProvider => 'AI provider';

  @override
  String get projectPreviewTruncated =>
      'Preview truncated to first 200k characters';

  @override
  String get projectProject => 'Project';

  @override
  String get projectProjectName => 'Project name';

  @override
  String get projectProjectPaused => 'Project paused';

  @override
  String get projectProjectResumed => 'Project resumed';

  @override
  String get projectProjectSettings => 'Project settings';

  @override
  String get projectProjectStatus => 'Project status';

  @override
  String get projectRegenerate => 'Regenerate';

  @override
  String get projectReloadTranscript => 'Reload transcript';

  @override
  String get projectRematerializeWorkspace => 'Rematerialize workspace';

  @override
  String projectRematerializedPackages(String packages) {
    return 'Rematerialized packages: $packages';
  }

  @override
  String get projectRematerializing => 'Rematerializing…';

  @override
  String get projectResumeProject => 'Resume project';

  @override
  String get projectResuming => 'Resuming…';

  @override
  String projectSizeBytes(String size) {
    return '$size B';
  }

  @override
  String get projectSubscriptionExpiredBanner =>
      'Company subscription expired — chat and uploads are disabled';

  @override
  String get projectToolApprovalHint =>
      'This tool requires human approval before the agent can continue.';

  @override
  String get projectUploadMissingId => 'Upload missing id/storage_ref';

  @override
  String get projectWorking => 'Working…';

  @override
  String get projectWorkspaceRematerializedNoPackages =>
      'Workspace rematerialized (no MCP packages)';

  @override
  String get navProjects => 'Projects';

  @override
  String get projectAddHint => 'Add project';

  @override
  String get projectAboutLabel => 'About';

  @override
  String get projectAboutColumn => 'About';

  @override
  String get projectCreatorColumn => 'Creator';

  @override
  String get projectCreatorLabel => 'Creator';

  @override
  String get projectLaunchProject => 'Launch project';

  @override
  String get projectContainer => 'Container';

  @override
  String get projectReload => 'Reload';

  @override
  String get projectWorkspaceFiles => 'Files';

  @override
  String get projectWorkspacePreview => 'Preview';

  @override
  String get projectWorkspaceDownload => 'Download';

  @override
  String get projectWorkspaceDownloaded => 'File saved';

  @override
  String get projectUpdateProject => 'Update project';

  @override
  String get projectResetAgent => 'Reset agent';

  @override
  String get projectConfigureBeforeLaunch => 'Select AI provider before launch';

  @override
  String get projectAiProviderNotSelected => 'Not selected';

  @override
  String get projectAiKeyColumnProvider => 'Provider';

  @override
  String get projectAiKeyColumnSubscription => 'Subscription';

  @override
  String get projectModuleProfileColumn => 'Profile';

  @override
  String get projectSelectModuleProfile => 'Select profile';

  @override
  String get projectModuleNoProfiles => 'This module has no profiles';

  @override
  String get projectLaunchSuccess => 'Project launched';

  @override
  String get projectLaunchInProgress => 'Launching project…';

  @override
  String get projectLaunchStartingSnack => 'Project is launching…';

  @override
  String get projectResumeInProgress => 'Resuming project…';

  @override
  String get projectResumeStartingSnack => 'Project is resuming…';

  @override
  String get projectPauseConfirmMessage =>
      'While paused, the agent will be unavailable.';

  @override
  String get projectReloadSuccess => 'Project reloaded';

  @override
  String get projectUpdateSuccess => 'Project updated';

  @override
  String get projectResetSuccess => 'Agent reset';

  @override
  String get projectAiKeyLabel => 'AI key';

  @override
  String get projectModulesLabel => 'Modules';

  @override
  String get commonAuto => 'Auto';

  @override
  String get employeeContactEmail => 'Email';

  @override
  String get employeePassword => 'Password';

  @override
  String get employeeSwitchCabinet => 'Switch cabinet';

  @override
  String get employeeSingleCabinet => 'Only one cabinet available';

  @override
  String get employeePasswordChanged => 'Password updated';

  @override
  String get employeeContactEmailSaved => 'Email saved';

  @override
  String get settings => 'Settings';

  @override
  String get sessionRestoreOffline =>
      'Server unavailable. Your session is saved.';

  @override
  String get settingsLanguage => 'Language';

  @override
  String get settingsLanguageEn => 'English';

  @override
  String get settingsLanguageRu => 'Русский';

  @override
  String get settingsTheme => 'Theme';

  @override
  String get settingsThemeDark => 'Dark';

  @override
  String get settingsThemeLight => 'Light';

  @override
  String get settingsThemeUltraDark => 'Ultra dark';

  @override
  String get settingsRefresh => 'Refresh';

  @override
  String get settingsRefreshOff => 'Off';

  @override
  String settingsRefreshSeconds(String n) {
    return '$n s';
  }

  @override
  String settingsRefreshMinutes(String n) {
    return '$n min';
  }

  @override
  String metaSecretConfigured(String prefix) {
    return 'Secret configured: $prefix';
  }

  @override
  String get metaSecretEnter => 'Enter secret';

  @override
  String get metaSecretReplace => 'Replace secret';

  @override
  String get metaSecretSave => 'Save secret';
}
