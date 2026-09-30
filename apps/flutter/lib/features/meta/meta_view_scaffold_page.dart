import 'package:flutter/material.dart';

import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_icon_button.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/features/meta/interpreters/hub_interpreter.dart';
import 'package:prodavan/features/meta/meta_icon.dart';
import 'package:prodavan/features/meta/module_action_file_download.dart';
import 'package:prodavan/features/meta/meta_label.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/runtime/cabinet_data_controller.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Pushed meta view page with live AppBar title from `title_template` + row body.
class MetaViewScaffoldPage extends StatelessWidget {
  const MetaViewScaffoldPage({
    super.key,
    required this.manifest,
    required this.view,
    required this.seeds,
    required this.onOpenView,
    this.rowId,
    this.readOnly = false,
    this.wrapBody,
  });

  final ModuleMetaManifest manifest;
  final Map<String, dynamic> view;
  final dynamic seeds;
  final void Function(String viewSlug, {String? rowId}) onOpenView;
  final String? rowId;
  final bool readOnly;
  final Widget Function(Widget body)? wrapBody;

  String _title(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final locale = Localizations.localeOf(context);
    Map<String, dynamic>? body;
    final id = rowId;
    if (id != null && id.isNotEmpty) {
      try {
        body = Map<String, dynamic>.from(seeds.bodyFor(id) as Map? ?? const {});
      } catch (_) {
        body = null;
      }
    }
    final resolved = resolveViewScaffoldTitle(
      view,
      l10n,
      locale: locale,
      rowBody: body,
    );
    if (resolved != null && resolved.trim().isNotEmpty) return resolved.trim();
    return view['label'] as String? ?? view['slug'] as String? ?? '';
  }

  /// AppBar actions from `ui_json.scaffold.actions` (invoke_action entries
  /// with optional `file_ref` download, e.g. budget xlsx / КП PDF export).
  List<Widget>? _scaffoldActions(BuildContext context) {
    final ui = view['ui_json'];
    if (ui is! Map) return null;
    final scaffold = ui['scaffold'];
    if (scaffold is! Map) return null;
    final actions = scaffold['actions'];
    if (actions is! List) return null;
    final l10n = AppLocalizations.of(context);
    final locale = Localizations.localeOf(context);
    final out = <Widget>[];
    for (final a in actions.whereType<Map>()) {
      if (a['kind']?.toString() != 'invoke_action') continue;
      final actionId = a['action']?.toString() ?? '';
      if (actionId.isEmpty || readOnly) continue;
      final label = resolveMetaLabel(a['label'], l10n, locale: locale);
      out.add(
        AppIconButton(
          icon: metaIconFromName(
            a['icon']?.toString(),
            fallback: Icons.bolt_outlined,
          ),
          tooltip: label.isNotEmpty ? label : actionId,
          onPressed: () => _invokeScaffoldAction(context, actionId),
        ),
      );
    }
    return out.isEmpty ? null : out;
  }

  Future<void> _invokeScaffoldAction(BuildContext context, String actionId) async {
    if (seeds is! CabinetDataController) {
      return;
    }
    final controller = seeds as CabinetDataController;
    try {
      final result = await controller.invokeAction(actionId, rowId: rowId);
      final saved = await saveModuleActionFile(controller, result);
      if (!context.mounted) return;
      if (saved) {
        AppSnackBar.success(
          context,
          AppLocalizations.of(context).projectWorkspaceDownloaded,
        );
      } else {
        AppSnackBar.info(context, actionId);
      }
    } catch (e) {
      if (context.mounted) AppErrors.showSnack(context, e);
    }
  }

  @override
  Widget build(BuildContext context) {
    final listenable =
        seeds is Listenable ? seeds as Listenable : const _NeverListenable();
    final body = ViewInterpreterHost(
      manifest: manifest,
      view: view,
      seeds: seeds,
      rowId: rowId,
      readOnly: readOnly,
      onOpenView: onOpenView,
    );
    return ListenableBuilder(
      listenable: listenable,
      builder: (context, _) {
        final page = AppScaffold(
          title: Text(_title(context)),
          actions: _scaffoldActions(context),
          body: body,
        );
        return wrapBody != null ? wrapBody!(page) : page;
      },
    );
  }
}

class _NeverListenable extends Listenable {
  const _NeverListenable();
  @override
  void addListener(VoidCallback listener) {}
  @override
  void removeListener(VoidCallback listener) {}
}
