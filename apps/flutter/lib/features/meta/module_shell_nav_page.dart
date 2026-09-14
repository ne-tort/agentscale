import 'package:flutter/material.dart';

import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/meta/company_module_meta_repository.dart';
import 'package:prodavan/features/meta/interpreters/hub_interpreter.dart';
import 'package:prodavan/features/meta/meta_icon.dart';
import 'package:prodavan/features/meta/meta_view_scaffold_page.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/module_meta_repository.dart';
import 'package:prodavan/features/meta/runtime/module_runtime_scope.dart';
import 'package:prodavan/features/meta/runtime/owner_module_data_controller.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Renders a module view opened from product-shell navigation (admin / company).
///
/// Data rows come from the caller's module instance (platform or company), not
/// from template seed_rows — so deletes persist and do not cascade to children.
class ModuleShellNavPage extends StatefulWidget {
  const ModuleShellNavPage({
    super.key,
    required this.entry,
    this.embedded = false,
    this.companyId,
  });

  final ShellNavEntry entry;
  final bool embedded;

  /// When set, load meta via company API instead of admin.
  final String? companyId;

  @override
  State<ModuleShellNavPage> createState() => _ModuleShellNavPageState();
}

class _ModuleShellNavPageState extends State<ModuleShellNavPage> {
  bool _loading = true;
  ModuleMetaManifest? _manifest;
  OwnerModuleDataController? _seeds;
  Object? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _seeds?.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final manifest = widget.companyId != null
          ? await CompanyModuleMetaRepository.load(
              companyContext.api,
              companyId: widget.companyId!,
              moduleId: widget.entry.moduleId,
            )
          : await ModuleMetaRepository.load(adminContext.api, widget.entry.moduleId);
      if (!mounted) return;
      final controller = widget.companyId != null
          ? OwnerModuleDataController.company(
              api: companyContext.api,
              companyId: widget.companyId!,
              moduleId: widget.entry.moduleId,
              manifest: manifest,
            )
          : OwnerModuleDataController.platform(
              api: adminContext.api,
              moduleId: widget.entry.moduleId,
              manifest: manifest,
            );
      await controller.loadAll();
      if (!mounted) {
        controller.dispose();
        return;
      }
      _seeds?.dispose();
      setState(() {
        _manifest = manifest;
        _seeds = controller;
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

  Widget _wrapScope(Widget child) {
    final companyId = widget.companyId;
    if (companyId != null && companyId.isNotEmpty) {
      final api = companyContext.api;
      return ModuleRuntimeScope.company(
        companyId: companyId,
        moduleId: widget.entry.moduleId,
        uploadContentFn: ({
          required String filename,
          required List<int> bytes,
          String? mime,
        }) {
          return api.uploadModuleContent(
            companyId: companyId,
            moduleId: widget.entry.moduleId,
            filename: filename,
            bytes: bytes,
            mime: mime,
          );
        },
        uploadSecretFn: ({
          required String secret,
          String? label,
        }) {
          return api.uploadModuleSecret(
            companyId: companyId,
            moduleId: widget.entry.moduleId,
            secret: secret,
            label: label,
          );
        },
        invokeActionFn: ({
          required String actionId,
          String? rowId,
        }) {
          return api.invokeModuleAction(
            companyId: companyId,
            moduleId: widget.entry.moduleId,
            actionId: actionId,
            rowId: rowId,
          );
        },
        child: child,
      );
    }
    final api = adminContext.api;
    return ModuleRuntimeScope.platform(
      moduleId: widget.entry.moduleId,
      uploadContentFn: ({
        required String filename,
        required List<int> bytes,
        String? mime,
      }) {
        return api.uploadModuleContent(
          moduleId: widget.entry.moduleId,
          filename: filename,
          bytes: bytes,
          mime: mime,
        );
      },
      uploadSecretFn: ({
        required String secret,
        String? label,
      }) {
        return api.uploadModuleSecret(
          moduleId: widget.entry.moduleId,
          secret: secret,
          label: label,
        );
      },
      invokeActionFn: ({
        required String actionId,
        String? rowId,
      }) {
        return api.invokeModuleAction(
          moduleId: widget.entry.moduleId,
          actionId: actionId,
          rowId: rowId,
        );
      },
      child: child,
    );
  }

  void _openView(String viewSlug, {String? rowId}) {
    final manifest = _manifest;
    final seeds = _seeds;
    if (manifest == null || seeds == null) return;
    final view = manifest.viewBySlug(viewSlug);
    if (view == null) return;
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => MetaViewScaffoldPage(
          manifest: manifest,
          view: view,
          seeds: seeds,
          rowId: rowId,
          readOnly: false,
          onOpenView: _openView,
          wrapBody: (page) => _wrapScope(page),
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
    final seeds = _seeds!;
    final viewSlug = widget.entry.viewSlug;
    if (viewSlug.isEmpty) {
      return EmptyPlaceholder(title: l10n.adminMetaInvalid);
    }
    final view = manifest.viewBySlug(viewSlug);
    if (view == null) {
      return EmptyPlaceholder(title: l10n.adminMetaInvalid);
    }

    final body = _wrapScope(
      ViewInterpreterHost(
        manifest: manifest,
        view: view,
        seeds: seeds,
        readOnly: false,
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

/// Opens live platform/company instance data for a module (admin «Предзаполнение»).
///
/// Prefers hub views, then first enabled tab by order — works for employee-only
/// modules like [mod_equipment] that never appear on the admin shell rail.
ShellNavEntry? shellNavEntryForModuleSeed({
  required String moduleId,
  required String moduleName,
  required ModuleMetaManifest manifest,
}) {
  final tabs = manifest.enabledTabs();
  if (tabs.isEmpty) return null;
  Map<String, dynamic>? chosen;
  for (final tab in tabs) {
    final slug = tab['view_slug'] as String? ?? '';
    final view = slug.isEmpty ? null : manifest.viewBySlug(slug);
    if (view != null && (view['kind'] as String?) == 'hub') {
      chosen = tab;
      break;
    }
  }
  chosen ??= tabs.first;
  final title = chosen['title'] as String? ?? moduleName;
  return ShellNavEntry(
    moduleId: moduleId,
    moduleName: moduleName,
    tab: chosen,
    label: title,
  );
}
