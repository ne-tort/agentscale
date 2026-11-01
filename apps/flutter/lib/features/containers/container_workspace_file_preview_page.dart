import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/features/containers/container_workspace_api.dart';
import 'package:prodavan/features/containers/workspace_download_error.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Text preview for a workspace file in live Pod.
class ContainerWorkspaceFilePreviewPage extends StatefulWidget {
  const ContainerWorkspaceFilePreviewPage({
    super.key,
    required this.title,
    required this.path,
    required this.api,
  });

  final String title;
  final String path;
  final ContainerWorkspaceApi api;

  @override
  State<ContainerWorkspaceFilePreviewPage> createState() =>
      _ContainerWorkspaceFilePreviewPageState();
}

class _ContainerWorkspaceFilePreviewPageState extends State<ContainerWorkspaceFilePreviewPage> {
  bool _loading = true;
  String? _content;
  bool _truncated = false;
  bool _downloading = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final body = await widget.api.previewWorkspaceFile(path: widget.path);
      if (!mounted) return;
      setState(() {
        _content = body['content'] as String? ?? '';
        _truncated = body['truncated'] == true;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      AppErrors.showSnack(context, e);
      Navigator.of(context).maybePop();
    }
  }

  Future<void> _download() async {
    if (_downloading) return;
    setState(() => _downloading = true);
    try {
      final download = await widget.api.downloadWorkspaceFile(path: widget.path);
      if (!mounted) return;
      final saved = await saveWorkspaceFileBytes(
        filename: workspaceDownloadFilename(download, widget.title),
        bytes: download.bytes,
      );
      if (!mounted) return;
      if (saved) {
        AppSnackBar.success(context, AppLocalizations.of(context).projectWorkspaceDownloaded);
      }
    } catch (e) {
      if (!mounted) return;
      if (workspaceDownloadLimit(e) != null) {
        final l10n = AppLocalizations.of(context);
        AppSnackBar.warning(
          context,
          AppErrors.present(e, l10n).display,
          copyOnTap: true,
        );
      } else {
        AppErrors.showSnack(context, e);
      }
    } finally {
      if (mounted) setState(() => _downloading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);

    return AppScaffold(
      title: Text(widget.title),
      actions: [
        IconButton(
          icon: _downloading
              ? const SizedBox(
                  width: 20,
                  height: 20,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : const Icon(Icons.download_outlined),
          tooltip: l10n.projectWorkspaceDownload,
          onPressed: _downloading ? null : _download,
        ),
      ],
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : Padding(
              padding: const EdgeInsets.all(AppSpacing.md),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  if (_truncated)
                    Padding(
                      padding: const EdgeInsets.only(bottom: AppSpacing.sm),
                      child: Material(
                        color: Theme.of(context).colorScheme.errorContainer,
                        borderRadius: BorderRadius.circular(6),
                        child: Padding(
                          padding: const EdgeInsets.symmetric(
                            horizontal: AppSpacing.sm,
                            vertical: AppSpacing.xs,
                          ),
                          child: Row(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Icon(
                                Icons.warning_amber_outlined,
                                size: 18,
                                color: Theme.of(context).colorScheme.onErrorContainer,
                              ),
                              const SizedBox(width: AppSpacing.xs),
                              Expanded(
                                child: Text(
                                  l10n.projectPreviewTruncated,
                                  style: Theme.of(context).textTheme.bodySmall?.copyWith(
                                        color: Theme.of(context).colorScheme.onErrorContainer,
                                        fontWeight: FontWeight.w600,
                                      ),
                                ),
                              ),
                            ],
                          ),
                        ),
                      ),
                    ),
                  Expanded(
                    child: SingleChildScrollView(
                      child: SelectableText(
                        _content ?? '',
                        style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                              fontFamily: 'monospace',
                            ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
    );
  }
}
