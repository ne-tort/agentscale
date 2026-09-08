import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_multi_choice_preference.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/features/meta/meta_label.dart';
import 'package:prodavan/features/meta/runtime/module_runtime_scope.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Multi-select cabinet projects stored as JSON list in row body.
class ProjectMultiselectField extends StatefulWidget {
  const ProjectMultiselectField({
    super.key,
    required this.label,
    required this.value,
    required this.readOnly,
    required this.onChanged,
    this.subtitleMode,
  });

  final String label;
  final dynamic value;
  final bool readOnly;
  final void Function(List<String> projectIds) onChanged;
  /// `count_or_hide` — empty selection hides subtitle; else show count.
  final String? subtitleMode;

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
    try {
      final rows = await scope.api.listProjects(scope.cabinetId);
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
      return locale.languageCode == 'ru' ? 'Все проекты' : 'All projects';
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
    if (_error != null) {
      return ListTile(
        title: Text(label),
        subtitle: Text(
          _error == 'preview'
              ? l10n.adminMetaInvalid
              : AppErrors.localize(context, _error!),
        ),
      );
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
