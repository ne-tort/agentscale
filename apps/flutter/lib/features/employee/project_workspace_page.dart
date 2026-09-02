import 'package:flutter/material.dart';

import 'package:prodavan/core/chat/controller/chat_session_controller.dart';
import 'package:prodavan/core/chat/widgets/chat_scaffold.dart';
import 'package:prodavan/core/containers/container_runtime_presenter.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/features/employee/agent_chat_errors.dart';
import 'package:prodavan/features/employee/cabinet_project_settings_page.dart';
import 'package:prodavan/features/employee/tool_approve_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Project agent workspace — SSE chat + HITL approvals (block-based UI).
class ProjectWorkspacePage extends StatefulWidget {
  const ProjectWorkspacePage({
    super.key,
    required this.cabinetId,
    required this.projectId,
    required this.projectName,
  });

  final String cabinetId;
  final String projectId;
  final String projectName;

  @override
  State<ProjectWorkspacePage> createState() => _ProjectWorkspacePageState();
}

class _ProjectWorkspacePageState extends State<ProjectWorkspacePage> {
  late final ChatSessionController _chat;
  bool _loading = true;
  bool _chatAvailable = true;
  Map<String, dynamic>? _project;
  Object? _lastSnackError;

  @override
  void initState() {
    super.initState();
    workContext.enterProject(widget.projectId);
    _chat = ChatSessionController(api: workContext.api, projectId: widget.projectId)
      ..changes.listen((_) {
        if (!mounted) return;
        final err = _chat.error;
        if (err != null && err != _lastSnackError) {
          _lastSnackError = err;
          showAgentChatSnack(context, err);
          _chat.error = null;
        }
        setState(() {});
      });
    _bootstrap();
  }

  Future<void> _bootstrap() async {
    try {
      _project = await workContext.api.getProject(widget.projectId);
      _chatAvailable = projectChatAvailable(_project);
      if (!_chatAvailable) {
        if (mounted) {
          await _redirectToSettings();
        }
        return;
      }
      await _chat.loadTranscript();
      await _chat.loadModels();
    } catch (e) {
      if (mounted) showAgentChatSnack(context, e);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _redirectToSettings() async {
    if (!mounted) return;
    final l10n = AppLocalizations.of(context);
    await Navigator.of(context).pushReplacement(
      MaterialPageRoute<void>(
        builder: (_) => CabinetProjectSettingsPage(
          cabinetId: widget.cabinetId,
          projectId: widget.projectId,
        ),
      ),
    );
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(l10n.errorPodNotRunning)),
    );
  }

  @override
  void dispose() {
    _chat.dispose();
    super.dispose();
  }

  Future<void> _openSettings() async {
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => CabinetProjectSettingsPage(
          cabinetId: widget.cabinetId,
          projectId: widget.projectId,
        ),
      ),
    );
    _project = await workContext.api.getProject(widget.projectId);
    _chatAvailable = projectChatAvailable(_project);
    if (_chatAvailable) {
      await _chat.loadTranscript();
      await _chat.loadModels();
    }
    if (mounted) setState(() {});
  }

  Future<void> _openApproval(Map<String, dynamic> approval) async {
    final sid = _chat.sessionId;
    if (sid == null) return;
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => ToolApprovePage(
          projectId: widget.projectId,
          sessionId: sid,
          approval: approval,
          onDecision: (decision) => _chat.resolveApproval(
            (approval['id'] ?? approval['approval_id']) as String,
            decision,
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    if (!_chatAvailable) {
      return AppScaffold(
        title: Text(widget.projectName),
        body: Center(
          child: AppStatusBanner(
            severity: AppStatusSeverity.warning,
            message: l10n.errorPodNotRunning,
          ),
        ),
      );
    }
    return AppScaffold(
      title: Text(widget.projectName),
      actions: [
        IconButton(
          icon: const Icon(Icons.settings_outlined),
          tooltip: l10n.projectProjectSettings,
          onPressed: _openSettings,
        ),
      ],
      body: ChatScaffold(
        controller: _chat,
        api: workContext.api,
        chatAvailable: _chatAvailable,
        loading: _loading,
        title: Text(widget.projectName),
        onOpenSettings: _openSettings,
        onOpenApproval: _openApproval,
      ),
    );
  }
}
