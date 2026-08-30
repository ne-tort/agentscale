import 'package:flutter/material.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/employee/cabinet_nav_loader.dart';
import 'package:prodavan/features/meta/interpreters/hub_interpreter.dart';
import 'package:prodavan/features/meta/meta_label.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/runtime/cabinet_data_controller.dart';
import 'package:prodavan/features/meta/runtime/module_runtime_scope.dart';
import 'package:prodavan/features/meta/runtime/runtime_data_adapter.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Renders one module tab view inside [CabinetShell] or pushed from [CabinetManagementPage].
class CabinetModuleHost extends StatefulWidget {
  const CabinetModuleHost({
    super.key,
    required this.cabinetId,
    required this.entry,
    this.embedded = false,
  });

  final String cabinetId;
  final CabinetNavEntry entry;
  final bool embedded;

  @override
  State<CabinetModuleHost> createState() => _CabinetModuleHostState();
}

class _CabinetModuleHostState extends State<CabinetModuleHost> {
  bool _loading = true;
  Object? _error;
  ModuleMetaManifest? _manifest;
  RuntimeDataAdapter? _adapter;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final ProdavanApi api = workContext.api;
      final docs = <String, dynamic>{};
      for (final slug in ModuleMetaSlugs.all) {
        try {
          final doc = await api.getCabinetModuleMeta(
            cabinetId: widget.cabinetId,
            moduleId: widget.entry.moduleId,
            slug: slug,
          );
          docs[slug] = doc['body'];
        } catch (_) {
          if (ModuleMetaSlugs.required.contains(slug)) {
            rethrow;
          }
        }
      }
      final manifest = ModuleMetaManifest.fromSlugMap(docs);
      final data = CabinetDataController(
        api: api,
        cabinetId: widget.cabinetId,
        moduleId: widget.entry.moduleId,
        manifest: manifest,
      );
      await data.loadAll();
      if (!mounted) return;
      setState(() {
        _manifest = manifest;
        _adapter = RuntimeDataAdapter(data);
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e;
        _loading = false;
      });
    }
  }

  void _openView(String viewSlug, {String? rowId}) {
    final manifest = _manifest;
    final adapter = _adapter;
    if (manifest == null || adapter == null) return;
    final view = manifest.viewBySlug(viewSlug);
    if (view == null) return;
    final l10n = AppLocalizations.of(context);
    final locale = Localizations.localeOf(context);
    final title = resolveViewScaffoldTitle(view, l10n, locale: locale);
    final fallback = view['label'] as String? ?? viewSlug;
    final pageTitle = (title != null && title.isNotEmpty) ? title : fallback;
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (ctx) => ModuleRuntimeScope(
          cabinetId: widget.cabinetId,
          moduleId: widget.entry.moduleId,
          api: workContext.api,
          child: AppScaffold(
            title: Text(pageTitle),
            body: ViewInterpreterHost(
              manifest: manifest,
              view: view,
              seeds: adapter,
              rowId: rowId,
              onOpenView: _openView,
            ),
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_error != null) {
      return Center(child: Text('$_error'));
    }
    final manifest = _manifest!;
    final adapter = _adapter!;
    final viewSlug = widget.entry.viewSlug;
    if (viewSlug.isEmpty) {
      return EmptyPlaceholder(title: l10n.adminMetaInvalid);
    }
    final view = manifest.viewBySlug(viewSlug);
    if (view == null) {
      return EmptyPlaceholder(title: l10n.adminMetaInvalid);
    }

    final body = ModuleRuntimeScope(
      cabinetId: widget.cabinetId,
      moduleId: widget.entry.moduleId,
      api: workContext.api,
      child: ViewInterpreterHost(
        manifest: manifest,
        view: view,
        seeds: adapter,
        onOpenView: _openView,
      ),
    );

    if (widget.embedded) return body;

    return AppScaffold(
      title: Text(widget.entry.label),
      body: body,
    );
  }
}
