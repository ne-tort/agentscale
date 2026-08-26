import 'package:flutter/material.dart';

import 'package:prodavan/core/refresh/app_auto_refresh.dart';
import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/widgets/app_collection_view_mode.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_inline_add_field.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_confirm_page.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/admin/ai_http_provider_select_page.dart';
import 'package:prodavan/features/admin/ai_key_detail_page.dart';
import 'package:prodavan/features/admin/ai_key_integration_type.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Platform Admin AI keys list (L03/L04).
class AdminAiKeyListPage extends StatefulWidget {
  const AdminAiKeyListPage({super.key, this.embedded = false});

  final bool embedded;

  @override
  State<AdminAiKeyListPage> createState() => _AdminAiKeyListPageState();
}

class _AdminAiKeyListPageState extends State<AdminAiKeyListPage> {
  static const _viewPageKey = 'admin.aiKeys';

  final _viewMode = AppCollectionViewModeStore(_viewPageKey);
  late final AppAutoRefreshBinder _autoRefresh;
  bool _loading = true;
  List<Map<String, dynamic>> _keys = const [];
  List<Map<String, dynamic>> _httpProviders = const [];

  @override
  void initState() {
    super.initState();
    _autoRefresh = AppAutoRefreshBinder(
      onTick: () => _reload(silent: true),
      isActive: () => appAutoRefreshIsActive(context),
    )..attach();
    _viewMode.load();
    _reload();
  }

  @override
  void dispose() {
    _autoRefresh.dispose();
    _viewMode.dispose();
    super.dispose();
  }

  Future<void> _reload({bool silent = false}) async {
    if (!silent && mounted) {
      setState(() => _loading = true);
    }
    try {
      final items = await adminContext.api.listAiKeys();
      List<Map<String, dynamic>> providers = const [];
      try {
        providers = await adminContext.api
            .listCatalogEntries(kAiHttpProvidersCatalogId);
      } catch (_) {}
      if (!mounted) return;
      if (silent &&
          appRefreshDataEquals(_keys, items) &&
          appRefreshDataEquals(_httpProviders, providers) &&
          !_loading) {
        return;
      }
      setState(() {
        _keys = items;
        _httpProviders = providers;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      if (silent) return;
      setState(() => _loading = false);
      AppErrors.showSnack(context, e);
    }
  }

  Future<void> _createKey(String name) async {
    final created = await adminContext.api.createAiKey(name: name);
    if (!mounted) return;
    await _reload();
    if (!mounted) return;
    final keyId = created['id'] as String?;
    final keyName = created['name'] as String? ?? name;
    if (keyId == null) return;
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => AdminAiKeyDetailPage(keyId: keyId, keyName: keyName),
      ),
    );
    if (mounted) await _reload();
  }

  void _openKey(AppEntityRow row) {
    Navigator.of(context)
        .push(
          MaterialPageRoute<void>(
            builder: (_) =>
                AdminAiKeyDetailPage(keyId: row.id, keyName: row.title),
          ),
        )
        .then((_) => _reload());
  }

  Future<void> _editKey(AppEntityRow row) async {
    _openKey(row);
  }

  Future<void> _deleteKey(AppEntityRow row) async {
    final l10n = AppLocalizations.of(context);
    final ok = await AppConfirmPage.push(
      context,
      title: l10n.commonDelete,
      message: row.title,
      confirmLabel: l10n.commonDelete,
      severity: AppStatusSeverity.error,
    );
    if (!ok) return;
    try {
      await adminContext.api.deleteAiKey(row.id);
      await _reload();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  bool _keyEnabled(AppEntityRow row) {
    for (final k in _keys) {
      if (k['id'] == row.id) return k['status'] != 'disabled';
    }
    return true;
  }

  Future<void> _setKeyEnabled(AppEntityRow row, bool enabled) async {
    if (!enabled) {
      final l10n = AppLocalizations.of(context);
      final ok = await AppConfirmPage.push(
        context,
        title: l10n.commonDisable,
        message: l10n.adminDisableAiKeyConfirm(row.title),
        confirmLabel: l10n.commonDisable,
        severity: AppStatusSeverity.warning,
      );
      if (!ok) return;
    }
    try {
      await adminContext.api.patchAiKey(
        keyId: row.id,
        status: enabled ? 'active' : 'disabled',
      );
      await _reload();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  String _typeLabel(AppLocalizations l10n, Map<String, dynamic> k) {
    final t = AiKeyIntegrationType.fromKey(
      provider: k['provider'] as String? ?? '',
      apiKind: k['api_kind'] as String? ?? '',
    );
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

  String _providerCell(Map<String, dynamic> k) {
    final t = AiKeyIntegrationType.fromKey(
      provider: k['provider'] as String? ?? '',
      apiKind: k['api_kind'] as String? ?? '',
    );
    if (!t.isApiKey) return '—';
    final apiKind = k['api_kind'] as String? ?? '';
    final provider = k['provider'] as String? ?? '';
    for (final p in _httpProviders) {
      final payload = (p['payload'] as Map?)?.cast<String, dynamic>() ?? {};
      if (payload['api_kind'] == apiKind &&
          payload['agent_provider'] == provider) {
        return p['title'] as String? ?? apiKind;
      }
    }
    for (final p in _httpProviders) {
      final payload = (p['payload'] as Map?)?.cast<String, dynamic>() ?? {};
      if (payload['api_kind'] == apiKind) {
        return p['title'] as String? ?? apiKind;
      }
    }
    return apiKind.isEmpty ? '—' : apiKind;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final rows = _keys.map((k) {
      final bindings = k['company_ids'];
      final bindCount = bindings is List ? bindings.length : 0;
      final typeLabel = _typeLabel(l10n, k);
      final providerLabel = _providerCell(k);
      return AppEntityRow(
        id: k['id'] as String,
        title: k['name'] as String? ?? k['id'] as String,
        subtitle: l10n.adminKeyListSubtitle(typeLabel, providerLabel),
        cells: {
          'type': typeLabel,
          'provider': providerLabel,
          'bindings': '$bindCount',
        },
      );
    }).toList();

    return ListenableBuilder(
      listenable: _viewMode,
      builder: (context, _) {
        final body = Column(
          children: [
            AppInlineAddField(
              title: l10n.adminAddAiKey,
              hintText: l10n.adminAddAiKey,
              validator: (v) => v.trim().isNotEmpty,
              invalidMessage: l10n.commonRequired,
              onSave: _createKey,
            ),
            Expanded(
              child: AppEntityCollection(
                loading: _loading,
                mode: _viewMode.resolve(context),
                rows: rows,
                primaryColumnLabel: l10n.adminKey,
                columns: [
                  AppEntityColumn(id: 'type', label: l10n.adminIntegrationType),
                  AppEntityColumn(id: 'provider', label: l10n.commonProvider),
                  AppEntityColumn(
                    id: 'bindings',
                    label: l10n.navCompanies,
                    width: 100,
                    align: AppEntityColumnAlign.end,
                  ),
                ],
                onOpen: _openKey,
                onEdit: _editKey,
                onDelete: _deleteKey,
                enabledOf: _keyEnabled,
                onEnabledChanged: _setKeyEnabled,
                empty: EmptyPlaceholder(
                  title: l10n.adminNoAiKeys,
                  subtitle: l10n.adminCreateRuntimeKeyHint,
                ),
              ),
            ),
          ],
        );

        return AppScaffold(
          actions: [
            AppCollectionViewModeButton(store: _viewMode),
          ],
          body: body,
        );
      },
    );
  }
}
