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
/// When [rowBody] is set, also resolves `title_template` with `{field}` placeholders.
String? resolveViewScaffoldTitle(
  Map<String, dynamic> view,
  AppLocalizations l10n, {
  Locale? locale,
  Map<String, dynamic>? rowBody,
}) {
  final ui = view['ui_json'];
  if (ui is! Map) return null;
  final uiJson = Map<String, dynamic>.from(ui);
  final scaffold = uiJson['scaffold'];
  final templateRaw = scaffold is Map
      ? (scaffold['title_template'] ?? uiJson['title_template'])
      : uiJson['title_template'];
  if (templateRaw != null && rowBody != null) {
    final template = resolveMetaLabel(templateRaw, l10n, locale: locale);
    if (template.isNotEmpty) {
      return _applyTitleTemplate(template, rowBody);
    }
  }
  if (scaffold is Map && scaffold['title'] != null) {
    final title = resolveMetaLabel(scaffold['title'], l10n, locale: locale);
    return title.isEmpty ? null : title;
  }
  // form/detail use ui_json.title; collections/hubs use scaffold.title (above)
  // or optional title when authors set it explicitly.
  final kind = uiJson['kind'];
  final pageTitle = uiJson['title'];
  if (pageTitle != null &&
      (kind == 'form' ||
          kind == 'detail' ||
          kind == 'collection' ||
          kind == 'hub')) {
    final title = resolveMetaLabel(pageTitle, l10n, locale: locale);
    return title.isEmpty ? null : title;
  }
  return null;
}

String _applyTitleTemplate(String template, Map<String, dynamic> rowBody) {
  return template.replaceAllMapped(RegExp(r'\{([a-zA-Z_][a-zA-Z0-9_]*)\}'), (m) {
    final key = m.group(1)!;
    final val = rowBody[key];
    if (val == null) return '';
    return val.toString();
  });
}
