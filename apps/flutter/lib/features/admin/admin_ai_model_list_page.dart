import 'package:flutter/material.dart';

import 'package:prodavan/core/refresh/app_auto_refresh.dart';
import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_inline_add_field.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_confirm_page.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/admin/admin_ai_model_detail_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Platform Admin AI models catalog list (MODELS-L1).
class AdminAiModelListPage extends StatefulWidget {
  const AdminAiModelListPage({super.key, this.embedded = false});

  final bool embedded;

  @override
  State<AdminAiModelListPage> createState() => _AdminAiModelListPageState();
}

class _AdminAiModelListPageState extends State<AdminAiModelListPage> {
  late final AppAutoRefreshBinder _autoRefresh;
  bool _loading = true;
  List<Map<String, dynamic>> _models = const [];

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

  Future<void> _reload({bool silent = false}) async {
    if (!silent && mounted) {
      setState(() => _loading = true);
    }
    try {
      final items = await adminContext.api.listAiModels();
      if (!mounted) return;
      if (silent && appRefreshDataEquals(_models, items) && !_loading) return;
      setState(() {
        _models = items;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      if (silent) return;
      setState(() => _loading = false);
      AppErrors.showSnack(context, e);
    }
  }

  Future<void> _createModel(String name) async {
    final trimmed = name.trim();
    if (trimmed.isEmpty) return;
    try {
      await adminContext.api.createAiModel(name: trimmed);
      await _reload();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  Future<void> _deleteModel(AppEntityRow row) async {
    final model = _models.firstWhere(
      (m) => m['id'] == row.id,
      orElse: () => const <String, dynamic>{},
    );
    final l10n = AppLocalizations.of(context);
    final name = model['name'] as String? ?? row.id;
    final ok = await AppConfirmPage.push(
      context,
      title: l10n.adminDeleteModel,
      message: l10n.adminDeleteModelConfirm(name),
      confirmLabel: l10n.adminDeleteModel,
      severity: AppStatusSeverity.warning,
    );
    if (!ok) return;
    try {
      await adminContext.api.deleteAiModel(row.id);
      await _reload();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  void _openModel(AppEntityRow row) {
    final model = _models.firstWhere(
      (m) => m['id'] == row.id,
      orElse: () => const <String, dynamic>{},
    );
    final name = model['name'] as String? ?? row.title;
    Navigator.of(context)
        .push<void>(
          MaterialPageRoute<void>(
            builder: (_) => AdminAiModelDetailPage(modelId: row.id, modelName: name),
          ),
        )
        .then((_) => _reload());
  }

  String _aliasesCell(Map<String, dynamic> m) {
    final aliases = m['key_aliases'];
    if (aliases is List && aliases.isNotEmpty) {
      return aliases.take(3).join(', ');
    }
    return '—';
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final rows = _models.map((m) {
      return AppEntityRow(
        id: m['id'] as String,
        title: m['name'] as String? ?? m['id'] as String,
        subtitle: m['provider'] as String? ?? '',
        cells: {
          'provider': m['provider'] as String? ?? '—',
          'aliases': _aliasesCell(m),
          'context': m['max_context_tokens']?.toString() ?? '—',
          'scope': m['owner_scope'] as String? ?? '',
        },
      );
    }).toList();

    final body = Column(
      children: [
        AppInlineAddField(
          title: l10n.adminAddModel,
          hintText: l10n.aiModelNameLabel,
          validator: (v) => v.trim().isNotEmpty,
          invalidMessage: l10n.commonRequired,
          onSave: _createModel,
        ),
        Expanded(
          child: AppEntityCollection(
            loading: _loading,
            rows: rows,
            primaryColumnLabel: l10n.aiModelNameLabel,
            columns: [
              AppEntityColumn(id: 'provider', label: l10n.commonProvider),
              AppEntityColumn(
                id: 'aliases',
                label: l10n.aiModelKeyAliasesLabel,
                flex: 2,
              ),
              AppEntityColumn(
                id: 'context',
                label: l10n.aiModelMaxTokensLabel,
                width: 120,
                align: AppEntityColumnAlign.center,
              ),
              AppEntityColumn(
                id: 'scope',
                label: l10n.commonScope,
                width: 100,
                align: AppEntityColumnAlign.center,
              ),
            ],
            onOpen: _openModel,
            onDelete: _deleteModel,
            empty: EmptyPlaceholder(
              title: l10n.adminNoModels,
              icon: Icons.model_training_outlined,
            ),
          ),
        ),
      ],
    );

    if (widget.embedded) {
      return body;
    }
    return AppScaffold(
      title: Text(l10n.navAiModels),
      body: body,
    );
  }
}
