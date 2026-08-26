import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_collection_view_mode.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';
import 'package:prodavan/features/employee/cabinet_create_page.dart';
import 'package:prodavan/features/employee/cabinet_import_bundle_page.dart';
import 'package:prodavan/features/employee/dynamic_cabinet_shell.dart';
import 'package:prodavan/features/settings/open_app_settings.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Employee cabinet home — owned cabinets only (L05).
class CabinetListPage extends StatefulWidget {
  const CabinetListPage({super.key});

  @override
  State<CabinetListPage> createState() => _CabinetListPageState();
}

class _CabinetListPageState extends State<CabinetListPage> {
  static const _viewPageKey = 'employee.cabinets';

  final _viewMode = AppCollectionViewModeStore(_viewPageKey);
  bool _loading = true;
  String? _error;
  List<Map<String, dynamic>> _cabinets = const [];

  @override
  void initState() {
    super.initState();
    _viewMode.load();
    _reload();
  }

  @override
  void dispose() {
    _viewMode.dispose();
    super.dispose();
  }

  Future<void> _reload() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final items = await workContext.api.listCabinets();
      if (!mounted) return;
      setState(() {
        _cabinets = items;
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

  Future<void> _createCabinet() async {
    await Navigator.of(context).push(
      MaterialPageRoute<void>(builder: (_) => const CabinetCreatePage()),
    );
    await _reload();
  }

  void _openImportBundle() {
    Navigator.of(context).push(
      MaterialPageRoute<void>(builder: (_) => const CabinetImportBundlePage()),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final rows = _cabinets
        .map(
          (c) => AppEntityRow(
            id: c['id'] as String,
            title: c['name'] as String? ?? c['id'] as String,
            subtitle: c['status'] as String?,
          ),
        )
        .toList();

    return ListenableBuilder(
      listenable: _viewMode,
      builder: (context, _) {
        return AppScaffold(
          title: Text(l10n.commonCabinets),
          actions: [
            IconButton(onPressed: _reload, icon: const Icon(Icons.refresh)),
            AppCollectionViewModeButton(store: _viewMode),
            IconButton(
              onPressed: _openImportBundle,
              icon: const Icon(Icons.upload_file),
              tooltip: l10n.cabinetImportBundleTooltip,
            ),
            IconButton(onPressed: _createCabinet, icon: const Icon(Icons.add)),
            IconButton(
              tooltip: l10n.settings,
              icon: const Icon(Icons.settings_outlined),
              onPressed: () => openAppSettings(context),
            ),
          ],
          body: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              if (_error != null) InlineErrorBanner(message: _error!),
              Expanded(
                child: AppEntityCollection(
                  loading: _loading,
                  mode: _viewMode.resolve(context),
                  rows: rows,
                  primaryColumnLabel: l10n.commonName,
                  columns: [
                    AppEntityColumn(id: 'status', label: l10n.commonStatus, width: 96),
                  ],
                  onOpen: (row) {
                    workContext.enterCabinet(row.id);
                    Navigator.of(context).push(
                      MaterialPageRoute<void>(
                        builder: (_) => DynamicCabinetShell(
                          cabinetId: row.id,
                          cabinetName: row.title,
                        ),
                      ),
                    );
                  },
                  empty: EmptyPlaceholder(
                    title: l10n.companyNoCabinets,
                    subtitle: l10n.cabinetCreateBaseCabinetHint,
                    action: AppButton(
                      label: l10n.commonCreate,
                      expanded: false,
                      onPressed: _createCabinet,
                    ),
                  ),
                ),
              ),
            ],
          ),
        );
      },
    );
  }
}
