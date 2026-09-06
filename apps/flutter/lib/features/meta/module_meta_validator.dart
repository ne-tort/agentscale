import 'package:prodavan/features/meta/meta_icon.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';

abstract final class ModuleMetaValidator {
  static final _slugRe = RegExp(r'^[a-z][a-z0-9_]{0,63}$');
  static const columnTypes = {
    'text',
    'number',
    'bool',
    'datetime',
    'json',
    'enum',
    'ref',
    'file_ref',
    'secret_ref',
  };
  static const viewKinds = {'collection', 'form', 'hub', 'detail', 'board', 'profile_hub'};

  static String? validate(Object? parsed) {
    ModuleMetaManifest manifest;
    try {
      manifest = ModuleMetaManifest.fromJson(parsed);
    } catch (e) {
      return e.toString();
    }

    final tableSlugs = <String>{};
    for (final t in manifest.tables) {
      final slug = t['slug'];
      if (slug is! String || !_slugRe.hasMatch(slug)) {
        return 'invalid table slug: $slug';
      }
      if (!tableSlugs.add(slug)) return 'duplicate table slug: $slug';
    }

    for (final c in manifest.columns) {
      final tableSlug = c['table_slug'];
      if (tableSlug is! String || !tableSlugs.contains(tableSlug)) {
        return 'column references unknown table: $tableSlug';
      }
      final name = c['name'];
      if (name is! String || !_slugRe.hasMatch(name)) {
        return 'invalid column name: $name';
      }
      final type = c['type'];
      if (type is! String || !columnTypes.contains(type)) {
        return 'invalid column type: $type';
      }
    }

    final viewSlugs = <String>{};
    for (final v in manifest.views) {
      final slug = v['slug'];
      if (slug is! String || !_slugRe.hasMatch(slug)) {
        return 'invalid view slug: $slug';
      }
      if (!viewSlugs.add(slug)) return 'duplicate view slug: $slug';
      final tableSlug = v['table_slug'];
      if (tableSlug is String && !tableSlugs.contains(tableSlug)) {
        return 'view references unknown table: $tableSlug';
      }
      final ui = v['ui_json'];
      if (ui is Map) {
        final kind = ui['kind'];
        if (kind is String && !viewKinds.contains(kind)) {
          return 'invalid view kind: $kind';
        }
      }
    }

    for (final tab in manifest.tabs) {
      final viewSlug = tab['view_slug'];
      if (viewSlug is String && !viewSlugs.contains(viewSlug)) {
        return 'tab references unknown view: $viewSlug';
      }
      final tableSlug = tab['table_slug'];
      if (tableSlug is String && !tableSlugs.contains(tableSlug)) {
        return 'tab references unknown table: $tableSlug';
      }
      final nav = tab['nav'];
      if (nav != null) {
        if (nav is! Map) return 'tab nav must be an object';
        final contour = nav['contour'];
        if (contour != null &&
            (contour is! String || !ShellNavContour.all.contains(contour))) {
          return 'invalid tab nav.contour: $contour';
        }
        final placement = nav['placement'];
        if (placement != null &&
            (placement is! String || !ShellNavPlacement.all.contains(placement))) {
          return 'invalid tab nav.placement: $placement';
        }
      }
    }

    final rowIdRe = RegExp(r'^[a-zA-Z0-9_-]{1,64}$');
    for (final item in manifest.seedRows) {
      final tableSlug = item['table_slug'];
      if (tableSlug is! String || !tableSlugs.contains(tableSlug)) {
        return 'seed_rows references unknown table: $tableSlug';
      }
      final rowId = item['row_id'];
      if (rowId is! String || !rowIdRe.hasMatch(rowId)) {
        return 'invalid seed_rows row_id: $rowId';
      }
      final body = item['body'];
      if (body != null && body is! Map) {
        return 'seed_rows body must be an object for $rowId';
      }
    }

    const materializeWhen = {'project.created', 'project.resumed', 'project.sync'};
    const sourceTypes = {'row', 'rows', 'static', 'meta_document'};
    const targetFormats = {
      'raw',
      'json_rows',
      'json_single',
      'template',
      'copy_blob',
      'mcp_package',
      'merge_mapped_sqlite',
    };
    const blobFormats = {'copy_blob', 'mcp_package'};

    final columnNamesByTable = <String, Set<String>>{};
    for (final c in manifest.columns) {
      final tableSlug = c['table_slug'];
      final name = c['name'];
      if (tableSlug is String && name is String) {
        columnNamesByTable.putIfAbsent(tableSlug, () => {}).add(name);
      }
    }

    final ruleIds = <String>{};
    for (var i = 0; i < manifest.materialize.length; i++) {
      final rule = manifest.materialize[i];
      var label = 'materialize[$i]';
      final rid = rule['id'];
      if (rid != null) {
        if (rid is! String || rid.trim().isEmpty) {
          return '$label id must be a non-empty string';
        }
        if (!ruleIds.add(rid)) return 'duplicate materialize rule id: $rid';
        label = 'materialize[$rid]';
      }

      final when = rule['when'];
      if (when != null) {
        if (when is! List || when.isEmpty) {
          return '$label when must be a non-empty array';
        }
        for (final event in when) {
          if (event is! String || !materializeWhen.contains(event)) {
            return '$label invalid when event: $event';
          }
        }
      }

      final priority = rule['priority'];
      if (priority != null && priority is! int) {
        return '$label priority must be an integer';
      }

      final source = rule['source'];
      if (source is! Map) return '$label source must be an object';
      final sourceType = source['type'] ?? 'row';
      if (sourceType is! String || !sourceTypes.contains(sourceType)) {
        return '$label invalid source.type: $sourceType';
      }

      final tableSlug = source['table_slug'];
      if (sourceType == 'row' || sourceType == 'rows') {
        if (tableSlug is! String || !tableSlugs.contains(tableSlug)) {
          return '$label source references unknown table: $tableSlug';
        }
      } else if (sourceType == 'meta_document') {
        final docSlug = source['slug'];
        if (docSlug is! String || docSlug.trim().isEmpty) {
          return '$label meta_document source requires slug';
        }
      } else if (sourceType == 'static') {
        if (source['value'] == null && source['text'] == null) {
          return '$label static source requires value or text';
        }
      }

      final target = rule['target'];
      if (target is! Map) return '$label target must be an object';
      final wsPath = target['workspace_path'];
      if (wsPath is! String) {
        return '$label target.workspace_path must be a string';
      }
      if (wsPath.trim().isEmpty) {
        return '$label target.workspace_path required';
      }
      if (wsPath.startsWith('/') ||
          wsPath.startsWith(r'\') ||
          wsPath.replaceAll(r'\', '/').contains('..')) {
        return '$label target.workspace_path must be relative: $wsPath';
      }

      final fmt = target['format'] ?? 'raw';
      if (fmt is! String || !targetFormats.contains(fmt)) {
        return '$label invalid target.format: $fmt';
      }
      if (fmt == 'template' && target['template'] is! String) {
        return '$label template format requires target.template string';
      }
      if (blobFormats.contains(fmt)) {
        final field = target['field'];
        if (field is! String || field.trim().isEmpty) {
          return '$label target.field required for format $fmt';
        }
        if (sourceType == 'row' || sourceType == 'rows') {
          final cols = columnNamesByTable[tableSlug as String? ?? ''];
          if (cols == null || !cols.contains(field)) {
            return '$label target.field references unknown column: $field';
          }
        }
      }
    }

    return null;
  }
}
