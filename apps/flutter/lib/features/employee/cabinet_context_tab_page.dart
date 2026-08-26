import 'dart:convert';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';

import 'package:prodavan/core/refresh/app_auto_refresh.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/core/widgets/stat_tile.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/features/employee/cabinet_agents_edit_page.dart';
import 'package:prodavan/features/employee/cabinet_audit_events_page.dart';
import 'package:prodavan/features/employee/cabinet_meta_tabs_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

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
  late final AppAutoRefreshBinder _autoRefresh;
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
      final results = await Future.wait([
        workContext.api.getCabinet(widget.cabinetId),
        workContext.api.listProjects(widget.cabinetId),
        workContext.api.listMetaTables(widget.cabinetId),
        workContext.api.listCabinetMcpTools(widget.cabinetId),
        workContext.api.listAuditEvents(cabinetId: widget.cabinetId, limit: 20),
      ]);
      if (!mounted) return;
      final cabinet = results[0] as Map<String, dynamic>;
      final projects = (results[1] as List).length;
      final tables = (results[2] as List).length;
      final tools = (results[3] as List).length;
      final auditEvents = (results[4] as List).length;
      if (silent &&
          appRefreshDataEquals(_cabinet, cabinet) &&
          _projects == projects &&
          _tables == tables &&
          _tools == tools &&
          _auditEvents == auditEvents &&
          !_loading) {
        return;
      }
      setState(() {
        _cabinet = cabinet;
        _projects = projects;
        _tables = tables;
        _tools = tools;
        _auditEvents = auditEvents;
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

  bool _exporting = false;

  Future<void> _exportBundle() async {
    final l10n = AppLocalizations.of(context);
    setState(() {
      _exporting = true;
      _error = null;
    });
    try {
      final exported = await workContext.api.exportCabinetBundle(widget.cabinetId);
      if (!mounted) return;
      final b64 = exported['zip_base64'] as String? ?? '';
      if (b64.isEmpty) {
        setState(() => _error = l10n.cabinetEmptyBundleExport);
        return;
      }
      final bytes = base64Decode(b64);
      final safeName = widget.cabinetName.replaceAll(RegExp(r'[^\w\-]+'), '_');
      final saved = await FilePicker.platform.saveFile(
        fileName: '$safeName.bundle.zip',
        bytes: bytes,
      );
      if (!mounted) return;
      AppSnackBar.success(
        context,
        saved == null
            ? l10n.cabinetExportReady('${bytes.length}')
            : l10n.cabinetSavedTo(saved),
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
    final l10n = AppLocalizations.of(context);
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    final cab = _cabinet;
    return RefreshIndicator(
      onRefresh: _reload,
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (_error != null) AppStatusBanner(severity: AppStatusSeverity.error, message: _error!),
          Text(widget.cabinetName, style: Theme.of(context).textTheme.titleLarge),
          if (cab != null) ...[
            const SizedBox(height: 8),
            Text(
              l10n.cabinetStatusCompanyLine('${cab['status'] ?? l10n.commonEmDash}', '${cab['company_id'] ?? l10n.commonEmDash}'),
              style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                    color: context.appColors.muted,
                  ),
            ),
          ],
          const SizedBox(height: 16),
          Wrap(
            spacing: 12,
            runSpacing: 12,
            children: [
              SizedBox(width: 140, child: StatTile(label: l10n.commonProjects, value: '$_projects')),
              SizedBox(width: 140, child: StatTile(label: l10n.cabinetMetaTables, value: '$_tables')),
              SizedBox(width: 140, child: StatTile(label: l10n.cabinetMcpTools, value: '$_tools')),
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
                  child: StatTile(label: l10n.cabinetAuditRecent, value: '$_auditEvents'),
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),
          AppButton(
            label: _exporting ? l10n.cabinetExporting : l10n.cabinetExportCabinetBundle,
            expanded: false,
            onPressed: _exporting ? null : _exportBundle,
          ),
          const SizedBox(height: 8),
          AppButton(
            label: l10n.cabinetEditAgentsMd,
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
            label: l10n.cabinetManageCustomTabs,
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
            l10n.cabinetContextHint,
            style: Theme.of(context).textTheme.bodySmall?.copyWith(
                  color: context.appColors.muted,
                ),
          ),
        ],
      ),
    );
  }
}
