import 'package:flutter/material.dart';

import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_inline_add_field.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
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
  List<Map<String, dynamic>> _keys = const [];

  @override
  void initState() {
    super.initState();
    _reload();
  }

  Future<void> _reload() async {
    setState(() => _loading = true);
    try {
      final items = await adminContext.api.listAiKeys();
      if (!mounted) return;
      setState(() {
        _keys = items;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
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
        AppInlineAddField(
          title: l10n.commonName,
          hintText: l10n.commonName,
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
            ),
          ),
        ),
      ],
    );

    return AppScaffold(
      title: Text(l10n.navAiKeys),
      actions: [
        IconButton(onPressed: _reload, icon: const Icon(Icons.refresh)),
      ],
      body: body,
    );
  }
}
