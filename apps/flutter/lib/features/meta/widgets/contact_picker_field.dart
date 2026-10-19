import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/widgets/app_catalog_select_page.dart';
import 'package:prodavan/core/widgets/app_trailing_chevron.dart';
import 'package:prodavan/features/meta/meta_label.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Выбор строки связанной таблицы в list_header (напр. «Получатель» на
/// Мастер-прайсе: контакт из mail_contacts).
///
/// Значение — row_id выбранной строки; показывает её имя/email, сохраняет
/// `row_id` через [onChanged]. Источник строк — [rows] (их уже загрузил
/// адаптер: contacts_table читается общим списком таблиц модуля).
class ContactPickerField extends StatelessWidget {
  const ContactPickerField({
    super.key,
    required this.label,
    required this.rows,
    required this.value,
    required this.onChanged,
    this.readOnly = false,
    this.emptyStyle = 'warning',
  });

  final String label;
  /// Кандидаты: [{row_id, body: {name, email, ...}}].
  final List<Map<String, dynamic>> rows;
  final String? value;
  final void Function(String? rowId) onChanged;
  final bool readOnly;
  /// `warning` — незаполненное поле подсвечивается (критично для отправки).
  final String? emptyStyle;

  String _titleOf(Map<String, dynamic> row) {
    final body = row['body'];
    return (body is Map ? body['name'] : null)?.toString() ??
        row['row_id']?.toString() ??
        '';
  }

  String _emailOf(Map<String, dynamic> row) {
    final body = row['body'];
    return (body is Map ? body['email'] : null)?.toString() ?? '';
  }

  Map<String, dynamic>? _selected() {
    final id = value?.trim() ?? '';
    if (id.isEmpty) return null;
    for (final row in rows) {
      if (row['row_id']?.toString() == id) return row;
    }
    return null;
  }

  Future<void> _pick(BuildContext context, AppLocalizations l10n) async {
    final selected = _selected();
    // AppCatalogSelectPage попает с результатом Set<String> (радио-режим):
    // ждём Object и нормализуем в строку/сброс сами.
    final dynamic picked = await Navigator.of(context).push<Object?>(
      MaterialPageRoute<Object?>(
        builder: (_) => AppCatalogSelectPage(
          title: label,
          items: [
            for (final row in rows)
              AppCatalogSelectItem(
                id: row['row_id']?.toString() ?? '',
                title: _titleOf(row),
                subtitle: _emailOf(row),
                icon: Icons.person_outline,
              ),
          ],
          selectedIds:
              selected != null ? {selected['row_id']?.toString() ?? ''} : const {},
          searchEnabled: rows.length > 8,
          onConfirm: (_) {},
        ),
      ),
    );
    if (picked is Set || picked is List) {
      final first = picked.isEmpty ? null : picked.first?.toString();
      onChanged((first != null && first.isNotEmpty) ? first : null);
      return;
    }
    if (picked is String) {
      onChanged(picked.isEmpty ? null : picked);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final locale = Localizations.localeOf(context);
    final colors = context.appColors;
    final resolved = resolveMetaLabel(label, l10n, locale: locale);
    final selected = _selected();
    final empty = selected == null;
    final accent = empty && emptyStyle == 'warning' ? colors.warning : null;
    final title = selected != null ? _titleOf(selected) : '';
    final subtitle = selected != null ? _emailOf(selected) : '';

    return ListTile(
      enabled: !readOnly,
      onTap: readOnly ? null : () => _pick(context, l10n),
      leading: Icon(
        Icons.person_outline,
        color: accent ?? Theme.of(context).colorScheme.onSurfaceVariant,
      ),
      title: Text(
        resolved,
        style: accent != null
            ? Theme.of(context)
                .textTheme
                .titleMedium
                ?.copyWith(color: accent)
            : Theme.of(context).textTheme.titleMedium,
      ),
      subtitle: empty
          ? Text(
              l10n.commonNone,
              style: TextStyle(color: colors.muted),
            )
          : Text(
              subtitle.isEmpty ? title : '$title · $subtitle',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
      trailing: readOnly ? null : const AppTrailingChevron(),
    );
  }
}
