import 'package:flutter/material.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/employee/cabinet_nav_loader.dart';
import 'package:prodavan/features/meta/interpreters/hub_interpreter.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/runtime/cabinet_data_controller.dart';
import 'package:prodavan/features/meta/runtime/runtime_data_adapter.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Renders one module tab view inside [CabinetShell].
class CabinetModuleHost extends StatefulWidget {
  const CabinetModuleHost({
    super.key,
    required this.cabinetId,
    required this.entry,
  });

  final String cabinetId;
  final CabinetNavEntry entry;

  @override
  State<CabinetModuleHost> createState() => _CabinetModuleHostState();
}

class _CabinetModuleHostState extends State<CabinetModuleHost> {
  bool _loading = true;
  Object? _error;
  ModuleMetaManifest? _manifest;
  RuntimeDataAdapter? _adapter;
  String? _nestedViewSlug;
  String? _nestedRowId;

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
    setState(() {
      _nestedViewSlug = viewSlug;
      _nestedRowId = rowId;
    });
  }

  void _popView() {
    setState(() {
      _nestedViewSlug = null;
      _nestedRowId = null;
    });
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    if (_loading) {
      return const AppScaffold(body: Center(child: CircularProgressIndicator()));
    }
    if (_error != null) {
      return AppScaffold(
        body: Center(child: Text('$_error')),
      );
    }
    final manifest = _manifest!;
    final adapter = _adapter!;
    final viewSlug = _nestedViewSlug ?? widget.entry.viewSlug;
    final view = manifest.viewBySlug(viewSlug);
    if (view == null) {
      return AppScaffold(
        body: EmptyPlaceholder(title: l10n.adminMetaInvalid),
      );
    }

    return AppScaffold(
      title: Text(_nestedViewSlug == null ? widget.entry.label : (view['title'] as String? ?? viewSlug)),
      actions: _nestedViewSlug != null
          ? [
              IconButton(
                icon: const Icon(Icons.arrow_back),
                onPressed: _popView,
              ),
            ]
          : null,
      body: ViewInterpreterHost(
        manifest: manifest,
        view: view,
        seeds: adapter,
        rowId: _nestedRowId,
        onOpenView: _openView,
      ),
    );
  }
}
