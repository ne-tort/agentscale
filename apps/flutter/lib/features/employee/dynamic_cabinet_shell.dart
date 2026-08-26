import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/employee/project_list_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Cabinet shell — placeholder until module meta UI.
class DynamicCabinetShell extends StatefulWidget {
  const DynamicCabinetShell({
    super.key,
    required this.cabinetId,
    required this.cabinetName,
  });

  final String cabinetId;
  final String cabinetName;

  @override
  State<DynamicCabinetShell> createState() => _DynamicCabinetShellState();
}

class _DynamicCabinetShellState extends State<DynamicCabinetShell> {
  @override
  void initState() {
    super.initState();
    workContext.enterCabinet(widget.cabinetId);
  }

  void _openProjects() {
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => ProjectListPage(cabinetId: widget.cabinetId),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      title: Text(widget.cabinetName),
      body: EmptyPlaceholder(
        title: widget.cabinetName,
        subtitle: l10n.cabinetOpenedPlaceholder,
        action: AppButton(
          label: l10n.commonProjects,
          expanded: false,
          onPressed: _openProjects,
        ),
      ),
    );
  }
}
