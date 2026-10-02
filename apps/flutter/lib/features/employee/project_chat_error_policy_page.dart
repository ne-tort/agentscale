import 'package:flutter/material.dart';

import 'package:prodavan/core/chat/controller/chat_session_controller.dart';
import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_checkbox.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_trailing_chevron.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// «Обработка ошибок» — per-project chat reconnect policy (chat settings
/// subpage): retry interval, attempt budget (incl. unlimited) and fallback
/// models picked from the project's live model list (same key-filtered list
/// as the session model picker).
class ProjectChatErrorPolicyPage extends StatefulWidget {
  const ProjectChatErrorPolicyPage({
    super.key,
    required this.projectId,
    this.controller,
  });

  final String projectId;

  /// Live chat controller — provides the available (key-filtered) models.
  final ChatSessionController? controller;

  static Future<bool?> push(
    BuildContext context, {
    required String projectId,
    ChatSessionController? controller,
  }) {
    return Navigator.of(context).push<bool>(
      MaterialPageRoute(
        builder: (_) => ProjectChatErrorPolicyPage(
          projectId: projectId,
          controller: controller,
        ),
      ),
    );
  }

  @override
  State<ProjectChatErrorPolicyPage> createState() =>
      _ProjectChatErrorPolicyPageState();
}

class _ProjectChatErrorPolicyPageState extends State<ProjectChatErrorPolicyPage> {
  bool _loading = true;
  bool _saving = false;
  int _intervalSec = 10;
  int _maxAttempts = 0;
  bool _tryOtherModels = false;
  List<String> _fallbackModels = const [];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final body = await workContext.api.getProjectChatErrorPolicy(widget.projectId);
      if (!mounted) return;
      setState(() {
        _intervalSec = (body['interval_sec'] as num?)?.toInt() ?? 10;
        _maxAttempts = (body['max_attempts'] as num?)?.toInt() ?? 0;
        _tryOtherModels =
            (body['fallback_models'] as List?)?.isNotEmpty ?? false;
        _fallbackModels =
            ((body['fallback_models'] as List?) ?? const []).cast<String>();
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      AppErrors.showSnack(context, e);
    }
  }

  Future<void> _save({
    int? intervalSec,
    int? maxAttempts,
    List<String>? fallbackModels,
  }) async {
    if (_saving) return;
    setState(() => _saving = true);
    try {
      final body = await workContext.api.putProjectChatErrorPolicy(
        widget.projectId,
        intervalSec: intervalSec,
        maxAttempts: maxAttempts,
        fallbackModels: fallbackModels,
      );
      if (!mounted) return;
      setState(() {
        _intervalSec = (body['interval_sec'] as num?)?.toInt() ?? _intervalSec;
        _maxAttempts = (body['max_attempts'] as num?)?.toInt() ?? _maxAttempts;
        _fallbackModels =
            ((body['fallback_models'] as List?) ?? const []).cast<String>();
        _saving = false;
      });
    } catch (e) {
      if (mounted) {
        setState(() => _saving = false);
        AppErrors.showSnack(context, e);
      }
    }
  }

  Future<void> _pickFallbackModels() async {
    final models = widget.controller?.availableModels ?? const <Map<String, dynamic>>[];
    final picked = await _FallbackModelsPage.push(
      context,
      models: models,
      selected: _fallbackModels,
    );
    if (picked == null || !mounted) return;
    setState(() => _fallbackModels = picked);
    await _save(fallbackModels: _tryOtherModels ? picked : const []);
  }

  Future<void> _setTryOtherModels(bool value) async {
    setState(() => _tryOtherModels = value);
    // Switching on with an empty list still means "retry the same model";
    // switching off clears the fallbacks server-side.
    await _save(fallbackModels: value ? _fallbackModels : const []);
  }

  String _fallbackModelsSubtitle(AppLocalizations l10n) {
    if (!_tryOtherModels) return l10n.projectChatErrorPolicyFallbackModelsNone;
    if (_fallbackModels.isEmpty) return l10n.projectChatErrorPolicyFallbackModelsNone;
    return _fallbackModels.join(', ');
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final enabled = !_loading && !_saving;
    return AppScaffold(
      title: Text(l10n.projectChatErrorPolicyTitle),
      body: _loading
          ? const Center(child: CircularProgressIndicator(strokeWidth: 2))
          : ListView(
              padding: const EdgeInsets.all(AppSpacing.md),
              children: [
                AppValuePreference<int>(
                  title: l10n.projectChatErrorPolicyInterval,
                  icon: Icons.timer_outlined,
                  value: _intervalSec,
                  digitsOnly: true,
                  enabled: enabled,
                  onSave: (v) => _save(intervalSec: v),
                ),
                AppSwitchPreference(
                  title: l10n.projectChatErrorPolicyInfinite,
                  icon: Icons.all_inclusive,
                  value: _maxAttempts == 0,
                  enabled: enabled,
                  onChanged: (infinite) =>
                      _save(maxAttempts: infinite ? 0 : 1),
                ),
                if (_maxAttempts > 0)
                  AppValuePreference<int>(
                    title: l10n.projectChatErrorPolicyAttempts,
                    icon: Icons.repeat_outlined,
                    value: _maxAttempts,
                    digitsOnly: true,
                    enabled: enabled,
                    onSave: (v) => _save(maxAttempts: v < 0 ? 0 : v),
                  ),
                AppSwitchPreference(
                  title: l10n.projectChatErrorPolicyTryOtherModels,
                  icon: Icons.alt_route_outlined,
                  value: _tryOtherModels,
                  enabled: enabled,
                  onChanged: _setTryOtherModels,
                ),
                if (_tryOtherModels)
                  AppPreferenceTile(
                    title: l10n.projectChatErrorPolicyFallbackModels,
                    icon: Icons.list_alt_outlined,
                    enabled: enabled,
                    subtitle: Text(
                      _fallbackModelsSubtitle(l10n),
                      maxLines: 3,
                      overflow: TextOverflow.ellipsis,
                    ),
                    trailing: const AppTrailingChevron(),
                    onTap: _pickFallbackModels,
                  ),
              ],
            ),
    );
  }
}

/// Multi-select picker for fallback models (same live list as the session
/// model picker); pops with the selected model ids (null when dismissed).
class _FallbackModelsPage extends StatefulWidget {
  const _FallbackModelsPage({
    required this.models,
    required this.selected,
  });

  final List<Map<String, dynamic>> models;
  final List<String> selected;

  static Future<List<String>?> push(
    BuildContext context, {
    required List<Map<String, dynamic>> models,
    required List<String> selected,
  }) {
    return Navigator.of(context).push<List<String>>(
      MaterialPageRoute(
        builder: (_) => _FallbackModelsPage(models: models, selected: selected),
      ),
    );
  }

  @override
  State<_FallbackModelsPage> createState() => _FallbackModelsPageState();
}

class _FallbackModelsPageState extends State<_FallbackModelsPage> {
  late final Set<String> _selected;

  @override
  void initState() {
    super.initState();
    _selected = {...widget.selected};
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    if (widget.models.isEmpty) {
      return AppScaffold(
        title: Text(l10n.projectChatErrorPolicyFallbackModels),
        body: EmptyPlaceholder(
          title: l10n.projectChatModelLabel,
          icon: Icons.list_alt_outlined,
        ),
      );
    }
    return AppScaffold(
      title: Text(l10n.projectChatErrorPolicyFallbackModels),
      // Selection pops on the app-bar check action — the host page persists
      // the list, mirroring the app's save-on-commit preference semantics.
      actions: [
        IconButton(
          tooltip: l10n.commonSave,
          icon: const Icon(Icons.check_rounded),
          onPressed: () => Navigator.of(context).pop(_selected.toList()),
        ),
      ],
      body: ListView.builder(
        padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
        itemCount: widget.models.length,
        itemBuilder: (context, i) {
          final m = widget.models[i];
          final id = m['id'] as String? ?? m['label'] as String? ?? '';
          final label = m['label'] as String? ?? id;
          return ListTile(
            title: Text(label),
            subtitle: id == label ? null : Text(id),
            dense: true,
            onTap: () => setState(() {
              _selected.contains(id) ? _selected.remove(id) : _selected.add(id);
            }),
            trailing: AppCheckbox(
              value: _selected.contains(id),
              semanticLabel: label,
              onChanged: (v) => setState(() {
                if (v == true) {
                  _selected.add(id);
                } else {
                  _selected.remove(id);
                }
              }),
            ),
          );
        },
      ),
    );
  }
}
