import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_subscription_preference.dart';
import 'package:prodavan/core/session/admin_context.dart';

/// Shared company detail state for admin hub + sub-pages.
class AdminCompanyDetailController extends ChangeNotifier {
  AdminCompanyDetailController({
    required this.companyId,
    required this.companyName,
  });

  final String companyId;
  String companyName;


  bool loading = true;
  bool busy = false;
  String? error;
  Map<String, dynamic>? metrics;
  List<Map<String, dynamic>> platformEvents = const [];

  String description = '';
  String contactEmail = '';
  String phone = '';
  int maxCabinets = 10;
  int maxPackages = 20;
  int maxBundleMb = 50;
  String toolPreset = 'workspace_dev';
  String preferredProvider = '';
  bool platformFallback = true;
  String modelAllowlist = '';
  String maxTokensMonth = '';
  String maxTokensPerRun = '';
  String maxCostUsdMonth = '';
  int maxAttachmentMb = 20;
  String idlePauseHours = '';
  bool webhookHmacConfigured = false;
  bool telegramHmacConfigured = false;
  bool subscriptionLifetime = false;
  String subscriptionEnds = '';

  static const toolPresets = ['chat_readonly', 'workspace_dev', 'workspace_full'];
  static const providerChoices = ['', 'cursor', 'codex', 'claude_code'];
  /// Idle pause after N hours (`''` = off / never).
  static const idlePauseChoices = ['', '5', '10', '20', '40', '50', '60'];
  static const attachmentMbChoices = [10, 20, 50, 100, 200];

  Future<void> load({bool silent = false}) async {
    if (!silent) {
      loading = true;
      error = null;
      notifyListeners();
    }
    try {
      final detail = await adminContext.api.getCompany(companyId);
      final events = await adminContext.api.listPlatformEvents(
        companyId: companyId,
        limit: 20,
      );
      final quota = detail['cabinet_quota'] as Map<String, dynamic>? ?? const {};
      final policy = detail['agent_policy'] as Map<String, dynamic>? ?? const {};
      final m = detail['metrics'] as Map<String, dynamic>? ?? const {};
      final allow = policy['model_allowlist'];

      final nextName = detail['name'] as String? ?? companyName;
      final nextDescription = detail['description'] as String? ?? '';
      final nextEmail = detail['contact_email'] as String? ?? '';
      final nextPhone = detail['phone'] as String? ?? '';
      final nextMaxCabinets = (quota['max_cabinets'] as num?)?.toInt() ?? 10;
      final nextMaxPackages = (quota['max_packages_per_cabinet'] as num?)?.toInt() ?? 20;
      final nextMaxBundleMb = (quota['max_bundle_import_mb'] as num?)?.toInt() ?? 50;
      final nextToolPreset = policy['tool_preset'] as String? ?? 'workspace_dev';
      final nextPreferredProvider = policy['preferred_provider'] as String? ?? '';
      final nextPlatformFallback = policy['platform_fallback'] as bool? ?? true;
      final nextModelAllowlist = allow is List
          ? allow.map((e) => e.toString()).where((s) => s.trim().isNotEmpty).join(', ')
          : '';
      final nextMaxTokensMonth = policy['max_agent_tokens_month']?.toString() ?? '';
      final nextMaxTokensPerRun = policy['max_tokens_per_run']?.toString() ?? '';
      final nextMaxCostUsdMonth = policy['max_cost_usd_month']?.toString() ?? '';
      final nextMaxAttachmentMb = (policy['max_attachment_mb'] as num?)?.toInt() ?? 20;
      final nextIdlePauseHours = policy['idle_pause_after_hours']?.toString() ?? '';
      final nextWebhook = policy['webhook_hmac_configured'] == true;
      final nextTelegram = policy['telegram_hmac_configured'] == true;
      final nextLifetime = m['subscription_lifetime'] == true;
      final endsAt = m['subscription_ends_at'];
      final String nextSubscriptionEnds;
      if (nextLifetime || endsAt == null) {
        nextSubscriptionEnds = '';
      } else if (endsAt is String) {
        nextSubscriptionEnds = formatSubscriptionDate(endsAt);
      } else {
        nextSubscriptionEnds = '';
      }

      final unchanged = silent &&
          !loading &&
          companyName == nextName &&
          description == nextDescription &&
          contactEmail == nextEmail &&
          phone == nextPhone &&
          maxCabinets == nextMaxCabinets &&
          maxPackages == nextMaxPackages &&
          maxBundleMb == nextMaxBundleMb &&
          toolPreset == nextToolPreset &&
          preferredProvider == nextPreferredProvider &&
          platformFallback == nextPlatformFallback &&
          modelAllowlist == nextModelAllowlist &&
          maxTokensMonth == nextMaxTokensMonth &&
          maxTokensPerRun == nextMaxTokensPerRun &&
          maxCostUsdMonth == nextMaxCostUsdMonth &&
          maxAttachmentMb == nextMaxAttachmentMb &&
          idlePauseHours == nextIdlePauseHours &&
          webhookHmacConfigured == nextWebhook &&
          telegramHmacConfigured == nextTelegram &&
          subscriptionLifetime == nextLifetime &&
          subscriptionEnds == nextSubscriptionEnds &&
          metrics == m &&
          platformEvents == events;
      if (unchanged) return;

      metrics = m;
      platformEvents = events;
      companyName = nextName;
      description = nextDescription;
      contactEmail = nextEmail;
      phone = nextPhone;
      maxCabinets = nextMaxCabinets;
      maxPackages = nextMaxPackages;
      maxBundleMb = nextMaxBundleMb;
      toolPreset = nextToolPreset;
      preferredProvider = nextPreferredProvider;
      platformFallback = nextPlatformFallback;
      modelAllowlist = nextModelAllowlist;
      maxTokensMonth = nextMaxTokensMonth;
      maxTokensPerRun = nextMaxTokensPerRun;
      maxCostUsdMonth = nextMaxCostUsdMonth;
      maxAttachmentMb = nextMaxAttachmentMb;
      idlePauseHours = nextIdlePauseHours;
      webhookHmacConfigured = nextWebhook;
      telegramHmacConfigured = nextTelegram;
      subscriptionLifetime = nextLifetime;
      subscriptionEnds = nextSubscriptionEnds;
      loading = false;
      if (!silent) error = null;
      notifyListeners();
    } catch (e) {
      if (silent) rethrow;
      error = e.toString();
      loading = false;
      notifyListeners();
    }
  }

  Future<void> saveName(String value) async {
    final trimmed = value.trim();
    if (trimmed.isEmpty) return;
    await adminContext.api.patchCompany(companyId: companyId, name: trimmed);
    companyName = trimmed;
    notifyListeners();
  }

  Future<void> saveDescription(String value) async {
    final trimmed = value.trim();
    await adminContext.api.patchCompany(
      companyId: companyId,
      description: trimmed.isEmpty ? null : trimmed,
      patchDescription: true,
    );
    description = trimmed;
    notifyListeners();
  }

  Future<void> saveContactEmail(String value) async {
    final trimmed = value.trim();
    await adminContext.api.patchCompany(
      companyId: companyId,
      contactEmail: trimmed.isEmpty ? null : trimmed,
      patchContactEmail: true,
    );
    contactEmail = trimmed;
    notifyListeners();
  }

  Future<void> savePhone(String value) async {
    final trimmed = value.trim();
    await adminContext.api.patchCompany(
      companyId: companyId,
      phone: trimmed.isEmpty ? null : trimmed,
      patchPhone: true,
    );
    phone = trimmed;
    notifyListeners();
  }

  Future<void> inviteCompanyAdmin(String email) async {
    final trimmed = email.trim();
    if (trimmed.isEmpty || !trimmed.contains('@')) {
      throw FormatException('invalid email');
    }
    await adminContext.api.inviteEmployee(
      companyId: companyId,
      email: trimmed,
      role: 'company.admin',
    );
  }

  Future<void> saveQuotas({int? maxCabinets, int? maxPackages, int? maxBundleMb}) async {
    final mc = maxCabinets ?? this.maxCabinets;
    final mp = maxPackages ?? this.maxPackages;
    final mb = maxBundleMb ?? this.maxBundleMb;
    await adminContext.api.setCabinetQuotas(
      companyId: companyId,
      maxCabinets: mc,
      maxPackagesPerCabinet: mp,
      maxBundleImportMb: mb,
    );
    this.maxCabinets = mc;
    this.maxPackages = mp;
    this.maxBundleMb = mb;
    await load();
  }

  Future<void> saveSubscription({String? endsAt}) async {
    final endsRaw = (endsAt ?? subscriptionEnds).trim();
    final lifetime = endsRaw.isEmpty;
    final endsIso = lifetime ? null : subscriptionDateToIso(endsRaw);
    if (!lifetime && endsIso == null) {
      throw FormatException('invalid date');
    }
    await adminContext.api.setCompanySubscription(
      companyId: companyId,
      subscriptionLifetime: lifetime,
      subscriptionEndsAt: endsIso,
    );
    subscriptionLifetime = lifetime;
    subscriptionEnds = endsRaw;
    await load();
  }

  Future<void> savePolicy({
    String? toolPreset,
    String? preferredProvider,
    bool? platformFallback,
    String? modelAllowlist,
    String? maxTokensMonth,
    String? maxTokensPerRun,
    String? maxCostUsdMonth,
    int? maxAttachmentMb,
    String? idlePauseHours,
    String? webhookSecret,
    String? telegramSecret,
  }) async {
    final provider = (preferredProvider ?? this.preferredProvider).trim();
    final allowlist = (modelAllowlist ?? this.modelAllowlist)
        .split(RegExp(r'[,;\s]+'))
        .map((s) => s.trim())
        .where((s) => s.isNotEmpty)
        .toList();
    await adminContext.api.setAgentPolicy(
      companyId: companyId,
      toolPreset: toolPreset ?? this.toolPreset,
      preferredProvider: provider.isEmpty ? null : provider,
      platformFallback: platformFallback ?? this.platformFallback,
      modelAllowlist: allowlist,
      maxAgentTokensMonth: _optionalPositiveInt(maxTokensMonth ?? this.maxTokensMonth),
      maxTokensPerRun: _optionalPositiveInt(maxTokensPerRun ?? this.maxTokensPerRun),
      maxCostUsdMonth: _optionalPositiveDouble(maxCostUsdMonth ?? this.maxCostUsdMonth),
      maxAttachmentMb: maxAttachmentMb ?? this.maxAttachmentMb,
      idlePauseAfterHours: _optionalNonNegativeInt(idlePauseHours ?? this.idlePauseHours),
      webhookHmacSecret: webhookSecret,
      telegramHmacSecret: telegramSecret,
    );
    await load();
  }

  Future<Map<String, dynamic>> drainTriggers() async {
    busy = true;
    error = null;
    notifyListeners();
    try {
      final result = await adminContext.api.drainTriggers();
      await load();
      return result;
    } catch (e) {
      error = e.toString();
      rethrow;
    } finally {
      busy = false;
      notifyListeners();
    }
  }

  Future<Map<String, dynamic>> sweepIdlePause({String? targetCompanyId}) async {
    busy = true;
    error = null;
    notifyListeners();
    try {
      final result = await adminContext.api.sweepIdlePause(companyId: targetCompanyId);
      await load();
      return result;
    } catch (e) {
      error = e.toString();
      rethrow;
    } finally {
      busy = false;
      notifyListeners();
    }
  }

  int? _optionalPositiveInt(String value) {
    final trimmed = value.trim();
    if (trimmed.isEmpty) return null;
    final n = int.tryParse(trimmed);
    if (n == null || n < 1) return null;
    return n;
  }

  int? _optionalNonNegativeInt(String value) {
    final trimmed = value.trim();
    if (trimmed.isEmpty) return null;
    final n = int.tryParse(trimmed);
    if (n == null || n < 0) return null;
    return n;
  }

  double? _optionalPositiveDouble(String value) {
    final trimmed = value.trim();
    if (trimmed.isEmpty) return null;
    final n = double.tryParse(trimmed);
    if (n == null || n <= 0) return null;
    return n;
  }

  bool validatePositiveInt(String raw, {required int min}) {
    final n = int.tryParse(raw.trim());
    return n != null && n >= min;
  }
}

class AdminCompanyDetailScope extends InheritedNotifier<AdminCompanyDetailController> {
  const AdminCompanyDetailScope({
    super.key,
    required AdminCompanyDetailController controller,
    required super.child,
  }) : super(notifier: controller);

  static AdminCompanyDetailController of(BuildContext context) {
    final scope = context.dependOnInheritedWidgetOfExactType<AdminCompanyDetailScope>();
    assert(scope != null, 'AdminCompanyDetailScope not found');
    return scope!.notifier!;
  }
}

void pushCompanySubPage(BuildContext context, Widget page) {
  final ctrl = AdminCompanyDetailScope.of(context);
  Navigator.of(context).push(
    MaterialPageRoute<void>(
      builder: (_) => AdminCompanyDetailScope(
        controller: ctrl,
        child: page,
      ),
    ),
  );
}
