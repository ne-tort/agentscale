import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_nav_preference.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/meta/meta_icon.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Profile selector (radio) + navigation to prompt blocks.
class ProfileHubInterpreter extends StatelessWidget {
  const ProfileHubInterpreter({
    super.key,
    required this.manifest,
    required this.view,
    required this.seeds,
    required this.onOpenView,
    this.readOnly = false,
  });

  final ModuleMetaManifest manifest;
  final Map<String, dynamic> view;
  final dynamic seeds;
  final void Function(String viewSlug, {String? rowId}) onOpenView;
  final bool readOnly;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final ui = view['ui_json'];
    if (ui is! Map) {
      return EmptyPlaceholder(title: l10n.adminMetaInvalid);
    }
    final profileTable = ui['profile_table'] as String? ?? 'prompt_profiles';
    final settingsTable = ui['settings_table'] as String? ?? 'profile_settings';
    final profileIdField = ui['profile_id_field'] as String? ?? 'profile_id';
    final activeField = ui['active_field'] as String? ?? 'active';
    final blocks = ui['blocks'];
    if (blocks is! List) {
      return EmptyPlaceholder(title: l10n.adminMetaInvalid);
    }

    final profiles = seeds.itemsForTable(profileTable);
    if (profiles.isEmpty) {
      return EmptyPlaceholder(title: l10n.adminModuleSeedEmpty);
    }

    String? activeProfileId;
    for (final s in seeds.itemsForTable(settingsTable)) {
      final body = s['body'];
      if (body is Map && body[activeField] == true) {
        activeProfileId = body[profileIdField]?.toString();
        break;
      }
    }
    activeProfileId ??= _profileIdFromRow(profiles.first);

    return ListView(
      padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
      children: [
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
          child: Text(
            l10n.adminPromptProfiles,
            style: Theme.of(context).textTheme.titleMedium,
          ),
        ),
        for (final p in profiles)
          RadioListTile<String>(
            value: _profileIdFromRow(p) ?? '',
            groupValue: activeProfileId ?? '',
            title: Text(_profileName(p)),
            onChanged: readOnly
                ? null
                : (v) {
                    if (v == null) return;
                    _setActiveProfile(
                      settingsTable: settingsTable,
                      profileIdField: profileIdField,
                      activeField: activeField,
                      profileId: v,
                    );
                  },
          ),
        const Divider(),
        for (final item in blocks.whereType<Map>())
          AppNavPreference(
            title: item['title'] as String? ?? '—',
            icon: metaIconFromName(item['icon'] as String?, fallback: Icons.chevron_right),
            onTap: () {
              final target = item['target'];
              if (target is Map && target['kind'] == 'view') {
                final viewSlug = target['view'] as String?;
                if (viewSlug != null) {
                  onOpenView(viewSlug, rowId: activeProfileId);
                }
              }
            },
          ),
      ],
    );
  }

  String? _profileIdFromRow(Map<String, dynamic> item) {
    return item['row_id'] as String?;
  }

  String _profileName(Map<String, dynamic> item) {
    final body = item['body'];
    if (body is Map && body['name'] != null) {
      return body['name'].toString();
    }
    return item['row_id'] as String? ?? '—';
  }

  void _setActiveProfile({
    required String settingsTable,
    required String profileIdField,
    required String activeField,
    required String profileId,
  }) {
    final settings = seeds.itemsForTable(settingsTable);
    for (final s in settings) {
      final rowId = s['row_id'] as String?;
      if (rowId == null) continue;
      final body = seeds.bodyFor(rowId);
      final match = body[profileIdField]?.toString() == profileId;
      seeds.patchField(rowId, activeField, match);
    }
    if (settings.isEmpty) {
      final rowId = seeds.createRow(settingsTable);
      seeds.patchField(rowId, profileIdField, profileId);
      seeds.patchField(rowId, activeField, true);
    }
    seeds.refresh();
  }
}
