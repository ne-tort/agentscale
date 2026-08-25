import 'package:flutter/material.dart';

import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';
import 'package:prodavan/features/admin/ai_key_create_page.dart';
import 'package:prodavan/features/admin/ai_key_detail_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Platform Admin AI keys list (L03/L04).
class AdminAiKeyListPage extends StatefulWidget {
  const AdminAiKeyListPage({super.key, this.embedded = false});

  final bool embedded;

  @override
  State<AdminAiKeyListPage> createState() => _AdminAiKeyListPageState();
}

class _AdminAiKeyListPageState extends State<AdminAiKeyListPage> {
  bool _loading = true;
  String? _error;
  List<Map<String, dynamic>> _keys = const [];

  @override
  void initState() {
    super.initState();
    _reload();
  }

  Future<void> _reload() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final items = await adminContext.api.listAiKeys();
      if (!mounted) return;
      setState(() {
        _keys = items;
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

  Future<void> _createKey() async {
    final created = await Navigator.of(context).push<bool>(
      MaterialPageRoute<bool>(builder: (_) => const AdminAiKeyCreatePage()),
    );
    if (created == true) await _reload();
  }

  void _openKey(AppEntityRow row) {
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => AdminAiKeyDetailPage(keyId: row.id, keyName: row.title),
      ),
    ).then((_) => _reload());
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final rows = _keys.map((k) {
      final bindings = k['company_ids'];
      final bindCount = bindings is List ? bindings.length : 0;
      return AppEntityRow(
        id: k['id'] as String,
        title: k['name'] as String? ?? k['id'] as String,
        subtitle: l10n.adminKeyListSubtitle('${k['provider']}', '${k['api_kind']}', '${k['status']}'),
        cells: {
          'provider': k['provider'] as String? ?? '—',
          'status': k['status'] as String? ?? '—',
          'bindings': '$bindCount',
        },
      );
    }).toList();

    final body = Column(
      children: [
        if (_error != null) InlineErrorBanner(message: _error!),
        Expanded(
          child: AppEntityCollection(
            loading: _loading,
            rows: rows,
            primaryColumnLabel: l10n.adminKey,
            columns: [
              AppEntityColumn(id: 'provider', label: l10n.commonProvider),
              AppEntityColumn(id: 'status', label: l10n.commonStatus),
              AppEntityColumn(
                id: 'bindings',
                label: l10n.navCompanies,
                width: 72,
                align: AppEntityColumnAlign.end,
              ),
            ],
            onOpen: _openKey,
            empty: EmptyPlaceholder(
              title: l10n.adminNoAiKeys,
              subtitle: l10n.adminCreateRuntimeKeyHint,
              action: TextButton(onPressed: _createKey, child: Text(l10n.adminCreateKey)),
            ),
          ),
        ),
      ],
    );

    if (widget.embedded) {
      return AppScaffold(
        title: Text(l10n.navAiKeys),
        actions: [
          IconButton(onPressed: _reload, icon: const Icon(Icons.refresh)),
          IconButton(onPressed: _createKey, icon: const Icon(Icons.add)),
        ],
        body: body,
      );
    }

    return AppScaffold(
      title: Text(l10n.navAiKeys),
      actions: [
        IconButton(onPressed: _reload, icon: const Icon(Icons.refresh)),
        IconButton(onPressed: _createKey, icon: const Icon(Icons.add)),
      ],
      body: body,
    );
  }
}
