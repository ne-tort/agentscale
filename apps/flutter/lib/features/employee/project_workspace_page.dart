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

/// Project agent workspace — requires explicit [sessionId] (multi-chat).
class ProjectWorkspacePage extends StatefulWidget {
  const ProjectWorkspacePage({
    super.key,
    required this.cabinetId,
    required this.projectId,
    required this.projectName,
    required this.sessionId,
    this.initialTitle,
    this.initiallyPinned = false,
  });

  final String cabinetId;
  final String projectId;
  final String projectName;
  final String sessionId;
  final String? initialTitle;
  final bool initiallyPinned;

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
  late String _title;
  late bool _pinned;

  @override
  void initState() {
    super.initState();
    workContext.enterProject(widget.projectId);
    unawaited(
      workContext.selectProject(
        cabinetId: widget.cabinetId,
        projectId: widget.projectId,
      ),
    );
    _title = (widget.initialTitle ?? '').trim();
    _pinned = widget.initiallyPinned;
    _chat = ChatSessionController(
      api: workContext.api,
      projectId: widget.projectId,
      sessionId: widget.sessionId,
    )..changes.listen((_) {
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
        builder: (_) => ProjectChatSettingsPage(
          controller: _chat,
          projectId: widget.projectId,
          sessionId: widget.sessionId,
          title: _title,
          pinned: _pinned,
          onTitleChanged: (v) {
            if (mounted) setState(() => _title = v);
          },
          onPinnedChanged: (v) {
            if (mounted) setState(() => _pinned = v);
          },
        ),
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

  String get _displayTitle {
    final l10n = AppLocalizations.of(context);
    if (_title.isNotEmpty) return _title;
    return l10n.chatUntitled;
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
      title: Text(_displayTitle),
      actions: [
        IconButton(
          icon: Icon(_pinned ? Icons.push_pin : Icons.push_pin_outlined),
          tooltip: _pinned ? l10n.chatUnpin : l10n.chatPin,
          onPressed: () async {
            // Quick pin without leaving workspace; settings page also toggles.
            try {
              final body = await workContext.api.patchAgentSession(
                projectId: widget.projectId,
                sessionId: widget.sessionId,
                pin: !_pinned,
              );
              if (!mounted) return;
              setState(() => _pinned = body['pinned'] == true);
            } catch (e) {
              if (mounted) showAgentChatSnack(context, e);
            }
          },
        ),
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
        title: Text(_displayTitle),
        onOpenChatSettings: _openChatSettings,
      ),
    );
  }
}
