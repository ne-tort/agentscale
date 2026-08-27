import 'package:prodavan/core/api/company_api.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';

abstract final class CompanyModuleMetaRepository {
  static Future<ModuleMetaManifest> load(
    CompanyApi api, {
    required String companyId,
    required String moduleId,
  }) async {
    final existingSlugs = await api.listModuleMetaSlugs(companyId: companyId, moduleId: moduleId);
    final slugMap = <String, dynamic>{};
    for (final slug in ModuleMetaSlugs.all) {
      if (!existingSlugs.contains(slug)) continue;
      try {
        final doc = await api.getModuleMetaDocument(
          companyId: companyId,
          moduleId: moduleId,
          slug: slug,
        );
        slugMap[slug] = doc['body'];
      } catch (_) {
        // skip
      }
    }
    if (slugMap.isEmpty) return ModuleMetaManifest.empty();
    return ModuleMetaManifest.fromSlugMap(slugMap);
  }

  static Future<void> save(
    CompanyApi api, {
    required String companyId,
    required String moduleId,
    required ModuleMetaManifest manifest,
  }) async {
    final existing = await api.listModuleMetaSlugs(companyId: companyId, moduleId: moduleId);
    final existingSet = existing.toSet();
    final slugMap = manifest.toSlugMap();
    for (final slug in ModuleMetaSlugs.all) {
      final body = slugMap[slug] ?? const [];
      if (body.isEmpty) {
        if (existingSet.contains(slug)) {
          await api.putModuleMetaDocument(
            companyId: companyId,
            moduleId: moduleId,
            slug: slug,
            body: const [],
          );
        }
        continue;
      }
      await api.putModuleMetaDocument(
        companyId: companyId,
        moduleId: moduleId,
        slug: slug,
        body: body,
      );
    }
  }
}
