import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/features/meta/meta_icon.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/runtime/chat_scope.dart';

/// One cabinet module tab merged into [CabinetShell] rail / management / data hub.
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

  /// Optional hub subtitle from tab meta (`subtitle`).
  String? get subtitle {
    final raw = tab['subtitle'];
    if (raw is! String) return null;
    final trimmed = raw.trim();
    return trimmed.isEmpty ? null : trimmed;
  }

  int get order => tab['order'] is int ? tab['order'] as int : 999;

  /// `cabinet` or `project` — which instance leaf hosts this tab's data.
  /// Derived from `default_project_bind` (global→cabinet, local→project).
  String get instanceOwner => moduleInstanceOwnerOf(tab);

  /// True when the tab needs a project leaf (local bind); false for global→cabinet SoT.
  bool get usesProjectLeaf => instanceOwner != 'cabinet';

  /// Opt-in meta ``scope.active_chat: required`` — hide without a live chat.
  bool get requiresActiveChat => scopeRequiresActiveChat(tab);
}

List<CabinetNavEntry> _finalizeCabinetNavEntries(List<CabinetNavEntry> raw) {
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

Future<List<CabinetNavEntry>> _loadRawFromModules(
  List<Map<String, dynamic>> modules, {
  required Future<Map<String, dynamic>> Function(String moduleId) fetchTabs,
}) async {
  final raw = <CabinetNavEntry>[];

  for (final mod in modules) {
    final moduleId = mod['module_id'] as String? ?? mod['id'] as String? ?? '';
    if (moduleId.isEmpty) continue;
    final moduleName = mod['name'] as String? ?? moduleId;
    try {
      final doc = await fetchTabs(moduleId);
      final body = doc['body'];
      final tabs = body is Map ? body['items'] : body;
      if (tabs is! List) continue;
      for (final tab in tabs.whereType<Map>()) {
        final map = Map<String, dynamic>.from(tab);
        if (map['enabled'] == false) continue;
        if (cabinetNavPlacementOf(map) == CabinetNavPlacement.none) continue;
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

  return raw;
}

Future<List<CabinetNavEntry>> _loadRawCabinetNavEntries(String cabinetId) async {
  final api = workContext.api;
  final modules = await api.listCabinetModules(cabinetId);
  return _loadRawFromModules(
    modules,
    fetchTabs: (moduleId) => api.getCabinetModuleMeta(
      cabinetId: cabinetId,
      moduleId: moduleId,
      slug: ModuleMetaSlugs.tabs,
    ),
  );
}

/// Loads Management/Data hub tabs from the selected project's leaf instances.
Future<List<CabinetNavEntry>> _loadRawProjectNavEntries(String projectId) async {
  final api = workContext.api;
  final modules = await api.listProjectRuntimeModules(projectId);
  return _loadRawFromModules(
    modules,
    fetchTabs: (moduleId) => api.getProjectRuntimeModuleMeta(
      projectId: projectId,
      moduleId: moduleId,
      slug: ModuleMetaSlugs.tabs,
    ),
  );
}

/// Merges management/data hub tabs from cabinet + project leaves.
///
/// Cabinet-owned tabs come from [cabinetRaw]; project-owned from [projectRaw].
/// Deduplicates by `moduleId`+`viewSlug`, preferring cabinet-owned.
List<CabinetNavEntry> _mergeHubPlacementEntries({
  required List<CabinetNavEntry> cabinetRaw,
  required List<CabinetNavEntry> projectRaw,
  required CabinetNavPlacement placement,
}) {
  final fromCabinet = cabinetRaw.where(
    (e) =>
        cabinetNavPlacementOf(e.tab) == placement && isCabinetInstanceOwner(e.tab),
  );
  final fromProject = projectRaw.where(
    (e) =>
        cabinetNavPlacementOf(e.tab) == placement && !isCabinetInstanceOwner(e.tab),
  );

  final byKey = <String, CabinetNavEntry>{};
  for (final e in fromProject) {
    byKey['${e.moduleId}:${e.viewSlug}'] = e;
  }
  for (final e in fromCabinet) {
    byKey['${e.moduleId}:${e.viewSlug}'] = e;
  }
  return byKey.values.toList();
}

/// Loads module tabs for cabinet shell navigation filtered by [placement].
Future<List<CabinetNavEntry>> loadCabinetNavEntries(
  String cabinetId, {
  required CabinetNavPlacement placement,
  String? projectId,
}) async {
  final bundle = await loadCabinetNavBundle(cabinetId, projectId: projectId);
  switch (placement) {
    case CabinetNavPlacement.rail:
      return bundle.rail;
    case CabinetNavPlacement.management:
      return bundle.management;
    case CabinetNavPlacement.data:
      return bundle.data;
    case CabinetNavPlacement.none:
      return const [];
  }
}

/// Loads rail (cabinet) + management/data (cabinet-owned ∪ project-owned) in one pass.
Future<
    ({
      List<CabinetNavEntry> rail,
      List<CabinetNavEntry> management,
      List<CabinetNavEntry> data,
    })> loadCabinetNavBundle(String cabinetId, {String? projectId}) async {
  final cabinetRaw = await _loadRawCabinetNavEntries(cabinetId);
  final projectRaw = (projectId == null || projectId.isEmpty)
      ? <CabinetNavEntry>[]
      : await _loadRawProjectNavEntries(projectId);
  return (
    rail: _finalizeCabinetNavEntries(
      cabinetRaw.where((e) => cabinetNavPlacementOf(e.tab) == CabinetNavPlacement.rail).toList(),
    ),
    management: _finalizeCabinetNavEntries(
      _mergeHubPlacementEntries(
        cabinetRaw: cabinetRaw,
        projectRaw: projectRaw,
        placement: CabinetNavPlacement.management,
      ),
    ),
    data: _finalizeCabinetNavEntries(
      _mergeHubPlacementEntries(
        cabinetRaw: cabinetRaw,
        projectRaw: projectRaw,
        placement: CabinetNavPlacement.data,
      ),
    ),
  );
}

/// First enabled nav tab for a module (management/rail/data/none — any placement).
Future<CabinetNavEntry?> loadFirstModuleNavEntry(String cabinetId, String moduleId) async {
  final raw = await _loadRawCabinetNavEntries(cabinetId);
  final matches = raw.where((e) => e.moduleId == moduleId).toList();
  if (matches.isEmpty) return null;
  final finalized = _finalizeCabinetNavEntries(matches);
  return finalized.first;
}
