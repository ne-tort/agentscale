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
import 'package:prodavan/core/widgets/session_metrics_wrap.dart';
import 'package:prodavan/features/employee/cabinet_project_settings_page.dart';
import 'package:prodavan/features/employee/project_chat_error_policy_page.dart';
import 'package:prodavan/features/employee/project_chat_model_select_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Chat settings — metrics, title, pin, optional model (when [controller] is set), delete.
class ProjectChatSettingsPage extends StatefulWidget {
  const ProjectChatSettingsPage({
    super.key,
    this.controller,
    required this.projectId,
    required this.sessionId,
    required this.title,
    required this.pinned,
    this.agentTokensUsed,
    this.agentRequests,
    this.cabinetId,
    this.onProjectSettingsClosed,
    required this.onTitleChanged,
    required this.onPinnedChanged,
  });

  /// Live chat controller — when null, model picker is hidden.
  final ChatSessionController? controller;
  final String projectId;
  final String sessionId;
  final String title;
  final bool pinned;
  final Object? agentTokensUsed;
  final Object? agentRequests;

  /// When set, chat settings embeds a «Настройки проекта» hub tile.
  final String? cabinetId;

  /// Fired after the embedded project settings page pops — the host refreshes
  /// chat readability / transcript / model catalog.
  final VoidCallback? onProjectSettingsClosed;
  final ValueChanged<String> onTitleChanged;
  final ValueChanged<bool> onPinnedChanged;

  @override
  State<ProjectChatSettingsPage> createState() => _ProjectChatSettingsPageState();
}

class _ProjectChatSettingsPageState extends State<ProjectChatSettingsPage> {
  late String _title;
  late bool _pinned;
  bool _deleting = false;
  Map<String, dynamic>? _metrics;

  /// Лимит шагов модели на один ход; null = без ограничений (дефолт).
  int? _maxTurns;
  bool _maxTurnsLoaded = false;

  /// Значение, которое подставляется при включении ограничения.
  static const int _defaultMaxTurns = 25;

  /// Потолок настройки — как `MAX_TURNS_CEILING` на сервере; больше не имеет
  /// смысла (это уже «без ограничений»).
  static const int _maxTurnsCeiling = 1000;

  @override
  void initState() {
    super.initState();
    _title = widget.title;
    _pinned = widget.pinned;
    _metrics = {
      'agent_tokens_used': widget.agentTokensUsed ?? 0,
      'agent_requests': widget.agentRequests ?? 0,
    };
    widget.controller?.changes.listen((_) {
      if (mounted) setState(() {});
    });
    _loadMetrics();
  }

  Future<void> _loadMetrics() async {
    try {
      final body = await workContext.api.getAgentSession(
        projectId: widget.projectId,
        sessionId: widget.sessionId,
      );
      if (!mounted) return;
      setState(() {
        _metrics = {
          'agent_tokens_used': body['agent_tokens_used'] ?? 0,
          'agent_requests':
              body['agent_requests'] ?? body['agent_messages'] ?? 0,
        };
        _maxTurns = (body['max_turns'] as num?)?.toInt();
        _maxTurnsLoaded = true;
      });
    } catch (_) {
      // Keep initial metrics from list row / defaults.
      if (mounted) setState(() => _maxTurnsLoaded = true);
    }
  }

  @override
  void didUpdateWidget(covariant ProjectChatSettingsPage oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.title != widget.title) _title = widget.title;
    if (oldWidget.pinned != widget.pinned) _pinned = widget.pinned;
  }

  Future<void> _pickModel() async {
    final controller = widget.controller;
    if (controller == null || controller.streaming || _deleting) return;
    final picked = await ProjectChatModelSelectPage.push(
      context,
      models: controller.availableModels,
      selectedModelId: controller.selectedModel ?? controller.defaultModel,
      enabled: !controller.streaming,
    );
    if (picked == null || !mounted) return;
    controller.selectedModel = picked;
    controller.notifyImmediate();
    setState(() {});
  }

  /// Project-level settings hub (moved here from the workspace AppBar):
  /// push the full [CabinetProjectSettingsPage], then let the host refresh
  /// chat state via [widget.onProjectSettingsClosed].
  Future<void> _openProjectSettings() async {
    final cabinetId = widget.cabinetId;
    if (cabinetId == null) return;
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => CabinetProjectSettingsPage(
          cabinetId: cabinetId,
          projectId: widget.projectId,
        ),
      ),
    );
    if (!mounted) return;
    widget.onProjectSettingsClosed?.call();
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

  /// null снимает лимит («без ограничений»), иначе — положительное целое.
  Future<void> _saveMaxTurns(int? value) async {
    final body = await workContext.api.patchAgentSession(
      projectId: widget.projectId,
      sessionId: widget.sessionId,
      maxTurns: value,
      updateMaxTurns: true,
    );
    final next = (body['max_turns'] as num?)?.toInt();
    if (!mounted) return;
    setState(() => _maxTurns = next);
  }

  Future<void> _deleteDialog() async {
    final streaming = widget.controller?.streaming == true;
    if (_deleting || streaming) return;
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
    final controller = widget.controller;
    final enabled = !_deleting && controller?.streaming != true;
    return AppScaffold(
      title: Text(l10n.projectChatSettingsTitle),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.md),
        children: [
          SessionMetricsWrap(metrics: _metrics),
          const SizedBox(height: AppSpacing.md),
          AppValuePreference<String>(
            title: l10n.chatRenameTitle,
            icon: Icons.title_outlined,
            value: _title.isEmpty ? '' : _title,
            hintText: l10n.chatUntitled,
            onSave: _saveTitle,
          ),
          AppSwitchPreference(
            title: l10n.chatPin,
            icon: Icons.push_pin_outlined,
            value: _pinned,
            enabled: enabled,
            onChanged: _setPinned,
          ),
          if (widget.cabinetId != null)
            AppNavPreference(
              title: l10n.projectProjectSettings,
              icon: Icons.settings_outlined,
              onTap: _openProjectSettings,
            ),
          if (controller != null)
            AppPreferenceTile(
              title: l10n.projectChatModelLabel,
              icon: Icons.smart_toy_outlined,
              subtitle: Text(controller.selectedModelLabel),
              trailing: const AppTrailingChevron(),
              enabled: enabled,
              onTap: _pickModel,
            ),
          AppSwitchPreference(
            title: l10n.projectChatMaxTurnsLimit,
            icon: Icons.format_list_numbered,
            // До загрузки не показываем выключенным — иначе первый же тап
            // молча перезапишет серверное значение
            value: _maxTurns != null,
            enabled: enabled && _maxTurnsLoaded,
            onChanged: (on) => _saveMaxTurns(on ? _defaultMaxTurns : null),
          ),
          if (_maxTurns != null)
            AppValuePreference<int>(
              title: l10n.projectChatMaxTurnsValue,
              icon: Icons.tag_outlined,
              value: _maxTurns!,
              digitsOnly: true,
              enabled: enabled,
              invalidMessage: l10n.projectChatMaxTurnsInvalid,
              validateInput: (raw) {
                final parsed = int.tryParse(raw);
                return parsed != null &&
                    parsed >= 1 &&
                    parsed <= _maxTurnsCeiling;
              },
              onSave: _saveMaxTurns,
            ),
          AppNavPreference(
            title: l10n.projectChatErrorPolicyTitle,
            icon: Icons.error_outline,
            onTap: () => ProjectChatErrorPolicyPage.push(
              context,
              projectId: widget.projectId,
              controller: controller,
            ),
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
