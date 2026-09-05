import 'package:flutter/material.dart';

import 'package:prodavan/core/chat/controller/chat_session_controller.dart';
import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_confirm_page.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/core/widgets/app_trailing_chevron.dart';
import 'package:prodavan/features/employee/project_chat_model_select_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Chat settings — model, title, pin for the current agent session.
class ProjectChatSettingsPage extends StatefulWidget {
  const ProjectChatSettingsPage({
    super.key,
    required this.controller,
    required this.projectId,
    required this.sessionId,
    required this.title,
    required this.pinned,
    required this.onTitleChanged,
    required this.onPinnedChanged,
  });

  final ChatSessionController controller;
  final String projectId;
  final String sessionId;
  final String title;
  final bool pinned;
  final ValueChanged<String> onTitleChanged;
  final ValueChanged<bool> onPinnedChanged;

  @override
  State<ProjectChatSettingsPage> createState() => _ProjectChatSettingsPageState();
}

class _ProjectChatSettingsPageState extends State<ProjectChatSettingsPage> {
  late String _title;
  late bool _pinned;
  bool _deleting = false;

  @override
  void initState() {
    super.initState();
    _title = widget.title;
    _pinned = widget.pinned;
    widget.controller.changes.listen((_) {
      if (mounted) setState(() {});
    });
  }

  @override
  void didUpdateWidget(covariant ProjectChatSettingsPage oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.title != widget.title) _title = widget.title;
    if (oldWidget.pinned != widget.pinned) _pinned = widget.pinned;
  }

  Future<void> _pickModel() async {
    if (widget.controller.streaming || _deleting) return;
    final picked = await ProjectChatModelSelectPage.push(
      context,
      models: widget.controller.availableModels,
      selectedModelId: widget.controller.selectedModel ?? widget.controller.defaultModel,
      enabled: !widget.controller.streaming,
    );
    if (picked == null || !mounted) return;
    widget.controller.selectedModel = picked;
    widget.controller.notifyImmediate();
    setState(() {});
  }

  Future<void> _saveTitle(String value) async {
    final body = await workContext.api.patchAgentSession(
      projectId: widget.projectId,
      sessionId: widget.sessionId,
      title: value.trim(),
    );
    final next = (body['title'] as String?)?.trim() ?? value.trim();
    setState(() => _title = next);
    widget.onTitleChanged(next);
  }

  Future<void> _setPinned(bool value) async {
    final body = await workContext.api.patchAgentSession(
      projectId: widget.projectId,
      sessionId: widget.sessionId,
      pin: value,
    );
    final next = body['pinned'] == true;
    setState(() => _pinned = next);
    widget.onPinnedChanged(next);
  }

  Future<void> _deleteDialog() async {
    if (_deleting || widget.controller.streaming) return;
    final l10n = AppLocalizations.of(context);
    final ok = await AppConfirmPage.push(
      context,
      title: l10n.chatDeleteDialog,
      message: l10n.chatDeleteDialogConfirmMessage,
      confirmLabel: l10n.chatDeleteDialog,
      severity: AppStatusSeverity.warning,
    );
    if (!ok || !mounted) return;
    setState(() => _deleting = true);
    try {
      await workContext.api.deleteAgentSession(
        projectId: widget.projectId,
        sessionId: widget.sessionId,
      );
      if (!mounted) return;
      Navigator.of(context).pop(true);
    } catch (e) {
      if (mounted) {
        setState(() => _deleting = false);
        AppErrors.showSnack(context, e);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final warning = context.appColors.warning;
    final enabled = !_deleting && !widget.controller.streaming;
    return AppScaffold(
      title: Text(l10n.projectChatSettingsTitle),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.md),
        children: [
          AppValuePreference<String>(
            title: l10n.chatRenameTitle,
            icon: Icons.title_outlined,
            value: _title.isEmpty ? '' : _title,
            hintText: l10n.chatUntitled,
            onSave: _saveTitle,
          ),
          AppSwitchPreference(
            title: l10n.chatPin,
            value: _pinned,
            enabled: enabled,
            onChanged: _setPinned,
          ),
          AppPreferenceTile(
            title: l10n.projectChatModelLabel,
            subtitle: Text(widget.controller.selectedModelLabel),
            trailing: const AppTrailingChevron(),
            enabled: enabled,
            onTap: _pickModel,
          ),
          AppNavPreference(
            title: l10n.chatDeleteDialog,
            icon: Icons.delete_outline,
            accentColor: warning,
            enabled: enabled,
            loading: _deleting,
            loadingLabel: l10n.chatDeleteDialog,
            onTap: _deleteDialog,
          ),
        ],
      ),
    );
  }
}
