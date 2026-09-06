import 'package:flutter/material.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/meta/interpreters/hub_interpreter.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/runtime/cabinet_data_controller.dart';
import 'package:prodavan/features/meta/runtime/module_runtime_scope.dart';
import 'package:prodavan/features/meta/runtime/runtime_data_adapter.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Live cabinet module UI — meta views + data CRUD via API.
class CabinetModuleRuntimePage extends StatefulWidget {
  const CabinetModuleRuntimePage({
    super.key,
    required this.cabinetId,
    required this.moduleId,
    required this.moduleName,
  });

  final String cabinetId;
  final String moduleId;
  final String moduleName;

  @override
  State<CabinetModuleRuntimePage> createState() => _CabinetModuleRuntimePageState();
}

class _CabinetModuleRuntimePageState extends State<CabinetModuleRuntimePage> {
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
            moduleId: widget.moduleId,
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
        moduleId: widget.moduleId,
        manifest: manifest,
      );
      data.onProjectsRematerialize = (count, {required inline}) {
        if (!mounted) return;
        final l10n = AppLocalizations.of(context);
        final message = inline
            ? l10n.cabinetModuleRematerializeDone(count)
            : l10n.cabinetModuleRematerializeScheduled(count);
        AppSnackBar.info(context, message);
      };
      data.onWorkspaceOutdated = () {
        if (!mounted) return;
        final l10n = AppLocalizations.of(context);
        AppSnackBar.info(context, l10n.cabinetModuleWorkspaceOutdated);
      };
      await data.loadAll();
      if (!mounted) return;
      setState(() {
        _manifest = manifest;
        _adapter = RuntimeDataAdapter(data);
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      AppErrors.showSnack(context, e);
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

  Map<String, dynamic>? _rootView(ModuleMetaManifest manifest) {
    final tabs = manifest.enabledTabs();
    if (tabs.isEmpty) return null;
    final viewSlug = tabs.first['view_slug'] as String?;
    if (viewSlug == null) return null;
    return manifest.viewBySlug(viewSlug);
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_error != null || _manifest == null || _adapter == null) {
      return EmptyPlaceholder(
        title: _error != null
            ? AppErrors.localize(context, _error!)
            : l10n.errorUnexpected,
        action: TextButton(
          onPressed: _load,
          child: Text(l10n.commonRetry),
        ),
      );
    }
    final manifest = _manifest!;
    final adapter = _adapter!;
    final viewSlug = _nestedViewSlug;
    final view = viewSlug != null ? manifest.viewBySlug(viewSlug) : _rootView(manifest);

    if (view == null) {
      return EmptyPlaceholder(title: l10n.adminMetaInvalid);
    }

    return ModuleRuntimeScope(
      cabinetId: widget.cabinetId,
      moduleId: widget.moduleId,
      api: workContext.api,
      child: AppScaffold(
        title: Row(
          children: [
            if (viewSlug != null)
              IconButton(
                icon: const Icon(Icons.arrow_back),
                onPressed: () => setState(() {
                  _nestedViewSlug = null;
                  _nestedRowId = null;
                }),
              ),
            Expanded(
              child: Text(
                viewSlug != null ? (view['slug'] as String? ?? '') : widget.moduleName,
                overflow: TextOverflow.ellipsis,
              ),
            ),
          ],
        ),
        body: ListenableBuilder(
          listenable: adapter,
          builder: (context, _) {
            return ViewInterpreterHost(
              manifest: manifest,
              view: view,
              seeds: adapter,
              rowId: _nestedRowId,
              onOpenView: _openView,
            );
          },
        ),
      ),
    );
  }
}
