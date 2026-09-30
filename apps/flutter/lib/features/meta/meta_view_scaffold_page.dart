import 'package:flutter/material.dart';

import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/features/meta/interpreters/hub_interpreter.dart';
import 'package:prodavan/features/meta/meta_label.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/module_scaffold_actions.dart';
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

  List<Widget>? _scaffoldActions(BuildContext context) =>
      buildModuleScaffoldActions(
        context: context,
        view: view,
        seeds: seeds,
        rowId: rowId,
        readOnly: readOnly,
      );

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
