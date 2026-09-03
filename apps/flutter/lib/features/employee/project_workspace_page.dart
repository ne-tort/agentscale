import 'dart:async';

import 'package:flutter/material.dart';

import 'package:prodavan/core/chat/controller/chat_session_controller.dart';
import 'package:prodavan/core/chat/widgets/chat_scaffold.dart';
import 'package:prodavan/core/containers/container_runtime_presenter.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/features/employee/agent_chat_errors.dart';
import 'package:prodavan/features/employee/cabinet_project_settings_page.dart';
import 'package:prodavan/features/employee/project_chat_settings_page.dart';
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
  bool _chatReadable = true;
  bool _chatSendable = true;
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
    final hadCache = _chat.hasCachedTranscript;
    if (hadCache && mounted) {
      setState(() => _loading = false);
    }
    try {
      final results = await Future.wait([
        workContext.api.getProject(widget.projectId),
        _chat.loadTranscript(background: hadCache),
      ]);
      _project = results[0] as Map<String, dynamic>;
      _chatReadable = projectChatReadable(_project);
      _chatSendable = projectChatSendable(_project);
      if (_chatSendable) {
        unawaited(_chat.loadModels());
      }
    } catch (e) {
      if (mounted) showAgentChatSnack(context, e);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  void dispose() {
    _chat.dispose();
    super.dispose();
  }

  Future<void> _openChatSettings() async {
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => ProjectChatSettingsPage(controller: _chat),
      ),
    );
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
    _chatReadable = projectChatReadable(_project);
    _chatSendable = projectChatSendable(_project);
    if (_chatReadable) {
      await _chat.loadTranscript();
      if (_chatSendable) {
        unawaited(_chat.loadModels());
      }
    }
    if (mounted) setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    if (!_chatReadable) {
      return AppScaffold(
        title: Text(widget.projectName),
        body: Center(child: Text(l10n.projectProjectPaused)),
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
        chatSendable: _chatSendable,
        loading: _loading,
        disabledHint: _chatSendable ? null : l10n.errorPodNotRunning,
        title: Text(widget.projectName),
        onOpenChatSettings: _openChatSettings,
      ),
    );
  }
}
