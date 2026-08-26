import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/meta/interpreters/hub_interpreter.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/l10n/app_localizations.dart';

class ModuleMetaPreviewPage extends StatefulWidget {
  const ModuleMetaPreviewPage({
    super.key,
    required this.manifest,
    required this.moduleName,
  });

  final ModuleMetaManifest manifest;
  final String moduleName;

  @override
  State<ModuleMetaPreviewPage> createState() => _ModuleMetaPreviewPageState();
}

class _ModuleMetaPreviewPageState extends State<ModuleMetaPreviewPage>
    with SingleTickerProviderStateMixin {
  TabController? _tabController;
  List<Map<String, dynamic>> _tabs = const [];

  @override
  void initState() {
    super.initState();
    _initTabs();
  }

  void _initTabs() {
    _tabs = widget.manifest.enabledTabs();
    if (_tabs.isNotEmpty) {
      _tabController = TabController(length: _tabs.length, vsync: this);
    }
  }

  @override
  void dispose() {
    _tabController?.dispose();
    super.dispose();
  }

  void _openView(String viewSlug) {
    final view = widget.manifest.viewBySlug(viewSlug);
    if (view == null) return;
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => AppScaffold(
          title: Text(view['label'] as String? ?? viewSlug),
          body: ViewInterpreterHost(
            manifest: widget.manifest,
            view: view,
            onOpenView: _openView,
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final tokens = Theme.of(context).extension<AppColorTokens>()!;

    if (_tabs.isEmpty) {
      return AppScaffold(
        title: _title(l10n, tokens),
        body: EmptyPlaceholder(title: l10n.commonEmpty),
      );
    }

    return AppScaffold(
      title: _title(l10n, tokens),
      body: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Material(
            color: Theme.of(context).colorScheme.surfaceContainerLow,
            child: TabBar(
              controller: _tabController,
              isScrollable: true,
              tabs: [
                for (final tab in _tabs)
                  Tab(text: tab['title'] as String? ?? '—'),
              ],
            ),
          ),
          Expanded(
            child: TabBarView(
              controller: _tabController,
              children: [
                for (final tab in _tabs) _tabBody(tab, l10n),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _title(AppLocalizations l10n, AppColorTokens tokens) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Flexible(child: Text(widget.moduleName)),
        const SizedBox(width: AppSpacing.sm),
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
          decoration: BoxDecoration(
            color: tokens.warning.withValues(alpha: 0.15),
            borderRadius: BorderRadius.circular(4),
          ),
          child: Text(
            l10n.adminModulePreviewMode,
            style: TextStyle(
              fontSize: 12,
              color: tokens.warning,
              fontWeight: FontWeight.w600,
            ),
          ),
        ),
      ],
    );
  }

  Widget _tabBody(Map<String, dynamic> tab, AppLocalizations l10n) {
    final viewSlug = tab['view_slug'] as String?;
    if (viewSlug == null) {
      return EmptyPlaceholder(title: l10n.adminMetaInvalid);
    }
    final view = widget.manifest.viewBySlug(viewSlug);
    if (view == null) {
      return EmptyPlaceholder(title: l10n.adminMetaInvalid);
    }
    return ViewInterpreterHost(
      manifest: widget.manifest,
      view: view,
      onOpenView: _openView,
    );
  }
}
