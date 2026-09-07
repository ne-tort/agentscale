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
      return Icons.hub_outlined;
    case 'apps_outlined':
      return Icons.apps_outlined;
    case 'dashboard_outlined':
      return Icons.dashboard_outlined;
    case 'folder_outlined':
      return Icons.folder_outlined;
    case 'psychology':
    case 'psychology_outlined':
      return Icons.psychology_outlined;
    case 'attach_file':
      return Icons.attach_file;
    case 'table_chart_outlined':
      return Icons.table_chart_outlined;
    case 'integration_instructions':
    case 'integration_instructions_outlined':
      return Icons.integration_instructions_outlined;
    case 'precision_manufacturing':
      return Icons.precision_manufacturing_outlined;
    case 'storage':
      return Icons.storage_outlined;
    case 'tune':
      return Icons.tune;
    case 'category':
      return Icons.category_outlined;
    case 'label_outline':
      return Icons.label_outline;
    case 'qr_code_2':
      return Icons.qr_code_2_outlined;
    case 'numbers':
      return Icons.numbers;
    case 'sort':
      return Icons.sort;
    case 'list_alt':
      return Icons.list_alt_outlined;
    case 'inventory_2':
      return Icons.inventory_2_outlined;
    case 'sync':
      return Icons.sync;
    case 'pause':
    case 'pause_outlined':
      return Icons.pause_outlined;
    case 'pause_circle':
    case 'pause_circle_outline':
      return Icons.pause_circle_outline;
    case 'play_arrow':
    case 'play_arrow_outlined':
      return Icons.play_arrow_outlined;
    case 'play_circle':
    case 'play_circle_outline':
      return Icons.play_circle_outline;
    case 'link':
      return Icons.link_rounded;
    default:
      return fallback;
  }
}

/// Contours for product shell navigation (`nav.contour` on TabDefinition).
abstract final class ShellNavContour {
  static const admin = 'admin';
  static const company = 'company';
  static const employee = 'employee';
  static const cabinet = 'cabinet';

  static const all = {admin, company, employee, cabinet};
}

/// Placement within employee cabinet or admin/company shell (`nav.placement`).
abstract final class ShellNavPlacement {
  static const rail = 'rail';
  static const management = 'management';
  static const data = 'data';
  static const none = 'none';

  static const all = {rail, management, data, none};
}

/// Resolved placement for employee [CabinetShell] navigation.
enum CabinetNavPlacement { rail, management, data, none }

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

/// Parses tab `nav.placement` when present.
String? shellNavPlacementOf(Map<String, dynamic> tab) {
  final nav = tab['nav'];
  if (nav is! Map) return null;
  final placement = nav['placement'];
  return placement is String ? placement : null;
}

/// Resolves employee cabinet shell placement for a tab.
///
/// Admin/company contours are excluded from cabinet shell (`none`).
/// Tabs without `nav` default to [CabinetNavPlacement.management].
CabinetNavPlacement cabinetNavPlacementOf(Map<String, dynamic> tab) {
  final contour = shellNavContourOf(tab);
  if (contour == ShellNavContour.admin || contour == ShellNavContour.company) {
    return CabinetNavPlacement.none;
  }
  final explicit = shellNavPlacementOf(tab);
  if (explicit == ShellNavPlacement.none) return CabinetNavPlacement.none;
  if (explicit == ShellNavPlacement.rail) return CabinetNavPlacement.rail;
  if (explicit == ShellNavPlacement.management) {
    return CabinetNavPlacement.management;
  }
  if (explicit == ShellNavPlacement.data) return CabinetNavPlacement.data;
  return CabinetNavPlacement.management;
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

/// Split catalog shell entries by placement (admin/company contours).
({List<ShellNavEntry> rail, List<ShellNavEntry> management}) splitShellNavEntries(
  List<ShellNavEntry> entries,
) {
  final rail = <ShellNavEntry>[];
  final management = <ShellNavEntry>[];
  for (final entry in entries) {
    final placement = shellNavPlacementOf(entry.tab);
    if (placement == ShellNavPlacement.none) continue;
    if (placement == ShellNavPlacement.rail) {
      rail.add(entry);
    } else if (placement == ShellNavPlacement.management) {
      management.add(entry);
    } else {
      rail.add(entry);
      management.add(entry);
    }
  }
  return (rail: rail, management: management);
}
