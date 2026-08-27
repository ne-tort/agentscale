import 'package:prodavan/core/api/admin_api.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';

abstract final class ModuleMetaRepository {
  static Future<ModuleMetaManifest> load(AdminApi api, String moduleId) async {
    final existingSlugs = await api.listModuleMetaSlugs(moduleId);
    final slugMap = <String, dynamic>{};
    for (final slug in ModuleMetaSlugs.all) {
      if (!existingSlugs.contains(slug)) continue;
      try {
        final doc = await api.getModuleMetaDocument(moduleId: moduleId, slug: slug);
        slugMap[slug] = doc['body'];
      } catch (_) {
        // skip
      }
    }
    if (slugMap.isEmpty) return ModuleMetaManifest.empty();
    return ModuleMetaManifest.fromSlugMap(slugMap);
  }

  static Future<void> save(AdminApi api, String moduleId, ModuleMetaManifest manifest) async {
    final existing = await api.listModuleMetaSlugs(moduleId);
    final existingSet = existing.toSet();
    final slugMap = manifest.toSlugMap();
    for (final slug in ModuleMetaSlugs.all) {
      final body = slugMap[slug];
      if (ModuleMetaManifest.isSlugBodyEmpty(body)) {
        if (existingSet.contains(slug)) {
          await api.deleteModuleMetaDocument(moduleId: moduleId, slug: slug);
        }
        continue;
      }
      await api.putModuleMetaDocument(moduleId: moduleId, slug: slug, body: body);
    }
  }
}
