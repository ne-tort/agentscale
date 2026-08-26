import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_catalog_select_page.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/features/admin/ai_key_integration_type.dart';
import 'package:prodavan/l10n/app_localizations.dart';

const kAiHttpProvidersCatalogId = 'ai.http_providers';

/// Editable catalog picker for AI HTTP providers (`ai.http_providers`).
class AiHttpProviderSelectPage extends StatefulWidget {
  const AiHttpProviderSelectPage({
    super.key,
    this.selectedEntryId,
    this.selectedApiKind,
    this.selectedProvider,
  });

  final String? selectedEntryId;
  final String? selectedApiKind;
  final String? selectedProvider;

  static Future<AppCatalogSelectItem?> push(
    BuildContext context, {
    String? selectedEntryId,
    String? selectedApiKind,
    String? selectedProvider,
  }) async {
    final ids = await Navigator.of(context).push<Set<String>>(
      MaterialPageRoute(
        builder: (_) => AiHttpProviderSelectPage(
          selectedEntryId: selectedEntryId,
          selectedApiKind: selectedApiKind,
          selectedProvider: selectedProvider,
        ),
      ),
    );
    if (ids == null || ids.isEmpty) return null;
    final rows =
        await adminContext.api.listCatalogEntries(kAiHttpProvidersCatalogId);
    for (final r in rows) {
      if (r['id'] == ids.first) {
        return AppCatalogSelectItem(
          id: r['id'] as String,
          title: r['title'] as String? ?? r['id'] as String,
          subtitle: r['subtitle'] as String?,
          icon: catalogIconForName(r['icon_name'] as String?),
          payload: {
            ...((r['payload'] as Map?)?.cast<String, dynamic>() ?? const {}),
            'title': r['title'],
          },
        );
      }
    }
    return null;
  }

  @override
  State<AiHttpProviderSelectPage> createState() =>
      _AiHttpProviderSelectPageState();
}

class _AiHttpProviderSelectPageState extends State<AiHttpProviderSelectPage> {
  bool _loading = true;
  List<AppCatalogSelectItem> _items = const [];

  @override
  void initState() {
    super.initState();
    _reload();
  }

  Future<void> _reload() async {
    setState(() => _loading = true);
    try {
      final rows =
          await adminContext.api.listCatalogEntries(kAiHttpProvidersCatalogId);
      if (!mounted) return;
      setState(() {
        _items = [
          for (final r in rows)
            AppCatalogSelectItem(
              id: r['id'] as String,
              title: r['title'] as String? ?? r['id'] as String,
              subtitle: r['subtitle'] as String?,
              icon: catalogIconForName(r['icon_name'] as String?),
              payload: {
                ...((r['payload'] as Map?)?.cast<String, dynamic>() ??
                    const {}),
              },
            ),
        ];
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      AppErrors.showSnack(context, e);
    }
  }

  String? get _selectedId {
    if (widget.selectedEntryId != null) return widget.selectedEntryId;
    final apiKind = widget.selectedApiKind;
    final provider = widget.selectedProvider;
    if (apiKind == null) return null;
    for (final item in _items) {
      final p = item.payload;
      if (p['api_kind'] == apiKind &&
          (provider == null || p['agent_provider'] == provider)) {
        return item.id;
      }
    }
    for (final item in _items) {
      if (item.payload['api_kind'] == apiKind) return item.id;
    }
    return null;
  }

  Future<void> _create(String name) async {
    await adminContext.api.createCatalogEntry(
      catalogId: kAiHttpProvidersCatalogId,
      title: name.trim(),
      payload: const {
        'api_kind': 'custom',
        'agent_provider': 'codex',
      },
    );
    await _reload();
  }

  Future<void> _edit(AppCatalogSelectItem item) async {
    final l10n = AppLocalizations.of(context);
    final updated = await Navigator.of(context).push<String>(
      MaterialPageRoute(
        builder: (_) => _CatalogEntryEditPage(
          title: l10n.commonEdit,
          initialTitle: item.title,
        ),
      ),
    );
    if (updated == null || updated.trim().isEmpty) return;
    await adminContext.api.patchCatalogEntry(
      catalogId: kAiHttpProvidersCatalogId,
      entryId: item.id,
      title: updated.trim(),
    );
    await _reload();
  }

  Future<void> _delete(AppCatalogSelectItem item) async {
    await adminContext.api.deleteCatalogEntry(
      catalogId: kAiHttpProvidersCatalogId,
      entryId: item.id,
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    if (_loading && _items.isEmpty) {
      return AppScaffold(
        title: Text(l10n.commonProvider),
        body: const Center(child: CircularProgressIndicator()),
      );
    }

    final selected = _selectedId;
    return AppCatalogSelectPage(
      title: l10n.commonProvider,
      items: _items,
      selectedIds: selected == null ? const {} : {selected},
      popOnSelect: true,
      allowCreate: true,
      allowEdit: true,
      allowDelete: true,
      addFieldTitle: l10n.adminAddHttpProvider,
      onCreate: _create,
      onEdit: _edit,
      onDelete: _delete,
    );
  }
}

class _CatalogEntryEditPage extends StatelessWidget {
  const _CatalogEntryEditPage({
    required this.title,
    required this.initialTitle,
  });

  final String title;
  final String initialTitle;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      title: Text(title),
      body: ListView(
        padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
        children: [
          AppValuePreference<String>(
            title: l10n.commonName,
            icon: Icons.label_outline_rounded,
            value: initialTitle,
            onSave: (v) async {
              Navigator.of(context).pop(v.trim());
            },
          ),
        ],
      ),
    );
  }
}
