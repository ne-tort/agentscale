import 'package:flutter/material.dart';

import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_icon_button.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/features/meta/meta_icon.dart';
import 'package:prodavan/features/meta/meta_label.dart';
import 'package:prodavan/features/meta/module_action_file_download.dart';
import 'package:prodavan/features/meta/runtime/cabinet_data_controller.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Shared `ui_json.scaffold.actions` AppBar buttons (invoke_action entries).
///
/// Used both by [MetaViewScaffoldPage] (pushed view page) and by module tab
/// hosts ([CabinetModuleHost]) so scaffold actions render in the app bar in
/// either entry point.
List<Widget>? buildModuleScaffoldActions({
  required BuildContext context,
  required Map<String, dynamic> view,
  required dynamic seeds,
  String? rowId,
  bool readOnly = false,
}) {
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
        onPressed: () => invokeModuleScaffoldAction(
          context: context,
          seeds: seeds,
          actionId: actionId,
          rowId: rowId,
          label: label,
        ),
      ),
    );
  }
  return out.isEmpty ? null : out;
}

/// Localized action label from the module `actions` meta doc (by id).
String moduleActionLabel(
  BuildContext context,
  String actionId,
  List<Map<String, dynamic>> actions,
) {
  final l10n = AppLocalizations.of(context);
  final locale = Localizations.localeOf(context);
  for (final a in actions) {
    if (a['id']?.toString() == actionId || a['name']?.toString() == actionId) {
      final label = resolveMetaLabel(a['label'], l10n, locale: locale);
      if (label.isNotEmpty) return label;
    }
  }
  return actionId;
}

/// Invokes a module action and shows a proper snackbar:
/// file actions → "downloaded", other actions (sync etc.) → localized label.
Future<void> invokeModuleScaffoldAction({
  required BuildContext context,
  required dynamic seeds,
  required String actionId,
  String? rowId,
  String? label,
  List<Map<String, dynamic>> manifestActions = const [],
}) async {
  if (seeds is! CabinetDataController) return;
  final controller = seeds;
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
      final text =
          (label != null && label.isNotEmpty)
          ? label
          : moduleActionLabel(context, actionId, manifestActions);
      AppSnackBar.success(context, text);
    }
  } catch (e) {
    if (context.mounted) AppErrors.showSnack(context, e);
  }
}
