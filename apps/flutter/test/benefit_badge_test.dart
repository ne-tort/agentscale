import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/features/meta/widgets/benefit_badge.dart';
import 'package:prodavan/l10n/app_localizations_ru.dart';

void main() {
  final l10n = AppLocalizationsRu();

  group('computeBenefitBadges (семантика «Выгода»)', () {
    test('единственный кандидат позиции → «Единственный»', () {
      final badges = computeBenefitBadges(
        rowIds: const ['g1'],
        priceOf: (id) => 100,
        currentOf: (id) => true,
        groupOf: (id) => 'line1',
        l10n: l10n,
      );
      expect(badges['g1']!.label, 'Единственный');
      expect(badges['g1']!.tone, BenefitTone.same);
    });

    test('лучшая цена позиции → «Лучшая цена», дороже неё → «−n%»', () {
      final prices = {'g1': 100.0, 'g2': 110.0, 'g3': 100.0};
      final badges = computeBenefitBadges(
        rowIds: prices.keys,
        priceOf: (id) => prices[id],
        currentOf: (id) => id == 'g1',
        groupOf: (id) => 'line1',
        l10n: l10n,
      );
      expect(badges['g1']!.label, 'Лучшая цена');
      expect(badges['g1']!.tone, BenefitTone.best);
      // дороже на 10% → выгода −10%, красный
      expect(badges['g2']!.label, '−10.0%');
      expect(badges['g2']!.tone, BenefitTone.worse);
      // вторая минимальная, но не выбранная → сравнение с выбранной (равна) —
      // та же цена, что у лучшей: бейдж минимальной не-выбранной — «Одинаковая»
      // по отношению к референсу той же цены
      expect(badges['g3']!.label, 'Одинаковая');
    });

    test('дешевле выбранного → «+n%» зелёный (выгода)', () {
      // вручную выбрана дорогая группа (110), у позиции есть 100
      final prices = {'g1': 100.0, 'g2': 110.0};
      final badges = computeBenefitBadges(
        rowIds: prices.keys,
        priceOf: (id) => prices[id],
        currentOf: (id) => id == 'g2',
        groupOf: (id) => 'line1',
        l10n: l10n,
      );
      // g1 — минимальная, но не выбранная: выгода относительно выбранной
      expect(badges['g1']!.label, '+9.1%');
      expect(badges['g1']!.tone, BenefitTone.better);
      // выбранная дороже лучшей → переплата красным относительно минимума
      expect(badges['g2']!.label, '−10.0%');
      expect(badges['g2']!.tone, BenefitTone.worse);
    });

    test('строки без цены не получают бейдж', () {
      final badges = computeBenefitBadges(
        rowIds: const ['g1', 'g2'],
        priceOf: (id) => id == 'g1' ? 100.0 : null,
        currentOf: (id) => false,
        groupOf: (id) => 'line1',
        l10n: l10n,
      );
      expect(badges.containsKey('g2'), isFalse);
      expect(badges['g1']!.label, 'Единственный');
    });
  });
}
