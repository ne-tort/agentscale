import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
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
      await _chat.loadTranscript();
    } catch (e) {
      _chat.error = e.toString();
    } finally {
      if (mounted) setState(() => _loading = false);
    }
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
    await _chat.loadTranscript();
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
                _chat.error!,
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
            ),
          ChatComposer(
            enabled: !_chat.streaming,
            onSend: (text) => _chat.send(text),
            onCancel: _chat.streaming ? () => _chat.cancelStream() : null,
          ),
        ],
      ),
    );
  }
}
