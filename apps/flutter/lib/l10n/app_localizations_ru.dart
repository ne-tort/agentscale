// ignore: unused_import
import 'package:intl/intl.dart' as intl;

import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for Russian (`ru`).
class AppLocalizationsRu extends AppLocalizations {
  AppLocalizationsRu([String locale = 'ru']) : super(locale);

  @override
  String get budgetBenefitBest => 'Лучшая цена';

  @override
  String get budgetBenefitSingle => 'Единственный';

  @override
  String get budgetBenefitSame => 'Одинаковая';

  @override
  String budgetBenefitDiff(Object diff) {
    return '$diff%';
  }

  @override
  String get adminActiveEmployees => 'Активные сотрудники';

  @override
  String get adminEmployeesOnline => 'Сотрудники онлайн';

  @override
  String get adminAdminEmail => 'Email администратора';

  @override
  String get adminAgentMessages => 'AI-Запросы';

  @override
  String get adminAgentPolicySaved => 'Политика агента сохранена';

  @override
  String get adminAgentRuntimePolicy => 'Политика';

  @override
  String get adminAiKeysBound => 'API-ключи';

  @override
  String adminAlertHighUsageTitle(String companyName) {
    return '$companyName: высокий расход токенов агента';
  }

  @override
  String adminAlertKeyRenewalTitle(String companyName) {
    return '$companyName: скоро продление AI-ключа';
  }

  @override
  String adminAlertKeysRenewingSoon(String count) {
    return 'Скоро продление: $count ключ(ей)';
  }

  @override
  String adminAlertKeysRenewingSoonNext(String count, String next) {
    return 'Скоро продление: $count · ближайшее $next';
  }

  @override
  String get adminAlertNoKeysSubtitle => 'Сессии агента завершатся с NO_AI_KEY';

  @override
  String adminAlertNoKeysTitle(String companyName) {
    return '$companyName: нет привязанных AI-ключей';
  }

  @override
  String adminAlertSubExpiredTitle(String companyName) {
    return '$companyName: подписка истекла';
  }

  @override
  String adminAlertSubExpiringTitle(String companyName) {
    return '$companyName: подписка скоро истекает';
  }

  @override
  String get adminAlertSubscriptionEnded => 'Подписка закончилась';

  @override
  String adminAlertSubscriptionEndedAt(String ends) {
    return 'Подписка закончилась · $ends';
  }

  @override
  String get adminAlertSubscriptionEndsSoon => 'Подписка скоро закончится';

  @override
  String adminAlertSubscriptionEndsSoonAt(String ends) {
    return 'Подписка скоро закончится · $ends';
  }

  @override
  String adminAlertTokensAboveThreshold(String tokens) {
    return 'Токены агента $tokens выше порога платформы';
  }

  @override
  String get adminAlerts => 'Предупреждения';

  @override
  String get adminApiKind => 'Тип API';

  @override
  String adminApiKindValue(String api_kind) {
    return 'Тип API: $api_kind';
  }

  @override
  String get adminBindCompanies => 'Привязать компании';

  @override
  String adminBindingsSelected(String count) {
    return 'Выбрано: $count';
  }

  @override
  String get adminBundle => 'Бандл';

  @override
  String get adminCabinetQuotas => 'Квоты';

  @override
  String get adminCabinetQuotasMustBePositive =>
      'Квоты кабинетов — целые числа больше нуля';

  @override
  String adminCabinetsProjectsSubtitle(String cabinets, String projects) {
    return '$cabinets кабинетов · $projects проектов';
  }

  @override
  String adminCabinetsQuotaCell(String active, String max) {
    return '$active / $max кабинетов';
  }

  @override
  String get adminCompanyAdminInvite => 'Приглашение admin компании';

  @override
  String get adminCompanyBindings => 'Привязки компаний';

  @override
  String get adminCompanyName => 'Название компании';

  @override
  String get adminAddAiKey => 'Добавить ключ';

  @override
  String get adminAddCompany => 'Добавить компанию';

  @override
  String get adminCompanyGeneral => 'Общее';

  @override
  String get adminEvents => 'События';

  @override
  String get adminInviteAdmin => 'Админ';

  @override
  String get adminPolicy => 'Политика';

  @override
  String get adminAgentLimits => 'Ограничения агента';

  @override
  String get adminQuotas => 'Квоты';

  @override
  String adminQuotasSummary(String cabinets, String packages, String bundleMb) {
    return '$cabinets · $packages · $bundleMb';
  }

  @override
  String get adminCreateAiKey => 'Создать AI-ключ';

  @override
  String get adminCreateCompany => 'Создать компанию';

  @override
  String get adminCreateCompanyAndInviteAdmin =>
      'Создайте компанию и пригласите company.admin';

  @override
  String get adminCreateCompanyToSeeMetrics =>
      'Создайте компанию, чтобы видеть метрики';

  @override
  String get adminCreateKey => 'Создать ключ';

  @override
  String get adminCreateRuntimeKeyHint =>
      'Добавьте ключ и настройте на странице';

  @override
  String get adminDisableAiKey => 'Приостановить AI-ключ';

  @override
  String get adminDisableKey => 'Приостановить ключ';

  @override
  String get adminResumeKey => 'Возобновить';

  @override
  String adminDisableKeyConfirm(String keyName) {
    return 'Приостановить ключ? Связанные проекты будут приостановлены';
  }

  @override
  String get adminDrainProjectTriggers => 'Триггеры';

  @override
  String adminDrainedTriggers(String count) {
    return 'Сброшено триггеров: $count';
  }

  @override
  String get adminDraining => 'Сброс…';

  @override
  String get adminEditBindings => 'Изменить привязки';

  @override
  String get adminEndsAt => 'Подписка';

  @override
  String get adminDateFormatHint => 'ДД.ММ.ГГ';

  @override
  String get adminInvalidDate => 'Формат: ДД.ММ.ГГ или ДД.ММ.ГГГГ';

  @override
  String get adminEndsAtIfNotLifetime => 'Подписка (если не бессрочная)';

  @override
  String get adminSubscription => 'Подписка';

  @override
  String adminEnterIntegerMin(String min) {
    return 'Введите целое ≥ $min';
  }

  @override
  String get adminIdlePauseAfterHours => 'Пауза после простоя';

  @override
  String adminIdlePausedProjectsInCompany(String count) {
    return 'На паузу по простою: $count проект(ов) в компании';
  }

  @override
  String get adminInviteCompanyAdminViaKeycloak =>
      'Приглашение company.admin через Keycloak — пароль не принимается.';

  @override
  String get adminKey => 'Ключ';

  @override
  String get adminKeyInfo => 'О ключе';

  @override
  String adminKeyListSubtitle(String type, String provider) {
    return '$type · $provider';
  }

  @override
  String get adminIntegrationType => 'Тип';

  @override
  String get adminTypeCursorSdk => 'Cursor SDK';

  @override
  String get adminTypeCodexSdk => 'Codex SDK';

  @override
  String get adminTypeClaudeSdk => 'Claude Agent SDK';

  @override
  String get adminTypeApiKey => 'API key';

  @override
  String get adminTypeGrokOauth => 'Grok (xAI OAuth)';

  @override
  String get aiKeyGrokAuthTitle => 'Авторизация Grok';

  @override
  String get aiKeyGrokAuthSubtitle => 'Подписка SuperGrok — вход по ссылке';

  @override
  String get aiKeyGrokAuthHint =>
      'Откройте ссылку, войдите в аккаунт SuperGrok и подтвердите код. Токены хранятся на сервере и обновляются автоматически.';

  @override
  String get aiKeyGrokAuthOpenLink => 'Открыть страницу авторизации';

  @override
  String get aiKeyGrokAuthCode => 'Код подтверждения';

  @override
  String get aiKeyGrokAuthWaiting => 'Ожидаем подтверждения…';

  @override
  String get aiKeyGrokAuthDone => 'Авторизация выполнена';

  @override
  String get aiKeyGrokAuthDenied => 'Доступ запрещён';

  @override
  String get aiKeyGrokAuthExpired => 'Код истёк — начните заново';

  @override
  String get aiKeyGrokAuthError => 'Ошибка авторизации';

  @override
  String get aiKeyGrokAuthRestart => 'Начать заново';

  @override
  String get adminNextRenewal => 'Подписка';

  @override
  String get adminAddHttpProvider => 'Добавить HTTP endpoint';

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
  String get adminHttpAuthNone => 'Без auth';

  @override
  String get adminHttpChatPath => 'Chat completions path';

  @override
  String get adminHttpModelsPath => 'Models path';

  @override
  String get adminToolPresetChatReadonly => 'Чат (только чтение)';

  @override
  String get adminToolPresetWorkspaceDev => 'Разработка';

  @override
  String get adminToolPresetWorkspaceFull => 'Полный доступ';

  @override
  String get adminIdlePauseNever => 'Без остановки';

  @override
  String adminIdlePauseHours(String hours) {
    return '$hours ч';
  }

  @override
  String get adminAlertTag => 'Тег';

  @override
  String get adminAlertTagNoKeys => 'нет ключей';

  @override
  String adminAlertTagKeyRenewal(int count) {
    return 'продление · $count';
  }

  @override
  String get adminAlertTagHighUsage => 'высокий usage';

  @override
  String get adminAlertTagSubExpiring => 'подписка';

  @override
  String get adminAlertTagSubExpired => 'истекла';

  @override
  String get adminAlertTagIdentityUnbound => 'IDENTITY_UNBOUND';

  @override
  String adminAlertTagEmployeesUnbound(int count) {
    return 'EMPLOYEES_UNBOUND · $count';
  }

  @override
  String get adminAlertTagCascadeIncomplete => 'CASCADE_INCOMPLETE';

  @override
  String get commonPhone => 'Телефон';

  @override
  String get adminKeyMetadata => 'Метаданные ключа';

  @override
  String get adminLifecycle => 'Жизненный цикл';

  @override
  String get adminLifetimeSubscription => 'Бессрочная подписка';

  @override
  String get adminMaxAgentTokensMonth => 'Токены агента / месяц';

  @override
  String get adminMaxBundleImportMb => 'Бандл';

  @override
  String get adminMaxCabinets => 'Кабинеты';

  @override
  String get adminMaxChatAttachmentMb => 'Вложение в чат';

  @override
  String get adminMaxPackagesPerCabinet => 'Пакеты';

  @override
  String get adminMaxTokensPerRun => 'Токены за запуск';

  @override
  String get adminMaxUsdCostMonth => 'Стоимость USD / месяц';

  @override
  String get adminMetadataOnly => 'только метаданные';

  @override
  String get adminMetrics => 'Метрики';

  @override
  String adminMetricsAiKeysRenewSoon(String count) {
    return 'Скоро продление AI-ключей: $count';
  }

  @override
  String adminMetricsAiKeysRenewSoonNext(String count, String next) {
    return 'Скоро продление AI-ключей: $count · ближайшее $next';
  }

  @override
  String adminMetricsHighTokenUsage(String tokens) {
    return 'Высокий расход токенов агента ($tokens)';
  }

  @override
  String get adminMetricsNoAiKeysBound =>
      'Нет AI-ключей — агент вернёт NO_AI_KEY';

  @override
  String get adminMetricsSubscriptionExpired => 'Подписка истекла';

  @override
  String adminMetricsSubscriptionExpiring(String ends) {
    return 'Подписка истекает · $ends';
  }

  @override
  String get adminMetricsIdentityUnbound => 'Ожидание сервиса аутентификации';

  @override
  String adminMetricsEmployeesUnbound(String count) {
    return 'Ожидание сервиса аутентификации · сотрудников: $count';
  }

  @override
  String get adminModelAllowlist => 'Allowlist моделей';

  @override
  String get adminNewSecret => 'Новый секрет';

  @override
  String adminNextRenewalValue(String next_renewal_at) {
    return 'Следующее продление: $next_renewal_at';
  }

  @override
  String get adminNoAiKeys => 'Нет AI-ключей';

  @override
  String get adminNoCompanies => 'Нет компаний';

  @override
  String get adminNoContainers => 'Нет проектов';

  @override
  String get adminNoCabinets => 'Нет кабинетов';

  @override
  String get adminAddCabinet => 'Добавить кабинет';

  @override
  String get adminNoModules => 'Нет модулей';

  @override
  String get adminAddModule => 'Добавить модуль';

  @override
  String adminDeleteModuleConfirm(String name) {
    return 'Удалить модуль «$name»? Будут удалены meta и привязки; кабинеты и проекты не затрагиваются.';
  }

  @override
  String get adminModuleCopied => 'ID модуля скопирован';

  @override
  String adminModuleCabinetsCount(int count) {
    return '$count кабинетов';
  }

  @override
  String get adminModuleJson => 'JSON';

  @override
  String get adminModuleJsonConfigured => 'Настроено';

  @override
  String get adminModulePreview => 'Предзаполнение';

  @override
  String get adminModulePreviewEmpty =>
      'Добавьте JSON модуля для предзаполнения';

  @override
  String get adminModuleJsonInvalid => 'Неверный JSON';

  @override
  String get adminMetaInvalid => 'Метаданные';

  @override
  String metaAddNew(String item) {
    return 'Добавить новый $item';
  }

  @override
  String get adminModulePreviewMode => 'Предзаполнение';

  @override
  String get adminModulePreviewShellNav => 'Навигация shell (предпросмотр)';

  @override
  String get adminModulePreviewCabinetTabs => 'Вкладки кабинета';

  @override
  String get adminModuleSeedEmpty => 'Нет предзаполненных строк';

  @override
  String get adminSelectCabinetsForModule => 'Выберите кабинеты для модуля';

  @override
  String get adminSelectModulesForCabinet => 'Выберите модули для кабинета';

  @override
  String get adminGrantAllCompanies => 'Все компании';

  @override
  String get adminPromptProfiles => 'Профили промптов';

  @override
  String get adminSelectCompaniesForModule => 'Выберите компании для модуля';

  @override
  String adminDeleteCabinetConfirm(String name) {
    return 'Удалить кабинет «$name»? Будут удалены все проекты и схема кабинета.';
  }

  @override
  String get adminCabinetCopied => 'ID кабинета скопирован';

  @override
  String get adminCabinetOwnerScope => 'Владение';

  @override
  String adminCabinetCompaniesCount(int count) {
    return '$count компаний';
  }

  @override
  String get adminSelectCompanyForCabinet => 'Выберите компанию';

  @override
  String get cabinetOpenedPlaceholder =>
      'Кабинет открыт. Meta UI вернётся в следующей итерации.';

  @override
  String get adminContainerColProject => 'Проект';

  @override
  String get adminContainerColStatus => 'Статус';

  @override
  String get adminContainerColCompany => 'Компания';

  @override
  String get adminContainerColEmployee => 'Сотрудник';

  @override
  String get adminContainerColProvider => 'Провайдер';

  @override
  String get adminContainerColCabinet => 'Кабинет';

  @override
  String get adminContainerStatusActive => 'Активен';

  @override
  String get adminContainerStatusPaused => 'Пауза';

  @override
  String get adminContainerStatusDraft => 'Черновик';

  @override
  String get adminContainerRuntimeRef => 'Runtime ref';

  @override
  String get adminContainerMetricsHole => 'K8s метрики';

  @override
  String get adminContainerK8sPhase => 'Фаза k8s';

  @override
  String get adminContainerPodStatus => 'Статус pod';

  @override
  String get adminContainerDesiredState => 'Желаемое состояние';

  @override
  String get adminContainerPodReadyTrue => 'Pod готов';

  @override
  String get adminContainerPodReadyFalse => 'Pod не готов';

  @override
  String get adminContainerPodRestarts => 'Перезапуски';

  @override
  String get adminContainerMetricsCpu => 'CPU';

  @override
  String get adminContainerMetricsMemory => 'Память';

  @override
  String get adminContainerMetricsUnavailable =>
      'Метрики ещё не получены или недоступны';

  @override
  String get adminContainerLastError => 'Ошибка';

  @override
  String get adminContainerRuntimeNotStarted =>
      'Pod не запущен. Стартует при первом обращении к агенту или после «Возобновить».';

  @override
  String get adminContainerRuntimeNotStartedShort => 'Не запущен';

  @override
  String get adminContainerRuntimePaused => 'Pod остановлен — проект на паузе.';

  @override
  String get adminContainerRuntimePausedShort => 'Остановлен';

  @override
  String get adminContainerRuntimeAttention =>
      'Проект активен, но pod ещё не создан в k8s. Откройте агент или нажмите «Возобновить».';

  @override
  String get adminContainerColK8s => 'K8s';

  @override
  String get containerObservedStateLabel => 'Состояние';

  @override
  String get containerStateLabel => 'Состояние';

  @override
  String get containerRuntimeLabel => 'Рантайм';

  @override
  String get containerLaunchTypeWarm => 'Тёплый старт';

  @override
  String get containerLaunchTypeCold => 'Холодный старт';

  @override
  String get containerCreatedAt => 'Создан';

  @override
  String get containerLastLaunch => 'Последний запуск';

  @override
  String get containerPodServiceId => 'Pod ID';

  @override
  String get containerPodIdCopied => 'Pod ID скопирован';

  @override
  String get containerKubId => 'Kub ID';

  @override
  String get containerKubIdCopied => 'Kub ID скопирован';

  @override
  String get projectBudgetLabel => 'Бюджет';

  @override
  String get containerStartedAt => 'Последний запуск';

  @override
  String get containerUptime => 'Время работы';

  @override
  String get containerRestarts => 'Перезапуски';

  @override
  String containerDurationDays(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count д',
      many: '$count д',
      few: '$count д',
      one: '$count д',
    );
    return '$_temp0';
  }

  @override
  String containerDurationHours(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count ч',
      many: '$count ч',
      few: '$count ч',
      one: '$count ч',
    );
    return '$_temp0';
  }

  @override
  String containerDurationMinutes(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count мин',
      many: '$count мин',
      few: '$count мин',
      one: '$count мин',
    );
    return '$_temp0';
  }

  @override
  String get containerObservedPreparing => 'Подготовка';

  @override
  String get containerObservedProvisioning => 'Создание';

  @override
  String get containerObservedPulling => 'Скачивание образа';

  @override
  String get containerObservedHydrating => 'Загрузка файлов';

  @override
  String get containerObservedStarting => 'Запуск';

  @override
  String get containerObservedRunning => 'Запущен';

  @override
  String get containerObservedDegraded => 'Сбой метрик';

  @override
  String get containerObservedFailed => 'Ошибка';

  @override
  String get containerObservedPaused => 'На паузе';

  @override
  String get containerObservedAbsent => 'Не создан';

  @override
  String get containerObservedUnknown => 'Неизвестно';

  @override
  String get containerObservedResuming => 'Пробуждение…';

  @override
  String get containerObservedSuspended => 'Приостановлен (данные сохранены)';

  @override
  String get containerObservedPausing => 'Приостанавливается';

  @override
  String get containerPollTimeout => 'Не удалось дождаться готовности агента';

  @override
  String get containerMetricsAwaiting =>
      'CPU/RAM: ожидание первого sample метрик';

  @override
  String get containerErrorCopied => 'Ошибка скопирована в буфер обмена';

  @override
  String get adminContainerOrchestratorStatus => 'Статус оркестратора';

  @override
  String adminDeleteContainerConfirm(String name) {
    return 'Удалить проект «$name»? Workspace будет очищен.';
  }

  @override
  String adminDeleteCompanyConfirm(String name) {
    return 'Удалить компанию «$name»? Будут отключены сотрудники, приостановлены и удалены проекты (wipe workspace), кабинеты удалятся навсегда.';
  }

  @override
  String adminDisableAiKeyConfirm(String name) {
    return 'Приостановить ключ? Связанные проекты будут приостановлены';
  }

  @override
  String get adminNoCompaniesBound => 'Компании не привязаны';

  @override
  String get adminNoCompaniesYet => 'Компаний пока нет';

  @override
  String get adminNoPlatformEventsYet => 'Событий платформы пока нет';

  @override
  String get adminNoStarterBundles => 'Нет стартовых бандлов';

  @override
  String get adminPlatformEvents => 'События';

  @override
  String get adminPlatformFallback => 'Fallback платформы';

  @override
  String adminPlatformIdleSweep(String count, String companies) {
    return 'Пауза по простою: $count проект(ов) ($companies компаний с политикой)';
  }

  @override
  String get adminPlatformTotals => 'Метрики';

  @override
  String adminCompanyCabinetsRunning(String running, String quota) {
    return '$running / $quota';
  }

  @override
  String get adminPreferredProvider => 'Провайдер';

  @override
  String get adminPreferredProviderOptional => 'Провайдер';

  @override
  String get adminProdavanSubscription => 'Подписка Agentscale';

  @override
  String get adminProdavanSubscriptionOptional => 'Подписка Agentscale';

  @override
  String adminProviderValue(String provider) {
    return 'Провайдер: $provider';
  }

  @override
  String get adminQuotasSaved => 'Квоты сохранены';

  @override
  String get adminRenewPlusOneMonth => 'Продлить +1 месяц';

  @override
  String get adminRenewing => 'Продление…';

  @override
  String adminRotateKeyTitle(String keyName) {
    return 'Смена секрета: $keyName';
  }

  @override
  String get adminRotateSecret => 'Сменить секрет';

  @override
  String get adminRotateSecretHint =>
      'Новый секрет заменит сохранённый. Старый будет удалён из file store.';

  @override
  String get adminRotating => 'Смена…';

  @override
  String get adminSaveAgentPolicy => 'Сохранить политику агента';

  @override
  String get adminSaveQuotas => 'Сохранить квоты';

  @override
  String get adminSaveSubscription => 'Сохранить подписку';

  @override
  String adminSecretRefValue(String secret_ref_prefix) {
    return 'Secret ref: $secret_ref_prefix';
  }

  @override
  String get adminSecretRequired => 'Укажите секрет';

  @override
  String get adminSecretStoredServerSide => 'Секрет хранится только на сервере';

  @override
  String get adminSetEndDateOrLifetime =>
      'Укажите дату окончания или бессрочную подписку';

  @override
  String get adminShipped => 'поставлен';

  @override
  String get adminStarterBundles => 'Стартовые бандлы';

  @override
  String get adminStarterBundlesHint =>
      'Записи каталога появляются при поставке в data/starter_bundles/';

  @override
  String adminStatusValue(String status) {
    return 'Статус: $status';
  }

  @override
  String get adminSubscriptionSaved => 'Подписка сохранена';

  @override
  String get adminSweepIdlePause => 'Простой';

  @override
  String get adminSweepIdlePauseAll => 'Простой · все';

  @override
  String get adminSweeping => 'Выполнение…';

  @override
  String get adminTelegramHmacConfigured =>
      'Telegram HMAC: задан (оставьте пустым, чтобы не менять)';

  @override
  String get adminTelegramHmacNotSet => 'Telegram HMAC: не задан';

  @override
  String get adminTelegramHmacSecret => 'Telegram HMAC';

  @override
  String get adminTokens => 'Токены';

  @override
  String get adminToolPreset => 'Пресет инструментов';

  @override
  String get adminWebhookHmacConfigured =>
      'Webhook HMAC: задан (оставьте пустым, чтобы не менять)';

  @override
  String get adminWebhookHmacNotSet => 'Webhook HMAC: не задан';

  @override
  String get adminWebhookHmacSecret => 'Webhook HMAC';

  @override
  String get authAdvanced => 'Дополнительно';

  @override
  String get authBearerAccessToken => 'Bearer access token';

  @override
  String get authBearerAccessTokenPaste => 'Bearer access token (вставить)';

  @override
  String authConfigUnavailableTestMode(String e) {
    return 'Конфиг auth недоступен — тестовый режим. $e';
  }

  @override
  String get authConnecting => 'Подключение…';

  @override
  String get authContinueAsDemoEmployee => 'Войти как Demo Employee';

  @override
  String get authContinueAsPlatformAdmin => 'Войти как Platform Admin';

  @override
  String get authContinueWithToken => 'Продолжить с токеном';

  @override
  String get authDevTestModeHint =>
      'Тестовый режим — вход в один клик (без Keycloak)';

  @override
  String get authHideAdvanced => 'Скрыть дополнительно';

  @override
  String get authNoAccessTokenInTestLogin =>
      'В ответе test login нет access_token';

  @override
  String get authOidcModeHint =>
      'OIDC — PKCE через Keycloak (AppAuth / loopback на десктопе)';

  @override
  String get authOpeningLogin => 'Открываем вход…';

  @override
  String get authReloadAuthConfig => 'Обновить конфиг auth';

  @override
  String get authSignIn => 'Вход';

  @override
  String get authLogin => 'Логин';

  @override
  String get authPassword => 'Пароль';

  @override
  String get authShowPassword => 'Показать пароль';

  @override
  String get authHidePassword => 'Скрыть пароль';

  @override
  String get authSignInWithKeycloak => 'Войти через Keycloak';

  @override
  String get authSigningIn => 'Входим…';

  @override
  String get authSignOut => 'Выйти';

  @override
  String get cabinetAddAtLeastOneColumn => 'Добавьте хотя бы одну колонку';

  @override
  String get cabinetAddColumn => 'Добавить колонку';

  @override
  String get cabinetAddRow => 'Добавить строку';

  @override
  String get cabinetAgentsInstructions => 'Инструкции для агентов';

  @override
  String get cabinetAgentsMd => 'AGENTS.md';

  @override
  String get cabinetAgentsMdHint =>
      'Пишется в workspace проекта при materialize (AGENTS.md). Пересоздайте проекты, чтобы применить.';

  @override
  String cabinetModuleRematerializeScheduled(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count проектов',
      many: '$count проектов',
      few: '$count проектов',
      one: '$count проекта',
    );
    return 'Обновляем workspace $_temp0 в фоне…';
  }

  @override
  String cabinetModuleRematerializeDone(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count проектов',
      many: '$count проектов',
      few: '$count проекта',
      one: '$count проект',
    );
    return 'Workspace обновлён: $_temp0';
  }

  @override
  String get cabinetModuleWorkspaceOutdated =>
      'Изменения модуля сохранены — нажмите «Обновить проект» на каждом запущенном проекте';

  @override
  String get cabinetArchiveTable => 'В архив';

  @override
  String get cabinetArchiveTableConfirm => 'Отправить таблицу в архив?';

  @override
  String cabinetArchiveTableMessage(String tableSlug) {
    return 'Архивировать «$tableSlug». Строки останутся в БД, таблица скрывается из списков. Сначала уберите представления с этой таблицей.';
  }

  @override
  String get cabinetArchivedBanner =>
      'В архиве — можно удалить навсегда или вернуться назад.';

  @override
  String get cabinetArchiving => 'Архивация…';

  @override
  String get cabinetAuditLog => 'Журнал аудита';

  @override
  String get cabinetAuditRecent => 'Аудит (недавние)';

  @override
  String get cabinetCabinetName => 'Название кабинета';

  @override
  String get cabinetChooseZipFile => 'Выбрать .zip';

  @override
  String get cabinetColumnName => 'Имя колонки';

  @override
  String get cabinetColumnSettings => 'Настройки колонки';

  @override
  String cabinetColumnTypeChip(String name, String type) {
    return '$name · $type';
  }

  @override
  String get cabinetColumns => 'Колонки';

  @override
  String get cabinetContextHint =>
      'Вкладка «Проекты» открывает workspace агента. Таблицы и Tools — runtime-данные кабинета.';

  @override
  String cabinetContextStatusLine(String status, String companyId) {
    return 'Статус: $status · Компания $companyId';
  }

  @override
  String get cabinetCouldNotReadZipBytes => 'Не удалось прочитать zip';

  @override
  String get cabinetCreateBaseCabinetHint =>
      'Создайте базовый кабинет, чтобы начать';

  @override
  String get cabinetCreateCabinet => 'Создать кабинет';

  @override
  String get cabinetCreateMetaTableFirst =>
      'Сначала создайте мета-таблицу (Таблицы → Новая таблица).';

  @override
  String get cabinetCreateTab => 'Создать вкладку';

  @override
  String get cabinetCreateTable => 'Создать таблицу';

  @override
  String get cabinetCustomTabs => 'Пользовательские вкладки';

  @override
  String get cabinetCustomTabsHint =>
      'После создания вкладки появятся в оболочке кабинета. Системные вкладки здесь не удаляются.';

  @override
  String get cabinetDeleteColumn => 'Удалить колонку';

  @override
  String get cabinetDeleteColumnConfirm => 'Удалить колонку?';

  @override
  String get cabinetDeletePermanently => 'Удалить навсегда';

  @override
  String get cabinetDeleteRow => 'Удалить строку?';

  @override
  String cabinetDeleteRowPermanently(String rowId) {
    return 'Удалить строку $rowId навсегда.';
  }

  @override
  String get cabinetDeleteTab => 'Удалить вкладку?';

  @override
  String get cabinetDeleteTablePermanently => 'Удалить таблицу навсегда?';

  @override
  String cabinetDropAllDataConfirm(String tableSlug) {
    return 'Удалить все данные «$tableSlug». Это необратимо.';
  }

  @override
  String get cabinetEditAgentsMd => 'Редактировать AGENTS.md';

  @override
  String get cabinetEditRow => 'Редактировать строку';

  @override
  String get cabinetEmptyBundleExport => 'Пустой экспорт бандла';

  @override
  String get cabinetEnterAtLeastOneField => 'Заполните хотя бы одно поле';

  @override
  String get cabinetExportCabinetBundle => 'Экспорт бандла кабинета';

  @override
  String cabinetExportReady(String bytes) {
    return 'Экспорт готов ($bytes байт)';
  }

  @override
  String get cabinetExporting => 'Экспорт…';

  @override
  String get cabinetFromFile => 'Из файла';

  @override
  String get cabinetImportBundleIntro =>
      'Импорт zip cabinet.bundle из другого кабинета. Создаётся новый кабинет со своей схемой.';

  @override
  String get cabinetImportBundleTooltip => 'Импорт бандла';

  @override
  String get cabinetImportCabinetBundle => 'Импорт бандла кабинета';

  @override
  String get cabinetImportedCabinetDefault => 'Импортированный кабинет';

  @override
  String get cabinetImporting => 'Импорт…';

  @override
  String get cabinetLettersDigitsUnderscore => 'Буквы, цифры и подчёркивание';

  @override
  String get cabinetLowercaseSlugRule =>
      'Строчные латинские буквы, цифры, подчёркивание';

  @override
  String get cabinetManageCustomTabs => 'Пользовательские вкладки';

  @override
  String get cabinetMetaTables => 'Мета-таблицы';

  @override
  String get cabinetMyCabinetDefault => 'Мой кабинет';

  @override
  String get cabinetNewCabinet => 'Новый кабинет';

  @override
  String get cabinetNewCustomTab => 'Новая пользовательская вкладка';

  @override
  String get cabinetNewTab => 'Новая вкладка';

  @override
  String get cabinetNewTable => 'Новая таблица';

  @override
  String get cabinetNoAuditEventsYet => 'Событий аудита пока нет.';

  @override
  String get cabinetNoCompanyIdFromMe => 'Нет company_id в memberships /me';

  @override
  String get cabinetNoCustomTabsYet => 'Пользовательских вкладок пока нет.';

  @override
  String cabinetNoInterpreterForView(String slug) {
    return 'Нет интерпретатора для представления «$slug».';
  }

  @override
  String get cabinetNoMetaTablesYet => 'В этом кабинете ещё нет мета-таблиц.';

  @override
  String get cabinetNoRows => 'Нет строк';

  @override
  String get cabinetNotShipped => 'не поставлен';

  @override
  String get cabinetOfficialStarterBundles => 'Официальные стартовые бандлы';

  @override
  String cabinetRemoveColumnData(String columnName) {
    return 'Удалить колонку «$columnName» и её данные.';
  }

  @override
  String cabinetRemoveTabAndView(String title) {
    return 'Удалить вкладку «$title» и её представление.';
  }

  @override
  String get cabinetReservedName => 'Зарезервированное имя';

  @override
  String cabinetRowFallback(String index) {
    return 'Строка $index';
  }

  @override
  String get cabinetSaveLabel => 'Сохранить подпись';

  @override
  String get cabinetSaveRow => 'Сохранить строку';

  @override
  String get cabinetSaveView => 'Сохранить представление';

  @override
  String cabinetSavedTo(String path) {
    return 'Сохранено: $path';
  }

  @override
  String get cabinetSelectATable => 'Выберите таблицу';

  @override
  String get cabinetSelectCabinetBundleZip => 'Выберите zip cabinet.bundle';

  @override
  String get cabinetSelectTableToPreview =>
      'Выберите таблицу для просмотра строк';

  @override
  String get cabinetSlug => 'Slug';

  @override
  String cabinetStatusCompanyLine(String status, String companyId) {
    return 'Статус: $status · Компания $companyId';
  }

  @override
  String get cabinetStorage => 'Хранение';

  @override
  String get cabinetStorageJsonDocument => 'json_document';

  @override
  String get cabinetStoragePhysical => 'physical';

  @override
  String get cabinetSystemColumnReadOnly =>
      'Системная колонка — только чтение.';

  @override
  String get cabinetTabFallback => 'Вкладка';

  @override
  String get cabinetTabOrder => 'Порядок вкладки';

  @override
  String cabinetTabSubtitle(String viewSlug, String tableSlug) {
    return 'view: $viewSlug · table: $tableSlug';
  }

  @override
  String get cabinetTabTitle => 'Заголовок вкладки';

  @override
  String get cabinetTableSettings => 'Настройки таблицы';

  @override
  String get cabinetTitleFieldColumnName => 'Поле заголовка (имя колонки)';

  @override
  String get cabinetType => 'Тип';

  @override
  String get cabinetUnique => 'Уникальная';

  @override
  String get cabinetUnknownTabNoViewSlug =>
      'Неизвестная вкладка — нет view_slug в meta.';

  @override
  String get cabinetViewNotFound => 'Представление не найдено';

  @override
  String get cabinetViewSlug => 'Slug представления';

  @override
  String cabinetViewTitle(String viewSlug) {
    return 'Представление $viewSlug';
  }

  @override
  String get commonAdd => 'Добавить';

  @override
  String get commonAdding => 'Добавление…';

  @override
  String get commonAgentTokens => 'Токены';

  @override
  String get commonAgentRequests => 'Запросы';

  @override
  String get commonId => 'ID';

  @override
  String get moduleBindKind => 'Связь';

  @override
  String get moduleBindLocal => 'Локальная';

  @override
  String get moduleBindGlobal => 'Глобальная';

  @override
  String get commonApiBaseUrl => 'Базовый URL API';

  @override
  String get commonArchive => 'В архив';

  @override
  String get commonCabinets => 'Кабинеты';

  @override
  String get commonCancel => 'Отмена';

  @override
  String get commonRetry => 'Повторить';

  @override
  String get commonCompany => 'Компания';

  @override
  String get commonCompanies => 'Компании';

  @override
  String get commonDescription => 'Описание';

  @override
  String get commonEntity => 'Название';

  @override
  String get commonContinueAction => 'Продолжить';

  @override
  String get commonSendAction => 'Отправить';

  @override
  String get commonCreate => 'Создать';

  @override
  String get commonCreating => 'Создание…';

  @override
  String get commonDelete => 'Удалить';

  @override
  String get commonRemove => 'Убрать';

  @override
  String get commonCopy => 'Копировать';

  @override
  String get commonEdit => 'Изменить';

  @override
  String get commonDeleting => 'Удаление…';

  @override
  String get commonDisable => 'Отключить';

  @override
  String get commonDisplayNameOptional => 'Отображаемое имя';

  @override
  String get commonMbUnit => 'Мб';

  @override
  String get commonNotSet => 'Не задан';

  @override
  String get commonOff => 'Выкл.';

  @override
  String get commonOnline => 'Онлайн';

  @override
  String get commonOffline => 'Оффлайн';

  @override
  String get commonUnlimited => 'Бессрочно';

  @override
  String adminBindingsCount(int count) {
    return '$count компаний';
  }

  @override
  String get commonDone => 'Готово';

  @override
  String get commonEmDash => '—';

  @override
  String get commonEmail => 'Email';

  @override
  String get commonEmployees => 'Сотрудники';

  @override
  String get commonEmpty => 'Пусто';

  @override
  String get commonFilter => 'Фильтр';

  @override
  String get commonImport => 'Импорт';

  @override
  String get commonInvite => 'Пригласить';

  @override
  String get commonLabel => 'Подпись';

  @override
  String get commonLastActivity => 'Последняя активность';

  @override
  String get commonList => 'Список';

  @override
  String get commonName => 'Название';

  @override
  String get commonNameRequired => 'Укажите название';

  @override
  String get commonSize => 'Размер';

  @override
  String get commonNone => 'Нет';

  @override
  String get commonNothingFound => 'Ничего не найдено';

  @override
  String get commonOverview => 'Обзор';

  @override
  String get commonPositiveInteger => 'Целое число больше нуля';

  @override
  String get commonProjects => 'Проекты';

  @override
  String get commonProvider => 'Провайдер';

  @override
  String get commonReload => 'Обновить';

  @override
  String get commonRequired => 'Обязательно';

  @override
  String get commonResume => 'Возобновить';

  @override
  String get commonSave => 'Сохранить';

  @override
  String get commonSaving => 'Сохранение…';

  @override
  String get commonSearch => 'Поиск';

  @override
  String get commonSecret => 'Секрет';

  @override
  String get commonSelect => 'Выбрать';

  @override
  String get commonSelectCompany => 'Выберите компанию';

  @override
  String get commonStatus => 'Статус';

  @override
  String get commonStorageBytes => 'Хранилище';

  @override
  String get commonTable => 'Таблица';

  @override
  String get commonTitle => 'Заголовок';

  @override
  String get commonUnknown => 'неизвестно';

  @override
  String get companyActive => 'Активен';

  @override
  String get companyCabinet => 'Кабинет';

  @override
  String get companyCabinetsEmptyHint =>
      'Кабинеты создают сотрудники в контуре Employee';

  @override
  String get companyDisableEmployee => 'Отключить сотрудника';

  @override
  String companyDisableEmployeeConfirm(String email) {
    return 'Отключить $email? Доступ будет закрыт.';
  }

  @override
  String get companyAddEmployee => 'Добавить сотрудника';

  @override
  String get companyAddCabinet => 'Добавить кабинет';

  @override
  String get companyPauseEmployee => 'Приостановить сотрудника';

  @override
  String companyPauseEmployeeConfirm(String login) {
    return 'Приостановить $login? Доступ будет закрыт до включения.';
  }

  @override
  String get companyEnableEmployee => 'Включить сотрудника';

  @override
  String companyEnableEmployeeConfirm(String login) {
    return 'Включить $login? Доступ будет восстановлен.';
  }

  @override
  String get companyInviteEmployee => 'Пригласить сотрудника';

  @override
  String get companyInviteViaKeycloakNoPassword =>
      'Приглашение через Keycloak — без поля пароля';

  @override
  String get companyInviteViaKeycloakPasswordNotAccepted =>
      'Приглашение через Keycloak — пароль здесь не принимается.';

  @override
  String get companyInviting => 'Приглашение…';

  @override
  String get companyNoCabinets => 'Нет кабинетов';

  @override
  String get companyAssignEmployeeToCabinet => 'Назначить сотрудника';

  @override
  String get companyAssignedEmployees => 'Назначены';

  @override
  String get companyNoAssignedEmployees => 'Сотрудники не назначены';

  @override
  String get companyAssignCabinetsToEmployee => 'Доступ к кабинетам';

  @override
  String get companyNoEmployees => 'Нет сотрудников';

  @override
  String get companyOrgMetrics => 'Метрики организации';

  @override
  String get companyLoginId => 'ID компании';

  @override
  String get companyLogin => 'Логин';

  @override
  String get credentialsInClipboard => 'Логин и пароль в буфере обмена';

  @override
  String get companyIdCopied => 'ID компании скопирован';

  @override
  String get companyPassword => 'Пароль';

  @override
  String get companyPasswordHint => 'Минимум 8 символов';

  @override
  String get companyPasswordChanged => 'Пароль успешно изменен';

  @override
  String get companyAddAiKey => 'Добавить ключ';

  @override
  String get companyNoAiKeys => 'Нет AI-ключей';

  @override
  String get companyKeyPlatformBound => 'От платформы';

  @override
  String get companyKeySourceLocal => 'Свой';

  @override
  String get companyAddModule => 'Добавить модуль';

  @override
  String get companyNoModules => 'Нет модулей';

  @override
  String get companyModulesEmptyHint =>
      'Создайте свой модуль или дождитесь назначения от платформы';

  @override
  String get companyCreateRuntimeKeyHint =>
      'Добавьте свой ключ для проектов сотрудников';

  @override
  String get errorConflict =>
      'Конфликт данных. Обновите страницу и попробуйте снова.';

  @override
  String get errorForbidden => 'Недостаточно прав для этого действия.';

  @override
  String get errorGateway => 'Сервер временно недоступен. Попробуйте ещё раз.';

  @override
  String errorHttpStatus(int status) {
    return 'Ошибка запроса (HTTP $status)';
  }

  @override
  String get errorIdentityProvider =>
      'Не удалось обновить учётные данные. Попробуйте ещё раз.';

  @override
  String get errorNetwork => 'Нет связи с сервером. Проверьте подключение.';

  @override
  String get errorNotFound => 'Запрошенный объект не найден.';

  @override
  String get errorRateLimited => 'Слишком много запросов. Подождите немного.';

  @override
  String get errorServer => 'Внутренняя ошибка сервера. Попробуйте позже.';

  @override
  String get errorUnauthorized => 'Авторизация не прошла. Войдите снова.';

  @override
  String get errorInvalidCredentials => 'Неправильный логин или пароль';

  @override
  String get errorUnexpected => 'Что-то пошло не так.';

  @override
  String get errorValidation => 'Проверьте введённые данные.';

  @override
  String get errorAttachmentTooLarge => 'Вложение слишком большое.';

  @override
  String get errorAttachmentTypeForbidden => 'Этот тип файла не разрешён.';

  @override
  String get errorAttachmentContentForbidden =>
      'Содержимое файла не разрешено.';

  @override
  String get errorTooManyAttachments =>
      'Слишком много вложений в одном сообщении.';

  @override
  String get errorProjectPaused =>
      'Проект на паузе. Возобновите, чтобы продолжить.';

  @override
  String get errorCabinetArchived => 'Кабинет недоступен для изменений.';

  @override
  String get errorCompanySuspended =>
      'Подписка компании приостановлена или истекла.';

  @override
  String get errorNoAiKey => 'Нет доступного AI-ключа для запуска.';

  @override
  String get errorSessionClosed => 'Сессия агента закрыта.';

  @override
  String get errorAgentBudget => 'Исчерпан лимит токенов агента.';

  @override
  String get errorPodNotRunning =>
      'Контейнер не запущен — откройте настройки проекта для запуска или перезагрузки.';

  @override
  String get errorAgentRuntimeUnavailable =>
      'Агент недоступен. Проверьте контейнер проекта.';

  @override
  String get errorAgentStubResponse =>
      'Агент вернул заглушку вместо реального ответа. Пересоберите образ контейнера.';

  @override
  String get errorAgentCredentialMissing =>
      'API-ключ не был доставлен агенту в контейнере. Проверьте ключ проекта.';

  @override
  String get errorAgentInvalidApiKey =>
      'Неверный API-ключ AI-провайдера. Обновите ключ проекта.';

  @override
  String get errorAgentInvalidModel =>
      'Выбранная модель недоступна для этого провайдера. Выберите другую модель.';

  @override
  String get aiKeyModelsTitle => 'Модели';

  @override
  String get aiKeyModelsNone => 'Не задано';

  @override
  String aiKeyModelsSelected(String count) {
    return 'Выбрано: $count';
  }

  @override
  String get aiKeyModelsAddHint => 'ID модели';

  @override
  String get aiKeyModelsEmpty => 'Нет моделей для этого SDK';

  @override
  String get aiKeyModelEnabled => 'Вкл';

  @override
  String get aiKeyModelDefault => 'По умолчанию';

  @override
  String get aiModelNameLabel => 'Название';

  @override
  String get aiModelModelIdsLabel => 'ID модели';

  @override
  String get aiModelSdkLabel => 'Привязка к SDK';

  @override
  String get aiModelInputPriceLabel => 'Цена вход (\$/1M tokens)';

  @override
  String get aiModelOutputPriceLabel => 'Цена выход (\$/1M tokens)';

  @override
  String get aiModelMaxTokensLabel => 'Max tokens';

  @override
  String get aiModelCostLabel => 'Стоимость';

  @override
  String get aiModelPublisherLabel => 'Издатель';

  @override
  String get aiModelReleasedAtLabel => 'Дата выпуска';

  @override
  String get projectChatModelLabel => 'Модель';

  @override
  String get projectChatReasoning => 'Размышление';

  @override
  String get projectChatReasoningStreaming => 'Размышление…';

  @override
  String projectChatReasonedPast(String duration) {
    return 'Размышлял $duration';
  }

  @override
  String get projectChatSettingsTitle => 'Настройки чата';

  @override
  String get projectChatModelSelectTitle => 'Выбор модели';

  @override
  String get projectChatModelColumnPrice => 'Цена вх/вых';

  @override
  String get projectChatModelColumnMaxTokens => 'Max tokens';

  @override
  String get projectChatModelColumnPublisher => 'Издатель';

  @override
  String get projectChatModelColumnReleased => 'Дата выпуска';

  @override
  String get projectChatAddAction => 'Настройки чата';

  @override
  String get projectChatWakePaused => 'Проект приостановлен. Возобновить?';

  @override
  String get projectChatWakeUnresponsive =>
      'Проект не отвечает. Перезагрузить?';

  @override
  String get projectChatNeedsUpdate => 'Проект требует обновления. Обновить?';

  @override
  String get projectChatCancelled => 'Отменено';

  @override
  String get projectChatInterrupted =>
      'Прервано — сообщение восстановлено в поле ввода';

  @override
  String chatMessageTooLong(int max) {
    return 'Сообщение слишком длинное (макс. $max знаков)';
  }

  @override
  String chatTooManyAttachments(int max) {
    return 'Слишком много вложений (макс. $max)';
  }

  @override
  String get chatFileTooLarge => 'Файл слишком большой';

  @override
  String get chatAttachmentUploadFailed => 'Не удалось загрузить вложение';

  @override
  String chatAttachmentLabel(String name) {
    return 'Вложение: $name';
  }

  @override
  String chatAttachmentJsonLabel(String name) {
    return 'Вложение: $name (JSON)';
  }

  @override
  String chatAttachmentPathLabel(String name, String path) {
    return 'Вложение: $name → /workspace/$path';
  }

  @override
  String chatAttachmentRowsLabel(String name, int rows) {
    String _temp0 = intl.Intl.pluralLogic(
      rows,
      locale: localeName,
      other: '$rows строки',
      many: '$rows строк',
      few: '$rows строки',
      one: '$rows строка',
    );
    return 'Вложение: $name ($_temp0)';
  }

  @override
  String projectAgentFailedToStart(String reason) {
    return 'Агент не запустился: $reason';
  }

  @override
  String get projectChatSidechainOffline =>
      'Sidechain недоступен — контейнер не запущен';

  @override
  String projectChatGroupThinking(int count) {
    return 'Размышление ($count)';
  }

  @override
  String projectChatGroupFilesEdited(int count) {
    return 'Изменено $count файлов';
  }

  @override
  String projectChatGroupFilesRead(int count) {
    return 'Прочитано $count файлов';
  }

  @override
  String projectChatGroupCommands(int count) {
    return 'Запущено $count команд';
  }

  @override
  String projectChatGroupMcp(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '# вызова инструмента',
      many: '# вызовов инструмента',
      few: '# вызова инструмента',
      one: '# вызов инструмента',
    );
    return '$_temp0';
  }

  @override
  String projectChatGroupDeleted(int count) {
    return 'Удалено $count файлов';
  }

  @override
  String projectChatGroupGlob(int count) {
    return 'Поиск файлов ($count)';
  }

  @override
  String projectChatGroupGrep(int count) {
    return 'Поиск ($count)';
  }

  @override
  String projectChatGroupListDir(int count) {
    return 'Список ($count)';
  }

  @override
  String projectChatGroupGeneric(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '# вызова инструмента',
      many: '# вызовов инструмента',
      few: '# вызова инструмента',
      one: '# вызов инструмента',
    );
    return '$_temp0';
  }

  @override
  String get projectChatWorking => 'Работаю…';

  @override
  String projectChatWorked(int count) {
    return 'Работал · $count действий';
  }

  @override
  String get projectChatAgentWorking => 'agentscale работает…';

  @override
  String get projectChatReconnecting => 'Попытка реконнекта';

  @override
  String projectChatReconnectingAttempt(Object n, Object y) {
    return 'Попытка реконнекта ($n/$y)';
  }

  @override
  String get projectChatErrorPolicyTitle => 'Обработка ошибок';

  @override
  String get projectChatErrorPolicyInterval => 'Интервал между попытками, сек';

  @override
  String get projectChatErrorPolicyInfinite => 'Бесконечные попытки';

  @override
  String get projectChatErrorPolicyAttempts => 'Количество попыток';

  @override
  String get projectChatErrorPolicyTryOtherModels => 'Пробовать другие модели';

  @override
  String get projectChatErrorPolicyFallbackModels => 'Модели для подмены';

  @override
  String get projectChatErrorPolicyFallbackModelsNone => 'Не выбраны';

  @override
  String get chatCopyMessage => 'Копировать ответ';

  @override
  String get chatCopiedMessage => 'Скопировано';

  @override
  String get chatUsageInputLabel => 'вход:';

  @override
  String get chatUsageOutputLabel => 'выход:';

  @override
  String get chatUsageCacheLabel => 'кэш:';

  @override
  String get chatChecklistTitle => 'Задачи';

  @override
  String chatChecklistProgress(int done, int total) {
    return '$done/$total задач выполнено';
  }

  @override
  String chatModelPricePerMtok(String priceIn, String priceOut) {
    return '$priceIn / $priceOut \$ · 1M токенов';
  }

  @override
  String get chatModelDefaultLabel => 'По умолчанию';

  @override
  String get chatModelPickerEmpty => 'Нет доступных моделей';

  @override
  String get chatModelSearchHint => 'Поиск модели';

  @override
  String get projectChatToolReadFile => 'Прочитан файл';

  @override
  String projectChatToolReadPath(String path) {
    return 'Прочитан $path';
  }

  @override
  String get projectChatToolWriteFile => 'Записан файл';

  @override
  String projectChatToolWritePath(String path) {
    return 'Записан $path';
  }

  @override
  String get projectChatToolEditFile => 'Изменён файл';

  @override
  String projectChatToolEditPath(String path) {
    return 'Изменён $path';
  }

  @override
  String get projectChatToolDeleteFile => 'Удалён файл';

  @override
  String projectChatToolDeletePath(String path) {
    return 'Удалён $path';
  }

  @override
  String get projectChatToolGlob => 'Поиск файлов';

  @override
  String projectChatToolGlobPattern(String pattern) {
    return 'Поиск файлов $pattern';
  }

  @override
  String get projectChatToolGrep => 'Поиск';

  @override
  String projectChatToolGrepPattern(String pattern) {
    return 'Поиск $pattern';
  }

  @override
  String get projectChatToolListDir => 'Список файлов';

  @override
  String projectChatToolListDirPath(String path) {
    return 'Список $path';
  }

  @override
  String get projectChatToolShell => 'Запущена команда';

  @override
  String get projectChatToolMcpGeneric => 'Инструмент';

  @override
  String projectChatToolMcp(String tool) {
    return '$tool';
  }

  @override
  String projectChatToolMcpServer(Object server, Object tool) {
    return '$server · $tool';
  }

  @override
  String get projectChatToolWebSearch => 'Поиск в интернете';

  @override
  String get projectChatToolWebFetch => 'Чтение веб-страницы';

  @override
  String get projectChatToolTodoWrite => 'Обновление плана задач';

  @override
  String get projectChatToolTodoList => 'План задач';

  @override
  String get projectChatToolNotesWrite => 'Запись заметки';

  @override
  String get projectChatToolNotesRead => 'Чтение заметок';

  @override
  String get projectChatToolGoalsSet => 'Постановка цели';

  @override
  String get projectChatToolGoalsUpdate => 'Обновление цели';

  @override
  String get projectChatToolGoalsList => 'Список целей';

  @override
  String get projectChatToolCompact => 'Сжатие контекста';

  @override
  String get projectChatToolMcpServers => 'Список инструментов';

  @override
  String get projectChatToolMcpTools => 'Список инструментов';

  @override
  String get projectChatToolErrorPrefix => 'Ошибка';

  @override
  String get projectChatPermissionDenied => 'Действие отклонено';

  @override
  String projectChatToolSubagent(String name) {
    return 'Субагент $name';
  }

  @override
  String projectChatToolGeneric(String name) {
    return 'Инструмент $name';
  }

  @override
  String get errorAgentProviderNetwork =>
      'AI-провайдер недоступен из контейнера (сеть).';

  @override
  String get errorAgentProviderRateLimit =>
      'Превышен лимит запросов к AI-провайдеру. Повторите позже.';

  @override
  String get errorAgentProviderUnavailable =>
      'AI-провайдер временно недоступен. Повторите позже.';

  @override
  String get errorAgentRuntimeError => 'Ошибка агента при обработке запроса.';

  @override
  String get errorAgentBridge => 'Не удалось связаться с агентом в контейнере.';

  @override
  String get errorAgentTurnFailed =>
      'Агент не смог завершить ход. Подробности — в чате.';

  @override
  String get errorCascadeIncomplete =>
      'Каскад удаления ещё не завершён. Подождите или повторите позже.';

  @override
  String companyCredentialsCreated(String companyId, String password) {
    return 'Логин: $companyId · пароль: $password';
  }

  @override
  String get companyOwner => 'Владелец';

  @override
  String get companyRole => 'Роль';

  @override
  String get companyValidEmailRequired => 'Нужен корректный email';

  @override
  String get devAdminSession => 'Сессия admin';

  @override
  String get devBearerTokenCompanyAdmin => 'Bearer token (JWT company.admin)';

  @override
  String get devBearerTokenPlatformAdmin => 'Bearer token (JWT platform_admin)';

  @override
  String get devBearerTokenTestJwt => 'Bearer token (JWT AUTH_MODE=test)';

  @override
  String get devCompanyAdminMembershipRequired =>
      'Нужен membership company.admin';

  @override
  String get devCompanySession => 'Сессия компании';

  @override
  String get devDevSession => 'Dev-сессия';

  @override
  String get devNoEmployeeMemberships => 'Нет memberships сотрудника';

  @override
  String get devTokenMustHaveCompanyContour =>
      'В токене нужен контур company (membership company.admin)';

  @override
  String get devTokenMustHavePlatformAdmin =>
      'В токене нужен контур platform_admin';

  @override
  String get galleryAlpha => 'Alpha';

  @override
  String get galleryBeta => 'Beta';

  @override
  String get galleryButtons => 'Кнопки';

  @override
  String get galleryCheck => 'Чекбокс';

  @override
  String get galleryCoreGallery => 'Галерея core';

  @override
  String get galleryCount => 'Счётчик';

  @override
  String get galleryDanger => 'Опасное';

  @override
  String get galleryDemo => 'demo';

  @override
  String get galleryDemoConfirm => 'Демо-подтверждение';

  @override
  String get galleryEntityCollection => 'EntityCollection';

  @override
  String get galleryListItem => 'Элемент списка';

  @override
  String get galleryOne => 'Один';

  @override
  String get galleryPick => 'Выбор';

  @override
  String get galleryRadio => 'Радио';

  @override
  String get gallerySampleRow => 'Пример строки';

  @override
  String get gallerySelection => 'Выбор';

  @override
  String get gallerySelector => 'Селектор';

  @override
  String get gallerySubtitle => 'подзаголовок';

  @override
  String get galleryTwo => 'Два';

  @override
  String get navAiKeys => 'AI-ключи';

  @override
  String get navAiModels => 'Модели';

  @override
  String get navBundles => 'Бандлы';

  @override
  String get navContainers => 'Проекты';

  @override
  String get navCabinets => 'Кабинеты';

  @override
  String get navModules => 'Модули';

  @override
  String get navCompanies => 'Компании';

  @override
  String get navEmployees => 'Сотрудники';

  @override
  String get navOverview => 'Обзор';

  @override
  String get navManagement => 'Управление';

  @override
  String get navData => 'Данные';

  @override
  String get navProdavan => 'Agentscale';

  @override
  String get projectAgentError => 'Ошибка агента';

  @override
  String get projectApproveAndContinue => 'Разрешить и продолжить';

  @override
  String get projectApproveTool => 'Разрешение инструмента';

  @override
  String projectApproveToolPrompt(String name) {
    return 'Разрешить $name?';
  }

  @override
  String get projectAttachFile => 'Прикрепить файл';

  @override
  String get projectAttachmentFallback => 'вложение';

  @override
  String get projectCancelledMarker => '(отменено)';

  @override
  String projectCancelledWithText(String text) {
    return '$text\n(отменено)';
  }

  @override
  String get projectCompanyDefault => 'По умолчанию для компании';

  @override
  String get projectCouldNotReadFileBytes => 'Не удалось прочитать файл';

  @override
  String get projectCreateAndOpenChat => 'Создать и открыть чат';

  @override
  String get projectCreateProject => 'Создать проект';

  @override
  String get projectCreateProjectHint => 'Создайте проект, чтобы открыть чат';

  @override
  String get projectDeny => 'Отклонить';

  @override
  String get projectEmptyChatHint =>
      'Отправьте сообщение, чтобы начать сессию агента';

  @override
  String get projectFileFallback => 'файл';

  @override
  String projectInbox(String count) {
    return 'Входящие ($count)';
  }

  @override
  String get projectMessageHint => 'Сообщение…';

  @override
  String get projectNewProject => 'Новый проект';

  @override
  String get projectNoAttachmentData => 'Нет данных вложения';

  @override
  String projectNoPreviewForType(String contentType, String bytes) {
    return 'Нет превью для $contentType ($bytes байт)';
  }

  @override
  String get projectNoProjects => 'Нет проектов';

  @override
  String get projectPauseProject => 'Приостановить проект';

  @override
  String get projectProjectManagement => 'Управление проектом';

  @override
  String get projectPausedBanner =>
      'Проект на паузе — чат, загрузки и запуски агента отключены';

  @override
  String get projectPausedDisabledHint =>
      'Чат, загрузки и запуски агента недоступны на паузе';

  @override
  String get projectPausedListSubtitle =>
      'На паузе — чат, загрузки и запуски агента отключены';

  @override
  String get projectPausing => 'Пауза…';

  @override
  String projectPdfPreviewUnavailable(String bytes, String contentType) {
    return 'Встроенный просмотр PDF пока недоступен.\n$bytes байт · $contentType';
  }

  @override
  String get projectPreferredAgentProvider => 'Провайдер AI';

  @override
  String get projectPreviewTruncated =>
      'Превью обрезано до первых 200k символов';

  @override
  String get projectProject => 'Проект';

  @override
  String get projectProjectName => 'Название проекта';

  @override
  String get projectOpenChat => 'Чат';

  @override
  String get projectProjectPaused => 'Проект на паузе';

  @override
  String get projectProjectResumed => 'Проект возобновлён';

  @override
  String get projectProjectSettings => 'Настройки проекта';

  @override
  String get projectProjectStatus => 'Статус проекта';

  @override
  String get projectRegenerate => 'Сгенерировать снова';

  @override
  String get projectReloadTranscript => 'Обновить переписку';

  @override
  String get projectRematerializeWorkspace => 'Пересоздать workspace';

  @override
  String projectRematerializedPackages(String packages) {
    return 'Пересозданы пакеты: $packages';
  }

  @override
  String get projectRematerializing => 'Пересоздание…';

  @override
  String get projectResumeProject => 'Возобновить проект';

  @override
  String get projectResuming => 'Возобновление…';

  @override
  String projectSizeBytes(String size) {
    return '$size B';
  }

  @override
  String get projectSubscriptionExpiredBanner =>
      'Подписка компании истекла — чат и загрузки недоступны';

  @override
  String get projectToolApprovalHint =>
      'Инструменту нужно ваше подтверждение, прежде чем агент продолжит.';

  @override
  String get projectUploadMissingId => 'В загрузке нет id/storage_ref';

  @override
  String get projectWorking => 'Работаем…';

  @override
  String get projectWorkspaceRematerializedNoPackages =>
      'Workspace пересоздан (без MCP-пакетов)';

  @override
  String get navProjects => 'Проекты';

  @override
  String get navChats => 'Чаты';

  @override
  String get navNewChat => 'Новый диалог';

  @override
  String get chatUntitled => 'Диалог';

  @override
  String chatDialogN(int n) {
    return 'Диалог $n';
  }

  @override
  String get chatPin => 'Закрепить чат';

  @override
  String get chatUnpin => 'Открепить чат';

  @override
  String get chatRenameTitle => 'Название';

  @override
  String get chatDeleteDialog => 'Удалить диалог';

  @override
  String get chatDeleteDialogConfirmMessage =>
      'Диалог и история сообщений будут удалены без возможности восстановления.';

  @override
  String get chatOpenFromSidebarHint =>
      'Откройте «Новый чат» или чат в боковой панели';

  @override
  String get chatSelectProjectHint => 'Выберите проект';

  @override
  String get chatCreateChatHint => 'Создайте чат';

  @override
  String get projectAddHint => 'Добавить проект';

  @override
  String get projectAboutLabel => 'О проекте';

  @override
  String get projectDescriptionLabel => 'Описание';

  @override
  String get projectDialogsLabel => 'Диалоги';

  @override
  String get projectCreateDialogHint => 'Создать диалог';

  @override
  String get projectAboutColumn => 'О проекте';

  @override
  String get projectChatsColumn => 'Чаты';

  @override
  String get projectBudgetColumn => 'Бюджет';

  @override
  String get projectBudgetStub => '—';

  @override
  String get projectCreatorColumn => 'Создатель';

  @override
  String get projectCreatorLabel => 'Создатель';

  @override
  String get projectLaunchProject => 'Запустить проект';

  @override
  String get projectContainer => 'Контейнер';

  @override
  String get projectReload => 'Перезагрузить';

  @override
  String get projectWorkspaceFiles => 'Файлы';

  @override
  String get projectWorkspacePreview => 'Просмотр';

  @override
  String get projectWorkspaceDownload => 'Скачать';

  @override
  String get projectWorkspaceDownloaded => 'Файл сохранён';

  @override
  String get projectUpdateProject => 'Обновить проект';

  @override
  String get projectResetAgent => 'Сбросить агента';

  @override
  String get projectConfigureBeforeLaunch =>
      'Укажите провайдер AI перед запуском';

  @override
  String get projectAiProviderNotSelected => 'Не выбран';

  @override
  String get projectAiKeyColumnProvider => 'Провайдер';

  @override
  String get projectAiKeyColumnSubscription => 'Подписка';

  @override
  String get projectSelectModuleProfile => 'Выбор профиля';

  @override
  String get projectModuleNoProfiles => 'У модуля нет профилей';

  @override
  String get projectLaunchSuccess => 'Проект запущен';

  @override
  String get projectLaunchInProgress => 'Запуск проекта…';

  @override
  String get projectLaunchStartingSnack => 'Проект запускается…';

  @override
  String get projectReloadInProgress => 'Перезагрузка проекта…';

  @override
  String get projectLifecycleTimedOut =>
      'Превышено время ожидания запуска контейнера';

  @override
  String get projectResumeInProgress => 'Возобновление проекта…';

  @override
  String get projectResumeStartingSnack => 'Проект возобновляется…';

  @override
  String get projectPauseInProgress => 'Приостановка проекта…';

  @override
  String get projectPauseConfirmMessage =>
      'При приостановке проекта агент будет недоступен.';

  @override
  String get projectReloadSuccess => 'Проект перезагружен';

  @override
  String get projectUpdateSuccess => 'Проект обновлён';

  @override
  String get projectWorkspaceOutdated =>
      'Workspace устарел — нажмите «Обновить проект», чтобы применить изменения модулей';

  @override
  String get projectResetSuccess => 'Агент сброшен';

  @override
  String get projectAiKeyLabel => 'AI-ключ';

  @override
  String get projectModulesLabel => 'Модули';

  @override
  String get commonAuto => 'Авто';

  @override
  String get employeeContactEmail => 'Email';

  @override
  String get employeePassword => 'Пароль';

  @override
  String get employeeSwitchCabinet => 'Сменить кабинет';

  @override
  String get employeeSingleCabinet => 'Доступен только один кабинет';

  @override
  String get employeePasswordChanged => 'Пароль обновлён';

  @override
  String get employeeContactEmailSaved => 'Email сохранён';

  @override
  String get settings => 'Настройки';

  @override
  String get sessionRestoreOffline =>
      'Сервер недоступен. Проверьте подключение';

  @override
  String get settingsLanguage => 'Язык';

  @override
  String get settingsLanguageEn => 'English';

  @override
  String get settingsLanguageRu => 'Русский';

  @override
  String get settingsTheme => 'Тема';

  @override
  String get settingsThemeDark => 'Тёмная';

  @override
  String get settingsThemeLight => 'Светлая';

  @override
  String get settingsThemeUltraDark => 'Ультратёмная';

  @override
  String get settingsRefresh => 'Обновление';

  @override
  String get settingsRefreshOff => 'Не обновлять';

  @override
  String settingsRefreshSeconds(String n) {
    return '$n с';
  }

  @override
  String settingsRefreshMinutes(String n) {
    return '$n мин';
  }

  @override
  String metaSecretConfigured(String prefix) {
    return 'Секрет настроен: $prefix';
  }

  @override
  String get metaSecretEnter => 'Введите секрет';

  @override
  String get metaSecretReplace => 'Заменить секрет';

  @override
  String get metaSecretSave => 'Сохранить секрет';

  @override
  String get aiKeyProbeTitle => 'Проверить';

  @override
  String get aiKeyProbeRun => 'Проверить';

  @override
  String get aiKeyProbeNoSecret =>
      'У ключа нет сохранённого секрета. Сначала добавьте секрет.';

  @override
  String get aiKeyProbeNeverRun => 'Ключ ещё не проверялся.';

  @override
  String get aiKeyProbeLatency => 'Задержка';

  @override
  String get aiKeyProbeModelsCount => 'Доступно моделей';

  @override
  String aiKeyProbeModelsCountValue(int count) {
    return 'Моделей: $count';
  }

  @override
  String get aiKeyProbeModelsNotReceived => 'Модели не получены';

  @override
  String get aiKeyProbeErrorCode => 'Код ошибки';

  @override
  String get aiKeyProbeErrorMessage => 'Сообщение ошибки';

  @override
  String get aiKeyProbeCheckedAt => 'Проверен';

  @override
  String get aiKeyProbeModelSelect => 'Выбор модели (информационно)';

  @override
  String get aiKeyProbeModelLabel => 'Модель';

  @override
  String get aiKeyProbeStatusCol => 'Статус';

  @override
  String get aiKeyProbeStatusOk => 'Ключ работает';

  @override
  String get aiKeyProbeStatusError => 'Ошибка ключа';

  @override
  String get aiKeyProbeStatusUnavailable => 'Проверка недоступна';

  @override
  String get aiKeyProbeStatusNone => 'Не проверялся';

  @override
  String get commonScope => 'Область';

  @override
  String get aiModelKeyAliasesLabel => 'ID модели';

  @override
  String get aiModelReasoningLevelLabel => 'Уровень рассуждений';

  @override
  String get adminAddModel => 'Добавить модель';

  @override
  String get adminDeleteModel => 'Удалить модель';

  @override
  String adminDeleteModelConfirm(String name) {
    return 'Удалить модель «$name»?';
  }

  @override
  String get adminNoModels => 'Нет моделей';

  @override
  String get adminAddCompanyHint =>
      'Название компании (вход: название@agentscale.local)';

  @override
  String get companyAddEmployeeHint =>
      'Имя сотрудника (вход: имя@компания.local)';

  @override
  String get authLoginHint => 'company@agentscale.local';
}
