import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/danger_confirm_page.dart';
import 'package:prodavan/core/widgets/empty_state.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';
import 'package:prodavan/features/employee/cabinet_meta_tab_create_page.dart';
import 'package:prodavan/features/employee/cabinet_meta_view_edit_page.dart';

/// Manage non-system cabinet tabs (L06 meta views/tabs).
class CabinetMetaTabsPage extends StatefulWidget {
  const CabinetMetaTabsPage({super.key, required this.cabinetId});

  final String cabinetId;

  @override
  State<CabinetMetaTabsPage> createState() => _CabinetMetaTabsPageState();
}

class _CabinetMetaTabsPageState extends State<CabinetMetaTabsPage> {
  bool _loading = true;
  String? _error;
  List<Map<String, dynamic>> _tabs = const [];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final tabs = await workContext.api.listMetaTabs(widget.cabinetId);
      if (!mounted) return;
      setState(() {
        _tabs = tabs.where((t) => t['system'] != true).toList();
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
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
      final views = await workContext.api.listMetaViews(widget.cabinetId);
      final view = views.cast<Map<String, dynamic>?>().firstWhere(
            (v) => v?['slug'] == viewSlug,
            orElse: () => null,
          );
      if (view == null) {
        setState(() => _error = 'View not found');
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
    final tabId = tab['id'] as String?;
    final viewSlug = tab['view_slug'] as String?;
    if (tabId == null) return;

    final ok = await DangerConfirmPage.push(
      context,
      title: 'Delete tab?',
      message: 'Remove tab "${tab['title']}" and its view.',
      confirmLabel: 'Delete',
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
    return AppScaffold(
      title: const Text('Custom tabs'),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(
                padding: const EdgeInsets.all(AppSpacing.lg),
                children: [
                  if (_error != null) InlineErrorBanner(message: _error!),
                  Align(
                    alignment: Alignment.centerRight,
                    child: TextButton.icon(
                      onPressed: _createTab,
                      icon: const Icon(Icons.add),
                      label: const Text('New tab'),
                    ),
                  ),
                  if (_tabs.isEmpty)
                    const EmptyState(title: 'No custom tabs yet.')
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
                    'Custom tabs appear in the cabinet shell after creation. System tabs cannot be removed here.',
                    style: Theme.of(context).textTheme.bodySmall?.copyWith(
                          color: Theme.of(context).colorScheme.onSurfaceVariant,
                        ),
                  ),
                ],
              ),
            ),
    );
  }
}
