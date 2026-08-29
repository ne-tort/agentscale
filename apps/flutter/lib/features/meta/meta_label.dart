import 'package:flutter/material.dart';

import 'package:prodavan/l10n/app_localizations.dart';

/// Resolves a meta-syntax label: plain string, locale map, or l10n reference.
String resolveMetaLabel(
  dynamic value,
  AppLocalizations l10n, {
  Locale? locale,
}) {
  if (value == null) return '';
  if (value is String) return value;
  if (value is! Map) return value.toString();

  final map = Map<String, dynamic>.from(value);
  final l10nKey = map['l10n'];
  if (l10nKey is String) {
    final argsRaw = map['args'];
    final args = argsRaw is Map
        ? argsRaw.map((k, v) => MapEntry(k.toString(), v?.toString() ?? ''))
        : null;
    return _resolveL10nKey(l10n, l10nKey, args);
  }

  final loc = locale ?? WidgetsBinding.instance.platformDispatcher.locale;
  final lang = loc.languageCode;
  final localized = map[lang];
  if (localized is String && localized.isNotEmpty) return localized;
  if (map['ru'] is String) return map['ru'] as String;
  if (map['en'] is String) return map['en'] as String;
  for (final v in map.values) {
    if (v is String && v.isNotEmpty) return v;
  }
  return '';
}

String _resolveL10nKey(
  AppLocalizations l10n,
  String key,
  Map<String, String>? args,
) {
  switch (key) {
    case 'metaAddNew':
      return l10n.metaAddNew(args?['item'] ?? '');
    case 'commonAdd':
      return l10n.commonAdd;
    case 'companyAddEmployee':
      return l10n.companyAddEmployee;
    case 'companyAddCabinet':
      return l10n.companyAddCabinet;
    case 'companyAddModule':
      return l10n.companyAddModule;
    case 'adminAddCabinet':
      return l10n.adminAddCabinet;
    case 'adminAddModule':
      return l10n.adminAddModule;
    case 'projectAddHint':
      return l10n.projectAddHint;
    default:
      return key;
  }
}

/// Reads optional scaffold title from view ui_json (explicit syntax only).
String? resolveViewScaffoldTitle(
  Map<String, dynamic> view,
  AppLocalizations l10n, {
  Locale? locale,
}) {
  final ui = view['ui_json'];
  if (ui is! Map) return null;
  final uiJson = Map<String, dynamic>.from(ui);
  final scaffold = uiJson['scaffold'];
  if (scaffold is Map && scaffold['title'] != null) {
    final title = resolveMetaLabel(scaffold['title'], l10n, locale: locale);
    return title.isEmpty ? null : title;
  }
  final formTitle = uiJson['title'];
  if (formTitle != null && uiJson['kind'] == 'form') {
    final title = resolveMetaLabel(formTitle, l10n, locale: locale);
    return title.isEmpty ? null : title;
  }
  return null;
}
