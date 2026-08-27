import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/employee/project_list_page.dart';
import 'package:prodavan/features/meta/runtime/cabinet_module_runtime_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Cabinet shell — bound modules + projects.
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
  bool _loading = true;
  Object? _error;
  List<Map<String, dynamic>> _modules = const [];

  @override
  void initState() {
    super.initState();
    workContext.enterCabinet(widget.cabinetId);
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final modules = await workContext.api.listCabinetModules(widget.cabinetId);
      if (!mounted) return;
      setState(() {
        _modules = modules;
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

  void _openProjects() {
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => ProjectListPage(cabinetId: widget.cabinetId),
      ),
    );
  }

  void _openModule(Map<String, dynamic> mod) {
    final id = mod['id'] as String? ?? '';
    final name = mod['name'] as String? ?? id;
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => CabinetModuleRuntimePage(
          cabinetId: widget.cabinetId,
          moduleId: id,
          moduleName: name,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      title: Text(widget.cabinetName),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.symmetric(vertical: 8),
              children: [
                if (_error != null)
                  Padding(
                    padding: const EdgeInsets.all(16),
                    child: Text(_error.toString()),
                  ),
                AppNavPreference(
                  title: l10n.commonProjects,
                  icon: Icons.folder_outlined,
                  onTap: _openProjects,
                ),
                for (final mod in _modules)
                  AppNavPreference(
                    title: mod['name'] as String? ?? mod['id'] as String? ?? '—',
                    icon: Icons.extension_outlined,
                    onTap: () => _openModule(mod),
                  ),
                if (_modules.isEmpty)
                  EmptyPlaceholder(
                    title: l10n.commonEmpty,
                    subtitle: l10n.cabinetContextHint,
                  ),
              ],
            ),
    );
  }
}
