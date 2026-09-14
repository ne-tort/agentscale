import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_nav_preference.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/meta/interpreters/collection_interpreter.dart';
import 'package:prodavan/features/meta/interpreters/form_interpreter.dart';
import 'package:prodavan/features/meta/interpreters/profile_hub_interpreter.dart';
import 'package:prodavan/features/meta/meta_icon.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/preview/preview_stub.dart';
import 'package:prodavan/features/meta/runtime/chat_scope.dart';
import 'package:prodavan/features/meta/runtime/module_runtime_scope.dart';
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
  final void Function(String viewSlug, {String? rowId}) onOpenView;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final ui = view['ui_json'];
    if (ui is! Map) {
      return EmptyPlaceholder(title: l10n.adminMetaInvalid);
    }
    final items = ui['items'];
    if (items is! List || items.isEmpty) {
      return EmptyPlaceholder(
        title: l10n.cabinetNoRows,
        icon: Icons.list_alt_outlined,
      );
    }

    final sessionId = ModuleRuntimeScope.maybeOf(context)?.sessionId;
    final visible = items.whereType<Map>().where((item) {
      final map = Map<String, dynamic>.from(item);
      return scopeVisibleForSession(map, sessionId);
    }).toList();
    if (visible.isEmpty) {
      return EmptyPlaceholder(
        title: l10n.cabinetNoRows,
        icon: Icons.list_alt_outlined,
      );
    }

    return ListView(
      padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
      children: [
        for (final item in visible)
          AppNavPreference(
            title: item['title'] as String? ?? '—',
            icon: metaIconFromName(item['icon'] as String?, fallback: Icons.chevron_right),
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
}

/// Resolves view kind → interpreter widget.
class ViewInterpreterHost extends StatelessWidget {
  const ViewInterpreterHost({
    super.key,
    required this.manifest,
    required this.view,
    required this.seeds,
    required this.onOpenView,
    this.rowId,
    this.readOnly = false,
  });

  final ModuleMetaManifest manifest;
  final Map<String, dynamic> view;
  final dynamic seeds;
  final void Function(String viewSlug, {String? rowId}) onOpenView;
  final String? rowId;
  final bool readOnly;

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
          seeds: seeds,
          onOpenForm: onOpenView,
          readOnly: readOnly,
          contextRowId: rowId,
        );
      case 'form':
      case 'detail':
        return FormViewInterpreter(
          manifest: manifest,
          view: view,
          seeds: seeds,
          rowId: rowId,
          readOnly: readOnly,
          onOpenView: onOpenView,
        );
      case 'hub':
        return HubViewInterpreter(
          manifest: manifest,
          view: view,
          onOpenView: onOpenView,
        );
      case 'profile_hub':
        return ProfileHubInterpreter(
          manifest: manifest,
          view: view,
          seeds: seeds,
          onOpenView: onOpenView,
          readOnly: readOnly,
          contextProfileId: rowId,
        );
      default:
        return EmptyPlaceholder(title: AppLocalizations.of(context).adminMetaInvalid);
    }
  }
}
