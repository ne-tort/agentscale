import 'package:flutter/foundation.dart';

/// Ephemeral pick filter/key for collection `set_on_context` (not persisted in row body).
///
/// Keys match meta `row_filter_from_context` / `map_key_from_context` placeholders:
/// `_pick_type_id`, `_slot_key`.
mixin ModulePickContextMixin on ChangeNotifier {
  Map<String, String>? _pickContext;

  Map<String, String>? get pickContext =>
      _pickContext == null ? null : Map<String, String>.unmodifiable(_pickContext!);

  void setPickContext({required String typeId, required String slotKey}) {
    _pickContext = {
      '_pick_type_id': typeId,
      '_slot_key': slotKey,
    };
  }

  void clearPickContext() {
    _pickContext = null;
  }
}
