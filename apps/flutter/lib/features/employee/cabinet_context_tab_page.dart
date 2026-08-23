import 'dart:convert';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';
import 'package:prodavan/core/widgets/stat_tile.dart';
import 'package:prodavan/features/employee/cabinet_agents_edit_page.dart';
import 'package:prodavan/features/employee/cabinet_audit_events_page.dart';
import 'package:prodavan/features/employee/cabinet_meta_tabs_page.dart';

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
  int _auditEvents = 0;

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
        workContext.api.listAuditEvents(cabinetId: widget.cabinetId, limit: 20),
      ]);
      if (!mounted) return;
      setState(() {
        _cabinet = results[0] as Map<String, dynamic>;
        _projects = (results[1] as List).length;
        _tables = (results[2] as List).length;
        _tools = (results[3] as List).length;
        _auditEvents = (results[4] as List).length;
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
      if (b64.isEmpty) {
        setState(() => _error = 'Empty bundle export');
        return;
      }
      final bytes = base64Decode(b64);
      final safeName = widget.cabinetName.replaceAll(RegExp(r'[^\w\-]+'), '_');
      final saved = await FilePicker.platform.saveFile(
        fileName: '$safeName.bundle.zip',
        bytes: bytes,
      );
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(saved == null ? 'Export ready (${bytes.length} bytes)' : 'Saved to $saved'),
        ),
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
              SizedBox(
                width: 140,
                child: InkWell(
                  onTap: () {
                    Navigator.of(context).push(
                      MaterialPageRoute<void>(
                        builder: (_) => CabinetAuditEventsPage(cabinetId: widget.cabinetId),
                      ),
                    );
                  },
                  child: StatTile(label: 'Audit (recent)', value: '$_auditEvents'),
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),
          AppButton(
            label: _exporting ? 'Exporting…' : 'Export cabinet bundle',
            expanded: false,
            onPressed: _exporting ? null : _exportBundle,
          ),
          const SizedBox(height: 8),
          AppButton(
            label: 'Edit AGENTS.md',
            expanded: false,
            onPressed: () {
              Navigator.of(context).push(
                MaterialPageRoute<void>(
                  builder: (_) => CabinetAgentsEditPage(cabinetId: widget.cabinetId),
                ),
              );
            },
          ),
          const SizedBox(height: 8),
          AppButton(
            label: 'Manage custom tabs',
            expanded: false,
            onPressed: () {
              Navigator.of(context).push(
                MaterialPageRoute<void>(
                  builder: (_) => CabinetMetaTabsPage(cabinetId: widget.cabinetId),
                ),
              );
            },
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
