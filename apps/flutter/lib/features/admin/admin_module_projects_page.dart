import 'package:flutter/material.dart';

import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/admin/admin_container_detail_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Admin module projects table — open → container detail.
class AdminModuleProjectsPage extends StatefulWidget {
  const AdminModuleProjectsPage({
    super.key,
    required this.moduleId,
    required this.moduleName,
  });

  final String moduleId;
  final String moduleName;

  @override
  State<AdminModuleProjectsPage> createState() => _AdminModuleProjectsPageState();
}

class _AdminModuleProjectsPageState extends State<AdminModuleProjectsPage> {
  bool _loading = true;
  List<Map<String, dynamic>> _items = const [];

  @override
  void initState() {
    super.initState();
    _reload();
  }

  Future<void> _reload() async {
    setState(() => _loading = true);
    try {
      final items = await adminContext.api.listModuleProjectBindings(widget.moduleId);
      if (!mounted) return;
      setState(() {
        _items = items;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      AppErrors.showSnack(context, e);
    }
  }

  Future<void> _open(AppEntityRow row) async {
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => AdminContainerDetailPage(
          projectId: row.id,
          projectName: row.title,
        ),
      ),
    );
    if (mounted) await _reload();
  }

  String _cell(dynamic v, AppLocalizations l10n) {
    if (v == null) return l10n.commonEmDash;
    final s = '$v'.trim();
    return s.isEmpty ? l10n.commonEmDash : s;
  }

  String _bindLabel(String? kind, AppLocalizations l10n) {
    switch (kind) {
      case 'local':
        return l10n.moduleBindLocal;
      case 'global':
        return l10n.moduleBindGlobal;
      default:
        return _cell(kind, l10n);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final rows = _items.map((item) {
      final id = item['project_id'] as String? ?? '';
      return AppEntityRow(
        id: id,
        title: item['project_name'] as String? ?? id,
        cells: {
          'company': _cell(item['company_name'], l10n),
          'id': _cell(id, l10n),
          'tokens': _cell(item['agent_tokens_used'], l10n),
          'requests': _cell(item['agent_requests'], l10n),
          'bind': _bindLabel(item['bind_kind'] as String?, l10n),
        },
      );
    }).toList();

    return AppScaffold(
      title: Text(l10n.commonProjects),
      body: AppEntityCollection(
        loading: _loading,
        rows: rows,
        primaryColumnLabel: l10n.commonName,
        columns: [
          AppEntityColumn(id: 'company', label: l10n.commonCompany, flex: 2),
          AppEntityColumn(id: 'id', label: l10n.commonId, flex: 2),
          AppEntityColumn(id: 'tokens', label: l10n.commonAgentTokens, width: 90),
          AppEntityColumn(id: 'requests', label: l10n.commonAgentRequests, width: 90),
          AppEntityColumn(id: 'bind', label: l10n.moduleBindKind, width: 100),
        ],
        onOpen: _open,
        empty: EmptyPlaceholder(
          title: l10n.commonNothingFound,
          icon: Icons.folder_outlined,
        ),
      ),
    );
  }
}
