import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/features/meta/meta_icon.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';

/// One cabinet module tab merged into [CabinetShell] rail.
class CabinetNavEntry {
  const CabinetNavEntry({
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

bool _tabVisibleInCabinet(Map<String, dynamic> tab) {
  if (tab['enabled'] == false) return false;
  final contour = shellNavContourOf(tab);
  if (contour == null) return true;
  return contour == 'cabinet' || contour == 'employee';
}

/// Loads and merges module tabs for cabinet shell navigation.
Future<List<CabinetNavEntry>> loadCabinetNavEntries(String cabinetId) async {
  final api = workContext.api;
  final modules = await api.listCabinetModules(cabinetId);
  final raw = <CabinetNavEntry>[];

  for (final mod in modules) {
    final moduleId = mod['module_id'] as String? ?? mod['id'] as String? ?? '';
    if (moduleId.isEmpty) continue;
    final moduleName = mod['name'] as String? ?? moduleId;
    try {
      final doc = await api.getCabinetModuleMeta(
        cabinetId: cabinetId,
        moduleId: moduleId,
        slug: ModuleMetaSlugs.tabs,
      );
      final body = doc['body'];
      final tabs = body is Map ? body['items'] : body;
      if (tabs is! List) continue;
      for (final tab in tabs.whereType<Map>()) {
        final map = Map<String, dynamic>.from(tab);
        if (!_tabVisibleInCabinet(map)) continue;
        raw.add(
          CabinetNavEntry(
            moduleId: moduleId,
            moduleName: moduleName,
            tab: map,
            label: map['title'] as String? ?? '—',
          ),
        );
      }
    } catch (_) {
      continue;
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
        CabinetNavEntry(
          moduleId: e.moduleId,
          moduleName: e.moduleName,
          tab: e.tab,
          label: '${e.label} · ${e.moduleName}',
        )
      else
        e,
  ];
}
