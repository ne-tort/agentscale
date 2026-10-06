import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Benefit badge semantics (ui_json column `format: "benefit"`):
/// how the row's price compares to the current offer of the same position
/// (or to the best price when no current is set).
///
/// Отображение:
/// - «Лучшая цена» — минимальная цена позиции (зелёный);
/// - «Единственный» — у позиции один кандидат (нейтральный);
/// - «+n%» — дешевле выбранного/лучшего на n% (выгода, зелёный);
/// - «−n%» — дороже на n% (переплата, красный);
/// - «Одинаковая» — разница < 0.5% (нейтральный).
enum BenefitTone { best, better, worse, same }

class BenefitBadgeData {
  const BenefitBadgeData({required this.label, required this.tone});

  /// Short label: «Лучшая цена», «Единственный», «+5%», «−3%», «Одинаковая».
  final String label;
  final BenefitTone tone;
}

/// Compute benefit badges for a collection of rows.
///
/// [priceOf] / [currentOf] / [groupOf] read the row's price, "current"
/// flag (selected/best offer) and position grouping key. Rows with no
/// parseable price get no badge. Groups without a current row fall back
/// to comparing against the best (minimum) price of the group.
Map<String, BenefitBadgeData> computeBenefitBadges({
  required Iterable<String> rowIds,
  required double? Function(String rowId) priceOf,
  required bool Function(String rowId) currentOf,
  required String Function(String rowId) groupOf,
  required AppLocalizations l10n,
}) {
  final prices = <String, double>{};
  final groups = <String, List<String>>{};
  final currentByGroup = <String, String?>{};
  for (final id in rowIds) {
    final price = priceOf(id);
    if (price == null || price <= 0) continue;
    prices[id] = price;
    final g = groupOf(id);
    groups.putIfAbsent(g, () => []).add(id);
    if (currentOf(id)) {
      currentByGroup[g] ??= id;
    }
  }

  const sameThresholdPct = 0.5;
  final out = <String, BenefitBadgeData>{};
  for (final entry in groups.entries) {
    final ids = entry.value;
    if (ids.isEmpty) continue;
    // Один кандидат у позиции — сравнивать не с чем: «Единственный».
    if (ids.length == 1) {
      out[ids.first] = BenefitBadgeData(
        label: l10n.budgetBenefitSingle,
        tone: BenefitTone.same,
      );
      continue;
    }
    final currentId = currentByGroup[entry.key];
    final pricesList = ids.map((id) => prices[id]!).toList()..sort();
    final minPrice = pricesList.first;

    String? referenceId = currentId;
    double reference;
    if (referenceId != null) {
      reference = prices[referenceId]!;
    } else {
      reference = minPrice;
      referenceId = ids.firstWhere(
        (id) => prices[id] == minPrice,
        orElse: () => ids.first,
      );
    }

      for (final id in ids) {
        final price = prices[id]!;
        final isCurrent = id == referenceId;
        if (price == minPrice && isCurrent) {
          out[id] = BenefitBadgeData(label: l10n.budgetBenefitBest, tone: BenefitTone.best);
          continue;
        }
        if (isCurrent) {
          // Выбранный/текущий дороже лучшего — переплата относительно
          // лучшей цены позиции, красным.
          out[id] = BenefitBadgeData(
            label: _savingsLabel(price, minPrice, l10n),
            tone: BenefitTone.worse,
          );
          continue;
        }
        final savingsPct = ((reference - price) / reference) * 100;
        if (savingsPct.abs() < sameThresholdPct) {
          out[id] = BenefitBadgeData(label: l10n.budgetBenefitSame, tone: BenefitTone.same);
        } else {
          out[id] = BenefitBadgeData(
            label: _savingsLabel(price, reference, l10n),
            tone: _toneForSavings(price, reference),
          );
        }
      }
  }
  return out;
}

/// Знак — выгода: дешевле референса → «+n%» (зелёный), дороже → «−n%».
BenefitTone _toneForSavings(double price, double reference) {
  if (price < reference) return BenefitTone.better;
  return BenefitTone.worse;
}

String _savingsLabel(double price, double reference, AppLocalizations l10n) {
  final savingsPct = ((reference - price) / reference) * 100;
  final sign = savingsPct > 0 ? '+' : '−';
  return l10n.budgetBenefitDiff('$sign${savingsPct.abs().toStringAsFixed(1)}');
}

/// Solid pill: filled background + always-white label (green / red / neutral).
class BenefitBadge extends StatelessWidget {
  const BenefitBadge({super.key, required this.data});

  final BenefitBadgeData data;

  @override
  Widget build(BuildContext context) {
    final colors = context.appColors;
    final Color fill;
    switch (data.tone) {
      case BenefitTone.best:
      case BenefitTone.better:
        fill = colors.success;
      case BenefitTone.worse:
        fill = colors.danger;
      case BenefitTone.same:
        fill = Theme.of(context).colorScheme.tertiary;
    }
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: fill,
        borderRadius: BorderRadius.circular(6),
      ),
      child: Text(
        data.label,
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: const TextStyle(
          color: Colors.white,
          fontSize: 11.5,
          fontWeight: FontWeight.w600,
          height: 1.25,
        ),
      ),
    );
  }
}
