import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
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
  String? _selectedPath;
  bool _downloading = false;

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
        if (_selectedPath != null &&
            !entries.any((e) => e['path'] == _selectedPath)) {
          _selectedPath = null;
        }
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      AppErrors.showSnack(context, e);
    }
  }

  void _openDir(String name) {
    final next = _path.isEmpty ? name : '$_path/$name';
    setState(() {
      _path = next;
      _selectedPath = null;
    });
    _load();
  }

  void _navigateToPath(String target) {
    setState(() {
      _path = target;
      _selectedPath = null;
    });
    _load();
  }

  List<String> get _segments {
    if (_path.isEmpty) return const [];
    return _path.split('/');
  }

  Future<void> _downloadSelected() async {
    final path = _selectedPath;
    if (path == null || _downloading) return;
    final entry = _entries.firstWhere((e) => e['path'] == path);
    final name = entry['name'] as String? ?? path.split('/').last;
    setState(() => _downloading = true);
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
      if (mounted) setState(() => _downloading = false);
    }
  }

  void _previewSelected() {
    final path = _selectedPath;
    if (path == null) return;
    final entry = _entries.firstWhere((e) => e['path'] == path);
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

  bool _isTextFile(Map<String, dynamic> entry) {
    if (entry['kind'] != 'file') return false;
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

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final theme = Theme.of(context);
    Map<String, dynamic>? selected;
    if (_selectedPath != null) {
      for (final entry in _entries) {
        if (entry['path'] == _selectedPath) {
          selected = entry;
          break;
        }
      }
    }

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
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(
              horizontal: AppSpacing.md,
              vertical: AppSpacing.sm,
            ),
            child: Row(
              children: [
                _BreadcrumbChip(
                  label: '/',
                  selected: _path.isEmpty,
                  onTap: () => _navigateToPath(''),
                ),
                for (var i = 0; i < _segments.length; i++) ...[
                  const Icon(Icons.chevron_right, size: 18),
                  _BreadcrumbChip(
                    label: _segments[i],
                    selected: i == _segments.length - 1,
                    onTap: () => _navigateToPath(_segments.sublist(0, i + 1).join('/')),
                  ),
                ],
              ],
            ),
          ),
          Expanded(
            child: _loading
                ? const Center(child: CircularProgressIndicator())
                : Row(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Expanded(
                        child: ListView.separated(
                          itemCount: _entries.length,
                          separatorBuilder: (_, _) => const Divider(height: 1),
                          itemBuilder: (context, index) {
                            final entry = _entries[index];
                            final kind = entry['kind'] as String? ?? 'file';
                            final name = entry['name'] as String? ?? '';
                            final path = entry['path'] as String? ?? name;
                            final isDir = kind == 'dir';
                            final isSelected = _selectedPath == path;
                            final size = entry['size'];
                            final sizeLabel = isDir
                                ? '—'
                                : formatWorkspaceFileSize(size is int ? size : int.tryParse('$size'));

                            return Material(
                              color: isSelected
                                  ? theme.colorScheme.primaryContainer.withValues(alpha: 0.35)
                                  : null,
                              child: InkWell(
                                onTap: () {
                                  if (isDir) {
                                    _openDir(name);
                                  }
                                },
                                onLongPress: isDir
                                    ? null
                                    : () => setState(() => _selectedPath = path),
                                child: Padding(
                                  padding: const EdgeInsets.symmetric(
                                    horizontal: AppSpacing.md,
                                    vertical: AppSpacing.sm,
                                  ),
                                  child: Row(
                                    children: [
                                      Icon(
                                        isDir ? Icons.folder_outlined : Icons.insert_drive_file_outlined,
                                        size: 22,
                                        color: theme.colorScheme.onSurfaceVariant,
                                      ),
                                      const SizedBox(width: AppSpacing.sm),
                                      Expanded(
                                        child: Text(
                                          name,
                                          overflow: TextOverflow.ellipsis,
                                        ),
                                      ),
                                      const SizedBox(width: AppSpacing.sm),
                                      SizedBox(
                                        width: 72,
                                        child: Text(
                                          sizeLabel,
                                          textAlign: TextAlign.end,
                                          style: theme.textTheme.bodySmall,
                                        ),
                                      ),
                                    ],
                                  ),
                                ),
                              ),
                            );
                          },
                        ),
                      ),
                      if (selected != null && selected['kind'] == 'file')
                        Container(
                          width: 56,
                          decoration: BoxDecoration(
                            border: Border(
                              left: BorderSide(color: theme.dividerColor),
                            ),
                          ),
                          child: Column(
                            mainAxisAlignment: MainAxisAlignment.center,
                            children: [
                              if (_isTextFile(selected))
                                IconButton(
                                  tooltip: l10n.projectWorkspacePreview,
                                  icon: const Icon(Icons.visibility_outlined),
                                  onPressed: _previewSelected,
                                ),
                              IconButton(
                                tooltip: l10n.projectWorkspaceDownload,
                                icon: _downloading
                                    ? const SizedBox(
                                        width: 20,
                                        height: 20,
                                        child: CircularProgressIndicator(strokeWidth: 2),
                                      )
                                    : const Icon(Icons.download_outlined),
                                onPressed: _downloading ? null : _downloadSelected,
                              ),
                            ],
                          ),
                        ),
                    ],
                  ),
          ),
        ],
      ),
    );
  }
}

class _BreadcrumbChip extends StatelessWidget {
  const _BreadcrumbChip({
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return ActionChip(
      label: Text(label),
      onPressed: onTap,
      backgroundColor: selected ? theme.colorScheme.primaryContainer : null,
    );
  }
}
