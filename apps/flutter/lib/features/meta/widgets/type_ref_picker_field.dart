import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_nav_preference.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/features/meta/meta_icon.dart';
import 'package:prodavan/features/meta/meta_label.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Opens a types/offers collection (switch pick) and shows warning when empty.
class TypeRefPickerField extends StatelessWidget {
  const TypeRefPickerField({
    super.key,
    required this.label,
    required this.typeId,
    required this.typeName,
    required this.readOnly,
    required this.emptyStyleWarning,
    required this.emptyLabel,
    required this.onOpenPick,
    this.icon = Icons.category_outlined,
  });

  final String label;
  final String? typeId;
  final String? typeName;
  final bool readOnly;
  final bool emptyStyleWarning;
  final String emptyLabel;
  final VoidCallback onOpenPick;
  final IconData icon;

  @override
  Widget build(BuildContext context) {
    final selected = typeId != null && typeId!.trim().isNotEmpty;
    final warning = context.appColors.warning;
    final subtitleText = selected
        ? (typeName?.trim().isNotEmpty == true ? typeName!.trim() : typeId!)
        : emptyLabel;
    return AppNavPreference(
      title: label,
      icon: icon,
      subtitle: Text(
        subtitleText,
        style: TextStyle(
          color: !selected && emptyStyleWarning ? warning : null,
        ),
      ),
      accentColor: !selected && emptyStyleWarning ? warning : null,
      enabled: !readOnly,
      onTap: onOpenPick,
    );
  }
}

/// Resolve empty / selected labels from meta field config.
String typeRefEmptyLabel(
  Map<String, dynamic>? fieldCfg,
  AppLocalizations l10n,
  Locale locale,
) {
  final raw = fieldCfg?['empty_label'];
  final resolved = resolveMetaLabel(raw, l10n, locale: locale);
  if (resolved.isNotEmpty) return resolved;
  return locale.languageCode == 'en' ? 'Not selected' : 'Не выбран';
}

IconData typeRefIcon(String? name) =>
    metaIconFromName(name, fallback: Icons.category_outlined);
