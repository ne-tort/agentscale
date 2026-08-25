import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/company_metrics_wrap.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';
import 'package:prodavan/features/admin/widgets/admin_metrics_alerts.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Platform Admin company detail — metrics, quotas, agent policy (L04).
class AdminCompanyDetailPage extends StatefulWidget {
  const AdminCompanyDetailPage({
    super.key,
    required this.companyId,
    required this.companyName,
  });

  final String companyId;
  final String companyName;

  @override
  State<AdminCompanyDetailPage> createState() => _AdminCompanyDetailPageState();
}

class _AdminCompanyDetailPageState extends State<AdminCompanyDetailPage> {
  bool _loading = true;
  bool _drainingTriggers = false;
  String? _error;
  Map<String, dynamic>? _metrics;
  List<Map<String, dynamic>> _platformEvents = const [];

  String _description = '';
  int _maxCabinets = 10;
  int _maxPackages = 20;
  int _maxBundleMb = 50;
  String _toolPreset = 'workspace_dev';
  String _preferredProvider = '';
  bool _platformFallback = true;
  String _modelAllowlist = '';
  String _maxTokensMonth = '';
  String _maxTokensPerRun = '';
  String _maxCostUsdMonth = '';
  int _maxAttachmentMb = 20;
  String _idlePauseHours = '';
  bool _webhookHmacConfigured = false;
  bool _telegramHmacConfigured = false;
  bool _subscriptionLifetime = false;
  String _subscriptionEnds = '';

  static const _toolPresets = ['chat_readonly', 'workspace_dev', 'workspace_full'];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final detail = await adminContext.api.getCompany(widget.companyId);
      final events = await adminContext.api.listPlatformEvents(
        companyId: widget.companyId,
        limit: 20,
      );
      if (!mounted) return;
      final quota = detail['cabinet_quota'] as Map<String, dynamic>? ?? const {};
      final policy = detail['agent_policy'] as Map<String, dynamic>? ?? const {};
      final metrics = detail['metrics'] as Map<String, dynamic>? ?? const {};
      final allow = policy['model_allowlist'];
      setState(() {
        _metrics = metrics;
        _platformEvents = events;
        _description = detail['description'] as String? ?? '';
        _maxCabinets = (quota['max_cabinets'] as num?)?.toInt() ?? 10;
        _maxPackages = (quota['max_packages_per_cabinet'] as num?)?.toInt() ?? 20;
        _maxBundleMb = (quota['max_bundle_import_mb'] as num?)?.toInt() ?? 50;
        _toolPreset = policy['tool_preset'] as String? ?? 'workspace_dev';
        _preferredProvider = policy['preferred_provider'] as String? ?? '';
        _platformFallback = policy['platform_fallback'] as bool? ?? true;
        _modelAllowlist = allow is List
            ? allow.map((e) => e.toString()).where((s) => s.trim().isNotEmpty).join(', ')
            : '';
        _maxTokensMonth = policy['max_agent_tokens_month']?.toString() ?? '';
        _maxTokensPerRun = policy['max_tokens_per_run']?.toString() ?? '';
        _maxCostUsdMonth = policy['max_cost_usd_month']?.toString() ?? '';
        _maxAttachmentMb = (policy['max_attachment_mb'] as num?)?.toInt() ?? 20;
        _idlePauseHours = policy['idle_pause_after_hours']?.toString() ?? '';
        _webhookHmacConfigured = policy['webhook_hmac_configured'] == true;
        _telegramHmacConfigured = policy['telegram_hmac_configured'] == true;
        _subscriptionLifetime = metrics['subscription_lifetime'] == true;
        final endsAt = metrics['subscription_ends_at'];
        _subscriptionEnds = endsAt is String ? endsAt.split('T').first : '';
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  Future<void> _saveDescription(String value) async {
    await adminContext.api.patchCompany(
      companyId: widget.companyId,
      description: value.trim().isEmpty ? null : value.trim(),
    );
    setState(() => _description = value.trim());
  }

  Future<void> _saveQuotas({int? maxCabinets, int? maxPackages, int? maxBundleMb}) async {
    final mc = maxCabinets ?? _maxCabinets;
    final mp = maxPackages ?? _maxPackages;
    final mb = maxBundleMb ?? _maxBundleMb;
    await adminContext.api.setCabinetQuotas(
      companyId: widget.companyId,
      maxCabinets: mc,
      maxPackagesPerCabinet: mp,
      maxBundleImportMb: mb,
    );
    setState(() {
      _maxCabinets = mc;
      _maxPackages = mp;
      _maxBundleMb = mb;
    });
    await _load();
  }

  Future<void> _saveSubscription() async {
    if (!_subscriptionLifetime && _subscriptionEnds.trim().isEmpty) {
      setState(() => _error = AppLocalizations.of(context).adminSetEndDateOrLifetime);
      return;
    }
    final endsRaw = _subscriptionEnds.trim();
    final endsAt = endsRaw.isEmpty
        ? null
        : endsRaw.contains('T') ? endsRaw : '${endsRaw}T00:00:00Z';
    await adminContext.api.setCompanySubscription(
      companyId: widget.companyId,
      subscriptionLifetime: _subscriptionLifetime,
      subscriptionEndsAt: _subscriptionLifetime ? null : endsAt,
    );
    await _load();
  }

  Future<void> _savePolicy({
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
    final provider = (preferredProvider ?? _preferredProvider).trim();
    final allowlist = (modelAllowlist ?? _modelAllowlist)
        .split(RegExp(r'[,;\s]+'))
        .map((s) => s.trim())
        .where((s) => s.isNotEmpty)
        .toList();
    await adminContext.api.setAgentPolicy(
      companyId: widget.companyId,
      toolPreset: toolPreset ?? _toolPreset,
      preferredProvider: provider.isEmpty ? null : provider,
      platformFallback: platformFallback ?? _platformFallback,
      modelAllowlist: allowlist,
      maxAgentTokensMonth: _optionalPositiveInt(maxTokensMonth ?? _maxTokensMonth),
      maxTokensPerRun: _optionalPositiveInt(maxTokensPerRun ?? _maxTokensPerRun),
      maxCostUsdMonth: _optionalPositiveDouble(maxCostUsdMonth ?? _maxCostUsdMonth),
      maxAttachmentMb: maxAttachmentMb ?? _maxAttachmentMb,
      idlePauseAfterHours: _optionalNonNegativeInt(idlePauseHours ?? _idlePauseHours),
      webhookHmacSecret: webhookSecret,
      telegramHmacSecret: telegramSecret,
    );
    await _load();
  }

  Future<void> _drainTriggers() async {
    setState(() {
      _drainingTriggers = true;
      _error = null;
    });
    try {
      final result = await adminContext.api.drainTriggers();
      await _load();
      if (!mounted) return;
      final l10n = AppLocalizations.of(context);
      setState(() => _drainingTriggers = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(l10n.adminDrainedTriggers('${result['count'] ?? 0}'))),
      );
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _drainingTriggers = false;
      });
    }
  }

  Future<void> _sweepIdlePause({String? companyId}) async {
    setState(() {
      _drainingTriggers = true;
      _error = null;
    });
    try {
      final result = await adminContext.api.sweepIdlePause(companyId: companyId);
      await _load();
      if (!mounted) return;
      final l10n = AppLocalizations.of(context);
      setState(() => _drainingTriggers = false);
      final count = result['count'] ?? 0;
      if (companyId != null) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(l10n.adminIdlePausedProjectsInCompany('$count'))),
        );
      } else {
        final companies = (result['companies'] is List) ? (result['companies'] as List).length : 0;
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(l10n.adminPlatformIdleSweep('$count', '$companies'))),
        );
      }
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _drainingTriggers = false;
      });
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

  bool _validatePositiveInt(String raw, {required int min}) {
    final n = int.tryParse(raw.trim());
    return n != null && n >= min;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      title: Text(widget.companyName),
      actions: [
        IconButton(onPressed: _loading ? null : _load, icon: const Icon(Icons.refresh)),
      ],
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(AppSpacing.md),
              children: [
                if (_error != null) ...[
                  InlineErrorBanner(message: _error!),
                  const SizedBox(height: AppSpacing.md),
                ],
                AdminMetricsAlerts(metrics: _metrics),
                AppPreferenceSection(
                  title: l10n.adminMetrics,
                  children: [
                    CompanyMetricsWrap(metrics: _metrics, includeAgentDetail: true),
                  ],
                ),
                AppPreferenceSection(
                  title: l10n.commonCompany,
                  children: [
                    AppValuePreference<String>(
                      title: l10n.commonDescription,
                      icon: Icons.notes_rounded,
                      value: _description,
                      onSave: _saveDescription,
                      maxLines: 2,
                    ),
                  ],
                ),
                AppPreferenceSection(
                  title: l10n.adminProdavanSubscription,
                  children: [
                    AppSwitchPreference(
                      title: l10n.adminLifetimeSubscription,
                      icon: Icons.all_inclusive_rounded,
                      value: _subscriptionLifetime,
                      onChanged: (v) async {
                        setState(() => _subscriptionLifetime = v);
                        await _saveSubscription();
                      },
                    ),
                    AppValuePreference<String>(
                      title: l10n.adminEndsAt,
                      icon: Icons.event_rounded,
                      value: _subscriptionEnds,
                      enabled: !_subscriptionLifetime,
                      onSave: (v) async {
                        setState(() => _subscriptionEnds = v);
                        await _saveSubscription();
                      },
                    ),
                  ],
                ),
                AppPreferenceSection(
                  title: l10n.adminPlatformEvents,
                  children: [
                    AppButton(
                      label: _drainingTriggers ? l10n.adminDraining : l10n.adminDrainProjectTriggers,
                      expanded: false,
                      onPressed: _drainingTriggers ? null : _drainTriggers,
                    ),
                    const SizedBox(height: AppSpacing.sm),
                    AppButton(
                      label: _drainingTriggers ? l10n.adminSweeping : l10n.adminSweepIdlePause,
                      expanded: false,
                      onPressed: _drainingTriggers ? null : () => _sweepIdlePause(companyId: widget.companyId),
                    ),
                    const SizedBox(height: AppSpacing.sm),
                    AppButton(
                      label: _drainingTriggers ? l10n.adminSweeping : l10n.adminSweepIdlePauseAll,
                      expanded: false,
                      variant: AppButtonVariant.outlined,
                      onPressed: _drainingTriggers ? null : () => _sweepIdlePause(),
                    ),
                    const SizedBox(height: AppSpacing.sm),
                    if (_platformEvents.isEmpty)
                      EmptyPlaceholder(
                        title: l10n.adminNoPlatformEventsYet,
                        icon: Icons.event_note_outlined,
                        iconSize: 32,
                        fillViewport: false,
                        padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
                      )
                    else
                      for (final ev in _platformEvents.take(12))
                        ListTile(
                          dense: true,
                          contentPadding: EdgeInsets.zero,
                          title: Text(ev['event_type']?.toString() ?? 'event'),
                          subtitle: Text(
                            [
                              if (ev['created_at'] != null) ev['created_at'].toString(),
                              if (ev['actor_sub'] != null) 'actor ${ev['actor_sub']}',
                            ].join(' · '),
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                  ],
                ),
                AppPreferenceSection(
                  title: l10n.adminCabinetQuotas,
                  children: [
                    AppValuePreference<String>(
                      title: l10n.adminMaxCabinets,
                      icon: Icons.dashboard_customize_outlined,
                      value: '$_maxCabinets',
                      digitsOnly: true,
                      validateInput: (v) => _validatePositiveInt(v, min: 1),
                      inputToValue: (v) => v,
                      onSave: (v) async {
                        final n = int.parse(v.trim());
                        await _saveQuotas(maxCabinets: n);
                      },
                    ),
                    AppValuePreference<String>(
                      title: l10n.adminMaxPackagesPerCabinet,
                      icon: Icons.inventory_2_outlined,
                      value: '$_maxPackages',
                      digitsOnly: true,
                      validateInput: (v) => _validatePositiveInt(v, min: 0),
                      inputToValue: (v) => v,
                      onSave: (v) async {
                        final n = int.parse(v.trim());
                        await _saveQuotas(maxPackages: n);
                      },
                    ),
                    AppValuePreference<String>(
                      title: l10n.adminMaxBundleImportMb,
                      icon: Icons.upload_file_outlined,
                      value: '$_maxBundleMb',
                      digitsOnly: true,
                      validateInput: (v) => _validatePositiveInt(v, min: 1),
                      inputToValue: (v) => v,
                      onSave: (v) async {
                        final n = int.parse(v.trim());
                        await _saveQuotas(maxBundleMb: n);
                      },
                    ),
                  ],
                ),
                AppPreferenceSection(
                  title: l10n.adminAgentRuntimePolicy,
                  children: [
                    AppChoicePreference<String>(
                      title: l10n.adminToolPreset,
                      icon: Icons.tune_rounded,
                      value: _toolPresets.contains(_toolPreset) ? _toolPreset : 'workspace_dev',
                      choices: _toolPresets,
                      keyFor: (v) => v,
                      labelFor: (v) => v,
                      iconFor: (v) => switch (v) {
                        'chat_readonly' => Icons.chat_bubble_outline_rounded,
                        'workspace_full' => Icons.code_rounded,
                        _ => Icons.developer_mode_outlined,
                      },
                      onSave: (v) async {
                        setState(() => _toolPreset = v);
                        await _savePolicy(toolPreset: v);
                      },
                    ),
                    AppValuePreference<String>(
                      title: l10n.adminPreferredProviderOptional,
                      icon: Icons.cloud_outlined,
                      value: _preferredProvider,
                      onSave: (v) async {
                        setState(() => _preferredProvider = v);
                        await _savePolicy(preferredProvider: v);
                      },
                    ),
                    AppValuePreference<String>(
                      title: l10n.adminModelAllowlist,
                      icon: Icons.list_alt_rounded,
                      value: _modelAllowlist,
                      onSave: (v) async {
                        setState(() => _modelAllowlist = v);
                        await _savePolicy(modelAllowlist: v);
                      },
                    ),
                    AppSwitchPreference(
                      title: l10n.adminPlatformFallback,
                      icon: Icons.swap_horiz_rounded,
                      value: _platformFallback,
                      onChanged: (v) async {
                        setState(() => _platformFallback = v);
                        await _savePolicy(platformFallback: v);
                      },
                    ),
                    AppValuePreference<String>(
                      title: l10n.adminMaxAgentTokensMonth,
                      icon: Icons.token_outlined,
                      value: _maxTokensMonth,
                      digitsOnly: true,
                      onSave: (v) async {
                        setState(() => _maxTokensMonth = v);
                        await _savePolicy(maxTokensMonth: v);
                      },
                    ),
                    AppValuePreference<String>(
                      title: l10n.adminMaxTokensPerRun,
                      icon: Icons.speed_rounded,
                      value: _maxTokensPerRun,
                      digitsOnly: true,
                      onSave: (v) async {
                        setState(() => _maxTokensPerRun = v);
                        await _savePolicy(maxTokensPerRun: v);
                      },
                    ),
                    AppValuePreference<String>(
                      title: l10n.adminMaxUsdCostMonth,
                      icon: Icons.attach_money_rounded,
                      value: _maxCostUsdMonth,
                      keyboardType: const TextInputType.numberWithOptions(decimal: true),
                      onSave: (v) async {
                        setState(() => _maxCostUsdMonth = v);
                        await _savePolicy(maxCostUsdMonth: v);
                      },
                    ),
                    AppValuePreference<String>(
                      title: l10n.adminMaxChatAttachmentMb,
                      icon: Icons.attachment_rounded,
                      value: '$_maxAttachmentMb',
                      digitsOnly: true,
                      validateInput: (v) => _validatePositiveInt(v, min: 1),
                      onSave: (v) async {
                        final n = int.parse(v.trim());
                        setState(() => _maxAttachmentMb = n);
                        await _savePolicy(maxAttachmentMb: n);
                      },
                    ),
                    AppValuePreference<String>(
                      title: l10n.adminIdlePauseAfterHours,
                      icon: Icons.pause_circle_outline_rounded,
                      value: _idlePauseHours,
                      digitsOnly: true,
                      onSave: (v) async {
                        setState(() => _idlePauseHours = v);
                        await _savePolicy(idlePauseHours: v);
                      },
                    ),
                    AppValuePreference<String>(
                      title: l10n.adminWebhookHmacSecret,
                      icon: Icons.webhook_rounded,
                      value: '',
                      obscureText: true,
                      presentValue: (_) => _webhookHmacConfigured
                          ? l10n.adminWebhookHmacConfigured
                          : l10n.adminWebhookHmacNotSet,
                      formatInputValue: (_) => '',
                      onSave: (v) async {
                        if (v.trim().isEmpty) return;
                        await _savePolicy(webhookSecret: v.trim());
                      },
                    ),
                    AppValuePreference<String>(
                      title: l10n.adminTelegramHmacSecret,
                      icon: Icons.telegram,
                      value: '',
                      obscureText: true,
                      presentValue: (_) => _telegramHmacConfigured
                          ? l10n.adminTelegramHmacConfigured
                          : l10n.adminTelegramHmacNotSet,
                      formatInputValue: (_) => '',
                      onSave: (v) async {
                        if (v.trim().isEmpty) return;
                        await _savePolicy(telegramSecret: v.trim());
                      },
                    ),
                  ],
                ),
              ],
            ),
    );
  }
}
