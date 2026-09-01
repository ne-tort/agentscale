import 'package:flutter/material.dart';

import 'package:prodavan/core/containers/container_runtime_presenter.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/features/employee/agent_chat_errors.dart';
import 'package:prodavan/features/employee/cabinet_project_settings_page.dart';
import 'package:prodavan/features/employee/project_chat_controller.dart';
import 'package:prodavan/features/employee/tool_approve_page.dart';
import 'package:prodavan/features/employee/widgets/chat_composer.dart';
import 'package:prodavan/features/employee/widgets/chat_message_bubble.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Project agent workspace — SSE chat + HITL approvals.
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
  late final ProjectChatController _chat;
  final _scroll = ScrollController();
  bool _loading = true;
  bool _chatAvailable = true;
  Map<String, dynamic>? _project;

  @override
  void initState() {
    super.initState();
    workContext.enterProject(widget.projectId);
    _chat = ProjectChatController(api: workContext.api, projectId: widget.projectId)
      ..changes.listen((_) {
        if (mounted) setState(() {});
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
    } catch (e) {
      if (mounted) _chat.error = e;
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
    _scroll.dispose();
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
      body: Column(
        children: [
          if (_chat.pendingApprovals.isNotEmpty)
            MaterialBanner(
              content: Text('${l10n.projectToolApprovalHint} (${_chat.pendingApprovals.length})'),
              actions: [
                TextButton(
                  onPressed: () => _openApproval(_chat.pendingApprovals.first),
                  child: Text(l10n.projectApproveTool),
                ),
              ],
            ),
          Expanded(
            child: _loading
                ? const Center(child: CircularProgressIndicator())
                : _chat.messages.isEmpty
                    ? Center(child: Text(l10n.projectEmptyChatHint))
                    : ListView.builder(
                        controller: _scroll,
                        padding: EdgeInsets.all(AppSpacing.md),
                        itemCount: _chat.messages.length,
                        itemBuilder: (context, index) {
                          return ChatMessageBubble(message: _chat.messages[index]);
                        },
                      ),
          ),
          if (_chat.error != null)
            Padding(
              padding: EdgeInsets.symmetric(horizontal: AppSpacing.md),
              child: Text(
                localizeAgentChatError(_chat.error!, l10n),
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
            ),
          ChatComposer(
            enabled: _chatAvailable && !_chat.streaming,
            disabledHint: l10n.errorPodNotRunning,
            onSend: (text) => _chat.send(text),
            onCancel: _chat.streaming ? () => _chat.cancelStream() : null,
          ),
        ],
      ),
    );
  }
}
