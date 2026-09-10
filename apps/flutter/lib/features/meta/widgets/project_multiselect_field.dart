import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_preference_tile.dart';
import 'package:prodavan/core/widgets/app_catalog_select_page.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_trailing_chevron.dart';
import 'package:prodavan/features/meta/meta_label.dart';
import 'package:prodavan/features/meta/runtime/module_runtime_scope.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Sentinel id for «all module-bound projects» (stored as empty `project_ids`).
const kProjectIdsAllSentinel = '__all__';

/// Picker UI selection → stored body list (empty = all bound).
Set<String> projectIdsStoredFromPicker(
  Set<String> picked, {
  required Set<String> allowedProjectIds,
}) {
  if (picked.contains(kProjectIdsAllSentinel) || picked.isEmpty) {
    return {};
  }
  return picked.intersection(allowedProjectIds);
}

/// Mutual exclusivity: «Все» vs concrete project ids.
Set<String> resolveProjectIdsPickerToggle(
  Set<String> previous,
  String toggledId,
  bool nowSelected,
) {
  if (toggledId == kProjectIdsAllSentinel) {
    return {kProjectIdsAllSentinel};
  }
  final next = {...previous}..remove(kProjectIdsAllSentinel);
  if (nowSelected) {
    next.add(toggledId);
  } else {
    next.remove(toggledId);
  }
  if (next.isEmpty) return {kProjectIdsAllSentinel};
  return next;
}

/// Multi-select **module-bound** projects stored as JSON list in row body.
///
/// Choices = alive projects with an MP bind for [moduleId] / runtime scope.
/// Empty bound list → field is hidden. Empty `project_ids` = «Все» (all bound).
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
    if (moduleId.isEmpty) {
      setState(() {
        _loading = false;
        _projects = const [];
      });
      return;
    }
    try {
      final rows = await scope.api.listModuleBoundProjects(
        cabinetId: scope.cabinetId,
        moduleId: moduleId,
      );
      if (!mounted) return;
      setState(() {
        _projects = rows;
        _loading = false;
        _error = null;
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
      return raw.map((e) => e.toString()).where((e) => e.isNotEmpty).toSet();
    }
    return {};
  }

  String _allLabel(Locale locale) =>
      locale.languageCode == 'en' ? 'All' : 'Все';

  String _present(Set<String> ids, Locale locale) {
    if (widget.subtitleMode == 'count_or_hide') {
      if (ids.isEmpty) return '';
      return '${ids.length}';
    }
    if (ids.isEmpty) return _allLabel(locale);
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

  Set<String> _pickerSelected(Set<String> stored) {
    if (stored.isEmpty) return {kProjectIdsAllSentinel};
    return {...stored};
  }

  Future<void> _pick(BuildContext context, String label, Locale locale) async {
    if (widget.readOnly) return;
    final allLabel = _allLabel(locale);
    final stored = _selectedIds();
    final allowed = _projects
        .map((p) => p['id'] as String?)
        .whereType<String>()
        .where((id) => id.isNotEmpty)
        .toSet();
    await Navigator.of(context).push<Set<String>>(
      MaterialPageRoute(
        builder: (_) => AppCatalogSelectPage(
          title: label,
          multiSelect: true,
          selectedIds: _pickerSelected(stored),
          items: [
            AppCatalogSelectItem(
              id: kProjectIdsAllSentinel,
              title: allLabel,
              icon: Icons.select_all_outlined,
            ),
            for (final p in _projects)
              if ((p['id'] as String?)?.isNotEmpty ?? false)
                AppCatalogSelectItem(
                  id: p['id'] as String,
                  title: p['name'] as String? ?? p['id'] as String,
                  icon: Icons.folder_outlined,
                ),
          ],
          onConfirm: (picked) async {
            try {
              final next = projectIdsStoredFromPicker(
                picked,
                allowedProjectIds: allowed,
              );
              widget.onChanged(next.toList());
            } catch (e) {
              if (context.mounted) AppErrors.showSnack(context, e);
            }
          },
          resolveToggle: resolveProjectIdsPickerToggle,
        ),
      ),
    );
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

    final text = _present(_selectedIds(), locale);
    return AppPreferenceTile(
      title: label,
      icon: Icons.folder_outlined,
      enabled: !widget.readOnly,
      subtitle: text.isEmpty ? null : Text(text),
      trailing: const AppTrailingChevron(),
      onTap: () => _pick(context, label, locale),
    );
  }
}
