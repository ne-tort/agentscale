import 'package:flutter/material.dart';

import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_state.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';

/// Read-only official starter bundle catalog (L04).
class AdminStarterBundlesPage extends StatefulWidget {
  const AdminStarterBundlesPage({super.key, this.embedded = false});

  final bool embedded;

  @override
  State<AdminStarterBundlesPage> createState() => _AdminStarterBundlesPageState();
}

class _AdminStarterBundlesPageState extends State<AdminStarterBundlesPage> {
  bool _loading = true;
  String? _error;
  List<Map<String, dynamic>> _items = const [];

  @override
  void initState() {
    super.initState();
    _reload();
  }

  Future<void> _reload() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final items = await adminContext.api.listStarterBundles();
      if (!mounted) return;
      setState(() {
        _items = items;
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

  @override
  Widget build(BuildContext context) {
    return AppScaffold(
      title: const Text('Starter bundles'),
      actions: [
        IconButton(onPressed: _loading ? null : _reload, icon: const Icon(Icons.refresh)),
      ],
      body: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (_error != null) InlineErrorBanner(message: _error!),
          Expanded(
            child: _loading
                ? const Center(child: CircularProgressIndicator())
                : _items.isEmpty
                    ? const EmptyState(
                        title: 'No starter bundles',
                        subtitle: 'Catalog entries appear when shipped under data/starter_bundles/',
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
                            trailing: Text(available ? 'shipped' : 'metadata only'),
                          );
                        },
                      ),
          ),
        ],
      ),
    );
  }
}
