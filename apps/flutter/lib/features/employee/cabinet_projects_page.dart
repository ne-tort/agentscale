import 'package:flutter/material.dart';

import 'package:prodavan/core/containers/container_runtime_presenter.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_confirm_page.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_inline_add_field.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/employee/cabinet_project_settings_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Projects table inside cabinet — inline add, long-press delete.
class CabinetProjectsPage extends StatefulWidget {
  const CabinetProjectsPage({super.key, required this.cabinetId});

  final String cabinetId;

  @override
  State<CabinetProjectsPage> createState() => _CabinetProjectsPageState();
}

class _CabinetProjectsPageState extends State<CabinetProjectsPage> {
  static const _aboutMaxLen = 80;

  bool _loading = true;
  Object? _error;
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
      final rows = await workContext.api.listProjects(widget.cabinetId);
      if (!mounted) return;
      setState(() {
        _projects = rows;
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

  Future<void> _create(String name) async {
    final trimmed = name.trim();
    if (trimmed.isEmpty) return;
    try {
      final created = await workContext.api.createProject(
        cabinetId: widget.cabinetId,
        name: trimmed,
      );
      if (!mounted) return;
      final id = created['id'] as String;
      await Navigator.of(context).push<void>(
        MaterialPageRoute<void>(
          builder: (_) => CabinetProjectSettingsPage(
            cabinetId: widget.cabinetId,
            projectId: id,
          ),
        ),
      );
      await _reload();
    } catch (e) {
      if (!mounted) return;
      AppErrors.showSnack(context, e);
    }
  }

  Future<void> _delete(AppEntityRow row) async {
    final l10n = AppLocalizations.of(context);
    final ok = await AppConfirmPage.push(
      context,
      title: l10n.commonDelete,
      message: row.title,
      confirmLabel: l10n.commonDelete,
      severity: AppStatusSeverity.error,
    );
    if (!ok) return;
    try {
      await workContext.api.deleteProject(row.id);
      await _reload();
    } catch (e) {
      if (!mounted) return;
      AppErrors.showSnack(context, e);
    }
  }

  void _openSettings(AppEntityRow row) {
    Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => CabinetProjectSettingsPage(
          cabinetId: widget.cabinetId,
          projectId: row.id,
        ),
      ),
    ).then((_) => _reload());
  }

  String _truncateAbout(String? about) {
    final text = (about ?? '').trim();
    if (text.isEmpty) return '—';
    if (text.length <= _aboutMaxLen) return text;
    return '${text.substring(0, _aboutMaxLen)}…';
  }

  String _statusLabel(Map<String, dynamic> project, AppLocalizations l10n) {
    final status = project['status'] as String?;
    if (projectShowsContainerError(project)) {
      return l10n.containerObservedFailed;
    }
    return switch (status) {
      'active' => l10n.adminContainerStatusActive,
      'paused' => l10n.adminContainerStatusPaused,
      'error' => l10n.containerObservedFailed,
      'draft' => l10n.adminContainerStatusDraft,
      _ => status ?? l10n.commonEmDash,
    };
  }

  Color? _statusColor(BuildContext context, Map<String, dynamic> project) {
    if (projectShowsContainerError(project)) {
      return context.appColors.danger;
    }
    final status = project['status'] as String?;
    if (status == 'error') return context.appColors.danger;
    if (status == 'draft' || status == 'paused') return context.appColors.warning;
    return null;
  }

  Color? _rowColor(BuildContext context, Map<String, dynamic> project) {
    final status = project['status'] as String?;
    if (status == 'draft' || status == 'paused') return context.appColors.warning;
    return null;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final rows = _projects
        .map(
          (p) => AppEntityRow(
            id: p['id'] as String,
            title: p['name'] as String? ?? p['id'] as String,
            rowColor: _rowColor(context, p),
            cellWidgets: {
              'status': Text(
                _statusLabel(p, l10n),
                style: TextStyle(color: _statusColor(context, p)),
              ),
            },
            cells: {
              'about': _truncateAbout(p['about'] as String?),
              'creator': p['created_by_login'] as String? ?? '—',
              'status': _statusLabel(p, l10n),
            },
          ),
        )
        .toList();

    return AppScaffold(
      body: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          AppInlineAddField(
            title: l10n.projectAddHint,
            hintText: l10n.projectAddHint,
            validator: (raw) => raw.trim().isNotEmpty,
            onSave: _create,
          ),
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
              child: Padding(
                padding: EdgeInsets.all(AppSpacing.md),
                child: AppEntityCollection(
                  rows: rows,
                  columns: [
                    AppEntityColumn(id: 'about', label: l10n.projectAboutColumn),
                    AppEntityColumn(id: 'creator', label: l10n.projectCreatorColumn),
                    AppEntityColumn(id: 'status', label: l10n.projectProjectStatus),
                  ],
                  onOpen: _openSettings,
                  onDelete: _delete,
                  empty: EmptyPlaceholder(
                    title: l10n.projectNoProjects,
                    icon: Icons.folder_outlined,
                  ),
                ),
              ),
            ),
        ],
      ),
    );
  }
}
