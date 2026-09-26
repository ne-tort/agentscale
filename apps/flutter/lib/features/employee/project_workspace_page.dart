import 'dart:async';

import 'package:flutter/material.dart';

import 'package:prodavan/core/chat/controller/chat_session_controller.dart';
import 'package:prodavan/core/chat/widgets/chat_scaffold.dart';
import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/containers/container_runtime_presenter.dart';
import 'package:prodavan/core/containers/project_container_poll.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/widgets/app_bar_title_editor.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/features/employee/agent_chat_errors.dart';
import 'package:prodavan/features/employee/cabinet_project_settings_page.dart';
import 'package:prodavan/features/employee/project_chat_settings_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Project agent workspace — [sessionId] null = pending «Новый диалог».
class ProjectWorkspacePage extends StatefulWidget {
  const ProjectWorkspacePage({
    super.key,
    required this.cabinetId,
    required this.projectId,
    required this.projectName,
    this.sessionId,
    this.initialTitle,
    this.initiallyPinned = false,
    this.onSessionMaterialized,
    this.onDraftPresenceChanged,
  });

  final String cabinetId;
  final String projectId;
  final String projectName;
  final String? sessionId;
  final String? initialTitle;
  final bool initiallyPinned;
  final void Function(String sessionId)? onSessionMaterialized;
  final VoidCallback? onDraftPresenceChanged;

  @override
  State<ProjectWorkspacePage> createState() => _ProjectWorkspacePageState();
}

class _ProjectWorkspacePageState extends State<ProjectWorkspacePage> {
  late final ChatSessionController _chat;
  bool _loading = true;
  bool _chatReadable = true;
  bool _chatSendable = true;
  bool _waking = false;
  bool _updating = false;
  Map<String, dynamic>? _project;
  Object? _lastSnackError;
  late String _title;
  late bool _pinned;
  late String? _sessionId;

  bool get _hasSession => _sessionId != null && _sessionId!.isNotEmpty;

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
    _sessionId = widget.sessionId;
    _chat = ChatSessionController(
      api: workContext.api,
      projectId: widget.projectId,
      sessionId: _sessionId ?? '',
      onSessionCreated: _onSessionMaterialized,
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

  void _onSessionMaterialized(String sessionId) {
    if (_sessionId == sessionId) return;
    setState(() => _sessionId = sessionId);
    workContext.setSelectedSessionId(sessionId);
    widget.onSessionMaterialized?.call(sessionId);
  }

  Future<void> _refreshProjectFlags() async {
    _project = await workContext.api.getProject(widget.projectId);
    _chatReadable = projectChatReadable(_project);
    _chatSendable = projectChatSendable(_project);
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

  /// Same resume/reload + poll + snack flow as project settings; stay on chat.
  Future<void> _wakeProject() async {
    if (_waking || _chatSendable) return;
    final l10n = AppLocalizations.of(context);
    setState(() => _waking = true);
    try {
      if (projectChatSuspended(_project)) {
        AppSnackBar.info(context, l10n.projectResumeStartingSnack);
        try {
          await workContext.api.resumeProject(widget.projectId);
        } on ProdavanApiException catch (e) {
          if (!_isAlreadyResumedError(e)) rethrow;
        }
      } else {
        await workContext.api.reloadProject(widget.projectId);
        if (!mounted) return;
        AppSnackBar.success(context, l10n.projectReloadSuccess);
      }
      final container = await pollProjectContainerUntilSettled(
        api: workContext.api,
        projectId: widget.projectId,
      );
      if (!mounted) return;
      final failure = containerObservedFailureMessage(container);
      if (failure != null) {
        AppErrors.showSnack(context, failure);
      }
      await _refreshProjectFlags();
      if (!mounted) return;
      if (_chatReadable) {
        await _chat.loadTranscript();
        if (_chatSendable) {
          unawaited(_chat.loadModels());
        }
      }
      workContext.notifyProjectLifecycleChanged();
    } catch (e) {
      if (mounted) {
        await _refreshProjectFlags();
        if (!_chatSendable) AppErrors.showSnack(context, e);
      }
    } finally {
      if (mounted) setState(() => _waking = false);
    }
  }

  Future<void> _updateProjectWorkspace() async {
    if (_updating) return;
    setState(() => _updating = true);
    try {
      await workContext.api.syncProject(widget.projectId);
      if (!mounted) return;
      await _refreshProjectFlags();
      workContext.notifyProjectLifecycleChanged();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    } finally {
      if (mounted) setState(() => _updating = false);
    }
  }

  Future<void> _dismissWorkspaceUpdate() async {
    try {
      await workContext.api.dismissWorkspaceOutdated(widget.projectId);
      if (!mounted) return;
      await _refreshProjectFlags();
      if (mounted) setState(() {});
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  bool _isAlreadyResumedError(ProdavanApiException e) {
    if (e.statusCode != 422) return false;
    final body = e.body.toLowerCase();
    return body.contains('not paused') || body.contains('not paused or completed');
  }

  Future<bool> _ensureSession() async {
    if (_hasSession) return true;
    try {
      final created = await workContext.api.createAgentSession(
        projectId: widget.projectId,
        model: _chat.selectedModel,
        title: _title.isEmpty ? null : _title,
      );
      final sid = created['id'] as String?;
      if (sid == null || sid.isEmpty) {
        if (mounted) {
          showAgentChatSnack(context, StateError('failed to create chat session'));
        }
        return false;
      }
      _chat.sessionId = sid;
      final createdTitle = (created['title'] as String?)?.trim();
      if (createdTitle != null && createdTitle.isNotEmpty) {
        _title = createdTitle;
      }
      _onSessionMaterialized(sid);
      if (_pinned) {
        try {
          final body = await workContext.api.patchAgentSession(
            projectId: widget.projectId,
            sessionId: sid,
            pin: true,
          );
          if (mounted) setState(() => _pinned = body['pinned'] == true);
        } catch (_) {
          // Pin is best-effort until settings page.
        }
      }
      return true;
    } catch (e) {
      if (mounted) showAgentChatSnack(context, e);
      return false;
    }
  }

  Future<void> _openChatSettings() async {
    if (!_hasSession) {
      final ok = await _ensureSession();
      if (!ok || !mounted) return;
    }
    final deleted = await Navigator.of(context).push<bool>(
      MaterialPageRoute<bool>(
        builder: (_) => ProjectChatSettingsPage(
          controller: _chat,
          projectId: widget.projectId,
          sessionId: _sessionId ?? '',
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
    if (deleted == true && mounted) {
      Navigator.of(context).pop();
    }
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
    await _refreshProjectFlags();
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

  String? _wakeHint(AppLocalizations l10n) {
    if (_chatSendable) return null;
    // Agent-sandbox: paused/suspended/pausing = fast resume (seconds),
    // not a destructive reload. Only unresponsive agents get "reload".
    if (projectChatSuspended(_project)) {
      return l10n.projectChatWakePaused;
    }
    return l10n.projectChatWakeUnresponsive;
  }

  bool get _needsWorkspaceUpdate {
    final raw = _project?['workspace_outdated_at'];
    return raw is String && raw.isNotEmpty;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final needsWake = projectChatNeedsWake(_project) || (!_chatSendable && _chatReadable);
    final needsUpdate = !needsWake && _chatSendable && _needsWorkspaceUpdate;
    final showChat = _chatReadable || _waking || _updating;
    return AppScaffold(
      title: showChat
          ? AppBarTitleEditor(
              value: _title,
              hintText: l10n.chatUntitled,
              onSave: (v) async {
                if (!_hasSession) {
                  setState(() => _title = v);
                  final ok = await _ensureSession();
                  if (!ok && mounted) {
                    // Session create failed — keep local title for retry.
                  }
                  return;
                }
                try {
                  final body = await workContext.api.patchAgentSession(
                    projectId: widget.projectId,
                    sessionId: _sessionId!,
                    title: v,
                  );
                  if (!mounted) return;
                  setState(() {
                    _title = (body['title'] as String?)?.trim() ?? v;
                  });
                } catch (e) {
                  if (mounted) showAgentChatSnack(context, e);
                }
              },
            )
          : Text(widget.projectName),
      actions: [
        if (showChat) ...[
          IconButton(
            icon: Icon(_pinned ? Icons.push_pin : Icons.push_pin_outlined),
            tooltip: _pinned ? l10n.chatUnpin : l10n.chatPin,
            onPressed: () async {
              if (!_hasSession) {
                final next = !_pinned;
                setState(() => _pinned = next);
                if (next) {
                  final ok = await _ensureSession();
                  if (!ok && mounted) setState(() => _pinned = false);
                }
                return;
              }
              try {
                final body = await workContext.api.patchAgentSession(
                  projectId: widget.projectId,
                  sessionId: _sessionId!,
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
      ],
      body: !showChat
          ? Center(child: Text(l10n.projectProjectPaused))
          : ChatScaffold(
              controller: _chat,
              api: workContext.api,
              chatSendable: _chatSendable && !needsUpdate,
              loading: _loading,
              disabledHint: needsWake
                  ? _wakeHint(l10n)
                  : (needsUpdate ? l10n.projectChatNeedsUpdate : null),
              wakeMode: needsWake || _waking,
              waking: _waking,
              onWake: needsWake && !_waking ? _wakeProject : null,
              updateMode: needsUpdate || _updating,
              updating: _updating,
              onUpdate: needsUpdate && !_updating ? _updateProjectWorkspace : null,
              onDismissUpdate: needsUpdate && !_updating ? _dismissWorkspaceUpdate : null,
              title: Text(_displayTitle),
              onOpenChatSettings: _openChatSettings,
              onSessionMaterialized: _onSessionMaterialized,
              onDraftPresenceChanged: widget.onDraftPresenceChanged,
            ),
    );
  }
}
