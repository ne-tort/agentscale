import 'package:flutter/foundation.dart';

import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/runtime/cabinet_data_controller.dart';

/// Sync facade over [CabinetDataController] for shared meta interpreters.
class RuntimeDataAdapter extends ChangeNotifier {
  RuntimeDataAdapter(this._inner) {
    _inner.addListener(notifyListeners);
  }

  final CabinetDataController _inner;

  ModuleMetaManifest get manifest => _inner.manifest;

  List<Map<String, dynamic>> itemsForTable(String tableSlug) =>
      _inner.itemsForTable(tableSlug);

  Map<String, dynamic>? itemById(String rowId) => _inner.itemById(rowId);

  Map<String, dynamic> bodyFor(String rowId) => _inner.bodyFor(rowId);

  String createRow(String tableSlug) {
    _inner.createRow(tableSlug).then((_) => notifyListeners());
    return 'pending_${DateTime.now().microsecondsSinceEpoch}';
  }

  Future<String> createRowAsync(String tableSlug, {Map<String, dynamic>? initial}) =>
      _inner.createRow(tableSlug, initial: initial);

  Future<void> upsertBody(String rowId, Map<String, dynamic> body) =>
      _inner.upsertBody(rowId, body);

  Future<void> patchField(String rowId, String field, dynamic value) =>
      _inner.patchField(rowId, field, value);

  Future<void> loadAll() => _inner.loadAll();

  bool isServerPaged(String tableSlug) => _inner.isServerPaged(tableSlug);

  int serverPage(String tableSlug) => _inner.serverPage(tableSlug);

  int serverPageSize(String tableSlug) => _inner.serverPageSize(tableSlug);

  int serverTotal(String tableSlug) => _inner.serverTotal(tableSlug);

  String serverSearch(String tableSlug) => _inner.serverSearch(tableSlug);

  Future<void> setServerPage(String tableSlug, int page) =>
      _inner.setServerPage(tableSlug, page);

  Future<void> setServerSearch(String tableSlug, String query) =>
      _inner.setServerSearch(tableSlug, query);

  Future<void> reloadServerPage(String tableSlug) =>
      _inner.reloadServerPage(tableSlug);

  Future<void> deleteRow(String rowId) => _inner.deleteRow(rowId);

  void refresh() => _inner.refresh();

  Map<String, dynamic> defaultBodyForTable(String tableSlug) =>
      _inner.defaultBodyForTable(tableSlug);

  List<dynamic> entityRows(String tableSlug, Map<String, dynamic> uiJson) =>
      _inner.entityRows(tableSlug, uiJson);

  /// Кросс-чатовые вьюхи (`ui_json.data_scope.chats == 'all'`, Закупка):
  /// ленивая проектно-широкая подгрузка строк таблицы.
  bool tableNeedsCrossChat(String tableSlug) =>
      _inner.tableNeedsCrossChat(tableSlug);

  Future<void> ensureCrossChat(String tableSlug) =>
      _inner.ensureCrossChat(tableSlug);

  /// The wrapped controller (module action invocation from tab hosts).
  CabinetDataController get controller => _inner;

  /// Module action invocation (budget sync / exports) from tab-host pages.
  Future<Map<String, dynamic>> invokeAction(String actionId, {String? rowId}) =>
      _inner.invokeAction(actionId, rowId: rowId);

  Map<String, String>? get pickContext => _inner.pickContext;

  void setPickContext({required String typeId, required String slotKey}) =>
      _inner.setPickContext(typeId: typeId, slotKey: slotKey);

  void clearPickContext() => _inner.clearPickContext();

  @override
  void dispose() {
    _inner.removeListener(notifyListeners);
    super.dispose();
  }
}
