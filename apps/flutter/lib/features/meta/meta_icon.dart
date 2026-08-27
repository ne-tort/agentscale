import 'package:flutter/material.dart';

/// Maps Material icon names from module meta JSON to [IconData].
IconData metaIconFromName(String? name, {IconData fallback = Icons.extension_outlined}) {
  switch (name) {
    case 'list':
      return Icons.list;
    case 'settings':
    case 'settings_outlined':
      return Icons.settings_outlined;
    case 'local_shipping_outlined':
      return Icons.local_shipping_outlined;
    case 'note':
    case 'note_outlined':
      return Icons.note_outlined;
    case 'extension':
    case 'extension_outlined':
      return Icons.extension_outlined;
    case 'hub':
    case 'apps_outlined':
      return Icons.apps_outlined;
    case 'dashboard_outlined':
      return Icons.dashboard_outlined;
    case 'folder_outlined':
      return Icons.folder_outlined;
    default:
      return fallback;
  }
}

/// Contours for product shell navigation (`nav.contour` on TabDefinition).
abstract final class ShellNavContour {
  static const admin = 'admin';
  static const company = 'company';

  static const all = {admin, company};
}

/// One dynamic nav item merged from module meta tabs.
class ShellNavEntry {
  const ShellNavEntry({
    required this.moduleId,
    required this.moduleName,
    required this.tab,
    required this.label,
  });

  final String moduleId;
  final String moduleName;
  final Map<String, dynamic> tab;
  final String label;

  String get viewSlug => tab['view_slug'] as String? ?? '';

  IconData get icon => metaIconFromName(tab['icon'] as String?);

  int get order => tab['order'] is int ? tab['order'] as int : 999;
}

/// Parses tab `nav.contour` when present.
String? shellNavContourOf(Map<String, dynamic> tab) {
  final nav = tab['nav'];
  if (nav is! Map) return null;
  final contour = nav['contour'];
  return contour is String ? contour : null;
}

/// Builds sorted shell nav entries from module list + tab arrays.
List<ShellNavEntry> mergeShellNavEntries({
  required String contour,
  required List<({String id, String name, List<Map<String, dynamic>> tabs})> modules,
}) {
  final raw = <ShellNavEntry>[];
  for (final mod in modules) {
    for (final tab in mod.tabs) {
      if (tab['enabled'] == false) continue;
      if (shellNavContourOf(tab) != contour) continue;
      final title = tab['title'] as String? ?? '—';
      raw.add(
        ShellNavEntry(
          moduleId: mod.id,
          moduleName: mod.name,
          tab: tab,
          label: title,
        ),
      );
    }
  }

  raw.sort((a, b) {
    final c = a.order.compareTo(b.order);
    if (c != 0) return c;
    return a.label.compareTo(b.label);
  });

  final titleCounts = <String, int>{};
  for (final e in raw) {
    titleCounts[e.label] = (titleCounts[e.label] ?? 0) + 1;
  }

  return [
    for (final e in raw)
      if ((titleCounts[e.label] ?? 0) > 1)
        ShellNavEntry(
          moduleId: e.moduleId,
          moduleName: e.moduleName,
          tab: e.tab,
          label: '${e.label} · ${e.moduleName}',
        )
      else
        e,
  ];
}
