import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/danger_confirm_page.dart';
import 'package:prodavan/features/admin/ai_http_provider_select_page.dart';
import 'package:prodavan/features/admin/ai_key_integration_type.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// AI key detail — type-first + optional HTTP provider catalog (L03/L04).
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
  bool _loading = true;
  Map<String, dynamic>? _key;
  List<Map<String, dynamic>> _companies = const [];
  List<Map<String, dynamic>> _httpProviders = const [];
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
      List<Map<String, dynamic>> providers = const [];
      try {
        providers = await adminContext.api
            .listCatalogEntries(kAiHttpProvidersCatalogId);
      } catch (_) {
        // Catalog optional until migration applied.
      }
      if (!mounted) return;
      setState(() {
        _key = key;
        _companies = companies;
        _httpProviders = providers;
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

  String _providerSubtitle(AppLocalizations l10n) {
    final apiKind = _key?['api_kind'] as String? ?? '';
    final provider = _key?['provider'] as String? ?? '';
    for (final p in _httpProviders) {
      final payload = (p['payload'] as Map?)?.cast<String, dynamic>() ?? {};
      if (payload['api_kind'] == apiKind &&
          payload['agent_provider'] == provider) {
        return p['title'] as String? ?? p['id'] as String;
      }
    }
    for (final p in _httpProviders) {
      final payload = (p['payload'] as Map?)?.cast<String, dynamic>() ?? {};
      if (payload['api_kind'] == apiKind) {
        return p['title'] as String? ?? p['id'] as String;
      }
    }
    if (apiKind.isEmpty) return l10n.commonNotSet;
    return apiKind;
  }

  Future<void> _saveType(AiKeyIntegrationType t) async {
    if (t.isApiKey) {
      // Keep current HTTP mapping until user picks a provider.
      return;
    }
    await adminContext.api.patchAiKey(
      keyId: widget.keyId,
      provider: t.provider,
      apiKind: t.apiKind,
    );
    await _load();
  }

  Future<void> _pickProvider() async {
    final item = await AiHttpProviderSelectPage.push(
      context,
      selectedApiKind: _key?['api_kind'] as String?,
      selectedProvider: _key?['provider'] as String?,
    );
    if (item == null) return;
    final apiKind = item.payload['api_kind'] as String? ?? 'custom';
    final provider = item.payload['agent_provider'] as String? ?? 'codex';
    try {
      await adminContext.api.patchAiKey(
        keyId: widget.keyId,
        provider: provider,
        apiKind: apiKind,
      );
      await _load();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
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

    final type = _type;
    final hasSecret = (_key?['secret_ref_prefix'] as String? ?? '').isNotEmpty;
    final status = _key?['status'] as String? ?? '';
    final nextRaw = _key?['next_renewal_at'] as String? ?? '';
    final nextDisplay = formatSubscriptionDate(nextRaw);

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
              await adminContext.api
                  .patchAiKey(keyId: widget.keyId, name: v.trim());
              await _load();
            },
          ),
          AppChoicePreference<AiKeyIntegrationType>(
            title: l10n.adminIntegrationType,
            icon: Icons.category_outlined,
            value: type,
            choices: AiKeyIntegrationType.all,
            keyFor: (v) => v.id,
            labelFor: (v) => _typeLabel(l10n, v),
            onSave: (v) async {
              if (v.isApiKey) {
                if (!type.isApiKey) {
                  await adminContext.api.patchAiKey(
                    keyId: widget.keyId,
                    provider: 'codex',
                    apiKind: 'openai_api',
                  );
                  await _load();
                }
                return;
              }
              await _saveType(v);
            },
          ),
          if (type.isApiKey)
            AppNavPreference(
              title: l10n.commonProvider,
              icon: Icons.cloud_outlined,
              subtitle: Text(_providerSubtitle(l10n)),
              onTap: _pickProvider,
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
          AppSubscriptionPreference(
            title: l10n.adminNextRenewal,
            endsAt: nextDisplay,
            emptyLabel: l10n.commonNotSet,
            onEndsAtSave: (raw) async {
              if (raw.trim().isEmpty) {
                await adminContext.api.patchAiKey(
                  keyId: widget.keyId,
                  clearNextRenewalAt: true,
                );
              } else {
                final iso = subscriptionDateToIso(raw);
                if (iso == null) return;
                await adminContext.api.patchAiKey(
                  keyId: widget.keyId,
                  nextRenewalAt: iso,
                );
              }
              await _load();
            },
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
        ],
      ),
    );
  }
}
