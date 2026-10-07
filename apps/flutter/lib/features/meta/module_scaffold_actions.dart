import 'package:flutter/material.dart';

import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_icon_button.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/features/meta/meta_icon.dart';
import 'package:prodavan/features/meta/meta_label.dart';
import 'package:prodavan/features/meta/module_action_file_download.dart';
import 'package:prodavan/features/meta/runtime/cabinet_data_controller.dart';
import 'package:prodavan/features/meta/runtime/runtime_data_adapter.dart';
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
  // Server-paged виртуальные таблицы: обновление и фильтр наличия — в шапке
  // СТРАНИЦЫ (не в шапке таблицы).
  final uiJson = ui['ui_json'] is Map ? Map<String, dynamic>.from(ui['ui_json'] as Map) : const <String, dynamic>{};
  if (uiJson['server_paged'] == true && seeds is RuntimeDataAdapter) {
    final RuntimeDataAdapter adapter = seeds;
    final tableSlug = view['table_slug']?.toString() ?? '';
    if (uiJson['stock_filter'] == true && tableSlug.isNotEmpty) {
      out.add(_StockFilterButton(adapter: adapter, tableSlug: tableSlug));
    }
    if (tableSlug.isNotEmpty) {
      out.add(
        _ScaffoldActionButton(
          icon: Icons.refresh,
          tooltip: 'Обновить данные',
          onInvoke: () => adapter.reloadServerPage(tableSlug),
        ),
      );
    }
  }
  for (final a in actions.whereType<Map>()) {
    if (a['kind']?.toString() != 'invoke_action') continue;
    final actionId = a['action']?.toString() ?? '';
    if (actionId.isEmpty || readOnly) continue;
    final label = resolveMetaLabel(a['label'], l10n, locale: locale);
    out.add(
      _ScaffoldActionButton(
        icon: metaIconFromName(
          a['icon']?.toString(),
          fallback: Icons.bolt_outlined,
        ),
        tooltip: label.isNotEmpty ? label : actionId,
        onInvoke: () => invokeModuleScaffoldAction(
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

/// Тумблер «только в наличии» в шапке страницы (server-paged вьюхи):
/// слушает адаптер, чтобы подсветка была актуальной после переключения.
class _StockFilterButton extends StatelessWidget {
  const _StockFilterButton({required this.adapter, required this.tableSlug});

  final RuntimeDataAdapter adapter;
  final String tableSlug;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: adapter,
      builder: (context, _) {
        final on = adapter.serverStockOnly(tableSlug);
        return AppIconButton(
          icon: on ? Icons.inventory : Icons.inventory_2_outlined,
          tooltip: on ? 'Показать всё (включая под заказ)' : 'Только в наличии',
          onPressed: () => adapter.setServerStockOnly(tableSlug, !on),
        );
      },
    );
  }
}

/// Одна кнопка-экшен AppBar: во время выполнения блокируется и показывает
/// спиннер (долгие экшены — экспорт мастер-прайса и т.п.), повторный тап
/// невозможен; ошибки показывает [invokeModuleScaffoldAction].
class _ScaffoldActionButton extends StatefulWidget {
  const _ScaffoldActionButton({
    required this.icon,
    required this.tooltip,
    required this.onInvoke,
  });

  final IconData icon;
  final String tooltip;
  final Future<void> Function() onInvoke;

  @override
  State<_ScaffoldActionButton> createState() => _ScaffoldActionButtonState();
}

class _ScaffoldActionButtonState extends State<_ScaffoldActionButton> {
  bool _busy = false;

  Future<void> _run() async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      await widget.onInvoke();
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_busy) {
      return Tooltip(
        message: widget.tooltip,
        child: const Padding(
          padding: EdgeInsets.symmetric(horizontal: 12),
          child: SizedBox(
            width: 18,
            height: 18,
            child: CircularProgressIndicator(strokeWidth: 2),
          ),
        ),
      );
    }
    return AppIconButton(
      icon: widget.icon,
      tooltip: widget.tooltip,
      onPressed: _run,
    );
  }
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
  final dynamic raw = seeds;
  if (raw is! CabinetDataController && raw is! RuntimeDataAdapter) return;
  final controller =
      raw is CabinetDataController ? raw : (raw as RuntimeDataAdapter).controller;
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
