import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_multi_choice_preference.dart';
import 'package:prodavan/features/meta/meta_label.dart';
import 'package:prodavan/features/meta/runtime/module_runtime_scope.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Multi-select **module-bound** projects stored as JSON list in row body.
///
/// Choices = projects with an MP bind for [moduleId] / runtime scope module.
/// Empty bound list → field is hidden (no cabinet-wide project dump).
class ProjectMultiselectField extends StatefulWidget {
  const ProjectMultiselectField({
    super.key,
    required this.label,
    required this.value,
    required this.readOnly,
    required this.onChanged,
    this.subtitleMode,
    this.moduleId,
  });

  final String label;
  final dynamic value;
  final bool readOnly;
  final void Function(List<String> projectIds) onChanged;
  /// `count_or_hide` — empty selection hides subtitle; else show count.
  final String? subtitleMode;
  /// Optional override; defaults to [ModuleRuntimeScope.moduleId].
  final String? moduleId;

  @override
  State<ProjectMultiselectField> createState() => _ProjectMultiselectFieldState();
}

class _ProjectMultiselectFieldState extends State<ProjectMultiselectField> {
  bool _loading = true;
  bool _started = false;
  Object? _error;
  List<Map<String, dynamic>> _projects = const [];

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (!_started) {
      _started = true;
      _load();
    }
  }

  Future<void> _load() async {
    final scope = ModuleRuntimeScope.maybeOf(context);
    if (scope == null) {
      setState(() {
        _loading = false;
        _error = 'preview';
      });
      return;
    }
    final moduleId = (widget.moduleId ?? scope.moduleId).trim();
    try {
      final rows = moduleId.isEmpty
          ? await scope.api.listProjects(scope.cabinetId)
          : await scope.api.listModuleBoundProjects(
              cabinetId: scope.cabinetId,
              moduleId: moduleId,
            );
      if (!mounted) return;
      setState(() {
        _projects = rows;
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

  Set<String> _selectedIds() {
    final raw = widget.value;
    if (raw is List) {
      return raw.map((e) => e.toString()).toSet();
    }
    return {};
  }

  String _present(Set<String> ids, AppLocalizations l10n, Locale locale) {
    if (widget.subtitleMode == 'count_or_hide') {
      if (ids.isEmpty) return '';
      return '${ids.length}';
    }
    if (ids.isEmpty) {
      return locale.languageCode == 'ru' ? 'Все привязанные' : 'All bound';
    }
    if (ids.length == 1) {
      final id = ids.first;
      final p = _projects.cast<Map<String, dynamic>?>().firstWhere(
            (x) => x?['id'] == id,
            orElse: () => null,
          );
      return p?['name'] as String? ?? id;
    }
    return '${ids.length}';
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final locale = Localizations.localeOf(context);
    final label = resolveMetaLabel(widget.label, l10n, locale: locale);

    if (_loading) {
      return ListTile(title: Text(label), subtitle: const LinearProgressIndicator());
    }
    // Preview / no runtime scope / load error / no binds → hide control.
    if (_error != null || _projects.isEmpty) {
      return const SizedBox.shrink();
    }

    final choices = _projects
        .map((p) => p['id'] as String?)
        .whereType<String>()
        .toList();

    return AppMultiChoicePreference<String>(
      title: label,
      icon: Icons.folder_outlined,
      enabled: !widget.readOnly,
      values: _selectedIds(),
      choices: choices,
      keyFor: (id) => id,
      labelFor: (id) {
        final p = _projects.firstWhere(
          (x) => x['id'] == id,
          orElse: () => {'name': id},
        );
        return p['name'] as String? ?? id;
      },
      presentValues: (ids) => _present(ids, l10n, locale),
      onSave: (ids) async {
        widget.onChanged(ids.toList());
      },
    );
  }
}
