import 'package:flutter/material.dart';

import 'package:prodavan/core/refresh/app_auto_refresh.dart';
import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/widgets/app_collection_view_mode.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Org cabinets list — metadata only, read-mostly (L04).
class CompanyCabinetsPage extends StatefulWidget {
  const CompanyCabinetsPage({super.key, required this.companyId});

  final String companyId;

  @override
  State<CompanyCabinetsPage> createState() => _CompanyCabinetsPageState();
}

class _CompanyCabinetsPageState extends State<CompanyCabinetsPage> {
  static const _viewPageKey = 'company.cabinets';

  final _viewMode = AppCollectionViewModeStore(_viewPageKey);
  late final AppAutoRefreshBinder _autoRefresh;
  bool _loading = true;
  String? _error;
  List<Map<String, dynamic>> _cabinets = const [];

  @override
  void initState() {
    super.initState();
    _autoRefresh = AppAutoRefreshBinder(
      onTick: () => _reload(silent: true),
      isActive: () => appAutoRefreshIsActive(context),
    )..attach();
    _viewMode.load();
    _reload();
  }

  @override
  void dispose() {
    _autoRefresh.dispose();
    _viewMode.dispose();
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
      final items = await companyContext.api.listOrgCabinets(widget.companyId);
      if (!mounted) return;
      if (silent && appRefreshDataEquals(_cabinets, items) && !_loading) return;
      setState(() {
        _cabinets = items;
        _loading = false;
        _error = null;
      });
    } catch (e) {
      if (!mounted) return;
      if (silent) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final rows = _cabinets
        .map(
          (c) => AppEntityRow(
            id: c['id'] as String,
            title: c['name'] as String? ?? c['id'] as String,
            subtitle: c['owner_email'] as String?,
            cells: {
              'owner': c['owner_email'] as String? ?? '—',
              'status': c['status'] as String? ?? '—',
            },
          ),
        )
        .toList();

    return ListenableBuilder(
      listenable: _viewMode,
      builder: (context, _) {
        return AppScaffold(
          actions: [
            AppCollectionViewModeButton(store: _viewMode),
          ],
          body: Column(
            children: [
              if (_error != null) InlineErrorBanner(message: _error!),
              Expanded(
                child: AppEntityCollection(
                  loading: _loading,
                  mode: _viewMode.resolve(context),
                  rows: rows,
                  primaryColumnLabel: l10n.companyCabinet,
                  columns: [
                    AppEntityColumn(id: 'owner', label: l10n.companyOwner),
                    AppEntityColumn(id: 'status', label: l10n.commonStatus, width: 96),
                  ],
                  onOpen: (row) {
                    ScaffoldMessenger.of(context).showSnackBar(
                      SnackBar(content: Text(l10n.companyReadOnlyOrgView(row.title))),
                    );
                  },
                  empty: EmptyPlaceholder(
                    title: l10n.companyNoCabinets,
                    subtitle: l10n.companyCabinetsEmptyHint,
                  ),
                ),
              ),
            ],
          ),
        );
      },
    );
  }
}
