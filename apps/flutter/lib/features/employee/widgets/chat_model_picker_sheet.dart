import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_modal_sheet.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Bottom-sheet model picker for the chat composer.
///
/// Compact alternative to the full-table [ProjectChatModelSelectPage]: label +
/// price per 1M tokens + «default» badge; tap a row to select. Returns the
/// picked model id, or null when dismissed. [enabled] `false` renders rows
/// inert (read-only sheet).
Future<String?> showChatModelPickerSheet(
  BuildContext context, {
  required List<Map<String, dynamic>> models,
  String? selectedModelId,
  String? defaultModelId,
  bool enabled = true,
}) {
  return showAppModalSheet<String>(
    context: context,
    backgroundColor: Theme.of(context).colorScheme.surfaceContainerLow,
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
    ),
    constraints: BoxConstraints(
      maxHeight: MediaQuery.sizeOf(context).height * 0.72,
    ),
    builder: (context) => _ChatModelPickerSheet(
      models: models,
      selectedModelId: selectedModelId,
      defaultModelId: defaultModelId,
      enabled: enabled,
    ),
  );
}

class _ChatModelPickerSheet extends StatelessWidget {
  const _ChatModelPickerSheet({
    required this.models,
    this.selectedModelId,
    this.defaultModelId,
    this.enabled = true,
  });

  final List<Map<String, dynamic>> models;
  final String? selectedModelId;
  final String? defaultModelId;
  final bool enabled;

  static num? _asNum(Object? raw) {
    if (raw is num) return raw;
    return num.tryParse('$raw');
  }

  /// Null → '—'; otherwise trailing zeros trimmed, max 2 decimals
  /// (0.15 → "0.15", 1.25 → "1.25", 25 → "25").
  static String _fmtPrice(num? v) {
    if (v == null) return '—';
    var s = v.toStringAsFixed(2);
    if (s.contains('.')) {
      s = s.replaceFirst(RegExp(r'0+$'), '').replaceFirst(RegExp(r'\.$'), '');
    }
    return s;
  }

  Widget _row(BuildContext context, Map<String, dynamic> m) {
    final l10n = AppLocalizations.of(context);
    final scheme = Theme.of(context).colorScheme;
    final id = m['id'] as String? ?? m['label'] as String? ?? '';
    final label = m['label'] as String? ?? id;
    final priceIn = _asNum(m['input_price_usd_per_mtok']);
    final priceOut = _asNum(m['output_price_usd_per_mtok']);
    final selected = id == selectedModelId;
    return InkWell(
      onTap: enabled && id.isNotEmpty
          ? () => Navigator.of(context).pop(id)
          : null,
      child: Padding(
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.md,
          vertical: AppSpacing.sm,
        ),
        child: Row(
          children: [
            SizedBox(
              width: 18,
              height: 18,
              child: selected
                  ? Icon(Icons.check, size: 18, color: scheme.primary)
                  : null,
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    label,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: Theme.of(context).textTheme.bodyMedium,
                  ),
                  if (priceIn != null || priceOut != null)
                    Text(
                      l10n.chatModelPricePerMtok(
                        _fmtPrice(priceIn),
                        _fmtPrice(priceOut),
                      ),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.bodySmall
                          ?.copyWith(color: scheme.onSurfaceVariant),
                    ),
                ],
              ),
            ),
            if (id == defaultModelId)
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                decoration: BoxDecoration(
                  color: scheme.secondaryContainer,
                  borderRadius: BorderRadius.circular(6),
                ),
                child: Text(
                  l10n.chatModelDefaultLabel,
                  style: Theme.of(context).textTheme.labelSmall,
                ),
              ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final scheme = Theme.of(context).colorScheme;
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Padding(
          padding: const EdgeInsets.symmetric(
            horizontal: AppSpacing.md,
            vertical: AppSpacing.sm,
          ),
          child: Row(
            children: [
              Expanded(
                child: Text(
                  l10n.projectChatModelSelectTitle,
                  style: Theme.of(context).textTheme.titleMedium,
                ),
              ),
              IconButton(
                icon: const Icon(Icons.close),
                tooltip: l10n.commonCancel,
                onPressed: () => Navigator.of(context).pop(),
              ),
            ],
          ),
        ),
        if (models.isEmpty)
          Padding(
            padding: const EdgeInsets.symmetric(
              horizontal: AppSpacing.md,
              vertical: AppSpacing.xl,
            ),
            child: Center(
              child: Text(
                l10n.chatModelPickerEmpty,
                style: Theme.of(context).textTheme.bodyMedium
                    ?.copyWith(color: scheme.onSurfaceVariant),
              ),
            ),
          )
        else
          Flexible(
            child: ListView.builder(
              shrinkWrap: true,
              itemCount: models.length,
              itemBuilder: (context, i) => _row(context, models[i]),
            ),
          ),
      ],
    );
  }
}
