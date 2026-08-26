import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_nav_preference.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/meta/interpreters/collection_interpreter.dart';
import 'package:prodavan/features/meta/interpreters/form_interpreter.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/preview/preview_stub.dart';
import 'package:prodavan/l10n/app_localizations.dart';

class HubViewInterpreter extends StatelessWidget {
  const HubViewInterpreter({
    super.key,
    required this.manifest,
    required this.view,
    required this.onOpenView,
  });

  final ModuleMetaManifest manifest;
  final Map<String, dynamic> view;
  final void Function(String viewSlug) onOpenView;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final ui = view['ui_json'];
    if (ui is! Map) {
      return EmptyPlaceholder(title: l10n.adminMetaInvalid);
    }
    final items = ui['items'];
    if (items is! List || items.isEmpty) {
      return EmptyPlaceholder(title: l10n.commonEmpty);
    }

    return ListView(
      padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
      children: [
        for (final item in items.whereType<Map>())
          AppNavPreference(
            title: item['title'] as String? ?? '—',
            icon: _icon(item['icon'] as String?) ?? Icons.chevron_right,
            onTap: () {
              final target = item['target'];
              if (target is Map && target['kind'] == 'view') {
                final viewSlug = target['view'] as String?;
                if (viewSlug != null) {
                  onOpenView(viewSlug);
                  return;
                }
              }
              PreviewStub.run(context, item['title'] as String? ?? '—');
            },
          ),
      ],
    );
  }

  IconData? _icon(String? name) {
    switch (name) {
      case 'list':
        return Icons.list;
      case 'settings':
        return Icons.settings_outlined;
      default:
        return Icons.chevron_right;
    }
  }
}

/// Resolves view kind → interpreter widget.
class ViewInterpreterHost extends StatelessWidget {
  const ViewInterpreterHost({
    super.key,
    required this.manifest,
    required this.view,
    required this.onOpenView,
  });

  final ModuleMetaManifest manifest;
  final Map<String, dynamic> view;
  final void Function(String viewSlug) onOpenView;

  @override
  Widget build(BuildContext context) {
    final ui = view['ui_json'];
    final kind = ui is Map
        ? ui['kind'] as String? ?? view['kind'] as String?
        : view['kind'] as String?;

    switch (kind) {
      case 'collection':
        return CollectionViewInterpreter(
          manifest: manifest,
          view: view,
          onOpenForm: onOpenView,
        );
      case 'form':
      case 'detail':
        return FormViewInterpreter(manifest: manifest, view: view);
      case 'hub':
        return HubViewInterpreter(
          manifest: manifest,
          view: view,
          onOpenView: onOpenView,
        );
      default:
        return EmptyPlaceholder(title: AppLocalizations.of(context).adminMetaInvalid);
    }
  }
}
