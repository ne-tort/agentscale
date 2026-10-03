import 'package:flutter/material.dart';

import 'package:prodavan/l10n/app_localizations.dart';

/// Benefit badge semantics (ui_json column `format: "benefit"`):
/// how the row's price compares to the current offer of the same position
/// (or to the best price when no current is set).
enum BenefitTone { best, better, worse, same }

class BenefitBadgeData {
  const BenefitBadgeData({required this.label, required this.tone});

  /// Short label: «Лучшая цена», «+5%», «−3%», «Одинаковая».
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
    if (ids.length < 2) continue;
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
      if (price == minPrice) {
        out[id] = isCurrent
            ? BenefitBadgeData(label: l10n.budgetBenefitBest, tone: BenefitTone.best)
            : BenefitBadgeData(
                label: _diffLabel(price, reference, l10n),
                tone: _toneForDiff(price, reference),
              );
        continue;
      }
      if (isCurrent) {
        // The chosen offer is more expensive than the best available —
        // show the overpay in red.
        out[id] = BenefitBadgeData(
          label: _diffLabel(price, minPrice, l10n),
          tone: _toneForDiff(price, minPrice),
        );
        continue;
      }
      final diffPct = ((price - reference) / reference) * 100;
      if (diffPct.abs() < sameThresholdPct) {
        out[id] = BenefitBadgeData(label: l10n.budgetBenefitSame, tone: BenefitTone.same);
      } else {
        out[id] = BenefitBadgeData(
          label: _diffLabel(price, reference, l10n),
          tone: _toneForDiff(price, reference),
        );
      }
    }
  }
  return out;
}

BenefitTone _toneForDiff(double price, double reference) {
  if (price < reference) return BenefitTone.better;
  return BenefitTone.worse;
}

String _diffLabel(double price, double reference, AppLocalizations l10n) {
  final diffPct = ((price - reference) / reference) * 100;
  final sign = diffPct > 0 ? '+' : '−';
  return l10n.budgetBenefitDiff('$sign${diffPct.abs().toStringAsFixed(1)}');
}

/// Solid pill: filled background + always-white label (green / red / yellow).
class BenefitBadge extends StatelessWidget {
  const BenefitBadge({super.key, required this.data});

  final BenefitBadgeData data;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final Color fill;
    switch (data.tone) {
      case BenefitTone.best:
      case BenefitTone.better:
        fill = scheme.primary;
      case BenefitTone.worse:
        fill = scheme.error;
      case BenefitTone.same:
        fill = scheme.tertiary;
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
