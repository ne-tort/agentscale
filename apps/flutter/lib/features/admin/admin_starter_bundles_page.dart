import 'package:flutter/material.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';

import 'package:prodavan/core/refresh/app_auto_refresh.dart';
import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Read-only official starter bundle catalog (L04).
class AdminStarterBundlesPage extends StatefulWidget {
  const AdminStarterBundlesPage({super.key, this.embedded = false});

  final bool embedded;

  @override
  State<AdminStarterBundlesPage> createState() => _AdminStarterBundlesPageState();
}

class _AdminStarterBundlesPageState extends State<AdminStarterBundlesPage> {
  late final AppAutoRefreshBinder _autoRefresh;
  bool _loading = true;
  Object? _error;
  List<Map<String, dynamic>> _items = const [];

  @override
  void initState() {
    super.initState();
    _autoRefresh = AppAutoRefreshBinder(
      onTick: () => _reload(silent: true),
      isActive: () => appAutoRefreshIsActive(context),
    )..attach();
    _reload();
  }

  @override
  void dispose() {
    _autoRefresh.dispose();
    super.dispose();
  }

  Future<void> _reload({bool silent = false}) async {
    if (!silent && mounted) {
      setState(() {
        _loading = true;
        _error = null;
      });
    }
    try {
      final items = await adminContext.api.listStarterBundles();
      if (!mounted) return;
      if (silent && appRefreshDataEquals(_items, items) && !_loading) return;
      setState(() {
        _items = items;
        _loading = false;
        _error = null;
      });
    } catch (e) {
      if (!mounted) return;
      if (silent) return;
      setState(() {
        _error = e;
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      body: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (_error != null) AppStatusBanner(severity: AppStatusSeverity.error, message: AppErrors.localize(context, _error!)),
          Expanded(
            child: _loading
                ? Center(child: CircularProgressIndicator())
                : _items.isEmpty
                    ? EmptyPlaceholder(
                        title: l10n.adminNoStarterBundles,
                        subtitle: l10n.adminStarterBundlesHint,
                      )
                    : ListView.separated(
                        padding: const EdgeInsets.all(AppSpacing.md),
                        itemCount: _items.length,
                        separatorBuilder: (_, __) => const Divider(height: 1),
                        itemBuilder: (context, index) {
                          final item = _items[index];
                          final available = item['bundle_available'] == true;
                          return ListTile(
                            leading: Icon(
                              available ? Icons.inventory_2_outlined : Icons.hourglass_empty,
                            ),
                            title: Text(item['name'] as String? ?? item['id'] as String? ?? 'Bundle'),
                            subtitle: Text(item['description'] as String? ?? ''),
                            trailing: Text(available ? l10n.adminShipped : l10n.adminMetadataOnly),
                          );
                        },
                      ),
          ),
        ],
      ),
    );
  }
}
