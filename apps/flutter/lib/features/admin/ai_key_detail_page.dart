import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/danger_confirm_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// AI key detail — seamless preference editing (L03/L04).
class AdminAiKeyDetailPage extends StatefulWidget {
  const AdminAiKeyDetailPage({
    super.key,
    required this.keyId,
    required this.keyName,
  });

  final String keyId;
  final String keyName;

  @override
  State<AdminAiKeyDetailPage> createState() => _AdminAiKeyDetailPageState();
}

class _AdminAiKeyDetailPageState extends State<AdminAiKeyDetailPage> {
  static const _providers = ['cursor', 'codex', 'claude_code'];
  static const _apiKinds = [
    'cursor_sdk',
    'codex_sdk',
    'claude_agent_sdk',
    'openai_api',
    'anthropic_api',
  ];

  bool _loading = true;
  Map<String, dynamic>? _key;
  List<Map<String, dynamic>> _companies = const [];
  String _displayName = '';

  @override
  void initState() {
    super.initState();
    _displayName = widget.keyName;
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final key = await adminContext.api.getAiKey(widget.keyId);
      final companies = await adminContext.api.listCompanies();
      if (!mounted) return;
      setState(() {
        _key = key;
        _companies = companies;
        _displayName = key['name'] as String? ?? widget.keyName;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      AppErrors.showSnack(context, e);
    }
  }

  List<String> get _boundIds {
    final ids = _key?['company_ids'];
    if (ids is List) return ids.cast<String>();
    return const [];
  }

  Set<String> get _companyChoices =>
      _companies.map((c) => c['id'] as String).toSet();

  String _companyLabel(String id) {
    for (final c in _companies) {
      if (c['id'] == id) return c['name'] as String? ?? id;
    }
    return id;
  }

  String _bindingsSubtitle(AppLocalizations l10n) {
    final count = _boundIds.length;
    if (count == 0) return l10n.commonNotSet;
    if (count == 1) return _companyLabel(_boundIds.first);
    return l10n.adminBindingsCount(count);
  }

  Future<void> _renewKey() async {
    try {
      await adminContext.api.renewAiKey(keyId: widget.keyId, months: 1);
      await _load();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  Future<void> _disableKey() async {
    final l10n = AppLocalizations.of(context);
    if (_key?['status'] == 'disabled') return;
    final ok = await DangerConfirmPage.push(
      context,
      title: l10n.adminDisableAiKey,
      message: l10n.adminDisableKeyConfirm(_displayName),
      confirmLabel: l10n.commonDisable,
    );
    if (!ok) return;
    try {
      await adminContext.api.patchAiKey(keyId: widget.keyId, status: 'disabled');
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

    final provider = _key?['provider'] as String? ?? 'cursor';
    final apiKind = _key?['api_kind'] as String? ?? 'cursor_sdk';
    final status = _key?['status'] as String? ?? '—';
    final hasSecret = (_key?['secret_ref_prefix'] as String? ?? '').isNotEmpty;

    return AppScaffold(
      title: Text(_displayName),
      actions: [
        IconButton(onPressed: _load, icon: const Icon(Icons.refresh)),
      ],
      body: ListView(
        padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
        children: [
          AppValuePreference<String>(
            title: l10n.commonName,
            icon: Icons.label_outline_rounded,
            value: _displayName,
            onSave: (v) async {
              await adminContext.api.patchAiKey(keyId: widget.keyId, name: v.trim());
              await _load();
            },
          ),
          AppChoicePreference<String>(
            title: l10n.commonProvider,
            icon: Icons.cloud_outlined,
            value: _providers.contains(provider) ? provider : 'cursor',
            choices: _providers,
            keyFor: (v) => v,
            labelFor: (v) => v,
            onSave: (v) async {
              await adminContext.api.patchAiKey(keyId: widget.keyId, provider: v);
              await _load();
            },
          ),
          AppChoicePreference<String>(
            title: l10n.adminApiKind,
            icon: Icons.api_outlined,
            value: _apiKinds.contains(apiKind) ? apiKind : 'cursor_sdk',
            choices: _apiKinds,
            keyFor: (v) => v,
            labelFor: (v) => v,
            onSave: (v) async {
              await adminContext.api.patchAiKey(keyId: widget.keyId, apiKind: v);
              await _load();
            },
          ),
          AppMultiChoicePreference<String>(
            title: l10n.adminCompanyBindings,
            icon: Icons.link_rounded,
            values: _boundIds.toSet(),
            choices: _companyChoices.toList(),
            keyFor: (v) => v,
            labelFor: _companyLabel,
            presentValues: (_) => _bindingsSubtitle(l10n),
            onSave: (ids) async {
              await adminContext.api.setAiKeyCompanies(
                keyId: widget.keyId,
                companyIds: ids.toList(),
              );
              await _load();
            },
          ),
          AppPreferenceTile(
            title: l10n.commonStatus,
            icon: Icons.info_outline_rounded,
            subtitle: Text(status),
          ),
          AppNavPreference(
            title: l10n.adminRenewPlusOneMonth,
            icon: Icons.update_rounded,
            onTap: _renewKey,
          ),
          if (status != 'disabled')
            AppNavPreference(
              title: l10n.adminDisableKey,
              icon: Icons.block_rounded,
              accentColor: Theme.of(context).colorScheme.error,
              onTap: _disableKey,
            ),
          AppValuePreference<String>(
            title: l10n.commonSecret,
            icon: Icons.key_outlined,
            value: '',
            obscureText: true,
            presentValue: (_) => hasSecret ? '••••••••' : l10n.commonNotSet,
            formatInputValue: (_) => '',
            onSave: (v) async {
              if (v.trim().isEmpty) return;
              await adminContext.api.rotateAiKeySecret(
                keyId: widget.keyId,
                secret: v.trim(),
              );
              await _load();
            },
          ),
        ],
      ),
    );
  }
}
