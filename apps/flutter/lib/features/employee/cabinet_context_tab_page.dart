import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';
import 'package:prodavan/core/widgets/stat_tile.dart';

/// Cabinet context summary — projects, meta tables, MCP tools (L05/L06).
class CabinetContextTabPage extends StatefulWidget {
  const CabinetContextTabPage({
    super.key,
    required this.cabinetId,
    required this.cabinetName,
  });

  final String cabinetId;
  final String cabinetName;

  @override
  State<CabinetContextTabPage> createState() => _CabinetContextTabPageState();
}

class _CabinetContextTabPageState extends State<CabinetContextTabPage> {
  bool _loading = true;
  String? _error;
  Map<String, dynamic>? _cabinet;
  int _projects = 0;
  int _tables = 0;
  int _tools = 0;

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
      final results = await Future.wait([
        workContext.api.getCabinet(widget.cabinetId),
        workContext.api.listProjects(widget.cabinetId),
        workContext.api.listMetaTables(widget.cabinetId),
        workContext.api.listCabinetMcpTools(widget.cabinetId),
      ]);
      if (!mounted) return;
      setState(() {
        _cabinet = results[0] as Map<String, dynamic>;
        _projects = (results[1] as List).length;
        _tables = (results[2] as List).length;
        _tools = (results[3] as List).length;
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

  bool _exporting = false;

  Future<void> _exportBundle() async {
    setState(() {
      _exporting = true;
      _error = null;
    });
    try {
      final exported = await workContext.api.exportCabinetBundle(widget.cabinetId);
      if (!mounted) return;
      final b64 = exported['zip_base64'] as String? ?? '';
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Bundle exported (${b64.length} base64 chars)')),
      );
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _exporting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    final cab = _cabinet;
    return RefreshIndicator(
      onRefresh: _reload,
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (_error != null) InlineErrorBanner(message: _error!),
          Text(widget.cabinetName, style: Theme.of(context).textTheme.titleLarge),
          if (cab != null) ...[
            const SizedBox(height: 8),
            Text(
              'Status: ${cab['status'] ?? '—'} · Company ${cab['company_id'] ?? '—'}',
              style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                    color: Theme.of(context).colorScheme.onSurfaceVariant,
                  ),
            ),
          ],
          const SizedBox(height: 16),
          Wrap(
            spacing: 12,
            runSpacing: 12,
            children: [
              SizedBox(width: 140, child: StatTile(label: 'Projects', value: '$_projects')),
              SizedBox(width: 140, child: StatTile(label: 'Meta tables', value: '$_tables')),
              SizedBox(width: 140, child: StatTile(label: 'MCP tools', value: '$_tools')),
            ],
          ),
          const SizedBox(height: 16),
          AppButton(
            label: _exporting ? 'Exporting…' : 'Export cabinet bundle',
            expanded: false,
            onPressed: _exporting ? null : _exportBundle,
          ),
          const SizedBox(height: 16),
          Text(
            'Use the Projects tab to open an agent workspace. Tables and Tools tabs expose cabinet runtime data.',
            style: Theme.of(context).textTheme.bodySmall?.copyWith(
                  color: Theme.of(context).colorScheme.onSurfaceVariant,
                ),
          ),
        ],
      ),
    );
  }
}
