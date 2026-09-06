import 'package:flutter/material.dart';

import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/meta/company_module_meta_repository.dart';
import 'package:prodavan/features/meta/interpreters/hub_interpreter.dart';
import 'package:prodavan/features/meta/meta_icon.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/module_meta_repository.dart';
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

  void _openView(String viewSlug, {String? rowId}) {
    final manifest = _manifest;
    final seeds = _seeds;
    if (manifest == null || seeds == null) return;
    final view = manifest.viewBySlug(viewSlug);
    if (view == null) return;
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => AppScaffold(
          title: Text(view['label'] as String? ?? viewSlug),
          body: ViewInterpreterHost(
            manifest: manifest,
            view: view,
            seeds: seeds,
            rowId: rowId,
            readOnly: false,
            onOpenView: _openView,
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
    final seeds = _seeds!;
    final viewSlug = widget.entry.viewSlug;
    if (viewSlug.isEmpty) {
      return EmptyPlaceholder(title: l10n.adminMetaInvalid);
    }
    final view = manifest.viewBySlug(viewSlug);
    if (view == null) {
      return EmptyPlaceholder(title: l10n.adminMetaInvalid);
    }

    final body = ViewInterpreterHost(
      manifest: manifest,
      view: view,
      seeds: seeds,
      readOnly: false,
      onOpenView: _openView,
    );

    if (widget.embedded) return body;

    return AppScaffold(
      title: Text(widget.entry.label),
      body: body,
    );
  }
}
