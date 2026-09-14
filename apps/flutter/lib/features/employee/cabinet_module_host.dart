import 'package:flutter/material.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/employee/cabinet_nav_loader.dart';
import 'package:prodavan/features/meta/interpreters/hub_interpreter.dart';
import 'package:prodavan/features/meta/meta_view_scaffold_page.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/runtime/cabinet_data_controller.dart';
import 'package:prodavan/features/meta/runtime/module_runtime_scope.dart';
import 'package:prodavan/features/meta/runtime/runtime_data_adapter.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Renders one module tab view inside [CabinetShell] or pushed from [CabinetManagementPage].
///
/// Pass [projectId] for Management/Data hubs (project leaf). Omit for rail (cabinet instance).
class CabinetModuleHost extends StatefulWidget {
  const CabinetModuleHost({
    super.key,
    required this.cabinetId,
    required this.entry,
    this.projectId,
    this.sessionId,
    this.embedded = false,
  });

  final String cabinetId;
  /// When set, meta/data come from the project leaf instance (hubs). Never inferred from selection.
  final String? projectId;
  /// Active agent chat for ``scope.chats=current`` tables.
  final String? sessionId;
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
  CabinetDataController? _data;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _data?.dispose();
    super.dispose();
  }

  @override
  void didUpdateWidget(covariant CabinetModuleHost oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.projectId != widget.projectId ||
        oldWidget.sessionId != widget.sessionId ||
        oldWidget.entry.moduleId != widget.entry.moduleId) {
      _load();
    }
  }

  Future<Map<String, dynamic>> _fetchMetaDoc(ProdavanApi api, String slug) async {
    final projectId = widget.projectId;
    if (projectId != null && projectId.isNotEmpty) {
      return api.getProjectRuntimeModuleMeta(
        projectId: projectId,
        moduleId: widget.entry.moduleId,
        slug: slug,
      );
    }
    return api.getCabinetModuleMeta(
      cabinetId: widget.cabinetId,
      moduleId: widget.entry.moduleId,
      slug: slug,
    );
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final ProdavanApi api = workContext.api;
      final projectId = widget.projectId;
      final docs = <String, dynamic>{};

      // Warm leaf/cabinet instance on the first required slug (fork once), then
      // fetch the rest in parallel so gateway timeouts are less likely.
      final warm = ModuleMetaSlugs.required.first;
      final warmDoc = await _fetchMetaDoc(api, warm);
      docs[warm] = warmDoc['body'];

      final remaining = ModuleMetaSlugs.all.where((s) => s != warm).toList();
      final settled = await Future.wait(
        remaining.map((slug) async {
          try {
            final doc = await _fetchMetaDoc(api, slug);
            return (slug, doc['body'], null);
          } catch (e) {
            return (slug, null, e);
          }
        }),
      );
      for (final (slug, body, err) in settled) {
        if (err != null) {
          if (ModuleMetaSlugs.required.contains(slug)) {
            throw err;
          }
          continue;
        }
        docs[slug] = body;
      }

      final manifest = ModuleMetaManifest.fromSlugMap(docs);
      _data?.dispose();
      final data = CabinetDataController(
        api: api,
        cabinetId: widget.cabinetId,
        projectId: projectId,
        sessionId: widget.sessionId,
        moduleId: widget.entry.moduleId,
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
      data.onWorkspaceOutdated = null;
      await data.loadAll();
      if (!mounted) return;
      setState(() {
        _data = data;
        _manifest = manifest;
        _adapter = RuntimeDataAdapter(data);
        _loading = false;
        _error = null;
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
    final manifest = _manifest;
    final adapter = _adapter;
    if (manifest == null || adapter == null) return;
    final view = manifest.viewBySlug(viewSlug);
    if (view == null) return;
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (ctx) => MetaViewScaffoldPage(
          manifest: manifest,
          view: view,
          seeds: adapter,
          rowId: rowId,
          onOpenView: _openView,
          wrapBody: (page) => ModuleRuntimeScope.cabinet(
            cabinetId: widget.cabinetId,
            projectId: widget.projectId,
            sessionId: widget.sessionId,
            moduleId: widget.entry.moduleId,
            api: workContext.api,
            child: page,
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
      return EmptyPlaceholder(
        title: AppErrors.localize(context, _error!),
        action: TextButton(
          onPressed: _load,
          child: Text(l10n.commonRetry),
        ),
      );
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

    final content = ViewInterpreterHost(
      manifest: manifest,
      view: view,
      seeds: adapter,
      onOpenView: _openView,
    );

    final body = ModuleRuntimeScope.cabinet(
      cabinetId: widget.cabinetId,
      projectId: widget.projectId,
      sessionId: widget.sessionId,
      moduleId: widget.entry.moduleId,
      api: workContext.api,
      child: content,
    );

    if (widget.embedded) return body;

    return AppScaffold(
      title: Text(widget.entry.label),
      body: body,
    );
  }
}
