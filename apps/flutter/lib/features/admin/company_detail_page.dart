import 'package:flutter/material.dart';

import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_form.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_section_header.dart';
import 'package:prodavan/core/widgets/app_text_field.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';
import 'package:prodavan/core/widgets/stat_tile.dart';

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
  final _quotaFormKey = GlobalKey<FormState>();
  final _maxCabinetsCtrl = TextEditingController();
  final _maxPackagesCtrl = TextEditingController();
  final _maxBundleMbCtrl = TextEditingController();
  final _preferredProviderCtrl = TextEditingController();
  final _maxTokensMonthCtrl = TextEditingController();
  final _maxTokensPerRunCtrl = TextEditingController();
  final _subscriptionEndsCtrl = TextEditingController();

  bool _loading = true;
  bool _savingQuotas = false;
  bool _savingPolicy = false;
  bool _savingSubscription = false;
  bool _subscriptionLifetime = false;
  String? _error;
  Map<String, dynamic>? _metrics;
  String _toolPreset = 'workspace_dev';
  bool _platformFallback = true;

  static const _toolPresets = ['chat_readonly', 'workspace_dev', 'workspace_full'];

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _maxCabinetsCtrl.dispose();
    _maxPackagesCtrl.dispose();
    _maxBundleMbCtrl.dispose();
    _preferredProviderCtrl.dispose();
    _maxTokensMonthCtrl.dispose();
    _maxTokensPerRunCtrl.dispose();
    _subscriptionEndsCtrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final detail = await adminContext.api.getCompany(widget.companyId);
      if (!mounted) return;
      final quota = detail['cabinet_quota'] as Map<String, dynamic>? ?? const {};
      final policy = detail['agent_policy'] as Map<String, dynamic>? ?? const {};
      setState(() {
        _metrics = detail['metrics'] as Map<String, dynamic>?;
        _maxCabinetsCtrl.text = '${quota['max_cabinets'] ?? 10}';
        _maxPackagesCtrl.text = '${quota['max_packages_per_cabinet'] ?? 20}';
        _maxBundleMbCtrl.text = '${quota['max_bundle_import_mb'] ?? 50}';
        _toolPreset = policy['tool_preset'] as String? ?? 'workspace_dev';
        _preferredProviderCtrl.text = policy['preferred_provider'] as String? ?? '';
        _platformFallback = policy['platform_fallback'] as bool? ?? true;
        _maxTokensMonthCtrl.text = policy['max_agent_tokens_month']?.toString() ?? '';
        _maxTokensPerRunCtrl.text = policy['max_tokens_per_run']?.toString() ?? '';
        final metrics = detail['metrics'] as Map<String, dynamic>? ?? const {};
        _subscriptionLifetime = metrics['subscription_lifetime'] == true;
        final endsAt = metrics['subscription_ends_at'];
        _subscriptionEndsCtrl.text = endsAt is String ? endsAt.split('T').first : '';
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

  Future<void> _saveQuotas() async {
    if (!_quotaFormKey.currentState!.validate()) return;
    setState(() {
      _savingQuotas = true;
      _error = null;
    });
    try {
      await adminContext.api.setCabinetQuotas(
        companyId: widget.companyId,
        maxCabinets: int.parse(_maxCabinetsCtrl.text.trim()),
        maxPackagesPerCabinet: int.parse(_maxPackagesCtrl.text.trim()),
        maxBundleImportMb: int.parse(_maxBundleMbCtrl.text.trim()),
      );
      await _load();
      if (!mounted) return;
      setState(() => _savingQuotas = false);
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Quotas saved')));
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _savingQuotas = false;
      });
    }
  }

  Future<void> _saveSubscription() async {
    if (!_subscriptionLifetime && _subscriptionEndsCtrl.text.trim().isEmpty) {
      setState(() => _error = 'Set end date or enable lifetime subscription');
      return;
    }
    setState(() {
      _savingSubscription = true;
      _error = null;
    });
    try {
      final endsRaw = _subscriptionEndsCtrl.text.trim();
      final endsAt = endsRaw.isEmpty
          ? null
          : endsRaw.contains('T')
              ? endsRaw
              : '${endsRaw}T00:00:00Z';
      await adminContext.api.setCompanySubscription(
        companyId: widget.companyId,
        subscriptionLifetime: _subscriptionLifetime,
        subscriptionEndsAt: _subscriptionLifetime ? null : endsAt,
      );
      await _load();
      if (!mounted) return;
      setState(() => _savingSubscription = false);
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Subscription saved')));
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _savingSubscription = false;
      });
    }
  }

  Future<void> _savePolicy() async {
    setState(() {
      _savingPolicy = true;
      _error = null;
    });
    try {
      final provider = _preferredProviderCtrl.text.trim();
      await adminContext.api.setAgentPolicy(
        companyId: widget.companyId,
        toolPreset: _toolPreset,
        preferredProvider: provider.isEmpty ? null : provider,
        platformFallback: _platformFallback,
        maxAgentTokensMonth: _optionalPositiveInt(_maxTokensMonthCtrl.text),
        maxTokensPerRun: _optionalPositiveInt(_maxTokensPerRunCtrl.text),
      );
      await _load();
      if (!mounted) return;
      setState(() => _savingPolicy = false);
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Agent policy saved')));
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _savingPolicy = false;
      });
    }
  }

  String _metric(String key, {String fallback = '0'}) {
    final v = _metrics?[key];
    if (v == null) return fallback;
    return '$v';
  }

  int _asInt(Object? v) {
    if (v is int) return v;
    if (v is num) return v.toInt();
    return 0;
  }

  @override
  Widget build(BuildContext context) {
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
                if (_asInt(_metrics?['ai_keys_expiring_soon']) > 0)
                  InlineErrorBanner(
                    message:
                        '${_metric('ai_keys_expiring_soon')} AI key(s) renew soon'
                        '${_metrics?['next_key_renewal_at'] != null ? ' · next ${_metrics!['next_key_renewal_at']}' : ''}',
                  ),
                if (_asInt(_metrics?['employees_total']) > 0 && _asInt(_metrics?['ai_keys_bound']) == 0)
                  const InlineErrorBanner(message: 'No AI keys bound — agent will return NO_AI_KEY'),
                if (_metrics?['high_agent_usage'] == true)
                  InlineErrorBanner(
                    message: 'High agent token usage (${_metric('agent_tokens_used')} tokens)',
                  ),
                if (_metrics?['subscription_lifetime'] != true && _metrics?['subscription_expired'] == true)
                  InlineErrorBanner(message: 'Subscription expired'),
                if (_metrics?['subscription_lifetime'] != true &&
                    _metrics?['subscription_expiring_soon'] == true)
                  InlineErrorBanner(
                    message: 'Subscription expiring · ${_metric('subscription_ends_at', fallback: '—')}',
                  ),
                const AppSectionHeader(title: 'Metrics'),
                Wrap(
                  spacing: AppSpacing.sm,
                  runSpacing: AppSpacing.sm,
                  children: [
                    SizedBox(
                      width: 160,
                      child: StatTile(label: 'Employees', value: _metric('employees_total')),
                    ),
                    SizedBox(
                      width: 160,
                      child: StatTile(
                        label: 'Active employees',
                        value: _metric('employees_active'),
                      ),
                    ),
                    SizedBox(
                      width: 160,
                      child: StatTile(
                        label: 'Cabinets',
                        value: '${_metric('active_cabinets')} / ${_metric('cabinets_quota')}',
                      ),
                    ),
                    SizedBox(
                      width: 160,
                      child: StatTile(label: 'Projects', value: _metric('projects_total')),
                    ),
                    SizedBox(
                      width: 160,
                      child: StatTile(label: 'Agent tokens', value: _metric('agent_tokens_used')),
                    ),
                    SizedBox(
                      width: 160,
                      child: StatTile(label: 'Agent messages', value: _metric('agent_messages')),
                    ),
                    SizedBox(
                      width: 160,
                      child: StatTile(label: 'AI keys bound', value: _metric('ai_keys_bound')),
                    ),
                    SizedBox(
                      width: 160,
                      child: StatTile(label: 'Storage (bytes)', value: _metric('storage_bytes')),
                    ),
                    if (_metrics?['last_activity_at'] != null)
                      SizedBox(
                        width: 200,
                        child: StatTile(
                          label: 'Last activity',
                          value: _metric('last_activity_at', fallback: '—'),
                        ),
                      ),
                  ],
                ),
                const SizedBox(height: AppSpacing.lg),
                const AppSectionHeader(title: 'Prodavan subscription'),
                SwitchListTile(
                  title: const Text('Lifetime subscription'),
                  value: _subscriptionLifetime,
                  onChanged: _savingSubscription
                      ? null
                      : (v) => setState(() => _subscriptionLifetime = v),
                ),
                AppTextField(
                  controller: _subscriptionEndsCtrl,
                  label: 'Ends at (YYYY-MM-DD)',
                  enabled: !_savingSubscription && !_subscriptionLifetime,
                ),
                AppButton(
                  label: _savingSubscription ? 'Saving…' : 'Save subscription',
                  expanded: false,
                  onPressed: _savingSubscription ? null : _saveSubscription,
                ),
                const SizedBox(height: AppSpacing.lg),
                const AppSectionHeader(title: 'Cabinet quotas'),
                AppForm(
                  formKey: _quotaFormKey,
                  children: [
                    AppTextField(
                      controller: _maxCabinetsCtrl,
                      label: 'Max cabinets',
                      keyboardType: TextInputType.number,
                      validator: (v) => _positiveInt(v, min: 1),
                    ),
                    AppTextField(
                      controller: _maxPackagesCtrl,
                      label: 'Max packages per cabinet',
                      keyboardType: TextInputType.number,
                      validator: (v) => _positiveInt(v, min: 0),
                    ),
                    AppTextField(
                      controller: _maxBundleMbCtrl,
                      label: 'Max bundle import (MB)',
                      keyboardType: TextInputType.number,
                      validator: (v) => _positiveInt(v, min: 1),
                    ),
                    AppButton(
                      label: _savingQuotas ? 'Saving…' : 'Save quotas',
                      expanded: false,
                      onPressed: _savingQuotas ? null : _saveQuotas,
                    ),
                  ],
                ),
                const SizedBox(height: AppSpacing.lg),
                const AppSectionHeader(title: 'Agent runtime policy'),
                InputDecorator(
                  decoration: const InputDecoration(labelText: 'Tool preset'),
                  child: DropdownButtonHideUnderline(
                    child: DropdownButton<String>(
                      value: _toolPresets.contains(_toolPreset) ? _toolPreset : 'workspace_dev',
                      isExpanded: true,
                      items: [
                        for (final p in _toolPresets)
                          DropdownMenuItem(value: p, child: Text(p)),
                      ],
                      onChanged: _savingPolicy
                          ? null
                          : (v) => setState(() => _toolPreset = v ?? 'workspace_dev'),
                    ),
                  ),
                ),
                const SizedBox(height: AppSpacing.md),
                AppTextField(
                  controller: _preferredProviderCtrl,
                  label: 'Preferred provider (optional)',
                  enabled: !_savingPolicy,
                ),
                const SizedBox(height: AppSpacing.sm),
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Platform fallback'),
                  value: _platformFallback,
                  onChanged: _savingPolicy ? null : (v) => setState(() => _platformFallback = v),
                ),
                AppTextField(
                  controller: _maxTokensMonthCtrl,
                  label: 'Max agent tokens / month (optional)',
                  keyboardType: TextInputType.number,
                  enabled: !_savingPolicy,
                ),
                AppTextField(
                  controller: _maxTokensPerRunCtrl,
                  label: 'Max tokens per run (optional)',
                  keyboardType: TextInputType.number,
                  enabled: !_savingPolicy,
                ),
                AppButton(
                  label: _savingPolicy ? 'Saving…' : 'Save agent policy',
                  expanded: false,
                  onPressed: _savingPolicy ? null : _savePolicy,
                ),
              ],
            ),
    );
  }

  String? _positiveInt(String? value, {required int min}) {
    final n = int.tryParse(value?.trim() ?? '');
    if (n == null || n < min) return 'Enter integer ≥ $min';
    return null;
  }

  int? _optionalPositiveInt(String value) {
    final trimmed = value.trim();
    if (trimmed.isEmpty) return null;
    final n = int.tryParse(trimmed);
    if (n == null || n < 1) return null;
    return n;
  }
}
