import 'package:flutter/material.dart';

import 'package:prodavan/core/refresh/app_auto_refresh.dart';
import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_inline_add_field.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_confirm_page.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/admin/ai_key_integration_type.dart';
import 'package:prodavan/features/company/company_entity_source.dart';
import 'package:prodavan/features/company/company_ai_key_detail_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Company AI keys — local CRUD + platform-bound read-only (P-CO-02).
class CompanyAiKeyListPage extends StatefulWidget {
  const CompanyAiKeyListPage({super.key, required this.companyId, this.embedded = false});

  final String companyId;
  final bool embedded;

  @override
  State<CompanyAiKeyListPage> createState() => _CompanyAiKeyListPageState();
}

class _CompanyAiKeyListPageState extends State<CompanyAiKeyListPage> {
  late final AppAutoRefreshBinder _autoRefresh;
  bool _loading = true;
  List<Map<String, dynamic>> _keys = const [];

  @override
  void initState() {
    super.initState();
    _autoRefresh = AppAutoRefreshBinder(
      onTick: () => _reload(silent: true),
      isActive: () => appAutoRefreshIsActive(context),
    )..attach();
    _reload();
  }

  @override
  void dispose() {
    _autoRefresh.dispose();
    super.dispose();
  }

  bool _isWritable(Map<String, dynamic> k) => k['writable'] == true;

  Future<void> _reload({bool silent = false}) async {
    if (!silent && mounted) setState(() => _loading = true);
    try {
      final items = await companyContext.api.listAiKeys(widget.companyId);
      if (!mounted) return;
      if (silent && appRefreshDataEquals(_keys, items) && !_loading) return;
      setState(() {
        _keys = items;
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
    final created = await companyContext.api.createAiKey(
      companyId: widget.companyId,
      name: name,
    );
    if (!mounted) return;
    await _reload();
    if (!mounted) return;
    final keyId = created['id'] as String?;
    final keyName = created['name'] as String? ?? name;
    if (keyId == null) return;
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => CompanyAiKeyDetailPage(
          companyId: widget.companyId,
          keyId: keyId,
          keyName: keyName,
        ),
      ),
    );
    if (mounted) await _reload();
  }

  void _openKey(AppEntityRow row) {
    Navigator.of(context)
        .push(
          MaterialPageRoute<void>(
            builder: (_) => CompanyAiKeyDetailPage(
              companyId: widget.companyId,
              keyId: row.id,
              keyName: row.title,
            ),
          ),
        )
        .then((_) => _reload());
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
      await companyContext.api.deleteAiKey(
        companyId: widget.companyId,
        keyId: row.id,
      );
      await _reload();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  bool _isSubscriptionExpired(Map<String, dynamic> k) {
    final raw = (k['next_renewal_at'] as String? ?? '').trim();
    if (raw.isEmpty) return false;
    final dt = DateTime.tryParse(raw);
    if (dt == null) return false;
    return !dt.toUtc().isAfter(DateTime.now().toUtc());
  }

  bool _isKeySuspended(Map<String, dynamic> k) {
    final status = k['status'] as String? ?? '';
    if (status == 'disabled' || status == 'expired') return true;
    if (_isSubscriptionExpired(k)) return true;
    final secret = k['secret_ref_prefix'] as String? ?? '';
    return secret.trim().isEmpty;
  }

  bool _keyEnabled(AppEntityRow row) {
    for (final k in _keys) {
      if (k['id'] == row.id) return !_isKeySuspended(k);
    }
    return true;
  }

  Future<void> _setKeyEnabled(AppEntityRow row, bool enabled) async {
    Map<String, dynamic>? key;
    for (final k in _keys) {
      if (k['id'] == row.id) {
        key = k;
        break;
      }
    }
    if (!_isWritable(key ?? const {})) return;
    if (enabled && key != null && _isSubscriptionExpired(key)) return;
    if (!enabled) {
      final l10n = AppLocalizations.of(context);
      final ok = await AppConfirmPage.push(
        context,
        title: l10n.adminDisableKey,
        message: l10n.adminDisableAiKeyConfirm(row.title),
        confirmLabel: l10n.adminDisableKey,
        severity: AppStatusSeverity.warning,
      );
      if (!ok) return;
    }
    try {
      await companyContext.api.patchAiKey(
        companyId: widget.companyId,
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

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final warning = context.appColors.warning;
    final rows = _keys.map((k) {
      final typeLabel = _typeLabel(l10n, k);
      final suspended = _isKeySuspended(k);
      final platformStyle = companyEntityRowStyle(context, k['source'] as String?);
      final warningStyle = suspended
          ? (rowColor: warning, titleBold: true)
          : (rowColor: platformStyle.rowColor, titleBold: platformStyle.titleBold);
      return AppEntityRow(
        id: k['id'] as String,
        title: k['name'] as String? ?? k['id'] as String,
        rowColor: warningStyle.rowColor,
        titleBold: warningStyle.titleBold,
        cells: {
          'type': typeLabel,
        },
      );
    }).toList();

    return AppScaffold(
      title: widget.embedded ? null : Text(l10n.navAiKeys),
      body: Column(
        children: [
          AppInlineAddField(
            title: l10n.companyAddAiKey,
            hintText: l10n.companyAddAiKey,
            validator: (v) => v.trim().isNotEmpty,
            invalidMessage: l10n.commonRequired,
            onSave: _createKey,
          ),
          Expanded(
            child: AppEntityCollection(
              loading: _loading,
              rows: rows,
              primaryColumnLabel: l10n.adminKey,
              columns: [
                AppEntityColumn(id: 'type', label: l10n.adminIntegrationType),
              ],
              onOpen: _openKey,
              onDelete: (row) async {
                final key = _keys.firstWhere((k) => k['id'] == row.id, orElse: () => const {});
                if (_isWritable(key)) await _deleteKey(row);
              },
              deletableOf: (row) {
                final key = _keys.firstWhere((k) => k['id'] == row.id, orElse: () => const {});
                return _isWritable(key);
              },
              enabledOf: _keyEnabled,
              onEnabledChanged: (row, enabled) async {
                final key = _keys.firstWhere((k) => k['id'] == row.id, orElse: () => const {});
                if (_isWritable(key)) await _setKeyEnabled(row, enabled);
              },
              empty: EmptyPlaceholder(
                title: l10n.companyNoAiKeys,
                subtitle: l10n.companyCreateRuntimeKeyHint,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
