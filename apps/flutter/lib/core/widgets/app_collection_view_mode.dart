import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:prodavan/core/responsive/app_breakpoints.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_icon_button.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Persisted list/table preference per page (`prodavan.entityView.<pageKey>`).
class AppCollectionViewModeStore extends ChangeNotifier {
  AppCollectionViewModeStore(this.pageKey);

  final String pageKey;

  static const _prefix = 'prodavan.entityView.';

  AppEntityCollectionMode? _mode;
  bool _loaded = false;

  bool get isLoaded => _loaded;

  /// Explicit preference, or null until loaded / unset (caller may fall back to breakpoint).
  AppEntityCollectionMode? get mode => _mode;

  String get _prefsKey => '$_prefix$pageKey';

  Future<void> load() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_prefsKey);
    _mode = switch (raw) {
      'list' => AppEntityCollectionMode.list,
      'table' => AppEntityCollectionMode.table,
      _ => null,
    };
    _loaded = true;
    notifyListeners();
  }

  AppEntityCollectionMode resolve(BuildContext context) {
    if (_mode != null) return _mode!;
    return AppBreakpoints.isWide(context)
        ? AppEntityCollectionMode.table
        : AppEntityCollectionMode.list;
  }

  Future<void> setMode(AppEntityCollectionMode mode) async {
    if (_mode == mode) return;
    _mode = mode;
    notifyListeners();
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(
      _prefsKey,
      mode == AppEntityCollectionMode.list ? 'list' : 'table',
    );
  }

  Future<void> toggle(BuildContext context) async {
    final next = resolve(context) == AppEntityCollectionMode.list
        ? AppEntityCollectionMode.table
        : AppEntityCollectionMode.list;
    await setMode(next);
  }
}

/// Single AppBar toggle: icon reflects current mode; tap switches list ↔ table.
class AppCollectionViewModeButton extends StatelessWidget {
  const AppCollectionViewModeButton({super.key, required this.store});

  final AppCollectionViewModeStore store;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return ListenableBuilder(
      listenable: store,
      builder: (context, _) {
        final mode = store.resolve(context);
        final isList = mode == AppEntityCollectionMode.list;
        return AppIconButton(
          icon: isList ? Icons.view_list_outlined : Icons.table_rows_outlined,
          tooltip: isList ? l10n.commonList : l10n.commonTable,
          onPressed: () => store.toggle(context),
        );
      },
    );
  }
}
