import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/refresh/app_auto_refresh.dart';
import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_confirm_page.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/features/company/company_entity_source.dart';
import 'package:prodavan/features/admin/ai_key_integration_type.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Company AI key detail — full edit for local keys; RO for platform-bound.
class CompanyAiKeyDetailPage extends StatefulWidget {
  const CompanyAiKeyDetailPage({
    super.key,
    required this.companyId,
    required this.keyId,
    required this.keyName,
  });

  final String companyId;
  final String keyId;
  final String keyName;

  @override
  State<CompanyAiKeyDetailPage> createState() => _CompanyAiKeyDetailPageState();
}

class _CompanyAiKeyDetailPageState extends State<CompanyAiKeyDetailPage> {
  late final AppAutoRefreshBinder _autoRefresh;
  bool _loading = true;
  Map<String, dynamic>? _key;
  String _displayName = '';
  List<Map<String, dynamic>> _employees = const [];
  List<Map<String, dynamic>> _cabinets = const [];
  List<Map<String, dynamic>> _projects = const [];
  Set<String> _boundEmployeeIds = const {};
  Set<String> _boundCabinetIds = const {};
  Set<String> _boundProjectIds = const {};

  bool get _writable => companyEntityWritable(_key ?? const {});

  bool get _scopeEditable => _key != null;

  String _bindingCountSubtitle(AppLocalizations l10n, int count) {
    if (count == 0) return l10n.commonNotSet;
    return l10n.adminBindingsSelected('$count');
  }

  @override
  void initState() {
    super.initState();
    _displayName = widget.keyName;
    _autoRefresh = AppAutoRefreshBinder(
      onTick: () => _load(silent: true),
      isActive: () => appAutoRefreshIsActive(context),
    )..attach();
    _load();
  }

  @override
  void dispose() {
    _autoRefresh.dispose();
    super.dispose();
  }

  Future<void> _load({bool silent = false}) async {
    if (!silent && mounted) setState(() => _loading = true);
    try {
      final key = await companyContext.api.getAiKey(
        companyId: widget.companyId,
        keyId: widget.keyId,
      );
      final employees = await companyContext.api.listEmployees(widget.companyId);
      final cabinets = await companyContext.api.listOrgCabinets(widget.companyId);
      final containers = await companyContext.api.listContainers(companyId: widget.companyId);
      Map<String, dynamic> bindings = const {};
      try {
        bindings = await companyContext.api.getAiKeyScopeBindings(
          companyId: widget.companyId,
          keyId: widget.keyId,
        );
      } catch (_) {}
      if (!mounted) return;
      if (silent && appRefreshDataEquals(_key, key) && !_loading) return;
      setState(() {
        _key = key;
        _displayName = key['name'] as String? ?? widget.keyName;
        _employees = employees;
        _cabinets = cabinets;
        _projects = containers;
        _boundEmployeeIds = (bindings['employee_ids'] as List?)?.cast<String>().toSet() ?? {};
        _boundCabinetIds = (bindings['cabinet_ids'] as List?)?.cast<String>().toSet() ?? {};
        _boundProjectIds = (bindings['project_ids'] as List?)?.cast<String>().toSet() ?? {};
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      if (silent) return;
      setState(() => _loading = false);
      AppErrors.showSnack(context, e);
    }
  }

  AiKeyIntegrationType get _type {
    final provider = _key?['provider'] as String? ?? 'cursor';
    final apiKind = _key?['api_kind'] as String? ?? 'cursor_sdk';
    return AiKeyIntegrationType.fromKey(provider: provider, apiKind: apiKind);
  }

  String _typeLabel(AppLocalizations l10n, AiKeyIntegrationType t) {
    switch (t.id) {
      case 'cursor_sdk':
        return l10n.adminTypeCursorSdk;
      case 'codex_sdk':
        return l10n.adminTypeCodexSdk;
      case 'claude_agent_sdk':
        return l10n.adminTypeClaudeSdk;
      default:
        return l10n.adminTypeApiKey;
    }
  }

  Future<void> _saveType(AiKeyIntegrationType t) async {
    if (!_writable || t.isApiKey) return;
    await companyContext.api.patchAiKey(
      companyId: widget.companyId,
      keyId: widget.keyId,
      provider: t.provider,
      apiKind: t.apiKind,
    );
    await _load();
  }

  Future<void> _renewKey() async {
    if (!_writable) return;
    try {
      await companyContext.api.renewAiKey(
        companyId: widget.companyId,
        keyId: widget.keyId,
      );
      await _load();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  bool _isSubscriptionExpired(Map<String, dynamic>? key) {
    if (key == null) return false;
    final raw = (key['next_renewal_at'] as String? ?? '').trim();
    if (raw.isEmpty) return false;
    final dt = DateTime.tryParse(raw);
    if (dt == null) return false;
    return !dt.toUtc().isAfter(DateTime.now().toUtc());
  }

  bool _isSuspended(Map<String, dynamic>? key) {
    if (key == null) return true;
    final status = key['status'] as String? ?? '';
    if (status == 'disabled' || status == 'expired') return true;
    if (_isSubscriptionExpired(key)) return true;
    final secret = key['secret_ref_prefix'] as String? ?? '';
    return secret.trim().isEmpty;
  }

  Future<void> _pauseKey() async {
    if (!_writable || _isSuspended(_key)) return;
    final l10n = AppLocalizations.of(context);
    final ok = await AppConfirmPage.push(
      context,
      title: l10n.adminDisableKey,
      message: l10n.adminDisableKeyConfirm(_displayName),
      confirmLabel: l10n.adminDisableKey,
      severity: AppStatusSeverity.warning,
    );
    if (!ok) return;
    try {
      await companyContext.api.patchAiKey(
        companyId: widget.companyId,
        keyId: widget.keyId,
        status: 'disabled',
      );
      await _load();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  Future<void> _resumeKey() async {
    if (!_writable || _isSubscriptionExpired(_key)) return;
    try {
      await companyContext.api.patchAiKey(
        companyId: widget.companyId,
        keyId: widget.keyId,
        status: 'active',
      );
      await _load();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    if (_loading) {
      return AppScaffold(
        title: Text(_displayName),
        body: const Center(child: CircularProgressIndicator()),
      );
    }

    final type = _type;
    final hasSecret = (_key?['secret_ref_prefix'] as String? ?? '').isNotEmpty;
    final suspended = _isSuspended(_key);
    final subscriptionExpired = _isSubscriptionExpired(_key);
    final nextRaw = _key?['next_renewal_at'] as String? ?? '';
    final nextDisplay = formatSubscriptionDate(nextRaw);
    final warning = context.appColors.warning;

    return AppScaffold(
      title: Text(_displayName),
      body: ListView(
        padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
        children: [
          AppValuePreference<String>(
            title: l10n.commonName,
            icon: Icons.label_outline_rounded,
            value: _displayName,
            enabled: _writable,
            onSave: (v) async {
              await companyContext.api.patchAiKey(
                companyId: widget.companyId,
                keyId: widget.keyId,
                name: v.trim(),
              );
              await _load();
            },
          ),
          AppChoicePreference<AiKeyIntegrationType>(
            title: l10n.adminIntegrationType,
            icon: Icons.category_outlined,
            value: type,
            enabled: _writable,
            choices: AiKeyIntegrationType.all,
            keyFor: (v) => v.id,
            labelFor: (v) => _typeLabel(l10n, v),
            onSave: _saveType,
          ),
          if (type.isApiKey)
            AppValuePreference<String>(
              title: l10n.adminHttpEndpoint,
              icon: Icons.cloud_outlined,
              value: _key?['api_kind'] as String? ?? '',
              enabled: false,
              presentValue: (v) => v.isEmpty ? l10n.commonNotSet : v,
              onSave: (_) async {},
            ),
          AppValuePreference<String>(
            title: l10n.commonSecret,
            icon: Icons.key_outlined,
            value: '',
            enabled: _writable,
            obscureText: true,
            presentValue: (_) => hasSecret ? '••••••••' : l10n.commonNotSet,
            formatInputValue: (_) => '',
            onSave: (v) async {
              if (v.trim().isEmpty) return;
              await companyContext.api.rotateAiKeySecret(
                companyId: widget.companyId,
                keyId: widget.keyId,
                secret: v.trim(),
              );
              await _load();
            },
          ),
          AppSubscriptionPreference(
            title: l10n.adminSubscription,
            enabled: _writable,
            endsAt: nextDisplay,
            emptyLabel: l10n.commonNotSet,
            accentColor: subscriptionExpired ? warning : null,
            onEndsAtSave: (raw) async {
              if (!_writable) return;
              if (raw.trim().isEmpty) {
                await companyContext.api.patchAiKey(
                  companyId: widget.companyId,
                  keyId: widget.keyId,
                  clearNextRenewalAt: true,
                );
              } else {
                final iso = subscriptionDateToIso(raw);
                if (iso == null) return;
                await companyContext.api.patchAiKey(
                  companyId: widget.companyId,
                  keyId: widget.keyId,
                  nextRenewalAt: iso,
                );
              }
              await _load();
            },
          ),
          if (_writable && nextDisplay.trim().isNotEmpty)
            AppNavPreference(
              title: l10n.adminRenewPlusOneMonth,
              icon: Icons.update_rounded,
              onTap: _renewKey,
            ),
          if (_writable && suspended && !subscriptionExpired)
            AppNavPreference(
              title: l10n.adminResumeKey,
              icon: Icons.play_circle_outline_rounded,
              onTap: _resumeKey,
            ),
          if (_writable && !suspended)
            AppNavPreference(
              title: l10n.adminDisableKey,
              icon: Icons.pause_circle_outline_rounded,
              onTap: _pauseKey,
            ),
          if (hasSecret && _scopeEditable && _employees.isNotEmpty)
            AppMultiChoicePreference<String>(
              title: l10n.navEmployees,
              icon: Icons.group_outlined,
              values: _boundEmployeeIds,
              choices: _employees.map((e) => e['id'] as String).whereType<String>().toList(),
              keyFor: (id) => id,
              labelFor: (id) {
                final e = _employees.firstWhere((x) => x['id'] == id, orElse: () => {'login': id});
                return e['login'] as String? ?? e['display_name'] as String? ?? id;
              },
              presentValues: (ids) => _bindingCountSubtitle(l10n, ids.length),
              onSave: (ids) async {
                await companyContext.api.setAiKeyScopeBindings(
                  companyId: widget.companyId,
                  keyId: widget.keyId,
                  employeeIds: ids.toList(),
                  cabinetIds: _boundCabinetIds.toList(),
                  projectIds: _boundProjectIds.toList(),
                );
                await _load();
              },
            ),
          if (hasSecret && _scopeEditable && _cabinets.isNotEmpty)
            AppMultiChoicePreference<String>(
              title: l10n.navCabinets,
              icon: Icons.view_module_outlined,
              values: _boundCabinetIds,
              choices: _cabinets.map((c) => c['id'] as String).whereType<String>().toList(),
              keyFor: (id) => id,
              labelFor: (id) {
                final c = _cabinets.firstWhere((x) => x['id'] == id, orElse: () => {'name': id});
                return c['name'] as String? ?? id;
              },
              presentValues: (ids) => _bindingCountSubtitle(l10n, ids.length),
              onSave: (ids) async {
                await companyContext.api.setAiKeyScopeBindings(
                  companyId: widget.companyId,
                  keyId: widget.keyId,
                  employeeIds: _boundEmployeeIds.toList(),
                  cabinetIds: ids.toList(),
                  projectIds: _boundProjectIds.toList(),
                );
                await _load();
              },
            ),
          if (hasSecret && _scopeEditable && _projects.isNotEmpty)
            AppMultiChoicePreference<String>(
              title: l10n.navProjects,
              icon: Icons.folder_outlined,
              values: _boundProjectIds,
              choices: _projects.map((p) => p['id'] as String).whereType<String>().toList(),
              keyFor: (id) => id,
              labelFor: (id) {
                final p = _projects.firstWhere(
                  (x) => x['id'] == id,
                  orElse: () => {'project_name': id},
                );
                return p['project_name'] as String? ?? p['name'] as String? ?? id;
              },
              presentValues: (ids) => _bindingCountSubtitle(l10n, ids.length),
              onSave: (ids) async {
                await companyContext.api.setAiKeyScopeBindings(
                  companyId: widget.companyId,
                  keyId: widget.keyId,
                  employeeIds: _boundEmployeeIds.toList(),
                  cabinetIds: _boundCabinetIds.toList(),
                  projectIds: ids.toList(),
                );
                await _load();
              },
            ),
        ],
      ),
    );
  }
}
