import 'dart:convert';

/// Canonical slugs stored in module_meta_documents.
abstract final class ModuleMetaSlugs {
  static const tables = 'tables';
  static const columns = 'columns';
  static const views = 'views';
  static const tabs = 'tabs';
  static const actions = 'actions';
  static const materialize = 'materialize';
  static const mcpTools = 'mcp_tools';

  static const all = [
    tables,
    columns,
    views,
    tabs,
    actions,
    materialize,
    mcpTools,
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
  });

  final int syntaxVersion;
  final List<Map<String, dynamic>> tables;
  final List<Map<String, dynamic>> columns;
  final List<Map<String, dynamic>> views;
  final List<Map<String, dynamic>> tabs;
  final List<Map<String, dynamic>> actions;
  final List<Map<String, dynamic>> materialize;
  final List<Map<String, dynamic>> mcpTools;

  static ModuleMetaManifest empty() => ModuleMetaManifest();

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
      };

  Map<String, List<Map<String, dynamic>>> toSlugMap() => {
        ModuleMetaSlugs.tables: tables,
        ModuleMetaSlugs.columns: columns,
        ModuleMetaSlugs.views: views,
        ModuleMetaSlugs.tabs: tabs,
        ModuleMetaSlugs.actions: actions,
        ModuleMetaSlugs.materialize: materialize,
        ModuleMetaSlugs.mcpTools: mcpTools,
      };

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
      ..sort((a, b) {
        final ao = a['order'] is int ? a['order'] as int : 999;
        final bo = b['order'] is int ? b['order'] as int : 999;
        final c = ao.compareTo(bo);
        if (c != 0) return c;
        return (a['title'] as String? ?? '').compareTo(b['title'] as String? ?? '');
      });
    return list;
  }

  List<Map<String, dynamic>> columnsForTable(String tableSlug) {
    return columns.where((c) => c['table_slug'] == tableSlug).toList();
  }

  static List<Map<String, dynamic>> _listOfMaps(Object? value) {
    if (value is! List) return const [];
    return value.whereType<Map>().map((e) => Map<String, dynamic>.from(e)).toList();
  }
}
