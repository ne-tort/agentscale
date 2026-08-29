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

    return null;
  }
}
