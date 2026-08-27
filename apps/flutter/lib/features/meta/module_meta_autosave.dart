import 'dart:async';
import 'dart:convert';

import 'package:prodavan/features/meta/module_meta_manifest.dart';

typedef ModuleMetaSaveHandler = Future<void> Function(ModuleMetaManifest manifest);

/// Debounced manifest autosave — flush on dispose, no timer leaks.
class ModuleMetaAutosave {
  ModuleMetaAutosave({
    required ModuleMetaSaveHandler onSave,
    Duration interval = const Duration(seconds: 5),
  })  : _onSave = onSave,
        _interval = interval;

  final ModuleMetaSaveHandler _onSave;
  final Duration _interval;

  Timer? _timer;
  bool _disposed = false;
  bool _dirty = false;
  bool _saving = false;
  String _pendingText = '';
  String _lastFingerprint = '';

  void onTextChanged(String text, {required bool canSave}) {
    if (_disposed) return;
    _pendingText = text;
    if (!canSave) {
      _timer?.cancel();
      _timer = null;
      return;
    }
    _dirty = true;
    _timer?.cancel();
    _timer = Timer(_interval, _onTimer);
  }

  Future<void> _onTimer() async {
    if (_disposed || !_dirty) return;
    await _saveOnce();
  }

  Future<void> flushIfDirty() async {
    if (_disposed || !_dirty) return;
    _timer?.cancel();
    _timer = null;
    await _saveOnce();
  }

  Future<void> _saveOnce() async {
    if (_disposed || _saving || !_dirty) return;
    final fingerprint = _fingerprint(_pendingText);
    if (fingerprint == _lastFingerprint) {
      _dirty = false;
      return;
    }
    _saving = true;
    try {
      final parsed = jsonDecode(_pendingText);
      final manifest = ModuleMetaManifest.fromJson(parsed);
      await _onSave(manifest);
      if (_disposed) return;
      _lastFingerprint = fingerprint;
      _dirty = false;
    } finally {
      _saving = false;
    }
  }

  void markSaved(String text) {
    _pendingText = text;
    _lastFingerprint = _fingerprint(text);
    _dirty = false;
  }

  void dispose() {
    _disposed = true;
    _timer?.cancel();
    _timer = null;
    if (_dirty) {
      unawaited(_saveOnce());
    }
  }

  static String _fingerprint(String text) {
    try {
      final decoded = jsonDecode(text);
      return jsonEncode(decoded);
    } catch (_) {
      return text;
    }
  }
}
