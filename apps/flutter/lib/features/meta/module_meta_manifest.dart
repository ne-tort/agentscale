import 'dart:convert';

import 'package:prodavan/features/meta/meta_icon.dart';

/// Canonical slugs stored in module_meta_documents.
abstract final class ModuleMetaSlugs {
  static const tables = 'tables';
  static const columns = 'columns';
  static const views = 'views';
  static const tabs = 'tabs';
  static const actions = 'actions';
  static const materialize = 'materialize';
  static const mcpTools = 'mcp_tools';
  static const seedRows = 'seed_rows';

  static const all = [
    tables,
    columns,
    views,
    tabs,
    actions,
    materialize,
    mcpTools,
    seedRows,
  ];

  static const required = [tables, columns, views, tabs];
}

class ModuleMetaManifest {
  ModuleMetaManifest({
    this.syntaxVersion = 1,
    this.tables = const [],
    this.columns = const [],
    this.views = const [],
    this.tabs = const [],
    this.actions = const [],
    this.materialize = const [],
    this.mcpTools = const [],
    this.seedRows = const [],
  });

  final int syntaxVersion;
  final List<Map<String, dynamic>> tables;
  final List<Map<String, dynamic>> columns;
  final List<Map<String, dynamic>> views;
  final List<Map<String, dynamic>> tabs;
  final List<Map<String, dynamic>> actions;
  final List<Map<String, dynamic>> materialize;
  final List<Map<String, dynamic>> mcpTools;

  /// Prefill rows (`seed_rows` items): `{table_slug, row_id, body}`.
  final List<Map<String, dynamic>> seedRows;

  static ModuleMetaManifest empty() => ModuleMetaManifest();

  ModuleMetaManifest copyWith({
    int? syntaxVersion,
    List<Map<String, dynamic>>? tables,
    List<Map<String, dynamic>>? columns,
    List<Map<String, dynamic>>? views,
    List<Map<String, dynamic>>? tabs,
    List<Map<String, dynamic>>? actions,
    List<Map<String, dynamic>>? materialize,
    List<Map<String, dynamic>>? mcpTools,
    List<Map<String, dynamic>>? seedRows,
  }) {
    return ModuleMetaManifest(
      syntaxVersion: syntaxVersion ?? this.syntaxVersion,
      tables: tables ?? this.tables,
      columns: columns ?? this.columns,
      views: views ?? this.views,
      tabs: tabs ?? this.tabs,
      actions: actions ?? this.actions,
      materialize: materialize ?? this.materialize,
      mcpTools: mcpTools ?? this.mcpTools,
      seedRows: seedRows ?? this.seedRows,
    );
  }

  static ModuleMetaManifest fromJson(Object? json) {
    if (json is! Map) {
      throw FormatException('manifest must be a JSON object');
    }
    return ModuleMetaManifest(
      syntaxVersion: json['syntax_version'] is int ? json['syntax_version'] as int : 1,
      tables: _listOfMaps(json['tables']),
      columns: _listOfMaps(json['columns']),
      views: _listOfMaps(json['views']),
      tabs: _listOfMaps(json['tabs']),
      actions: _listOfMaps(json['actions']),
      materialize: _listOfMaps(json['materialize']),
      mcpTools: _listOfMaps(json['mcp_tools']),
      seedRows: parseSeedItems(json['seed_rows']),
    );
  }

  static ModuleMetaManifest fromSlugMap(Map<String, dynamic> slugs) {
    return ModuleMetaManifest(
      tables: _listOfMaps(slugs[ModuleMetaSlugs.tables]),
      columns: _listOfMaps(slugs[ModuleMetaSlugs.columns]),
      views: _listOfMaps(slugs[ModuleMetaSlugs.views]),
      tabs: _listOfMaps(slugs[ModuleMetaSlugs.tabs]),
      actions: _listOfMaps(slugs[ModuleMetaSlugs.actions]),
      materialize: _listOfMaps(slugs[ModuleMetaSlugs.materialize]),
      mcpTools: _listOfMaps(slugs[ModuleMetaSlugs.mcpTools]),
      seedRows: parseSeedItems(slugs[ModuleMetaSlugs.seedRows]),
    );
  }

  Map<String, dynamic> toJson() => {
        'syntax_version': syntaxVersion,
        'tables': tables,
        'columns': columns,
        'views': views,
        'tabs': tabs,
        if (actions.isNotEmpty) 'actions': actions,
        if (materialize.isNotEmpty) 'materialize': materialize,
        if (mcpTools.isNotEmpty) 'mcp_tools': mcpTools,
        if (seedRows.isNotEmpty) 'seed_rows': {'items': seedRows},
      };

  /// Bodies for PUT meta/documents — list for schema slugs, object for seed_rows.
  Map<String, dynamic> toSlugMap() => {
        ModuleMetaSlugs.tables: tables,
        ModuleMetaSlugs.columns: columns,
        ModuleMetaSlugs.views: views,
        ModuleMetaSlugs.tabs: tabs,
        ModuleMetaSlugs.actions: actions,
        ModuleMetaSlugs.materialize: materialize,
        ModuleMetaSlugs.mcpTools: mcpTools,
        ModuleMetaSlugs.seedRows: {'items': seedRows},
      };

  /// Document body for slug `seed_rows`.
  Map<String, dynamic> toSeedDocument() => {'items': seedRows};

  String toPrettyJson() {
    const encoder = JsonEncoder.withIndent('  ');
    return encoder.convert(toJson());
  }

  Map<String, dynamic>? viewBySlug(String slug) {
    for (final v in views) {
      if (v['slug'] == slug) return v;
    }
    return null;
  }

  List<Map<String, dynamic>> enabledTabs() {
    final list = tabs.where((t) => t['enabled'] != false).toList()
      ..sort(_tabSort);
    return list;
  }

  /// Tabs with `nav.contour` matching [contour] (admin / company product shell).
  List<Map<String, dynamic>> enabledShellNavTabs(String contour) {
    final list = tabs
        .where((t) => t['enabled'] != false && shellNavContourOf(t) == contour)
        .toList()
      ..sort(_tabSort);
    return list;
  }

  static int _tabSort(Map<String, dynamic> a, Map<String, dynamic> b) {
    final ao = a['order'] is int ? a['order'] as int : 999;
    final bo = b['order'] is int ? b['order'] as int : 999;
    final c = ao.compareTo(bo);
    if (c != 0) return c;
    return (a['title'] as String? ?? '').compareTo(b['title'] as String? ?? '');
  }

  List<Map<String, dynamic>> columnsForTable(String tableSlug) {
    return columns.where((c) => c['table_slug'] == tableSlug).toList();
  }

  /// True when manifest has any non-empty slug payload (not the empty stub).
  bool get hasContent =>
      tables.isNotEmpty ||
      columns.isNotEmpty ||
      views.isNotEmpty ||
      tabs.isNotEmpty ||
      actions.isNotEmpty ||
      materialize.isNotEmpty ||
      mcpTools.isNotEmpty ||
      seedRows.isNotEmpty;

  /// Whether [text] parses to a manifest with real content (not empty stub).
  static bool isNonEmptyStubText(String text) {
    if (text.trim().isEmpty) return false;
    try {
      return fromJson(jsonDecode(text)).hasContent;
    } catch (_) {
      return false;
    }
  }

  /// Accept `{items:[…]}` or bare list of seed row defs.
  static List<Map<String, dynamic>> parseSeedItems(Object? value) {
    if (value is Map && value['items'] is List) {
      return _listOfMaps(value['items']);
    }
    return _listOfMaps(value);
  }

  static bool isSlugBodyEmpty(Object? body) {
    if (body == null) return true;
    if (body is List) return body.isEmpty;
    if (body is Map) {
      if (body.containsKey('items')) {
        final items = body['items'];
        return items is! List || items.isEmpty;
      }
      return body.isEmpty;
    }
    return true;
  }

  static List<Map<String, dynamic>> parseMetaList(Object? value) {
    return _listOfMaps(value);
  }

  static List<Map<String, dynamic>> _listOfMaps(Object? value) {
    if (value is! List) return const [];
    return value.whereType<Map>().map((e) => Map<String, dynamic>.from(e)).toList();
  }
}
