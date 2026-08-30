import 'package:prodavan/core/api/admin_api.dart';
import 'package:prodavan/core/api/company_api.dart';
import 'package:prodavan/features/meta/meta_icon.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';

/// Loads dynamic product-shell nav entries from module catalog meta.
abstract final class ShellNavLoader {
  static Future<List<ShellNavEntry>> loadAdmin(AdminApi api) async {
    final modules = await api.listModules();
    return _loadFromModules(
      modules: modules,
      fetchTabs: (moduleId) async {
        final slugs = await api.listModuleMetaSlugs(moduleId);
        if (!slugs.contains(ModuleMetaSlugs.tabs)) return const [];
        final doc = await api.getModuleMetaDocument(moduleId: moduleId, slug: ModuleMetaSlugs.tabs);
        return ModuleMetaManifest.parseMetaList(doc['body']);
      },
      contour: ShellNavContour.admin,
    );
  }

  static Future<List<ShellNavEntry>> loadCompany(CompanyApi api, String companyId) async {
    final modules = await api.listModules(companyId);
    return _loadFromModules(
      modules: modules,
      fetchTabs: (moduleId) async {
        final slugs = await api.listModuleMetaSlugs(companyId: companyId, moduleId: moduleId);
        if (!slugs.contains(ModuleMetaSlugs.tabs)) return const [];
        final doc = await api.getModuleMetaDocument(
          companyId: companyId,
          moduleId: moduleId,
          slug: ModuleMetaSlugs.tabs,
        );
        return ModuleMetaManifest.parseMetaList(doc['body']);
      },
      contour: ShellNavContour.company,
    );
  }

  static Future<List<ShellNavEntry>> _loadFromModules({
    required List<Map<String, dynamic>> modules,
    required Future<List<Map<String, dynamic>>> Function(String moduleId) fetchTabs,
    required String contour,
  }) async {
    final parsed = <({String id, String name, List<Map<String, dynamic>> tabs})>[];
    for (final mod in modules) {
      final id = mod['id'] as String?;
      if (id == null) continue;
      if (mod['status'] == 'archived') continue;
      try {
        final tabs = await fetchTabs(id);
        if (tabs.isEmpty) continue;
        parsed.add((id: id, name: mod['name'] as String? ?? id, tabs: tabs));
      } catch (_) {
        // skip modules without readable meta
      }
    }
    return mergeShellNavEntries(contour: contour, modules: parsed);
  }

  static Future<({List<ShellNavEntry> rail, List<ShellNavEntry> management})> loadAdminBundle(
    AdminApi api,
  ) async {
    final entries = await loadAdmin(api);
    return splitShellNavEntries(entries);
  }

  static Future<({List<ShellNavEntry> rail, List<ShellNavEntry> management})> loadCompanyBundle(
    CompanyApi api,
    String companyId,
  ) async {
    final entries = await loadCompany(api, companyId);
    return splitShellNavEntries(entries);
  }
}
