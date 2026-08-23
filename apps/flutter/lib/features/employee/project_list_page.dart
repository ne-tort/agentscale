import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/empty_state.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';
import 'package:prodavan/features/employee/project_create_page.dart';
import 'package:prodavan/features/employee/project_workspace_page.dart';

/// Projects tab — list + chat workspace (L05/L09).
class ProjectListPage extends StatefulWidget {
  const ProjectListPage({super.key, required this.cabinetId});

  final String cabinetId;

  @override
  State<ProjectListPage> createState() => _ProjectListPageState();
}

class _ProjectListPageState extends State<ProjectListPage> {
  bool _loading = true;
  String? _error;
  List<Map<String, dynamic>> _projects = const [];

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
      final items = await workContext.api.listProjects(widget.cabinetId);
      if (!mounted) return;
      setState(() {
        _projects = items;
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
    final rows = _projects
        .map(
          (p) => AppEntityRow(
            id: p['id'] as String,
            title: p['name'] as String? ?? p['id'] as String,
            subtitle: p['status'] as String?,
          ),
        )
        .toList();

    return Column(
      children: [
        if (_error != null) InlineErrorBanner(message: _error!),
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 8, 12, 0),
          child: Align(
            alignment: Alignment.centerRight,
            child: TextButton.icon(
              onPressed: _createProject,
              icon: const Icon(Icons.add),
              label: const Text('New project'),
            ),
          ),
        ),
        Expanded(
          child: AppEntityCollection(
            loading: _loading,
            rows: rows,
            columns: const [AppEntityColumn(id: 'name', label: 'Project')],
            onOpen: _openProject,
            empty: EmptyState(
              title: 'No projects',
              subtitle: 'Create a project to open chat workspace',
              action: TextButton(onPressed: _createProject, child: const Text('Create project')),
            ),
          ),
        ),
      ],
    );
  }
}
