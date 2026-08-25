import 'package:flutter/material.dart';

import 'package:prodavan/core/session/admin_context.dart';

/// Shared company detail state for admin hub + sub-pages.
class AdminCompanyDetailController extends ChangeNotifier {
  AdminCompanyDetailController({
    required this.companyId,
    required this.companyName,
  });

  final String companyId;
  final String companyName;

  bool loading = true;
  bool busy = false;
  String? error;
  Map<String, dynamic>? metrics;
  List<Map<String, dynamic>> platformEvents = const [];

  String description = '';
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

  Future<void> load() async {
    loading = true;
    error = null;
    notifyListeners();
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
      metrics = m;
      platformEvents = events;
      description = detail['description'] as String? ?? '';
      maxCabinets = (quota['max_cabinets'] as num?)?.toInt() ?? 10;
      maxPackages = (quota['max_packages_per_cabinet'] as num?)?.toInt() ?? 20;
      maxBundleMb = (quota['max_bundle_import_mb'] as num?)?.toInt() ?? 50;
      toolPreset = policy['tool_preset'] as String? ?? 'workspace_dev';
      preferredProvider = policy['preferred_provider'] as String? ?? '';
      platformFallback = policy['platform_fallback'] as bool? ?? true;
      modelAllowlist = allow is List
          ? allow.map((e) => e.toString()).where((s) => s.trim().isNotEmpty).join(', ')
          : '';
      maxTokensMonth = policy['max_agent_tokens_month']?.toString() ?? '';
      maxTokensPerRun = policy['max_tokens_per_run']?.toString() ?? '';
      maxCostUsdMonth = policy['max_cost_usd_month']?.toString() ?? '';
      maxAttachmentMb = (policy['max_attachment_mb'] as num?)?.toInt() ?? 20;
      idlePauseHours = policy['idle_pause_after_hours']?.toString() ?? '';
      webhookHmacConfigured = policy['webhook_hmac_configured'] == true;
      telegramHmacConfigured = policy['telegram_hmac_configured'] == true;
      subscriptionLifetime = m['subscription_lifetime'] == true;
      final endsAt = m['subscription_ends_at'];
      subscriptionEnds = endsAt is String ? endsAt.split('T').first : '';
      loading = false;
      notifyListeners();
    } catch (e) {
      error = e.toString();
      loading = false;
      notifyListeners();
    }
  }

  Future<void> saveDescription(String value) async {
    await adminContext.api.patchCompany(
      companyId: companyId,
      description: value.trim().isEmpty ? null : value.trim(),
    );
    description = value.trim();
    notifyListeners();
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

  Future<String?> saveSubscription({
    bool? lifetime,
    String? endsAt,
  }) async {
    final lt = lifetime ?? subscriptionLifetime;
    final ends = endsAt ?? subscriptionEnds;
    if (!lt && ends.trim().isEmpty) {
      return 'adminSetEndDateOrLifetime';
    }
    final endsRaw = ends.trim();
    final endsIso = endsRaw.isEmpty
        ? null
        : endsRaw.contains('T') ? endsRaw : '${endsRaw}T00:00:00Z';
    await adminContext.api.setCompanySubscription(
      companyId: companyId,
      subscriptionLifetime: lt,
      subscriptionEndsAt: lt ? null : endsIso,
    );
    subscriptionLifetime = lt;
    subscriptionEnds = endsRaw;
    await load();
    return null;
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
