import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_nav_preference.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/meta/meta_icon.dart';
import 'package:prodavan/features/meta/meta_label.dart';
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
    this.contextProfileId,
  });

  final ModuleMetaManifest manifest;
  final Map<String, dynamic> view;
  final dynamic seeds;
  final void Function(String viewSlug, {String? rowId}) onOpenView;
  final bool readOnly;
  final String? contextProfileId;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final locale = Localizations.localeOf(context);
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
      return EmptyPlaceholder(
        title: resolveMetaLabel(
          {'ru': 'Нет профилей', 'en': 'No profiles'},
          l10n,
          locale: locale,
        ),
      );
    }

    String? activeProfileId = contextProfileId;
    if (activeProfileId == null) {
      for (final s in seeds.itemsForTable(settingsTable)) {
        final body = s['body'];
        if (body is Map && body[activeField] == true) {
          activeProfileId = body[profileIdField]?.toString();
          break;
        }
      }
      activeProfileId ??= _profileIdFromRow(profiles.first);
    }

    final fixedProfile = contextProfileId != null;
    final children = <Widget>[];

    if (!fixedProfile) {
      children.addAll([
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
      ]);
    }

    children.addAll([
      for (final item in blocks.whereType<Map>())
        AppNavPreference(
          title: resolveMetaLabel(item['title'], l10n, locale: locale).isNotEmpty
              ? resolveMetaLabel(item['title'], l10n, locale: locale)
              : '—',
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
    ]);

    return ListView(
      padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
      children: children,
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

  Future<void> _setActiveProfile({
    required String settingsTable,
    required String profileIdField,
    required String activeField,
    required String profileId,
  }) async {
    final settings = seeds.itemsForTable(settingsTable) as List;
    for (final s in settings) {
      final rowId = (s as Map)['row_id'] as String?;
      if (rowId == null) continue;
      final body = seeds.bodyFor(rowId) as Map;
      final match = body[profileIdField]?.toString() == profileId;
      final patch = seeds.patchField(rowId, activeField, match);
      if (patch is Future) await patch;
    }
    if (settings.isEmpty) {
      final created = seeds.createRow(settingsTable);
      final rowId = created is Future ? await created as String : created as String;
      final p1 = seeds.patchField(rowId, profileIdField, profileId);
      if (p1 is Future) await p1;
      final p2 = seeds.patchField(rowId, activeField, true);
      if (p2 is Future) await p2;
    }
    seeds.refresh();
  }
}
