import 'package:flutter/material.dart';

import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_path_breadcrumbs.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/features/containers/container_workspace_api.dart';
import 'package:prodavan/features/containers/container_workspace_file_preview_page.dart';
import 'package:prodavan/features/containers/workspace_file_utils.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Live Pod `/workspace` browser — list, preview text, download.
class ContainerWorkspaceFilesPage extends StatefulWidget {
  const ContainerWorkspaceFilesPage({
    super.key,
    required this.title,
    required this.api,
    this.initialPath = '',
  });

  final String title;
  final ContainerWorkspaceApi api;
  final String initialPath;

  @override
  State<ContainerWorkspaceFilesPage> createState() => _ContainerWorkspaceFilesPageState();
}

class _ContainerWorkspaceFilesPageState extends State<ContainerWorkspaceFilesPage> {
  bool _loading = true;
  String _path = '';
  List<Map<String, dynamic>> _entries = const [];
  String? _downloadingPath;

  @override
  void initState() {
    super.initState();
    _path = widget.initialPath;
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final body = await widget.api.listWorkspaceEntries(path: _path);
      if (!mounted) return;
      final raw = body['entries'];
      final entries = raw is List
          ? raw.whereType<Map>().map((e) => Map<String, dynamic>.from(e)).toList()
          : <Map<String, dynamic>>[];
      setState(() {
        _entries = entries;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      AppErrors.showSnack(context, e);
    }
  }

  void _openDir(String name) {
    final next = _path.isEmpty ? name : '$_path/$name';
    setState(() => _path = next);
    _load();
  }

  void _navigateToPath(String target) {
    setState(() => _path = target);
    _load();
  }

  Map<String, dynamic>? _entryFor(String path) {
    for (final entry in _entries) {
      if (entry['path'] == path) return entry;
    }
    return null;
  }

  Future<void> _downloadFile(AppEntityRow row) async {
    if (_downloadingPath != null) return;
    final entry = _entryFor(row.id);
    if (entry == null || entry['kind'] != 'file') return;
    final path = row.id;
    final name = entry['name'] as String? ?? path.split('/').last;
    setState(() => _downloadingPath = path);
    try {
      final bytes = await widget.api.downloadWorkspaceFile(path: path);
      if (!mounted) return;
      final saved = await saveWorkspaceFileBytes(filename: name, bytes: bytes);
      if (!mounted) return;
      if (saved) {
        AppSnackBar.success(context, AppLocalizations.of(context).projectWorkspaceDownloaded);
      }
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    } finally {
      if (mounted) setState(() => _downloadingPath = null);
    }
  }

  void _previewFile(AppEntityRow row) {
    final entry = _entryFor(row.id);
    if (entry == null || entry['kind'] != 'file') return;
    final path = row.id;
    final name = entry['name'] as String? ?? path.split('/').last;
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (context) => ContainerWorkspaceFilePreviewPage(
          title: name,
          path: path,
          api: widget.api,
        ),
      ),
    );
  }

  bool _isTextFile(AppEntityRow row) {
    final entry = _entryFor(row.id);
    if (entry == null || entry['kind'] != 'file') return false;
    final name = (entry['name'] as String? ?? '').toLowerCase();
    const textExt = {
      '.md',
      '.txt',
      '.json',
      '.yaml',
      '.yml',
      '.toml',
      '.csv',
      '.log',
      '.py',
      '.sh',
      '.xml',
      '.html',
      '.css',
      '.js',
      '.ts',
      '.dart',
    };
    for (final ext in textExt) {
      if (name.endsWith(ext)) return true;
    }
    return false;
  }

  bool _isFile(AppEntityRow row) {
    final entry = _entryFor(row.id);
    return entry?['kind'] == 'file';
  }

  List<AppEntityRow> _buildRows(BuildContext context) {
    final theme = Theme.of(context);
    final iconColor = theme.colorScheme.onSurfaceVariant;
    return _entries.map((entry) {
      final kind = entry['kind'] as String? ?? 'file';
      final name = entry['name'] as String? ?? '';
      final path = entry['path'] as String? ?? name;
      final isDir = kind == 'dir';
      final size = entry['size'];
      final sizeLabel = isDir
          ? '—'
          : formatWorkspaceFileSize(size is int ? size : int.tryParse('$size'));

      return AppEntityRow(
        id: path,
        title: name,
        leading: Icon(
          isDir ? Icons.folder_outlined : Icons.insert_drive_file_outlined,
          size: 22,
          color: iconColor,
        ),
        cells: {'size': sizeLabel},
      );
    }).toList();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final theme = Theme.of(context);

    return AppScaffold(
      title: Text(widget.title),
      actions: [
        IconButton(
          icon: const Icon(Icons.refresh),
          onPressed: _loading ? null : _load,
        ),
      ],
      body: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          AppPathBreadcrumbs(
            path: _path,
            onNavigate: _navigateToPath,
          ),
          Expanded(
            child: AppEntityCollection(
              loading: _loading,
              rows: _buildRows(context),
              primaryColumnLabel: l10n.commonName,
              columns: [
                AppEntityColumn(
                  id: 'size',
                  label: l10n.commonSize,
                  width: 72,
                  align: AppEntityColumnAlign.end,
                ),
              ],
              onOpen: (row) {
                final entry = _entryFor(row.id);
                if (entry?['kind'] == 'dir') {
                  _openDir(entry!['name'] as String? ?? row.title);
                }
              },
              rowActions: [
                AppEntityRowAction(
                  icon: Icons.visibility_outlined,
                  tooltip: l10n.projectWorkspacePreview,
                  visible: _isTextFile,
                  onPressed: (row) async => _previewFile(row),
                ),
                AppEntityRowAction(
                  icon: Icons.download_outlined,
                  tooltip: l10n.projectWorkspaceDownload,
                  visible: _isFile,
                  iconBuilder: (context, row) {
                    if (_downloadingPath == row.id) {
                      return SizedBox(
                        width: 20,
                        height: 20,
                        child: CircularProgressIndicator(
                          strokeWidth: 2,
                          color: theme.colorScheme.onSurface,
                        ),
                      );
                    }
                    return Icon(
                      Icons.download_outlined,
                      size: 20,
                      color: theme.colorScheme.onSurface,
                    );
                  },
                  onPressed: _downloadFile,
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
