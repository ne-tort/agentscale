import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_confirm_page.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_inline_add_field.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/employee/project_chat_settings_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Project-scoped dialogs table — create, open settings, delete.
class ProjectDialogsPage extends StatefulWidget {
  const ProjectDialogsPage({
    super.key,
    required this.cabinetId,
    required this.projectId,
    required this.projectName,
  });

  final String cabinetId;
  final String projectId;
  final String projectName;

  @override
  State<ProjectDialogsPage> createState() => _ProjectDialogsPageState();
}

class _ProjectDialogsPageState extends State<ProjectDialogsPage> {
  bool _loading = true;
  Object? _error;
  List<Map<String, dynamic>> _sessions = const [];

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
      await workContext.selectProject(
        cabinetId: widget.cabinetId,
        projectId: widget.projectId,
      );
      final items = await workContext.api.listAgentSessions(
        projectId: widget.projectId,
      );
      if (!mounted) return;
      setState(() {
        _sessions = items;
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
      await workContext.api.createAgentSession(
        projectId: widget.projectId,
        title: trimmed,
      );
      if (!mounted) return;
      workContext.notifyProjectLifecycleChanged();
      await _reload();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  Future<void> _delete(AppEntityRow row) async {
    final l10n = AppLocalizations.of(context);
    final ok = await AppConfirmPage.push(
      context,
      title: l10n.chatDeleteDialog,
      message: l10n.chatDeleteDialogConfirmMessage,
      confirmLabel: l10n.chatDeleteDialog,
      severity: AppStatusSeverity.warning,
    );
    if (!ok) return;
    try {
      await workContext.api.deleteAgentSession(
        projectId: widget.projectId,
        sessionId: row.id,
      );
      if (!mounted) return;
      workContext.notifyProjectLifecycleChanged();
      await _reload();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  Future<void> _openSettings(AppEntityRow row) async {
    final session = _sessions.firstWhere(
      (s) => (s['id'] as String?) == row.id,
      orElse: () => <String, dynamic>{},
    );
    final title = (session['title'] as String?)?.trim() ?? '';
    final pinned = session['pinned'] == true;
    final deleted = await Navigator.of(context).push<bool>(
      MaterialPageRoute<bool>(
        builder: (_) => ProjectChatSettingsPage(
          projectId: widget.projectId,
          sessionId: row.id,
          title: title,
          pinned: pinned,
          onTitleChanged: (_) {},
          onPinnedChanged: (_) {},
        ),
      ),
    );
    if (!mounted) return;
    if (deleted == true) {
      workContext.notifyProjectLifecycleChanged();
    }
    await _reload();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final rows = [
      for (final s in _sessions)
        AppEntityRow(
          id: s['id'] as String,
          title: () {
            final t = (s['title'] as String?)?.trim();
            if (t == null || t.isEmpty) return l10n.chatUntitled;
            return t;
          }(),
          cells: {
            if (s['pinned'] == true) 'pin': l10n.chatPin,
          },
        ),
    ];

    return AppScaffold(
      title: Text(l10n.projectDialogsLabel),
      body: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(
              AppSpacing.md,
              AppSpacing.md,
              AppSpacing.md,
              AppSpacing.sm,
            ),
            child: AppInlineAddField(
              title: l10n.projectCreateDialogHint,
              hintText: l10n.projectCreateDialogHint,
              validator: (raw) => raw.trim().isNotEmpty,
              onSave: _create,
            ),
          ),
          Expanded(
            child: _loading
                ? const Center(child: CircularProgressIndicator())
                : _error != null
                    ? EmptyPlaceholder(
                        title: AppErrors.localize(context, _error!),
                        action: TextButton(
                          onPressed: _reload,
                          child: Text(l10n.commonRetry),
                        ),
                      )
                    : AppEntityCollection(
                        mode: AppEntityCollectionMode.table,
                        rows: rows,
                        primaryColumnLabel: l10n.chatUntitled,
                        columns: [
                          AppEntityColumn(
                            id: 'pin',
                            label: l10n.chatPin,
                            width: 100,
                          ),
                        ],
                        onOpen: _openSettings,
                        onDelete: _delete,
                        empty: EmptyPlaceholder(
                          title: l10n.chatCreateChatHint,
                          icon: Icons.forum_outlined,
                          fillViewport: false,
                        ),
                      ),
          ),
        ],
      ),
    );
  }
}
