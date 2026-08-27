import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:intl/intl.dart' as intl;

import 'app_localizations_en.dart';
import 'app_localizations_ru.dart';

// ignore_for_file: type=lint

/// Callers can lookup localized strings with an instance of AppLocalizations
/// returned by `AppLocalizations.of(context)`.
///
/// Applications need to include `AppLocalizations.delegate()` in their app's
/// `localizationDelegates` list, and the locales they support in the app's
/// `supportedLocales` list. For example:
///
/// ```dart
/// import 'l10n/app_localizations.dart';
///
/// return MaterialApp(
///   localizationsDelegates: AppLocalizations.localizationsDelegates,
///   supportedLocales: AppLocalizations.supportedLocales,
///   home: MyApplicationHome(),
/// );
/// ```
///
/// ## Update pubspec.yaml
///
/// Please make sure to update your pubspec.yaml to include the following
/// packages:
///
/// ```yaml
/// dependencies:
///   # Internationalization support.
///   flutter_localizations:
///     sdk: flutter
///   intl: any # Use the pinned version from flutter_localizations
///
///   # Rest of dependencies
/// ```
///
/// ## iOS Applications
///
/// iOS applications define key application metadata, including supported
/// locales, in an Info.plist file that is built into the application bundle.
/// To configure the locales supported by your app, you’ll need to edit this
/// file.
///
/// First, open your project’s ios/Runner.xcworkspace Xcode workspace file.
/// Then, in the Project Navigator, open the Info.plist file under the Runner
/// project’s Runner folder.
///
/// Next, select the Information Property List item, select Add Item from the
/// Editor menu, then select Localizations from the pop-up menu.
///
/// Select and expand the newly-created Localizations item then, for each
/// locale your application supports, add a new item and select the locale
/// you wish to add from the pop-up menu in the Value field. This list should
/// be consistent with the languages listed in the AppLocalizations.supportedLocales
/// property.
abstract class AppLocalizations {
  AppLocalizations(String locale)
    : localeName = intl.Intl.canonicalizedLocale(locale.toString());

  final String localeName;

  static AppLocalizations of(BuildContext context) {
    return Localizations.of<AppLocalizations>(context, AppLocalizations)!;
  }

  static const LocalizationsDelegate<AppLocalizations> delegate =
      _AppLocalizationsDelegate();

  /// A list of this localizations delegate along with the default localizations
  /// delegates.
  ///
  /// Returns a list of localizations delegates containing this delegate along with
  /// GlobalMaterialLocalizations.delegate, GlobalCupertinoLocalizations.delegate,
  /// and GlobalWidgetsLocalizations.delegate.
  ///
  /// Additional delegates can be added by appending to this list in
  /// MaterialApp. This list does not have to be used at all if a custom list
  /// of delegates is preferred or required.
  static const List<LocalizationsDelegate<dynamic>> localizationsDelegates =
      <LocalizationsDelegate<dynamic>>[
        delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
      ];

  /// A list of this localizations delegate's supported locales.
  static const List<Locale> supportedLocales = <Locale>[
    Locale('en'),
    Locale('ru'),
  ];

  /// No description provided for @adminActiveEmployees.
  ///
  /// In en, this message translates to:
  /// **'Employees online'**
  String get adminActiveEmployees;

  /// No description provided for @adminAdminEmail.
  ///
  /// In en, this message translates to:
  /// **'Admin email'**
  String get adminAdminEmail;

  /// No description provided for @adminAgentMessages.
  ///
  /// In en, this message translates to:
  /// **'AI requests'**
  String get adminAgentMessages;

  /// No description provided for @adminAgentPolicySaved.
  ///
  /// In en, this message translates to:
  /// **'Agent policy saved'**
  String get adminAgentPolicySaved;

  /// No description provided for @adminAgentRuntimePolicy.
  ///
  /// In en, this message translates to:
  /// **'Policy'**
  String get adminAgentRuntimePolicy;

  /// No description provided for @adminAiKeysBound.
  ///
  /// In en, this message translates to:
  /// **'API keys'**
  String get adminAiKeysBound;

  /// No description provided for @adminAlertHighUsageTitle.
  ///
  /// In en, this message translates to:
  /// **'{companyName}: high agent token usage'**
  String adminAlertHighUsageTitle(String companyName);

  /// No description provided for @adminAlertKeyRenewalTitle.
  ///
  /// In en, this message translates to:
  /// **'{companyName}: AI key renewal soon'**
  String adminAlertKeyRenewalTitle(String companyName);

  /// No description provided for @adminAlertKeysRenewingSoon.
  ///
  /// In en, this message translates to:
  /// **'{count} key(s) renewing soon'**
  String adminAlertKeysRenewingSoon(String count);

  /// No description provided for @adminAlertKeysRenewingSoonNext.
  ///
  /// In en, this message translates to:
  /// **'{count} key(s) renewing soon · next {next}'**
  String adminAlertKeysRenewingSoonNext(String count, String next);

  /// No description provided for @adminAlertNoKeysSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Agent sessions will fail with NO_AI_KEY'**
  String get adminAlertNoKeysSubtitle;

  /// No description provided for @adminAlertNoKeysTitle.
  ///
  /// In en, this message translates to:
  /// **'{companyName}: no AI keys bound'**
  String adminAlertNoKeysTitle(String companyName);

  /// No description provided for @adminAlertSubExpiredTitle.
  ///
  /// In en, this message translates to:
  /// **'{companyName}: subscription expired'**
  String adminAlertSubExpiredTitle(String companyName);

  /// No description provided for @adminAlertSubExpiringTitle.
  ///
  /// In en, this message translates to:
  /// **'{companyName}: subscription expiring'**
  String adminAlertSubExpiringTitle(String companyName);

  /// No description provided for @adminAlertSubscriptionEnded.
  ///
  /// In en, this message translates to:
  /// **'Subscription ended'**
  String get adminAlertSubscriptionEnded;

  /// No description provided for @adminAlertSubscriptionEndedAt.
  ///
  /// In en, this message translates to:
  /// **'Subscription ended · {ends}'**
  String adminAlertSubscriptionEndedAt(String ends);

  /// No description provided for @adminAlertSubscriptionEndsSoon.
  ///
  /// In en, this message translates to:
  /// **'Subscription ends soon'**
  String get adminAlertSubscriptionEndsSoon;

  /// No description provided for @adminAlertSubscriptionEndsSoonAt.
  ///
  /// In en, this message translates to:
  /// **'Subscription ends soon · {ends}'**
  String adminAlertSubscriptionEndsSoonAt(String ends);

  /// No description provided for @adminAlertTokensAboveThreshold.
  ///
  /// In en, this message translates to:
  /// **'Agent tokens {tokens} above platform threshold'**
  String adminAlertTokensAboveThreshold(String tokens);

  /// No description provided for @adminAlerts.
  ///
  /// In en, this message translates to:
  /// **'Alerts'**
  String get adminAlerts;

  /// No description provided for @adminApiKind.
  ///
  /// In en, this message translates to:
  /// **'API kind'**
  String get adminApiKind;

  /// No description provided for @adminApiKindValue.
  ///
  /// In en, this message translates to:
  /// **'API kind: {api_kind}'**
  String adminApiKindValue(String api_kind);

  /// No description provided for @adminBindCompanies.
  ///
  /// In en, this message translates to:
  /// **'Bind companies'**
  String get adminBindCompanies;

  /// No description provided for @adminBindingsSelected.
  ///
  /// In en, this message translates to:
  /// **'{count} selected'**
  String adminBindingsSelected(String count);

  /// No description provided for @adminBundle.
  ///
  /// In en, this message translates to:
  /// **'Bundle'**
  String get adminBundle;

  /// No description provided for @adminCabinetQuotas.
  ///
  /// In en, this message translates to:
  /// **'Quotas'**
  String get adminCabinetQuotas;

  /// No description provided for @adminCabinetQuotasMustBePositive.
  ///
  /// In en, this message translates to:
  /// **'Cabinet quotas must be positive integers'**
  String get adminCabinetQuotasMustBePositive;

  /// No description provided for @adminCabinetsProjectsSubtitle.
  ///
  /// In en, this message translates to:
  /// **'{cabinets} cabinets · {projects} projects'**
  String adminCabinetsProjectsSubtitle(String cabinets, String projects);

  /// No description provided for @adminCabinetsQuotaCell.
  ///
  /// In en, this message translates to:
  /// **'{active} / {max} cabinets'**
  String adminCabinetsQuotaCell(String active, String max);

  /// No description provided for @adminCompanyAdminInvite.
  ///
  /// In en, this message translates to:
  /// **'Company admin invite'**
  String get adminCompanyAdminInvite;

  /// No description provided for @adminCompanyBindings.
  ///
  /// In en, this message translates to:
  /// **'Company bindings'**
  String get adminCompanyBindings;

  /// No description provided for @adminCompanyName.
  ///
  /// In en, this message translates to:
  /// **'Company name'**
  String get adminCompanyName;

  /// No description provided for @adminAddAiKey.
  ///
  /// In en, this message translates to:
  /// **'Add key'**
  String get adminAddAiKey;

  /// No description provided for @adminAddCompany.
  ///
  /// In en, this message translates to:
  /// **'Add company'**
  String get adminAddCompany;

  /// No description provided for @adminCompanyGeneral.
  ///
  /// In en, this message translates to:
  /// **'General'**
  String get adminCompanyGeneral;

  /// No description provided for @adminEvents.
  ///
  /// In en, this message translates to:
  /// **'Events'**
  String get adminEvents;

  /// No description provided for @adminInviteAdmin.
  ///
  /// In en, this message translates to:
  /// **'Admin'**
  String get adminInviteAdmin;

  /// No description provided for @adminPolicy.
  ///
  /// In en, this message translates to:
  /// **'Policy'**
  String get adminPolicy;

  /// No description provided for @adminAgentLimits.
  ///
  /// In en, this message translates to:
  /// **'Agent limits'**
  String get adminAgentLimits;

  /// No description provided for @adminQuotas.
  ///
  /// In en, this message translates to:
  /// **'Quotas'**
  String get adminQuotas;

  /// No description provided for @adminQuotasSummary.
  ///
  /// In en, this message translates to:
  /// **'{cabinets} · {packages} · {bundleMb}'**
  String adminQuotasSummary(String cabinets, String packages, String bundleMb);

  /// No description provided for @adminCreateAiKey.
  ///
  /// In en, this message translates to:
  /// **'Create AI key'**
  String get adminCreateAiKey;

  /// No description provided for @adminCreateCompany.
  ///
  /// In en, this message translates to:
  /// **'Create company'**
  String get adminCreateCompany;

  /// No description provided for @adminCreateCompanyAndInviteAdmin.
  ///
  /// In en, this message translates to:
  /// **'Create a company and invite company.admin'**
  String get adminCreateCompanyAndInviteAdmin;

  /// No description provided for @adminCreateCompanyToSeeMetrics.
  ///
  /// In en, this message translates to:
  /// **'Create a company to see platform metrics'**
  String get adminCreateCompanyToSeeMetrics;

  /// No description provided for @adminCreateKey.
  ///
  /// In en, this message translates to:
  /// **'Create key'**
  String get adminCreateKey;

  /// No description provided for @adminCreateRuntimeKeyHint.
  ///
  /// In en, this message translates to:
  /// **'Add a key and configure on its page'**
  String get adminCreateRuntimeKeyHint;

  /// No description provided for @adminDisableAiKey.
  ///
  /// In en, this message translates to:
  /// **'Pause AI key'**
  String get adminDisableAiKey;

  /// No description provided for @adminDisableKey.
  ///
  /// In en, this message translates to:
  /// **'Pause key'**
  String get adminDisableKey;

  /// No description provided for @adminResumeKey.
  ///
  /// In en, this message translates to:
  /// **'Resume'**
  String get adminResumeKey;

  /// No description provided for @adminDisableKeyConfirm.
  ///
  /// In en, this message translates to:
  /// **'Pause key? Related projects will be paused'**
  String adminDisableKeyConfirm(String keyName);

  /// No description provided for @adminDrainProjectTriggers.
  ///
  /// In en, this message translates to:
  /// **'Triggers'**
  String get adminDrainProjectTriggers;

  /// No description provided for @adminDrainedTriggers.
  ///
  /// In en, this message translates to:
  /// **'Drained {count} trigger(s)'**
  String adminDrainedTriggers(String count);

  /// No description provided for @adminDraining.
  ///
  /// In en, this message translates to:
  /// **'Draining…'**
  String get adminDraining;

  /// No description provided for @adminEditBindings.
  ///
  /// In en, this message translates to:
  /// **'Edit bindings'**
  String get adminEditBindings;

  /// No description provided for @adminEndsAt.
  ///
  /// In en, this message translates to:
  /// **'Subscription'**
  String get adminEndsAt;

  /// No description provided for @adminDateFormatHint.
  ///
  /// In en, this message translates to:
  /// **'DD.MM.YY'**
  String get adminDateFormatHint;

  /// No description provided for @adminInvalidDate.
  ///
  /// In en, this message translates to:
  /// **'Format: DD.MM.YY or DD.MM.YYYY'**
  String get adminInvalidDate;

  /// No description provided for @adminEndsAtIfNotLifetime.
  ///
  /// In en, this message translates to:
  /// **'Subscription (if not lifetime)'**
  String get adminEndsAtIfNotLifetime;

  /// No description provided for @adminSubscription.
  ///
  /// In en, this message translates to:
  /// **'Subscription'**
  String get adminSubscription;

  /// No description provided for @adminEnterIntegerMin.
  ///
  /// In en, this message translates to:
  /// **'Enter integer ≥ {min}'**
  String adminEnterIntegerMin(String min);

  /// No description provided for @adminIdlePauseAfterHours.
  ///
  /// In en, this message translates to:
  /// **'Idle pause'**
  String get adminIdlePauseAfterHours;

  /// No description provided for @adminIdlePausedProjectsInCompany.
  ///
  /// In en, this message translates to:
  /// **'Idle-paused {count} project(s) in this company'**
  String adminIdlePausedProjectsInCompany(String count);

  /// No description provided for @adminInviteCompanyAdminViaKeycloak.
  ///
  /// In en, this message translates to:
  /// **'Invite company.admin via Keycloak — password is not accepted.'**
  String get adminInviteCompanyAdminViaKeycloak;

  /// No description provided for @adminKey.
  ///
  /// In en, this message translates to:
  /// **'Key'**
  String get adminKey;

  /// No description provided for @adminKeyInfo.
  ///
  /// In en, this message translates to:
  /// **'Key info'**
  String get adminKeyInfo;

  /// No description provided for @adminKeyListSubtitle.
  ///
  /// In en, this message translates to:
  /// **'{type} · {provider}'**
  String adminKeyListSubtitle(String type, String provider);

  /// No description provided for @adminIntegrationType.
  ///
  /// In en, this message translates to:
  /// **'Type'**
  String get adminIntegrationType;

  /// No description provided for @adminTypeCursorSdk.
  ///
  /// In en, this message translates to:
  /// **'Cursor SDK'**
  String get adminTypeCursorSdk;

  /// No description provided for @adminTypeCodexSdk.
  ///
  /// In en, this message translates to:
  /// **'Codex SDK'**
  String get adminTypeCodexSdk;

  /// No description provided for @adminTypeClaudeSdk.
  ///
  /// In en, this message translates to:
  /// **'Claude Agent SDK'**
  String get adminTypeClaudeSdk;

  /// No description provided for @adminTypeApiKey.
  ///
  /// In en, this message translates to:
  /// **'API key'**
  String get adminTypeApiKey;

  /// No description provided for @adminNextRenewal.
  ///
  /// In en, this message translates to:
  /// **'Subscription'**
  String get adminNextRenewal;

  /// No description provided for @adminAddHttpProvider.
  ///
  /// In en, this message translates to:
  /// **'Add HTTP endpoint'**
  String get adminAddHttpProvider;

  /// No description provided for @adminHttpEndpoint.
  ///
  /// In en, this message translates to:
  /// **'HTTP endpoint'**
  String get adminHttpEndpoint;

  /// No description provided for @adminHttpBaseUrl.
  ///
  /// In en, this message translates to:
  /// **'Base URL'**
  String get adminHttpBaseUrl;

  /// No description provided for @adminHttpApiKind.
  ///
  /// In en, this message translates to:
  /// **'API kind'**
  String get adminHttpApiKind;

  /// No description provided for @adminHttpApiKindOpenai.
  ///
  /// In en, this message translates to:
  /// **'OpenAI API'**
  String get adminHttpApiKindOpenai;

  /// No description provided for @adminHttpApiKindAnthropic.
  ///
  /// In en, this message translates to:
  /// **'Anthropic API'**
  String get adminHttpApiKindAnthropic;

  /// No description provided for @adminHttpApiKindOpenrouter.
  ///
  /// In en, this message translates to:
  /// **'OpenRouter'**
  String get adminHttpApiKindOpenrouter;

  /// No description provided for @adminHttpApiKindCustom.
  ///
  /// In en, this message translates to:
  /// **'Custom / OpenAPI'**
  String get adminHttpApiKindCustom;

  /// No description provided for @adminHttpOpenaiCompatible.
  ///
  /// In en, this message translates to:
  /// **'OpenAI-compatible'**
  String get adminHttpOpenaiCompatible;

  /// No description provided for @adminHttpAuthScheme.
  ///
  /// In en, this message translates to:
  /// **'Auth'**
  String get adminHttpAuthScheme;

  /// No description provided for @adminHttpAuthBearer.
  ///
  /// In en, this message translates to:
  /// **'Bearer'**
  String get adminHttpAuthBearer;

  /// No description provided for @adminHttpAuthXApiKey.
  ///
  /// In en, this message translates to:
  /// **'x-api-key'**
  String get adminHttpAuthXApiKey;

  /// No description provided for @adminHttpAuthNone.
  ///
  /// In en, this message translates to:
  /// **'No auth'**
  String get adminHttpAuthNone;

  /// No description provided for @adminHttpChatPath.
  ///
  /// In en, this message translates to:
  /// **'Chat completions path'**
  String get adminHttpChatPath;

  /// No description provided for @adminHttpModelsPath.
  ///
  /// In en, this message translates to:
  /// **'Models path'**
  String get adminHttpModelsPath;

  /// No description provided for @adminToolPresetChatReadonly.
  ///
  /// In en, this message translates to:
  /// **'Chat (read-only)'**
  String get adminToolPresetChatReadonly;

  /// No description provided for @adminToolPresetWorkspaceDev.
  ///
  /// In en, this message translates to:
  /// **'Development'**
  String get adminToolPresetWorkspaceDev;

  /// No description provided for @adminToolPresetWorkspaceFull.
  ///
  /// In en, this message translates to:
  /// **'Full access'**
  String get adminToolPresetWorkspaceFull;

  /// No description provided for @adminIdlePauseNever.
  ///
  /// In en, this message translates to:
  /// **'Never pause'**
  String get adminIdlePauseNever;

  /// No description provided for @adminIdlePauseHours.
  ///
  /// In en, this message translates to:
  /// **'{hours} h'**
  String adminIdlePauseHours(String hours);

  /// No description provided for @adminAlertTag.
  ///
  /// In en, this message translates to:
  /// **'Tag'**
  String get adminAlertTag;

  /// No description provided for @adminAlertTagNoKeys.
  ///
  /// In en, this message translates to:
  /// **'no keys'**
  String get adminAlertTagNoKeys;

  /// No description provided for @adminAlertTagKeyRenewal.
  ///
  /// In en, this message translates to:
  /// **'renewal · {count}'**
  String adminAlertTagKeyRenewal(int count);

  /// No description provided for @adminAlertTagHighUsage.
  ///
  /// In en, this message translates to:
  /// **'high usage'**
  String get adminAlertTagHighUsage;

  /// No description provided for @adminAlertTagSubExpiring.
  ///
  /// In en, this message translates to:
  /// **'subscription'**
  String get adminAlertTagSubExpiring;

  /// No description provided for @adminAlertTagSubExpired.
  ///
  /// In en, this message translates to:
  /// **'expired'**
  String get adminAlertTagSubExpired;

  /// No description provided for @adminAlertTagIdentityUnbound.
  ///
  /// In en, this message translates to:
  /// **'IDENTITY_UNBOUND'**
  String get adminAlertTagIdentityUnbound;

  /// No description provided for @adminAlertTagEmployeesUnbound.
  ///
  /// In en, this message translates to:
  /// **'EMPLOYEES_UNBOUND · {count}'**
  String adminAlertTagEmployeesUnbound(int count);

  /// No description provided for @adminAlertTagCascadeIncomplete.
  ///
  /// In en, this message translates to:
  /// **'CASCADE_INCOMPLETE'**
  String get adminAlertTagCascadeIncomplete;

  /// No description provided for @commonPhone.
  ///
  /// In en, this message translates to:
  /// **'Phone'**
  String get commonPhone;

  /// No description provided for @adminKeyMetadata.
  ///
  /// In en, this message translates to:
  /// **'Key metadata'**
  String get adminKeyMetadata;

  /// No description provided for @adminLifecycle.
  ///
  /// In en, this message translates to:
  /// **'Lifecycle'**
  String get adminLifecycle;

  /// No description provided for @adminLifetimeSubscription.
  ///
  /// In en, this message translates to:
  /// **'Lifetime subscription'**
  String get adminLifetimeSubscription;

  /// No description provided for @adminMaxAgentTokensMonth.
  ///
  /// In en, this message translates to:
  /// **'Agent tokens / month'**
  String get adminMaxAgentTokensMonth;

  /// No description provided for @adminMaxBundleImportMb.
  ///
  /// In en, this message translates to:
  /// **'Bundle'**
  String get adminMaxBundleImportMb;

  /// No description provided for @adminMaxCabinets.
  ///
  /// In en, this message translates to:
  /// **'Cabinets'**
  String get adminMaxCabinets;

  /// No description provided for @adminMaxChatAttachmentMb.
  ///
  /// In en, this message translates to:
  /// **'Chat attachment'**
  String get adminMaxChatAttachmentMb;

  /// No description provided for @adminMaxPackagesPerCabinet.
  ///
  /// In en, this message translates to:
  /// **'Packages'**
  String get adminMaxPackagesPerCabinet;

  /// No description provided for @adminMaxTokensPerRun.
  ///
  /// In en, this message translates to:
  /// **'Tokens per run'**
  String get adminMaxTokensPerRun;

  /// No description provided for @adminMaxUsdCostMonth.
  ///
  /// In en, this message translates to:
  /// **'USD cost / month'**
  String get adminMaxUsdCostMonth;

  /// No description provided for @adminMetadataOnly.
  ///
  /// In en, this message translates to:
  /// **'metadata only'**
  String get adminMetadataOnly;

  /// No description provided for @adminMetrics.
  ///
  /// In en, this message translates to:
  /// **'Metrics'**
  String get adminMetrics;

  /// No description provided for @adminMetricsAiKeysRenewSoon.
  ///
  /// In en, this message translates to:
  /// **'{count} AI key(s) renew soon'**
  String adminMetricsAiKeysRenewSoon(String count);

  /// No description provided for @adminMetricsAiKeysRenewSoonNext.
  ///
  /// In en, this message translates to:
  /// **'{count} AI key(s) renew soon · next {next}'**
  String adminMetricsAiKeysRenewSoonNext(String count, String next);

  /// No description provided for @adminMetricsHighTokenUsage.
  ///
  /// In en, this message translates to:
  /// **'High agent token usage ({tokens} tokens)'**
  String adminMetricsHighTokenUsage(String tokens);

  /// No description provided for @adminMetricsNoAiKeysBound.
  ///
  /// In en, this message translates to:
  /// **'No AI keys bound — agent will return NO_AI_KEY'**
  String get adminMetricsNoAiKeysBound;

  /// No description provided for @adminMetricsSubscriptionExpired.
  ///
  /// In en, this message translates to:
  /// **'Subscription expired'**
  String get adminMetricsSubscriptionExpired;

  /// No description provided for @adminMetricsSubscriptionExpiring.
  ///
  /// In en, this message translates to:
  /// **'Subscription expiring · {ends}'**
  String adminMetricsSubscriptionExpiring(String ends);

  /// No description provided for @adminMetricsIdentityUnbound.
  ///
  /// In en, this message translates to:
  /// **'Waiting for authentication service'**
  String get adminMetricsIdentityUnbound;

  /// No description provided for @adminMetricsEmployeesUnbound.
  ///
  /// In en, this message translates to:
  /// **'Waiting for authentication service · {count} employee(s)'**
  String adminMetricsEmployeesUnbound(String count);

  /// No description provided for @adminModelAllowlist.
  ///
  /// In en, this message translates to:
  /// **'Model allowlist'**
  String get adminModelAllowlist;

  /// No description provided for @adminNewSecret.
  ///
  /// In en, this message translates to:
  /// **'New secret'**
  String get adminNewSecret;

  /// No description provided for @adminNextRenewalValue.
  ///
  /// In en, this message translates to:
  /// **'Next renewal: {next_renewal_at}'**
  String adminNextRenewalValue(String next_renewal_at);

  /// No description provided for @adminNoAiKeys.
  ///
  /// In en, this message translates to:
  /// **'No AI keys'**
  String get adminNoAiKeys;

  /// No description provided for @adminNoCompanies.
  ///
  /// In en, this message translates to:
  /// **'No companies'**
  String get adminNoCompanies;

  /// No description provided for @adminNoContainers.
  ///
  /// In en, this message translates to:
  /// **'No projects'**
  String get adminNoContainers;

  /// No description provided for @adminNoCabinets.
  ///
  /// In en, this message translates to:
  /// **'No cabinets'**
  String get adminNoCabinets;

  /// No description provided for @adminAddCabinet.
  ///
  /// In en, this message translates to:
  /// **'Add cabinet'**
  String get adminAddCabinet;

  /// No description provided for @adminNoModules.
  ///
  /// In en, this message translates to:
  /// **'No modules'**
  String get adminNoModules;

  /// No description provided for @adminAddModule.
  ///
  /// In en, this message translates to:
  /// **'Add module'**
  String get adminAddModule;

  /// No description provided for @adminDeleteModuleConfirm.
  ///
  /// In en, this message translates to:
  /// **'Delete module \"{name}\"? Meta and bindings will be removed; cabinets and projects are not affected.'**
  String adminDeleteModuleConfirm(String name);

  /// No description provided for @adminModuleCopied.
  ///
  /// In en, this message translates to:
  /// **'Module ID copied'**
  String get adminModuleCopied;

  /// No description provided for @adminModuleCabinetsCount.
  ///
  /// In en, this message translates to:
  /// **'{count} cabinets'**
  String adminModuleCabinetsCount(int count);

  /// No description provided for @adminModuleJson.
  ///
  /// In en, this message translates to:
  /// **'JSON'**
  String get adminModuleJson;

  /// No description provided for @adminModuleJsonConfigured.
  ///
  /// In en, this message translates to:
  /// **'Configured'**
  String get adminModuleJsonConfigured;

  /// No description provided for @adminModulePreview.
  ///
  /// In en, this message translates to:
  /// **'Preview'**
  String get adminModulePreview;

  /// No description provided for @adminModulePreviewEmpty.
  ///
  /// In en, this message translates to:
  /// **'Add module JSON to preview'**
  String get adminModulePreviewEmpty;

  /// No description provided for @adminModuleJsonInvalid.
  ///
  /// In en, this message translates to:
  /// **'Invalid JSON'**
  String get adminModuleJsonInvalid;

  /// No description provided for @adminMetaInvalid.
  ///
  /// In en, this message translates to:
  /// **'Metadata'**
  String get adminMetaInvalid;

  /// No description provided for @adminModulePreviewMode.
  ///
  /// In en, this message translates to:
  /// **'Preview'**
  String get adminModulePreviewMode;

  /// No description provided for @adminSelectCabinetsForModule.
  ///
  /// In en, this message translates to:
  /// **'Select cabinets for module'**
  String get adminSelectCabinetsForModule;

  /// No description provided for @adminSelectModulesForCabinet.
  ///
  /// In en, this message translates to:
  /// **'Select modules for cabinet'**
  String get adminSelectModulesForCabinet;

  /// No description provided for @adminSelectCompaniesForModule.
  ///
  /// In en, this message translates to:
  /// **'Select companies for module'**
  String get adminSelectCompaniesForModule;

  /// No description provided for @adminDeleteCabinetConfirm.
  ///
  /// In en, this message translates to:
  /// **'Delete cabinet \"{name}\"? All projects and the cabinet schema will be removed.'**
  String adminDeleteCabinetConfirm(String name);

  /// No description provided for @adminCabinetCopied.
  ///
  /// In en, this message translates to:
  /// **'Cabinet ID copied'**
  String get adminCabinetCopied;

  /// No description provided for @adminCabinetOwnerScope.
  ///
  /// In en, this message translates to:
  /// **'Owner scope'**
  String get adminCabinetOwnerScope;

  /// No description provided for @adminCabinetCompaniesCount.
  ///
  /// In en, this message translates to:
  /// **'{count} companies'**
  String adminCabinetCompaniesCount(int count);

  /// No description provided for @adminSelectCompanyForCabinet.
  ///
  /// In en, this message translates to:
  /// **'Select company'**
  String get adminSelectCompanyForCabinet;

  /// No description provided for @cabinetOpenedPlaceholder.
  ///
  /// In en, this message translates to:
  /// **'Cabinet is open. Meta UI will return in a later iteration.'**
  String get cabinetOpenedPlaceholder;

  /// No description provided for @adminContainerColProject.
  ///
  /// In en, this message translates to:
  /// **'Project'**
  String get adminContainerColProject;

  /// No description provided for @adminContainerColStatus.
  ///
  /// In en, this message translates to:
  /// **'Status'**
  String get adminContainerColStatus;

  /// No description provided for @adminContainerColCompany.
  ///
  /// In en, this message translates to:
  /// **'Company'**
  String get adminContainerColCompany;

  /// No description provided for @adminContainerColEmployee.
  ///
  /// In en, this message translates to:
  /// **'Employee'**
  String get adminContainerColEmployee;

  /// No description provided for @adminContainerColProvider.
  ///
  /// In en, this message translates to:
  /// **'Provider'**
  String get adminContainerColProvider;

  /// No description provided for @adminContainerColCabinet.
  ///
  /// In en, this message translates to:
  /// **'Cabinet'**
  String get adminContainerColCabinet;

  /// No description provided for @adminContainerStatusActive.
  ///
  /// In en, this message translates to:
  /// **'Active'**
  String get adminContainerStatusActive;

  /// No description provided for @adminContainerStatusPaused.
  ///
  /// In en, this message translates to:
  /// **'Paused'**
  String get adminContainerStatusPaused;

  /// No description provided for @adminContainerRuntimeRef.
  ///
  /// In en, this message translates to:
  /// **'Runtime ref'**
  String get adminContainerRuntimeRef;

  /// No description provided for @adminContainerMetricsHole.
  ///
  /// In en, this message translates to:
  /// **'K8s metrics'**
  String get adminContainerMetricsHole;

  /// No description provided for @adminDeleteContainerConfirm.
  ///
  /// In en, this message translates to:
  /// **'Delete project \"{name}\"? Workspace will be wiped.'**
  String adminDeleteContainerConfirm(String name);

  /// No description provided for @adminDeleteCompanyConfirm.
  ///
  /// In en, this message translates to:
  /// **'Delete company \"{name}\"? This disables employees, pauses and deletes projects (workspace wipe), and hard-deletes cabinets.'**
  String adminDeleteCompanyConfirm(String name);

  /// No description provided for @adminDisableAiKeyConfirm.
  ///
  /// In en, this message translates to:
  /// **'Pause key? Related projects will be paused'**
  String adminDisableAiKeyConfirm(String name);

  /// No description provided for @adminNoCompaniesBound.
  ///
  /// In en, this message translates to:
  /// **'No companies bound'**
  String get adminNoCompaniesBound;

  /// No description provided for @adminNoCompaniesYet.
  ///
  /// In en, this message translates to:
  /// **'No companies yet'**
  String get adminNoCompaniesYet;

  /// No description provided for @adminNoPlatformEventsYet.
  ///
  /// In en, this message translates to:
  /// **'No platform events yet'**
  String get adminNoPlatformEventsYet;

  /// No description provided for @adminNoStarterBundles.
  ///
  /// In en, this message translates to:
  /// **'No starter bundles'**
  String get adminNoStarterBundles;

  /// No description provided for @adminPlatformEvents.
  ///
  /// In en, this message translates to:
  /// **'Events'**
  String get adminPlatformEvents;

  /// No description provided for @adminPlatformFallback.
  ///
  /// In en, this message translates to:
  /// **'Platform fallback'**
  String get adminPlatformFallback;

  /// No description provided for @adminPlatformIdleSweep.
  ///
  /// In en, this message translates to:
  /// **'Platform idle sweep: {count} project(s) ({companies} companies with policy)'**
  String adminPlatformIdleSweep(String count, String companies);

  /// No description provided for @adminPlatformTotals.
  ///
  /// In en, this message translates to:
  /// **'Metrics'**
  String get adminPlatformTotals;

  /// No description provided for @adminCompanyCabinetsRunning.
  ///
  /// In en, this message translates to:
  /// **'{running} / {quota}'**
  String adminCompanyCabinetsRunning(String running, String quota);

  /// No description provided for @adminPreferredProvider.
  ///
  /// In en, this message translates to:
  /// **'Provider'**
  String get adminPreferredProvider;

  /// No description provided for @adminPreferredProviderOptional.
  ///
  /// In en, this message translates to:
  /// **'Provider'**
  String get adminPreferredProviderOptional;

  /// No description provided for @adminProdavanSubscription.
  ///
  /// In en, this message translates to:
  /// **'Prodavan subscription'**
  String get adminProdavanSubscription;

  /// No description provided for @adminProdavanSubscriptionOptional.
  ///
  /// In en, this message translates to:
  /// **'Prodavan subscription'**
  String get adminProdavanSubscriptionOptional;

  /// No description provided for @adminProviderValue.
  ///
  /// In en, this message translates to:
  /// **'Provider: {provider}'**
  String adminProviderValue(String provider);

  /// No description provided for @adminQuotasSaved.
  ///
  /// In en, this message translates to:
  /// **'Quotas saved'**
  String get adminQuotasSaved;

  /// No description provided for @adminRenewPlusOneMonth.
  ///
  /// In en, this message translates to:
  /// **'Renew +1 month'**
  String get adminRenewPlusOneMonth;

  /// No description provided for @adminRenewing.
  ///
  /// In en, this message translates to:
  /// **'Renewing…'**
  String get adminRenewing;

  /// No description provided for @adminRotateKeyTitle.
  ///
  /// In en, this message translates to:
  /// **'Rotate {keyName}'**
  String adminRotateKeyTitle(String keyName);

  /// No description provided for @adminRotateSecret.
  ///
  /// In en, this message translates to:
  /// **'Rotate secret'**
  String get adminRotateSecret;

  /// No description provided for @adminRotateSecretHint.
  ///
  /// In en, this message translates to:
  /// **'New secret replaces the stored value. Old secret is deleted from the file store.'**
  String get adminRotateSecretHint;

  /// No description provided for @adminRotating.
  ///
  /// In en, this message translates to:
  /// **'Rotating…'**
  String get adminRotating;

  /// No description provided for @adminSaveAgentPolicy.
  ///
  /// In en, this message translates to:
  /// **'Save agent policy'**
  String get adminSaveAgentPolicy;

  /// No description provided for @adminSaveQuotas.
  ///
  /// In en, this message translates to:
  /// **'Save quotas'**
  String get adminSaveQuotas;

  /// No description provided for @adminSaveSubscription.
  ///
  /// In en, this message translates to:
  /// **'Save subscription'**
  String get adminSaveSubscription;

  /// No description provided for @adminSecretRefValue.
  ///
  /// In en, this message translates to:
  /// **'Secret ref: {secret_ref_prefix}'**
  String adminSecretRefValue(String secret_ref_prefix);

  /// No description provided for @adminSecretRequired.
  ///
  /// In en, this message translates to:
  /// **'Secret required'**
  String get adminSecretRequired;

  /// No description provided for @adminSecretStoredServerSide.
  ///
  /// In en, this message translates to:
  /// **'Secret is stored server-side only'**
  String get adminSecretStoredServerSide;

  /// No description provided for @adminSetEndDateOrLifetime.
  ///
  /// In en, this message translates to:
  /// **'Set end date or enable lifetime subscription'**
  String get adminSetEndDateOrLifetime;

  /// No description provided for @adminShipped.
  ///
  /// In en, this message translates to:
  /// **'shipped'**
  String get adminShipped;

  /// No description provided for @adminStarterBundles.
  ///
  /// In en, this message translates to:
  /// **'Starter bundles'**
  String get adminStarterBundles;

  /// No description provided for @adminStarterBundlesHint.
  ///
  /// In en, this message translates to:
  /// **'Catalog entries appear when shipped under data/starter_bundles/'**
  String get adminStarterBundlesHint;

  /// No description provided for @adminStatusValue.
  ///
  /// In en, this message translates to:
  /// **'Status: {status}'**
  String adminStatusValue(String status);

  /// No description provided for @adminSubscriptionSaved.
  ///
  /// In en, this message translates to:
  /// **'Subscription saved'**
  String get adminSubscriptionSaved;

  /// No description provided for @adminSweepIdlePause.
  ///
  /// In en, this message translates to:
  /// **'Idle pause'**
  String get adminSweepIdlePause;

  /// No description provided for @adminSweepIdlePauseAll.
  ///
  /// In en, this message translates to:
  /// **'Idle pause · all'**
  String get adminSweepIdlePauseAll;

  /// No description provided for @adminSweeping.
  ///
  /// In en, this message translates to:
  /// **'Sweeping…'**
  String get adminSweeping;

  /// No description provided for @adminTelegramHmacConfigured.
  ///
  /// In en, this message translates to:
  /// **'Telegram HMAC: configured (leave blank to keep)'**
  String get adminTelegramHmacConfigured;

  /// No description provided for @adminTelegramHmacNotSet.
  ///
  /// In en, this message translates to:
  /// **'Telegram HMAC: not set'**
  String get adminTelegramHmacNotSet;

  /// No description provided for @adminTelegramHmacSecret.
  ///
  /// In en, this message translates to:
  /// **'Telegram HMAC'**
  String get adminTelegramHmacSecret;

  /// No description provided for @adminTokens.
  ///
  /// In en, this message translates to:
  /// **'Tokens'**
  String get adminTokens;

  /// No description provided for @adminToolPreset.
  ///
  /// In en, this message translates to:
  /// **'Tool preset'**
  String get adminToolPreset;

  /// No description provided for @adminWebhookHmacConfigured.
  ///
  /// In en, this message translates to:
  /// **'Webhook HMAC: configured (leave blank to keep)'**
  String get adminWebhookHmacConfigured;

  /// No description provided for @adminWebhookHmacNotSet.
  ///
  /// In en, this message translates to:
  /// **'Webhook HMAC: not set'**
  String get adminWebhookHmacNotSet;

  /// No description provided for @adminWebhookHmacSecret.
  ///
  /// In en, this message translates to:
  /// **'Webhook HMAC'**
  String get adminWebhookHmacSecret;

  /// No description provided for @authAdvanced.
  ///
  /// In en, this message translates to:
  /// **'Advanced'**
  String get authAdvanced;

  /// No description provided for @authBearerAccessToken.
  ///
  /// In en, this message translates to:
  /// **'Bearer access token'**
  String get authBearerAccessToken;

  /// No description provided for @authBearerAccessTokenPaste.
  ///
  /// In en, this message translates to:
  /// **'Bearer access token (paste)'**
  String get authBearerAccessTokenPaste;

  /// No description provided for @authConfigUnavailableTestMode.
  ///
  /// In en, this message translates to:
  /// **'Auth config unavailable — using test mode. {e}'**
  String authConfigUnavailableTestMode(String e);

  /// No description provided for @authConnecting.
  ///
  /// In en, this message translates to:
  /// **'Connecting…'**
  String get authConnecting;

  /// No description provided for @authContinueAsDemoEmployee.
  ///
  /// In en, this message translates to:
  /// **'Continue as Demo Employee'**
  String get authContinueAsDemoEmployee;

  /// No description provided for @authContinueAsPlatformAdmin.
  ///
  /// In en, this message translates to:
  /// **'Continue as Platform Admin'**
  String get authContinueAsPlatformAdmin;

  /// No description provided for @authContinueWithToken.
  ///
  /// In en, this message translates to:
  /// **'Continue with token'**
  String get authContinueWithToken;

  /// No description provided for @authDevTestModeHint.
  ///
  /// In en, this message translates to:
  /// **'Dev test mode — one-click persona (no Keycloak)'**
  String get authDevTestModeHint;

  /// No description provided for @authHideAdvanced.
  ///
  /// In en, this message translates to:
  /// **'Hide advanced'**
  String get authHideAdvanced;

  /// No description provided for @authNoAccessTokenInTestLogin.
  ///
  /// In en, this message translates to:
  /// **'No access_token in test login response'**
  String get authNoAccessTokenInTestLogin;

  /// No description provided for @authOidcModeHint.
  ///
  /// In en, this message translates to:
  /// **'OIDC — PKCE via Keycloak (mobile AppAuth, desktop browser loopback)'**
  String get authOidcModeHint;

  /// No description provided for @authOpeningLogin.
  ///
  /// In en, this message translates to:
  /// **'Opening login…'**
  String get authOpeningLogin;

  /// No description provided for @authReloadAuthConfig.
  ///
  /// In en, this message translates to:
  /// **'Reload auth config'**
  String get authReloadAuthConfig;

  /// No description provided for @authSignIn.
  ///
  /// In en, this message translates to:
  /// **'Sign in'**
  String get authSignIn;

  /// No description provided for @authLogin.
  ///
  /// In en, this message translates to:
  /// **'Login'**
  String get authLogin;

  /// No description provided for @authPassword.
  ///
  /// In en, this message translates to:
  /// **'Password'**
  String get authPassword;

  /// No description provided for @authShowPassword.
  ///
  /// In en, this message translates to:
  /// **'Show password'**
  String get authShowPassword;

  /// No description provided for @authHidePassword.
  ///
  /// In en, this message translates to:
  /// **'Hide password'**
  String get authHidePassword;

  /// No description provided for @authSignInWithKeycloak.
  ///
  /// In en, this message translates to:
  /// **'Sign in with Keycloak'**
  String get authSignInWithKeycloak;

  /// No description provided for @authSigningIn.
  ///
  /// In en, this message translates to:
  /// **'Signing in…'**
  String get authSigningIn;

  /// No description provided for @authSignOut.
  ///
  /// In en, this message translates to:
  /// **'Sign out'**
  String get authSignOut;

  /// No description provided for @cabinetAddAtLeastOneColumn.
  ///
  /// In en, this message translates to:
  /// **'Add at least one column'**
  String get cabinetAddAtLeastOneColumn;

  /// No description provided for @cabinetAddColumn.
  ///
  /// In en, this message translates to:
  /// **'Add column'**
  String get cabinetAddColumn;

  /// No description provided for @cabinetAddRow.
  ///
  /// In en, this message translates to:
  /// **'Add row'**
  String get cabinetAddRow;

  /// No description provided for @cabinetAgentsInstructions.
  ///
  /// In en, this message translates to:
  /// **'Agents instructions'**
  String get cabinetAgentsInstructions;

  /// No description provided for @cabinetAgentsMd.
  ///
  /// In en, this message translates to:
  /// **'AGENTS.md'**
  String get cabinetAgentsMd;

  /// No description provided for @cabinetAgentsMdHint.
  ///
  /// In en, this message translates to:
  /// **'Written into project workspace on materialize (AGENTS.md + CLAUDE.md). Re-materialize projects to apply.'**
  String get cabinetAgentsMdHint;

  /// No description provided for @cabinetArchiveTable.
  ///
  /// In en, this message translates to:
  /// **'Archive table'**
  String get cabinetArchiveTable;

  /// No description provided for @cabinetArchiveTableConfirm.
  ///
  /// In en, this message translates to:
  /// **'Archive table?'**
  String get cabinetArchiveTableConfirm;

  /// No description provided for @cabinetArchiveTableMessage.
  ///
  /// In en, this message translates to:
  /// **'Archive \"{tableSlug}\". Rows stay in DB but table hides from lists. Remove views referencing this table first.'**
  String cabinetArchiveTableMessage(String tableSlug);

  /// No description provided for @cabinetArchivedBanner.
  ///
  /// In en, this message translates to:
  /// **'Archived — you can delete permanently or go back.'**
  String get cabinetArchivedBanner;

  /// No description provided for @cabinetArchiving.
  ///
  /// In en, this message translates to:
  /// **'Archiving…'**
  String get cabinetArchiving;

  /// No description provided for @cabinetAuditLog.
  ///
  /// In en, this message translates to:
  /// **'Audit log'**
  String get cabinetAuditLog;

  /// No description provided for @cabinetAuditRecent.
  ///
  /// In en, this message translates to:
  /// **'Audit (recent)'**
  String get cabinetAuditRecent;

  /// No description provided for @cabinetCabinetName.
  ///
  /// In en, this message translates to:
  /// **'Cabinet name'**
  String get cabinetCabinetName;

  /// No description provided for @cabinetChooseZipFile.
  ///
  /// In en, this message translates to:
  /// **'Choose .zip file'**
  String get cabinetChooseZipFile;

  /// No description provided for @cabinetColumnName.
  ///
  /// In en, this message translates to:
  /// **'Column name'**
  String get cabinetColumnName;

  /// No description provided for @cabinetColumnSettings.
  ///
  /// In en, this message translates to:
  /// **'Column settings'**
  String get cabinetColumnSettings;

  /// No description provided for @cabinetColumnTypeChip.
  ///
  /// In en, this message translates to:
  /// **'{name} · {type}'**
  String cabinetColumnTypeChip(String name, String type);

  /// No description provided for @cabinetColumns.
  ///
  /// In en, this message translates to:
  /// **'Columns'**
  String get cabinetColumns;

  /// No description provided for @cabinetContextHint.
  ///
  /// In en, this message translates to:
  /// **'Use the Projects tab to open an agent workspace. Tables and Tools tabs expose cabinet runtime data.'**
  String get cabinetContextHint;

  /// No description provided for @cabinetContextStatusLine.
  ///
  /// In en, this message translates to:
  /// **'Status: {status} · Company {companyId}'**
  String cabinetContextStatusLine(String status, String companyId);

  /// No description provided for @cabinetCouldNotReadZipBytes.
  ///
  /// In en, this message translates to:
  /// **'Could not read zip bytes'**
  String get cabinetCouldNotReadZipBytes;

  /// No description provided for @cabinetCreateBaseCabinetHint.
  ///
  /// In en, this message translates to:
  /// **'Create a Base cabinet to start'**
  String get cabinetCreateBaseCabinetHint;

  /// No description provided for @cabinetCreateCabinet.
  ///
  /// In en, this message translates to:
  /// **'Create cabinet'**
  String get cabinetCreateCabinet;

  /// No description provided for @cabinetCreateMetaTableFirst.
  ///
  /// In en, this message translates to:
  /// **'Create a meta table first (Tables tab → New table).'**
  String get cabinetCreateMetaTableFirst;

  /// No description provided for @cabinetCreateTab.
  ///
  /// In en, this message translates to:
  /// **'Create tab'**
  String get cabinetCreateTab;

  /// No description provided for @cabinetCreateTable.
  ///
  /// In en, this message translates to:
  /// **'Create table'**
  String get cabinetCreateTable;

  /// No description provided for @cabinetCustomTabs.
  ///
  /// In en, this message translates to:
  /// **'Custom tabs'**
  String get cabinetCustomTabs;

  /// No description provided for @cabinetCustomTabsHint.
  ///
  /// In en, this message translates to:
  /// **'Custom tabs appear in the cabinet shell after creation. System tabs cannot be removed here.'**
  String get cabinetCustomTabsHint;

  /// No description provided for @cabinetDeleteColumn.
  ///
  /// In en, this message translates to:
  /// **'Delete column'**
  String get cabinetDeleteColumn;

  /// No description provided for @cabinetDeleteColumnConfirm.
  ///
  /// In en, this message translates to:
  /// **'Delete column?'**
  String get cabinetDeleteColumnConfirm;

  /// No description provided for @cabinetDeletePermanently.
  ///
  /// In en, this message translates to:
  /// **'Delete permanently'**
  String get cabinetDeletePermanently;

  /// No description provided for @cabinetDeleteRow.
  ///
  /// In en, this message translates to:
  /// **'Delete row?'**
  String get cabinetDeleteRow;

  /// No description provided for @cabinetDeleteRowPermanently.
  ///
  /// In en, this message translates to:
  /// **'Delete row {rowId} permanently.'**
  String cabinetDeleteRowPermanently(String rowId);

  /// No description provided for @cabinetDeleteTab.
  ///
  /// In en, this message translates to:
  /// **'Delete tab?'**
  String get cabinetDeleteTab;

  /// No description provided for @cabinetDeleteTablePermanently.
  ///
  /// In en, this message translates to:
  /// **'Delete table permanently?'**
  String get cabinetDeleteTablePermanently;

  /// No description provided for @cabinetDropAllDataConfirm.
  ///
  /// In en, this message translates to:
  /// **'Drop all data for \"{tableSlug}\". This cannot be undone.'**
  String cabinetDropAllDataConfirm(String tableSlug);

  /// No description provided for @cabinetEditAgentsMd.
  ///
  /// In en, this message translates to:
  /// **'Edit AGENTS.md'**
  String get cabinetEditAgentsMd;

  /// No description provided for @cabinetEditRow.
  ///
  /// In en, this message translates to:
  /// **'Edit row'**
  String get cabinetEditRow;

  /// No description provided for @cabinetEmptyBundleExport.
  ///
  /// In en, this message translates to:
  /// **'Empty bundle export'**
  String get cabinetEmptyBundleExport;

  /// No description provided for @cabinetEnterAtLeastOneField.
  ///
  /// In en, this message translates to:
  /// **'Enter at least one field value'**
  String get cabinetEnterAtLeastOneField;

  /// No description provided for @cabinetExportCabinetBundle.
  ///
  /// In en, this message translates to:
  /// **'Export cabinet bundle'**
  String get cabinetExportCabinetBundle;

  /// No description provided for @cabinetExportReady.
  ///
  /// In en, this message translates to:
  /// **'Export ready ({bytes} bytes)'**
  String cabinetExportReady(String bytes);

  /// No description provided for @cabinetExporting.
  ///
  /// In en, this message translates to:
  /// **'Exporting…'**
  String get cabinetExporting;

  /// No description provided for @cabinetFromFile.
  ///
  /// In en, this message translates to:
  /// **'From file'**
  String get cabinetFromFile;

  /// No description provided for @cabinetImportBundleIntro.
  ///
  /// In en, this message translates to:
  /// **'Import a cabinet.bundle zip exported from another cabinet. Creates a new cabinet instance with a fresh schema.'**
  String get cabinetImportBundleIntro;

  /// No description provided for @cabinetImportBundleTooltip.
  ///
  /// In en, this message translates to:
  /// **'Import bundle'**
  String get cabinetImportBundleTooltip;

  /// No description provided for @cabinetImportCabinetBundle.
  ///
  /// In en, this message translates to:
  /// **'Import cabinet bundle'**
  String get cabinetImportCabinetBundle;

  /// No description provided for @cabinetImportedCabinetDefault.
  ///
  /// In en, this message translates to:
  /// **'Imported cabinet'**
  String get cabinetImportedCabinetDefault;

  /// No description provided for @cabinetImporting.
  ///
  /// In en, this message translates to:
  /// **'Importing…'**
  String get cabinetImporting;

  /// No description provided for @cabinetLettersDigitsUnderscore.
  ///
  /// In en, this message translates to:
  /// **'Letters, digits, underscore'**
  String get cabinetLettersDigitsUnderscore;

  /// No description provided for @cabinetLowercaseSlugRule.
  ///
  /// In en, this message translates to:
  /// **'Lowercase letters, digits, underscore'**
  String get cabinetLowercaseSlugRule;

  /// No description provided for @cabinetManageCustomTabs.
  ///
  /// In en, this message translates to:
  /// **'Manage custom tabs'**
  String get cabinetManageCustomTabs;

  /// No description provided for @cabinetMcpTools.
  ///
  /// In en, this message translates to:
  /// **'MCP tools'**
  String get cabinetMcpTools;

  /// No description provided for @cabinetMetaTables.
  ///
  /// In en, this message translates to:
  /// **'Meta tables'**
  String get cabinetMetaTables;

  /// No description provided for @cabinetMyCabinetDefault.
  ///
  /// In en, this message translates to:
  /// **'My cabinet'**
  String get cabinetMyCabinetDefault;

  /// No description provided for @cabinetNewCabinet.
  ///
  /// In en, this message translates to:
  /// **'New cabinet'**
  String get cabinetNewCabinet;

  /// No description provided for @cabinetNewCustomTab.
  ///
  /// In en, this message translates to:
  /// **'New custom tab'**
  String get cabinetNewCustomTab;

  /// No description provided for @cabinetNewTab.
  ///
  /// In en, this message translates to:
  /// **'New tab'**
  String get cabinetNewTab;

  /// No description provided for @cabinetNewTable.
  ///
  /// In en, this message translates to:
  /// **'New table'**
  String get cabinetNewTable;

  /// No description provided for @cabinetNoAuditEventsYet.
  ///
  /// In en, this message translates to:
  /// **'No audit events yet.'**
  String get cabinetNoAuditEventsYet;

  /// No description provided for @cabinetNoCompanyIdFromMe.
  ///
  /// In en, this message translates to:
  /// **'No company_id from /me memberships'**
  String get cabinetNoCompanyIdFromMe;

  /// No description provided for @cabinetNoCustomTabsYet.
  ///
  /// In en, this message translates to:
  /// **'No custom tabs yet.'**
  String get cabinetNoCustomTabsYet;

  /// No description provided for @cabinetNoInterpreterForView.
  ///
  /// In en, this message translates to:
  /// **'No interpreter registered for view \"{slug}\".'**
  String cabinetNoInterpreterForView(String slug);

  /// No description provided for @cabinetNoMcpTools.
  ///
  /// In en, this message translates to:
  /// **'No MCP tools exposed for this cabinet.'**
  String get cabinetNoMcpTools;

  /// No description provided for @cabinetNoMetaTablesYet.
  ///
  /// In en, this message translates to:
  /// **'No meta tables in this cabinet yet.'**
  String get cabinetNoMetaTablesYet;

  /// No description provided for @cabinetNoRows.
  ///
  /// In en, this message translates to:
  /// **'No rows'**
  String get cabinetNoRows;

  /// No description provided for @cabinetNotShipped.
  ///
  /// In en, this message translates to:
  /// **'not shipped'**
  String get cabinetNotShipped;

  /// No description provided for @cabinetOfficialStarterBundles.
  ///
  /// In en, this message translates to:
  /// **'Official starter bundles'**
  String get cabinetOfficialStarterBundles;

  /// No description provided for @cabinetRemoveColumnData.
  ///
  /// In en, this message translates to:
  /// **'Remove column \"{columnName}\" and its data.'**
  String cabinetRemoveColumnData(String columnName);

  /// No description provided for @cabinetRemoveTabAndView.
  ///
  /// In en, this message translates to:
  /// **'Remove tab \"{title}\" and its view.'**
  String cabinetRemoveTabAndView(String title);

  /// No description provided for @cabinetReservedName.
  ///
  /// In en, this message translates to:
  /// **'Reserved name'**
  String get cabinetReservedName;

  /// No description provided for @cabinetRowFallback.
  ///
  /// In en, this message translates to:
  /// **'Row {index}'**
  String cabinetRowFallback(String index);

  /// No description provided for @cabinetSaveLabel.
  ///
  /// In en, this message translates to:
  /// **'Save label'**
  String get cabinetSaveLabel;

  /// No description provided for @cabinetSaveRow.
  ///
  /// In en, this message translates to:
  /// **'Save row'**
  String get cabinetSaveRow;

  /// No description provided for @cabinetSaveView.
  ///
  /// In en, this message translates to:
  /// **'Save view'**
  String get cabinetSaveView;

  /// No description provided for @cabinetSavedTo.
  ///
  /// In en, this message translates to:
  /// **'Saved to {path}'**
  String cabinetSavedTo(String path);

  /// No description provided for @cabinetSelectATable.
  ///
  /// In en, this message translates to:
  /// **'Select a table'**
  String get cabinetSelectATable;

  /// No description provided for @cabinetSelectCabinetBundleZip.
  ///
  /// In en, this message translates to:
  /// **'Select a cabinet.bundle zip file'**
  String get cabinetSelectCabinetBundleZip;

  /// No description provided for @cabinetSelectTableToPreview.
  ///
  /// In en, this message translates to:
  /// **'Select a table to preview rows'**
  String get cabinetSelectTableToPreview;

  /// No description provided for @cabinetSlug.
  ///
  /// In en, this message translates to:
  /// **'Slug'**
  String get cabinetSlug;

  /// No description provided for @cabinetStatusCompanyLine.
  ///
  /// In en, this message translates to:
  /// **'Status: {status} · Company {companyId}'**
  String cabinetStatusCompanyLine(String status, String companyId);

  /// No description provided for @cabinetStorage.
  ///
  /// In en, this message translates to:
  /// **'Storage'**
  String get cabinetStorage;

  /// No description provided for @cabinetStorageJsonDocument.
  ///
  /// In en, this message translates to:
  /// **'json_document'**
  String get cabinetStorageJsonDocument;

  /// No description provided for @cabinetStoragePhysical.
  ///
  /// In en, this message translates to:
  /// **'physical'**
  String get cabinetStoragePhysical;

  /// No description provided for @cabinetSystemColumnReadOnly.
  ///
  /// In en, this message translates to:
  /// **'System column — read only.'**
  String get cabinetSystemColumnReadOnly;

  /// No description provided for @cabinetTabFallback.
  ///
  /// In en, this message translates to:
  /// **'Tab'**
  String get cabinetTabFallback;

  /// No description provided for @cabinetTabOrder.
  ///
  /// In en, this message translates to:
  /// **'Tab order'**
  String get cabinetTabOrder;

  /// No description provided for @cabinetTabSubtitle.
  ///
  /// In en, this message translates to:
  /// **'view: {viewSlug} · table: {tableSlug}'**
  String cabinetTabSubtitle(String viewSlug, String tableSlug);

  /// No description provided for @cabinetTabTitle.
  ///
  /// In en, this message translates to:
  /// **'Tab title'**
  String get cabinetTabTitle;

  /// No description provided for @cabinetTableSettings.
  ///
  /// In en, this message translates to:
  /// **'Table settings'**
  String get cabinetTableSettings;

  /// No description provided for @cabinetTitleFieldColumnName.
  ///
  /// In en, this message translates to:
  /// **'Title field (column name)'**
  String get cabinetTitleFieldColumnName;

  /// No description provided for @cabinetType.
  ///
  /// In en, this message translates to:
  /// **'Type'**
  String get cabinetType;

  /// No description provided for @cabinetUnique.
  ///
  /// In en, this message translates to:
  /// **'Unique'**
  String get cabinetUnique;

  /// No description provided for @cabinetUnknownTabNoViewSlug.
  ///
  /// In en, this message translates to:
  /// **'Unknown tab — no view_slug from meta.'**
  String get cabinetUnknownTabNoViewSlug;

  /// No description provided for @cabinetViewNotFound.
  ///
  /// In en, this message translates to:
  /// **'View not found'**
  String get cabinetViewNotFound;

  /// No description provided for @cabinetViewSlug.
  ///
  /// In en, this message translates to:
  /// **'View slug'**
  String get cabinetViewSlug;

  /// No description provided for @cabinetViewTitle.
  ///
  /// In en, this message translates to:
  /// **'View {viewSlug}'**
  String cabinetViewTitle(String viewSlug);

  /// No description provided for @commonAdd.
  ///
  /// In en, this message translates to:
  /// **'Add'**
  String get commonAdd;

  /// No description provided for @commonAdding.
  ///
  /// In en, this message translates to:
  /// **'Adding…'**
  String get commonAdding;

  /// No description provided for @commonAgentTokens.
  ///
  /// In en, this message translates to:
  /// **'Agent tokens'**
  String get commonAgentTokens;

  /// No description provided for @commonApiBaseUrl.
  ///
  /// In en, this message translates to:
  /// **'API base URL'**
  String get commonApiBaseUrl;

  /// No description provided for @commonArchive.
  ///
  /// In en, this message translates to:
  /// **'Archive'**
  String get commonArchive;

  /// No description provided for @commonCabinets.
  ///
  /// In en, this message translates to:
  /// **'Cabinets'**
  String get commonCabinets;

  /// No description provided for @commonCancel.
  ///
  /// In en, this message translates to:
  /// **'Cancel'**
  String get commonCancel;

  /// No description provided for @commonCompany.
  ///
  /// In en, this message translates to:
  /// **'Company'**
  String get commonCompany;

  /// No description provided for @commonCompanies.
  ///
  /// In en, this message translates to:
  /// **'Companies'**
  String get commonCompanies;

  /// No description provided for @commonDescription.
  ///
  /// In en, this message translates to:
  /// **'Description'**
  String get commonDescription;

  /// No description provided for @commonEntity.
  ///
  /// In en, this message translates to:
  /// **'Name'**
  String get commonEntity;

  /// No description provided for @commonContinueAction.
  ///
  /// In en, this message translates to:
  /// **'Continue'**
  String get commonContinueAction;

  /// No description provided for @commonCreate.
  ///
  /// In en, this message translates to:
  /// **'Create'**
  String get commonCreate;

  /// No description provided for @commonCreating.
  ///
  /// In en, this message translates to:
  /// **'Creating…'**
  String get commonCreating;

  /// No description provided for @commonDelete.
  ///
  /// In en, this message translates to:
  /// **'Delete'**
  String get commonDelete;

  /// No description provided for @commonRemove.
  ///
  /// In en, this message translates to:
  /// **'Remove'**
  String get commonRemove;

  /// No description provided for @commonCopy.
  ///
  /// In en, this message translates to:
  /// **'Copy'**
  String get commonCopy;

  /// No description provided for @commonEdit.
  ///
  /// In en, this message translates to:
  /// **'Edit'**
  String get commonEdit;

  /// No description provided for @commonDeleting.
  ///
  /// In en, this message translates to:
  /// **'Deleting…'**
  String get commonDeleting;

  /// No description provided for @commonDisable.
  ///
  /// In en, this message translates to:
  /// **'Disable'**
  String get commonDisable;

  /// No description provided for @commonDisplayNameOptional.
  ///
  /// In en, this message translates to:
  /// **'Display name'**
  String get commonDisplayNameOptional;

  /// No description provided for @commonMbUnit.
  ///
  /// In en, this message translates to:
  /// **'MB'**
  String get commonMbUnit;

  /// No description provided for @commonNotSet.
  ///
  /// In en, this message translates to:
  /// **'Not set'**
  String get commonNotSet;

  /// No description provided for @commonOff.
  ///
  /// In en, this message translates to:
  /// **'Off'**
  String get commonOff;

  /// No description provided for @commonUnlimited.
  ///
  /// In en, this message translates to:
  /// **'Unlimited'**
  String get commonUnlimited;

  /// No description provided for @adminBindingsCount.
  ///
  /// In en, this message translates to:
  /// **'{count} companies'**
  String adminBindingsCount(int count);

  /// No description provided for @commonDone.
  ///
  /// In en, this message translates to:
  /// **'Done'**
  String get commonDone;

  /// No description provided for @commonEmDash.
  ///
  /// In en, this message translates to:
  /// **'—'**
  String get commonEmDash;

  /// No description provided for @commonEmail.
  ///
  /// In en, this message translates to:
  /// **'Email'**
  String get commonEmail;

  /// No description provided for @commonEmployees.
  ///
  /// In en, this message translates to:
  /// **'Employees'**
  String get commonEmployees;

  /// No description provided for @commonEmpty.
  ///
  /// In en, this message translates to:
  /// **'Empty'**
  String get commonEmpty;

  /// No description provided for @commonFilter.
  ///
  /// In en, this message translates to:
  /// **'Filter'**
  String get commonFilter;

  /// No description provided for @commonImport.
  ///
  /// In en, this message translates to:
  /// **'Import'**
  String get commonImport;

  /// No description provided for @commonInvite.
  ///
  /// In en, this message translates to:
  /// **'Invite'**
  String get commonInvite;

  /// No description provided for @commonLabel.
  ///
  /// In en, this message translates to:
  /// **'Label'**
  String get commonLabel;

  /// No description provided for @commonLastActivity.
  ///
  /// In en, this message translates to:
  /// **'Last activity'**
  String get commonLastActivity;

  /// No description provided for @commonList.
  ///
  /// In en, this message translates to:
  /// **'List'**
  String get commonList;

  /// No description provided for @commonName.
  ///
  /// In en, this message translates to:
  /// **'Name'**
  String get commonName;

  /// No description provided for @commonNameRequired.
  ///
  /// In en, this message translates to:
  /// **'Name required'**
  String get commonNameRequired;

  /// No description provided for @commonNone.
  ///
  /// In en, this message translates to:
  /// **'None'**
  String get commonNone;

  /// No description provided for @commonNothingFound.
  ///
  /// In en, this message translates to:
  /// **'Nothing found'**
  String get commonNothingFound;

  /// No description provided for @commonOverview.
  ///
  /// In en, this message translates to:
  /// **'Overview'**
  String get commonOverview;

  /// No description provided for @commonPositiveInteger.
  ///
  /// In en, this message translates to:
  /// **'Positive integer'**
  String get commonPositiveInteger;

  /// No description provided for @commonProjects.
  ///
  /// In en, this message translates to:
  /// **'Projects'**
  String get commonProjects;

  /// No description provided for @commonProvider.
  ///
  /// In en, this message translates to:
  /// **'Provider'**
  String get commonProvider;

  /// No description provided for @commonReload.
  ///
  /// In en, this message translates to:
  /// **'Reload'**
  String get commonReload;

  /// No description provided for @commonRequired.
  ///
  /// In en, this message translates to:
  /// **'Required'**
  String get commonRequired;

  /// No description provided for @commonResume.
  ///
  /// In en, this message translates to:
  /// **'Resume'**
  String get commonResume;

  /// No description provided for @commonSave.
  ///
  /// In en, this message translates to:
  /// **'Save'**
  String get commonSave;

  /// No description provided for @commonSaving.
  ///
  /// In en, this message translates to:
  /// **'Saving…'**
  String get commonSaving;

  /// No description provided for @commonSearch.
  ///
  /// In en, this message translates to:
  /// **'Search'**
  String get commonSearch;

  /// No description provided for @commonSecret.
  ///
  /// In en, this message translates to:
  /// **'Secret'**
  String get commonSecret;

  /// No description provided for @commonSelect.
  ///
  /// In en, this message translates to:
  /// **'Select'**
  String get commonSelect;

  /// No description provided for @commonSelectCompany.
  ///
  /// In en, this message translates to:
  /// **'Select company'**
  String get commonSelectCompany;

  /// No description provided for @commonStatus.
  ///
  /// In en, this message translates to:
  /// **'Status'**
  String get commonStatus;

  /// No description provided for @commonStorageBytes.
  ///
  /// In en, this message translates to:
  /// **'Storage'**
  String get commonStorageBytes;

  /// No description provided for @commonTable.
  ///
  /// In en, this message translates to:
  /// **'Table'**
  String get commonTable;

  /// No description provided for @commonTitle.
  ///
  /// In en, this message translates to:
  /// **'Title'**
  String get commonTitle;

  /// No description provided for @commonUnknown.
  ///
  /// In en, this message translates to:
  /// **'unknown'**
  String get commonUnknown;

  /// No description provided for @companyActive.
  ///
  /// In en, this message translates to:
  /// **'Active'**
  String get companyActive;

  /// No description provided for @companyCabinet.
  ///
  /// In en, this message translates to:
  /// **'Cabinet'**
  String get companyCabinet;

  /// No description provided for @companyCabinetsEmptyHint.
  ///
  /// In en, this message translates to:
  /// **'Employees create cabinets in Employee contour'**
  String get companyCabinetsEmptyHint;

  /// No description provided for @companyDisableEmployee.
  ///
  /// In en, this message translates to:
  /// **'Disable employee'**
  String get companyDisableEmployee;

  /// No description provided for @companyDisableEmployeeConfirm.
  ///
  /// In en, this message translates to:
  /// **'Disable {email}? They will lose access.'**
  String companyDisableEmployeeConfirm(String email);

  /// No description provided for @companyInviteEmployee.
  ///
  /// In en, this message translates to:
  /// **'Invite employee'**
  String get companyInviteEmployee;

  /// No description provided for @companyInviteViaKeycloakNoPassword.
  ///
  /// In en, this message translates to:
  /// **'Invite via Keycloak — no password field'**
  String get companyInviteViaKeycloakNoPassword;

  /// No description provided for @companyInviteViaKeycloakPasswordNotAccepted.
  ///
  /// In en, this message translates to:
  /// **'Invite via Keycloak — password is not accepted here.'**
  String get companyInviteViaKeycloakPasswordNotAccepted;

  /// No description provided for @companyInviting.
  ///
  /// In en, this message translates to:
  /// **'Inviting…'**
  String get companyInviting;

  /// No description provided for @companyNoCabinets.
  ///
  /// In en, this message translates to:
  /// **'No cabinets'**
  String get companyNoCabinets;

  /// No description provided for @companyAssignEmployeeToCabinet.
  ///
  /// In en, this message translates to:
  /// **'Assign employee'**
  String get companyAssignEmployeeToCabinet;

  /// No description provided for @companyAssignedEmployees.
  ///
  /// In en, this message translates to:
  /// **'Assigned'**
  String get companyAssignedEmployees;

  /// No description provided for @companyNoAssignedEmployees.
  ///
  /// In en, this message translates to:
  /// **'No employees assigned'**
  String get companyNoAssignedEmployees;

  /// No description provided for @companyAssignCabinetsToEmployee.
  ///
  /// In en, this message translates to:
  /// **'Cabinet access'**
  String get companyAssignCabinetsToEmployee;

  /// No description provided for @companyNoEmployees.
  ///
  /// In en, this message translates to:
  /// **'No employees'**
  String get companyNoEmployees;

  /// No description provided for @companyOrgMetrics.
  ///
  /// In en, this message translates to:
  /// **'Org metrics'**
  String get companyOrgMetrics;

  /// No description provided for @companyLoginId.
  ///
  /// In en, this message translates to:
  /// **'Company ID'**
  String get companyLoginId;

  /// No description provided for @companyIdCopied.
  ///
  /// In en, this message translates to:
  /// **'Company ID copied'**
  String get companyIdCopied;

  /// No description provided for @companyPassword.
  ///
  /// In en, this message translates to:
  /// **'Password'**
  String get companyPassword;

  /// No description provided for @companyPasswordHint.
  ///
  /// In en, this message translates to:
  /// **'Minimum 8 characters'**
  String get companyPasswordHint;

  /// No description provided for @companyPasswordChanged.
  ///
  /// In en, this message translates to:
  /// **'Password changed successfully'**
  String get companyPasswordChanged;

  /// No description provided for @companyAddAiKey.
  ///
  /// In en, this message translates to:
  /// **'Add key'**
  String get companyAddAiKey;

  /// No description provided for @companyNoAiKeys.
  ///
  /// In en, this message translates to:
  /// **'No AI keys'**
  String get companyNoAiKeys;

  /// No description provided for @companyKeyPlatformBound.
  ///
  /// In en, this message translates to:
  /// **'From platform'**
  String get companyKeyPlatformBound;

  /// No description provided for @companyKeySourceLocal.
  ///
  /// In en, this message translates to:
  /// **'Local'**
  String get companyKeySourceLocal;

  /// No description provided for @companyKeyReadOnlyBanner.
  ///
  /// In en, this message translates to:
  /// **'Platform-bound key — read only'**
  String get companyKeyReadOnlyBanner;

  /// No description provided for @companyAddModule.
  ///
  /// In en, this message translates to:
  /// **'Add module'**
  String get companyAddModule;

  /// No description provided for @companyNoModules.
  ///
  /// In en, this message translates to:
  /// **'No modules'**
  String get companyNoModules;

  /// No description provided for @companyModulesEmptyHint.
  ///
  /// In en, this message translates to:
  /// **'Create a local module or wait for platform assignment'**
  String get companyModulesEmptyHint;

  /// No description provided for @companyModuleReadOnlyBanner.
  ///
  /// In en, this message translates to:
  /// **'Platform-assigned module — read only; you can bind cabinets'**
  String get companyModuleReadOnlyBanner;

  /// No description provided for @companyCreateRuntimeKeyHint.
  ///
  /// In en, this message translates to:
  /// **'Add a company-owned key for your employees\' projects'**
  String get companyCreateRuntimeKeyHint;

  /// No description provided for @errorConflict.
  ///
  /// In en, this message translates to:
  /// **'Data conflict. Refresh and try again.'**
  String get errorConflict;

  /// No description provided for @errorForbidden.
  ///
  /// In en, this message translates to:
  /// **'You do not have permission for this action.'**
  String get errorForbidden;

  /// No description provided for @errorGateway.
  ///
  /// In en, this message translates to:
  /// **'Server temporarily unavailable. Please try again.'**
  String get errorGateway;

  /// No description provided for @errorHttpStatus.
  ///
  /// In en, this message translates to:
  /// **'Request failed (HTTP {status})'**
  String errorHttpStatus(int status);

  /// No description provided for @errorIdentityProvider.
  ///
  /// In en, this message translates to:
  /// **'Could not update credentials. Please try again.'**
  String get errorIdentityProvider;

  /// No description provided for @errorNetwork.
  ///
  /// In en, this message translates to:
  /// **'No connection to the server. Check your network.'**
  String get errorNetwork;

  /// No description provided for @errorNotFound.
  ///
  /// In en, this message translates to:
  /// **'The requested item was not found.'**
  String get errorNotFound;

  /// No description provided for @errorRateLimited.
  ///
  /// In en, this message translates to:
  /// **'Too many requests. Please wait a moment.'**
  String get errorRateLimited;

  /// No description provided for @errorServer.
  ///
  /// In en, this message translates to:
  /// **'Internal server error. Please try again later.'**
  String get errorServer;

  /// No description provided for @errorUnauthorized.
  ///
  /// In en, this message translates to:
  /// **'Session expired. Please sign in again.'**
  String get errorUnauthorized;

  /// No description provided for @errorUnexpected.
  ///
  /// In en, this message translates to:
  /// **'Something went wrong.'**
  String get errorUnexpected;

  /// No description provided for @errorValidation.
  ///
  /// In en, this message translates to:
  /// **'Please check the entered data.'**
  String get errorValidation;

  /// No description provided for @errorProjectPaused.
  ///
  /// In en, this message translates to:
  /// **'Project is paused. Resume to continue.'**
  String get errorProjectPaused;

  /// No description provided for @errorCabinetArchived.
  ///
  /// In en, this message translates to:
  /// **'Cabinet is not available for changes.'**
  String get errorCabinetArchived;

  /// No description provided for @errorCompanySuspended.
  ///
  /// In en, this message translates to:
  /// **'Company subscription is suspended or expired.'**
  String get errorCompanySuspended;

  /// No description provided for @errorNoAiKey.
  ///
  /// In en, this message translates to:
  /// **'No AI key available to run.'**
  String get errorNoAiKey;

  /// No description provided for @errorSessionClosed.
  ///
  /// In en, this message translates to:
  /// **'Agent session is closed.'**
  String get errorSessionClosed;

  /// No description provided for @errorAgentBudget.
  ///
  /// In en, this message translates to:
  /// **'Agent token budget exhausted.'**
  String get errorAgentBudget;

  /// No description provided for @errorCascadeIncomplete.
  ///
  /// In en, this message translates to:
  /// **'Delete cascade is still in progress. Wait or retry later.'**
  String get errorCascadeIncomplete;

  /// No description provided for @companyCredentialsCreated.
  ///
  /// In en, this message translates to:
  /// **'Login: {companyId} · password: {password}'**
  String companyCredentialsCreated(String companyId, String password);

  /// No description provided for @companyOwner.
  ///
  /// In en, this message translates to:
  /// **'Owner'**
  String get companyOwner;

  /// No description provided for @companyReadOnlyOrgView.
  ///
  /// In en, this message translates to:
  /// **'{title} — read-only org view'**
  String companyReadOnlyOrgView(String title);

  /// No description provided for @companyRole.
  ///
  /// In en, this message translates to:
  /// **'Role'**
  String get companyRole;

  /// No description provided for @companyValidEmailRequired.
  ///
  /// In en, this message translates to:
  /// **'Valid email required'**
  String get companyValidEmailRequired;

  /// No description provided for @devAdminSession.
  ///
  /// In en, this message translates to:
  /// **'Admin session'**
  String get devAdminSession;

  /// No description provided for @devBearerTokenCompanyAdmin.
  ///
  /// In en, this message translates to:
  /// **'Bearer token (company.admin JWT)'**
  String get devBearerTokenCompanyAdmin;

  /// No description provided for @devBearerTokenPlatformAdmin.
  ///
  /// In en, this message translates to:
  /// **'Bearer token (platform_admin JWT)'**
  String get devBearerTokenPlatformAdmin;

  /// No description provided for @devBearerTokenTestJwt.
  ///
  /// In en, this message translates to:
  /// **'Bearer token (AUTH_MODE=test JWT)'**
  String get devBearerTokenTestJwt;

  /// No description provided for @devCompanyAdminMembershipRequired.
  ///
  /// In en, this message translates to:
  /// **'company.admin membership required'**
  String get devCompanyAdminMembershipRequired;

  /// No description provided for @devCompanySession.
  ///
  /// In en, this message translates to:
  /// **'Company session'**
  String get devCompanySession;

  /// No description provided for @devDevSession.
  ///
  /// In en, this message translates to:
  /// **'Dev session'**
  String get devDevSession;

  /// No description provided for @devNoEmployeeMemberships.
  ///
  /// In en, this message translates to:
  /// **'No employee memberships'**
  String get devNoEmployeeMemberships;

  /// No description provided for @devTokenMustHaveCompanyContour.
  ///
  /// In en, this message translates to:
  /// **'Token must have company contour (company.admin membership)'**
  String get devTokenMustHaveCompanyContour;

  /// No description provided for @devTokenMustHavePlatformAdmin.
  ///
  /// In en, this message translates to:
  /// **'Token must have platform_admin contour'**
  String get devTokenMustHavePlatformAdmin;

  /// No description provided for @galleryAlpha.
  ///
  /// In en, this message translates to:
  /// **'Alpha'**
  String get galleryAlpha;

  /// No description provided for @galleryBeta.
  ///
  /// In en, this message translates to:
  /// **'Beta'**
  String get galleryBeta;

  /// No description provided for @galleryButtons.
  ///
  /// In en, this message translates to:
  /// **'Buttons'**
  String get galleryButtons;

  /// No description provided for @galleryCheck.
  ///
  /// In en, this message translates to:
  /// **'Check'**
  String get galleryCheck;

  /// No description provided for @galleryCoreGallery.
  ///
  /// In en, this message translates to:
  /// **'Core gallery'**
  String get galleryCoreGallery;

  /// No description provided for @galleryCount.
  ///
  /// In en, this message translates to:
  /// **'Count'**
  String get galleryCount;

  /// No description provided for @galleryDanger.
  ///
  /// In en, this message translates to:
  /// **'Danger'**
  String get galleryDanger;

  /// No description provided for @galleryDemo.
  ///
  /// In en, this message translates to:
  /// **'demo'**
  String get galleryDemo;

  /// No description provided for @galleryDemoConfirm.
  ///
  /// In en, this message translates to:
  /// **'Demo confirm'**
  String get galleryDemoConfirm;

  /// No description provided for @galleryEntityCollection.
  ///
  /// In en, this message translates to:
  /// **'EntityCollection'**
  String get galleryEntityCollection;

  /// No description provided for @galleryListItem.
  ///
  /// In en, this message translates to:
  /// **'List item'**
  String get galleryListItem;

  /// No description provided for @galleryOne.
  ///
  /// In en, this message translates to:
  /// **'One'**
  String get galleryOne;

  /// No description provided for @galleryPick.
  ///
  /// In en, this message translates to:
  /// **'Pick'**
  String get galleryPick;

  /// No description provided for @galleryRadio.
  ///
  /// In en, this message translates to:
  /// **'Radio'**
  String get galleryRadio;

  /// No description provided for @gallerySampleRow.
  ///
  /// In en, this message translates to:
  /// **'Sample row'**
  String get gallerySampleRow;

  /// No description provided for @gallerySelection.
  ///
  /// In en, this message translates to:
  /// **'Selection'**
  String get gallerySelection;

  /// No description provided for @gallerySelector.
  ///
  /// In en, this message translates to:
  /// **'Selector'**
  String get gallerySelector;

  /// No description provided for @gallerySubtitle.
  ///
  /// In en, this message translates to:
  /// **'subtitle'**
  String get gallerySubtitle;

  /// No description provided for @galleryTwo.
  ///
  /// In en, this message translates to:
  /// **'Two'**
  String get galleryTwo;

  /// No description provided for @navAiKeys.
  ///
  /// In en, this message translates to:
  /// **'AI Keys'**
  String get navAiKeys;

  /// No description provided for @navBundles.
  ///
  /// In en, this message translates to:
  /// **'Bundles'**
  String get navBundles;

  /// No description provided for @navContainers.
  ///
  /// In en, this message translates to:
  /// **'Projects'**
  String get navContainers;

  /// No description provided for @navCabinets.
  ///
  /// In en, this message translates to:
  /// **'Cabinets'**
  String get navCabinets;

  /// No description provided for @navModules.
  ///
  /// In en, this message translates to:
  /// **'Modules'**
  String get navModules;

  /// No description provided for @navCompanies.
  ///
  /// In en, this message translates to:
  /// **'Companies'**
  String get navCompanies;

  /// No description provided for @navEmployees.
  ///
  /// In en, this message translates to:
  /// **'Employees'**
  String get navEmployees;

  /// No description provided for @navOverview.
  ///
  /// In en, this message translates to:
  /// **'Overview'**
  String get navOverview;

  /// No description provided for @navManagement.
  ///
  /// In en, this message translates to:
  /// **'Management'**
  String get navManagement;

  /// No description provided for @navProdavan.
  ///
  /// In en, this message translates to:
  /// **'Prodavan'**
  String get navProdavan;

  /// No description provided for @projectAgentError.
  ///
  /// In en, this message translates to:
  /// **'Agent error'**
  String get projectAgentError;

  /// No description provided for @projectApproveAndContinue.
  ///
  /// In en, this message translates to:
  /// **'Approve and continue'**
  String get projectApproveAndContinue;

  /// No description provided for @projectApproveTool.
  ///
  /// In en, this message translates to:
  /// **'Approve tool'**
  String get projectApproveTool;

  /// No description provided for @projectApproveToolPrompt.
  ///
  /// In en, this message translates to:
  /// **'Approve {name}?'**
  String projectApproveToolPrompt(String name);

  /// No description provided for @projectAttachFile.
  ///
  /// In en, this message translates to:
  /// **'Attach file'**
  String get projectAttachFile;

  /// No description provided for @projectAttachmentFallback.
  ///
  /// In en, this message translates to:
  /// **'attachment'**
  String get projectAttachmentFallback;

  /// No description provided for @projectCancelledMarker.
  ///
  /// In en, this message translates to:
  /// **'(cancelled)'**
  String get projectCancelledMarker;

  /// No description provided for @projectCancelledWithText.
  ///
  /// In en, this message translates to:
  /// **'{text}\n(cancelled)'**
  String projectCancelledWithText(String text);

  /// No description provided for @projectCompanyDefault.
  ///
  /// In en, this message translates to:
  /// **'Company default'**
  String get projectCompanyDefault;

  /// No description provided for @projectCouldNotReadFileBytes.
  ///
  /// In en, this message translates to:
  /// **'Could not read file bytes'**
  String get projectCouldNotReadFileBytes;

  /// No description provided for @projectCreateAndOpenChat.
  ///
  /// In en, this message translates to:
  /// **'Create and open chat'**
  String get projectCreateAndOpenChat;

  /// No description provided for @projectCreateProject.
  ///
  /// In en, this message translates to:
  /// **'Create project'**
  String get projectCreateProject;

  /// No description provided for @projectCreateProjectHint.
  ///
  /// In en, this message translates to:
  /// **'Create a project to open chat workspace'**
  String get projectCreateProjectHint;

  /// No description provided for @projectDeny.
  ///
  /// In en, this message translates to:
  /// **'Deny'**
  String get projectDeny;

  /// No description provided for @projectEmptyChatHint.
  ///
  /// In en, this message translates to:
  /// **'Send a message to start the agent session'**
  String get projectEmptyChatHint;

  /// No description provided for @projectFileFallback.
  ///
  /// In en, this message translates to:
  /// **'file'**
  String get projectFileFallback;

  /// No description provided for @projectInbox.
  ///
  /// In en, this message translates to:
  /// **'Inbox ({count})'**
  String projectInbox(String count);

  /// No description provided for @projectMessageHint.
  ///
  /// In en, this message translates to:
  /// **'Message…'**
  String get projectMessageHint;

  /// No description provided for @projectNewProject.
  ///
  /// In en, this message translates to:
  /// **'New project'**
  String get projectNewProject;

  /// No description provided for @projectNoAttachmentData.
  ///
  /// In en, this message translates to:
  /// **'No attachment data'**
  String get projectNoAttachmentData;

  /// No description provided for @projectNoPreviewForType.
  ///
  /// In en, this message translates to:
  /// **'No preview for {contentType} ({bytes} bytes)'**
  String projectNoPreviewForType(String contentType, String bytes);

  /// No description provided for @projectNoProjects.
  ///
  /// In en, this message translates to:
  /// **'No projects'**
  String get projectNoProjects;

  /// No description provided for @projectPauseProject.
  ///
  /// In en, this message translates to:
  /// **'Pause project'**
  String get projectPauseProject;

  /// No description provided for @projectPausedBanner.
  ///
  /// In en, this message translates to:
  /// **'Project is paused — chat, uploads and agent runs are disabled'**
  String get projectPausedBanner;

  /// No description provided for @projectPausedDisabledHint.
  ///
  /// In en, this message translates to:
  /// **'Chat, uploads and agent runs are disabled while paused'**
  String get projectPausedDisabledHint;

  /// No description provided for @projectPausedListSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Paused — chat, uploads and agent runs disabled'**
  String get projectPausedListSubtitle;

  /// No description provided for @projectPausing.
  ///
  /// In en, this message translates to:
  /// **'Pausing…'**
  String get projectPausing;

  /// No description provided for @projectPdfPreviewUnavailable.
  ///
  /// In en, this message translates to:
  /// **'PDF inline preview is not available yet.\n{bytes} bytes · {contentType}'**
  String projectPdfPreviewUnavailable(String bytes, String contentType);

  /// No description provided for @projectPreferredAgentProvider.
  ///
  /// In en, this message translates to:
  /// **'Preferred agent provider'**
  String get projectPreferredAgentProvider;

  /// No description provided for @projectPreviewTruncated.
  ///
  /// In en, this message translates to:
  /// **'Preview truncated to first 200k characters'**
  String get projectPreviewTruncated;

  /// No description provided for @projectProject.
  ///
  /// In en, this message translates to:
  /// **'Project'**
  String get projectProject;

  /// No description provided for @projectProjectName.
  ///
  /// In en, this message translates to:
  /// **'Project name'**
  String get projectProjectName;

  /// No description provided for @projectProjectPaused.
  ///
  /// In en, this message translates to:
  /// **'Project paused'**
  String get projectProjectPaused;

  /// No description provided for @projectProjectResumed.
  ///
  /// In en, this message translates to:
  /// **'Project resumed'**
  String get projectProjectResumed;

  /// No description provided for @projectProjectSettings.
  ///
  /// In en, this message translates to:
  /// **'Project settings'**
  String get projectProjectSettings;

  /// No description provided for @projectProjectStatus.
  ///
  /// In en, this message translates to:
  /// **'Project status'**
  String get projectProjectStatus;

  /// No description provided for @projectRegenerate.
  ///
  /// In en, this message translates to:
  /// **'Regenerate'**
  String get projectRegenerate;

  /// No description provided for @projectReloadTranscript.
  ///
  /// In en, this message translates to:
  /// **'Reload transcript'**
  String get projectReloadTranscript;

  /// No description provided for @projectRematerializeWorkspace.
  ///
  /// In en, this message translates to:
  /// **'Rematerialize workspace'**
  String get projectRematerializeWorkspace;

  /// No description provided for @projectRematerializedPackages.
  ///
  /// In en, this message translates to:
  /// **'Rematerialized packages: {packages}'**
  String projectRematerializedPackages(String packages);

  /// No description provided for @projectRematerializing.
  ///
  /// In en, this message translates to:
  /// **'Rematerializing…'**
  String get projectRematerializing;

  /// No description provided for @projectResumeProject.
  ///
  /// In en, this message translates to:
  /// **'Resume project'**
  String get projectResumeProject;

  /// No description provided for @projectResuming.
  ///
  /// In en, this message translates to:
  /// **'Resuming…'**
  String get projectResuming;

  /// No description provided for @projectSizeBytes.
  ///
  /// In en, this message translates to:
  /// **'{size} B'**
  String projectSizeBytes(String size);

  /// No description provided for @projectSubscriptionExpiredBanner.
  ///
  /// In en, this message translates to:
  /// **'Company subscription expired — chat and uploads are disabled'**
  String get projectSubscriptionExpiredBanner;

  /// No description provided for @projectToolApprovalHint.
  ///
  /// In en, this message translates to:
  /// **'This tool requires human approval before the agent can continue.'**
  String get projectToolApprovalHint;

  /// No description provided for @projectUploadMissingId.
  ///
  /// In en, this message translates to:
  /// **'Upload missing id/storage_ref'**
  String get projectUploadMissingId;

  /// No description provided for @projectWorking.
  ///
  /// In en, this message translates to:
  /// **'Working…'**
  String get projectWorking;

  /// No description provided for @projectWorkspaceRematerializedNoPackages.
  ///
  /// In en, this message translates to:
  /// **'Workspace rematerialized (no MCP packages)'**
  String get projectWorkspaceRematerializedNoPackages;

  /// No description provided for @settings.
  ///
  /// In en, this message translates to:
  /// **'Settings'**
  String get settings;

  /// No description provided for @settingsLanguage.
  ///
  /// In en, this message translates to:
  /// **'Language'**
  String get settingsLanguage;

  /// No description provided for @settingsLanguageEn.
  ///
  /// In en, this message translates to:
  /// **'English'**
  String get settingsLanguageEn;

  /// No description provided for @settingsLanguageRu.
  ///
  /// In en, this message translates to:
  /// **'Русский'**
  String get settingsLanguageRu;

  /// No description provided for @settingsTheme.
  ///
  /// In en, this message translates to:
  /// **'Theme'**
  String get settingsTheme;

  /// No description provided for @settingsThemeDark.
  ///
  /// In en, this message translates to:
  /// **'Dark'**
  String get settingsThemeDark;

  /// No description provided for @settingsThemeLight.
  ///
  /// In en, this message translates to:
  /// **'Light'**
  String get settingsThemeLight;

  /// No description provided for @settingsThemeUltraDark.
  ///
  /// In en, this message translates to:
  /// **'Ultra dark'**
  String get settingsThemeUltraDark;

  /// No description provided for @settingsRefresh.
  ///
  /// In en, this message translates to:
  /// **'Refresh'**
  String get settingsRefresh;

  /// No description provided for @settingsRefreshOff.
  ///
  /// In en, this message translates to:
  /// **'Off'**
  String get settingsRefreshOff;

  /// No description provided for @settingsRefreshSeconds.
  ///
  /// In en, this message translates to:
  /// **'{n} s'**
  String settingsRefreshSeconds(String n);

  /// No description provided for @settingsRefreshMinutes.
  ///
  /// In en, this message translates to:
  /// **'{n} min'**
  String settingsRefreshMinutes(String n);
}

class _AppLocalizationsDelegate
    extends LocalizationsDelegate<AppLocalizations> {
  const _AppLocalizationsDelegate();

  @override
  Future<AppLocalizations> load(Locale locale) {
    return SynchronousFuture<AppLocalizations>(lookupAppLocalizations(locale));
  }

  @override
  bool isSupported(Locale locale) =>
      <String>['en', 'ru'].contains(locale.languageCode);

  @override
  bool shouldReload(_AppLocalizationsDelegate old) => false;
}

AppLocalizations lookupAppLocalizations(Locale locale) {
  // Lookup logic when only language code is specified.
  switch (locale.languageCode) {
    case 'en':
      return AppLocalizationsEn();
    case 'ru':
      return AppLocalizationsRu();
  }

  throw FlutterError(
    'AppLocalizations.delegate failed to load unsupported locale "$locale". This is likely '
    'an issue with the localizations generation tool. Please file an issue '
    'on GitHub with a reproducible sample app and the gen-l10n configuration '
    'that was used.',
  );
}
