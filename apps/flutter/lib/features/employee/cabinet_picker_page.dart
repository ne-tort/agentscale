import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_section_header.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/employee/cabinet_shell.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Lists accessible cabinets; tap to enter [CabinetShell].
class CabinetPickerPage extends StatefulWidget {
  const CabinetPickerPage({super.key});

  @override
  State<CabinetPickerPage> createState() => _CabinetPickerPageState();
}

class _CabinetPickerPageState extends State<CabinetPickerPage> {
  bool _loading = true;
  Object? _error;
  List<Map<String, dynamic>> _cabinets = const [];

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
      final rows = await workContext.api.listCabinets();
      if (!mounted) return;
      setState(() {
        _cabinets = rows;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e;
        _loading = false;
      });
    }
  }

  void _enter(AppEntityRow row) {
    workContext.enterCabinet(row.id);
    Navigator.of(context).pushReplacement(
      MaterialPageRoute<void>(
        builder: (_) => CabinetShell(cabinetId: row.id, cabinetName: row.title),
      ),
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
            cells: {'status': c['status'] as String? ?? '—'},
          ),
        )
        .toList();

    return AppScaffold(
      body: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          AppSectionHeader(title: l10n.navCabinets),
          if (_loading)
            const Expanded(
              child: Center(child: CircularProgressIndicator()),
            )
          else if (_error != null)
            Expanded(
              child: Center(
                child: AppStatusBanner(
                  severity: AppStatusSeverity.error,
                  message: AppErrors.localize(context, _error!),
                ),
              ),
            )
          else
            Expanded(
              child: Padding(
                padding: EdgeInsets.all(AppSpacing.md),
                child: AppEntityCollection(
                  rows: rows,
                  columns: const [
                    AppEntityColumn(id: 'status', label: 'Status'),
                  ],
                  onOpen: _enter,
                  empty: EmptyPlaceholder(
                    title: l10n.adminNoCabinets,
                    icon: Icons.view_module_outlined,
                  ),
                ),
              ),
            ),
        ],
      ),
    );
  }
}
