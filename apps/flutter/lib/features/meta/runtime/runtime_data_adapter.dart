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

  Future<void> deleteRow(String rowId) => _inner.deleteRow(rowId);

  void refresh() => _inner.refresh();

  Map<String, dynamic> defaultBodyForTable(String tableSlug) =>
      _inner.defaultBodyForTable(tableSlug);

  List<dynamic> entityRows(String tableSlug, Map<String, dynamic> uiJson) =>
      _inner.entityRows(tableSlug, uiJson);

  @override
  void dispose() {
    _inner.removeListener(notifyListeners);
    super.dispose();
  }
}
