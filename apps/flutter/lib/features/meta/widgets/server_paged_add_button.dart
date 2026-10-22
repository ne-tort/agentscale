import 'package:flutter/material.dart';

/// «+» справа от пагинатора серверной (виртуальной) таблицы.
///
/// Строки таких таблиц живут не в БД модуля, а во внешнем индексе
/// (мастер-прайс — OpenSearch), поэтому обычный inline-add («введи название в
/// строке») к ним неприменим: нужна полная форма — продавец, PN, бренд, цена,
/// наличие. Кнопка открывает view из `ui_json.add_view`.
///
/// Отдельный виджет, а не инлайн в `_serverPagedBar`: панель пагинации целиком
/// требует живой [RuntimeDataAdapter] (его не подменить в тесте), а саму кнопку
/// нужно проверять независимо.
class ServerPagedAddButton extends StatelessWidget {
  const ServerPagedAddButton({
    super.key,
    required this.onPressed,
    this.tooltip = 'Добавить товар',
  });

  /// Открыть форму создания. null — кнопка не рендерится вызывающей стороной.
  final VoidCallback onPressed;
  final String tooltip;

  @override
  Widget build(BuildContext context) {
    return IconButton(
      visualDensity: VisualDensity.compact,
      tooltip: tooltip,
      onPressed: onPressed,
      icon: const Icon(Icons.add),
    );
  }
}
