import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';

import 'package:prodavan/core/refresh/app_auto_refresh.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_confirm_page.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/features/employee/cabinet_meta_tab_create_page.dart';
import 'package:prodavan/features/employee/cabinet_meta_view_edit_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Manage non-system cabinet tabs (L06 meta views/tabs).
class CabinetMetaTabsPage extends StatefulWidget {
  const CabinetMetaTabsPage({super.key, required this.cabinetId});

  final String cabinetId;

  @override
  State<CabinetMetaTabsPage> createState() => _CabinetMetaTabsPageState();
}

class _CabinetMetaTabsPageState extends State<CabinetMetaTabsPage> {
  late final AppAutoRefreshBinder _autoRefresh;
  bool _loading = true;
  String? _error;
  List<Map<String, dynamic>> _tabs = const [];

  @override
  void initState() {
    super.initState();
    _autoRefresh = AppAutoRefreshBinder(
      onTick: () => _load(silent: true),
      isActive: () => appAutoRefreshIsActive(context),
    )..attach();
    _load();
  }

  @override
  void dispose() {
    _autoRefresh.dispose();
    super.dispose();
  }

  Future<void> _load({bool silent = false}) async {
    if (!silent && mounted) {
      setState(() {
        _loading = true;
        _error = null;
      });
    }
    try {
      final tabs = await workContext.api.listMetaTabs(widget.cabinetId);
      if (!mounted) return;
      final filtered = tabs.where((t) => t['system'] != true).toList();
      if (silent && appRefreshDataEquals(_tabs, filtered) && !_loading) return;
      setState(() {
        _tabs = filtered;
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

  Future<void> _createTab() async {
    final created = await Navigator.of(context).push<bool>(
      MaterialPageRoute<bool>(
        builder: (_) => CabinetMetaTabCreatePage(cabinetId: widget.cabinetId),
      ),
    );
    if (created == true) {
      workContext.notifyCabinetMetaChanged();
      await _load();
    }
  }

  Future<void> _editView(Map<String, dynamic> tab) async {
    final viewSlug = tab['view_slug'] as String?;
    if (viewSlug == null || viewSlug.isEmpty) return;

    try {
      final l10n = AppLocalizations.of(context);
      final views = await workContext.api.listMetaViews(widget.cabinetId);
      final view = views.cast<Map<String, dynamic>?>().firstWhere(
            (v) => v?['slug'] == viewSlug,
            orElse: () => null,
          );
      if (view == null) {
        setState(() => _error = l10n.cabinetViewNotFound);
        return;
      }
      final ui = view['ui_json'];
      if (ui is! Map<String, dynamic>) return;

      if (!mounted) return;
      final saved = await Navigator.of(context).push<bool>(
        MaterialPageRoute<bool>(
          builder: (_) => CabinetMetaViewEditPage(
            cabinetId: widget.cabinetId,
            viewSlug: viewSlug,
            uiJson: ui,
          ),
        ),
      );
      if (saved == true) await _load();
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e.toString());
    }
  }

  Future<void> _deleteTab(Map<String, dynamic> tab) async {
    final l10n = AppLocalizations.of(context);
    final tabId = tab['id'] as String?;
    final viewSlug = tab['view_slug'] as String?;
    if (tabId == null) return;

    final ok = await AppConfirmPage.push(
      context,
      title: l10n.cabinetDeleteTab,
      message: l10n.cabinetRemoveTabAndView('${tab['title']}'),
      confirmLabel: l10n.commonDelete,
      severity: AppStatusSeverity.error,
    );
    if (ok != true) return;

    try {
      await workContext.api.deleteMetaTab(cabinetId: widget.cabinetId, tabId: tabId);
      if (viewSlug != null && viewSlug.isNotEmpty) {
        try {
          await workContext.api.deleteMetaView(cabinetId: widget.cabinetId, viewSlug: viewSlug);
        } catch (_) {
          // view may still be referenced elsewhere
        }
      }
      workContext.notifyCabinetMetaChanged();
      await _load();
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e.toString());
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      title: Text(l10n.cabinetCustomTabs),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(
                padding: const EdgeInsets.all(AppSpacing.lg),
                children: [
                  if (_error != null) AppStatusBanner(severity: AppStatusSeverity.error, message: _error!),
                  Align(
                    alignment: Alignment.centerRight,
                    child: TextButton.icon(
                      onPressed: _createTab,
                      icon: Icon(Icons.add),
                      label: Text(l10n.cabinetNewTab),
                    ),
                  ),
                  if (_tabs.isEmpty)
                    EmptyPlaceholder(
                      title: l10n.cabinetNoCustomTabsYet,
                      fillViewport: false,
                    )
                  else
                    ..._tabs.map(
                      (tab) => Card(
                        child: ListTile(
                          title: Text(tab['title'] as String? ?? 'Tab'),
                          subtitle: Text(
                            'view: ${tab['view_slug'] ?? '—'} · table: ${tab['table_slug'] ?? '—'}',
                          ),
                          onTap: () => _editView(tab),
                          trailing: IconButton(
                            icon: const Icon(Icons.delete_outline),
                            onPressed: () => _deleteTab(tab),
                          ),
                        ),
                      ),
                    ),
                  const SizedBox(height: AppSpacing.md),
                  Text(
                    l10n.cabinetCustomTabsHint,
                    style: Theme.of(context).textTheme.bodySmall?.copyWith(
                          color: context.appColors.muted,
                        ),
                  ),
                ],
              ),
            ),
    );
  }
}
