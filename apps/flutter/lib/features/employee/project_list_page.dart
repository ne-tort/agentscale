import 'package:flutter/material.dart';

import 'package:prodavan/core/refresh/app_auto_refresh.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/widgets/app_collection_view_mode.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_icon_button.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/features/employee/project_create_page.dart';
import 'package:prodavan/features/employee/project_workspace_page.dart';
import 'package:prodavan/features/employee/widgets/project_status_chip.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Projects tab — list + chat workspace (L05/L09).
class ProjectListPage extends StatefulWidget {
  const ProjectListPage({
    super.key,
    required this.cabinetId,
    required this.viewModeStore,
  });

  final String cabinetId;
  final AppCollectionViewModeStore viewModeStore;

  @override
  State<ProjectListPage> createState() => _ProjectListPageState();
}

class _ProjectListPageState extends State<ProjectListPage> {
  late final AppAutoRefreshBinder _autoRefresh;
  bool _loading = true;
  String? _error;
  List<Map<String, dynamic>> _projects = const [];

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
      setState(() {
        _loading = true;
        _error = null;
      });
    }
    try {
      final items = await workContext.api.listProjects(widget.cabinetId);
      if (!mounted) return;
      if (silent && appRefreshDataEquals(_projects, items) && !_loading) return;
      setState(() {
        _projects = items;
        _loading = false;
        _error = null;
      });
    } catch (e) {
      if (!mounted) return;
      if (silent) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  Future<void> _createProject() async {
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => ProjectCreatePage(cabinetId: widget.cabinetId),
      ),
    );
    await _reload();
  }

  void _openProject(AppEntityRow row) {
    workContext.enterProject(row.id);
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => ProjectWorkspacePage(
          projectId: row.id,
          projectName: row.title,
          cabinetId: widget.cabinetId,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final rows = _projects
        .map(
          (p) {
            final status = p['status'] as String? ?? 'active';
            return AppEntityRow(
              id: p['id'] as String,
              title: p['name'] as String? ?? p['id'] as String,
              subtitle: ProjectStatusChip.isPaused(status)
                  ? l10n.projectPausedListSubtitle
                  : status,
              trailing: ProjectStatusChip(status: status),
            );
          },
        )
        .toList();

    return ListenableBuilder(
      listenable: widget.viewModeStore,
      builder: (context, _) {
        return Column(
          children: [
            if (_error != null) AppStatusBanner(severity: AppStatusSeverity.error, message: _error!),
            Padding(
              padding: const EdgeInsets.fromLTRB(12, 8, 12, 0),
              child: Align(
                alignment: Alignment.centerRight,
                child: AppIconButton(
                  icon: Icons.add,
                  tooltip: l10n.projectNewProject,
                  onPressed: _createProject,
                ),
              ),
            ),
            Expanded(
              child: AppEntityCollection(
                loading: _loading,
                mode: widget.viewModeStore.resolve(context),
                rows: rows,
                primaryColumnLabel: l10n.projectProject,
                columns: const [],
                onOpen: _openProject,
                empty: EmptyPlaceholder(
                  title: l10n.projectNoProjects,
                  subtitle: l10n.projectCreateProjectHint,
                  action: TextButton(
                    onPressed: _createProject,
                    child: Text(l10n.projectCreateProject),
                  ),
                ),
              ),
            ),
          ],
        );
      },
    );
  }
}
