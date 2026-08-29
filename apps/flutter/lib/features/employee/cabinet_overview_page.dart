import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_section_header.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Cabinet overview — project count and bound modules.
class CabinetOverviewPage extends StatefulWidget {
  const CabinetOverviewPage({
    super.key,
    required this.cabinetId,
    required this.cabinetName,
  });

  final String cabinetId;
  final String cabinetName;

  @override
  State<CabinetOverviewPage> createState() => _CabinetOverviewPageState();
}

class _CabinetOverviewPageState extends State<CabinetOverviewPage> {
  bool _loading = true;
  Object? _error;
  int _projectCount = 0;
  int _moduleCount = 0;

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
      final projects = await workContext.api.listProjects(widget.cabinetId);
      final modules = await workContext.api.listCabinetModules(widget.cabinetId);
      if (!mounted) return;
      setState(() {
        _projectCount = projects.length;
        _moduleCount = modules.length;
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

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      body: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          AppSectionHeader(title: widget.cabinetName),
          if (_loading)
            const Expanded(child: Center(child: CircularProgressIndicator()))
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
              child: ListView(
                padding: EdgeInsets.all(AppSpacing.md),
                children: [
                  ListTile(
                    leading: const Icon(Icons.folder_outlined),
                    title: Text(l10n.navProjects),
                    trailing: Text('$_projectCount'),
                  ),
                  ListTile(
                    leading: const Icon(Icons.extension_outlined),
                    title: Text(l10n.navModules),
                    trailing: Text('$_moduleCount'),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}
