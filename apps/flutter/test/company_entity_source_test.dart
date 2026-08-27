import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/features/company/company_entity_source.dart';
import 'package:prodavan/l10n/app_localizations.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('companyEntityPlatformAssigned accepts canonical and legacy sources', () {
    expect(companyEntityPlatformAssigned('platform_assigned'), isTrue);
    expect(companyEntityPlatformAssigned('platform_bound'), isTrue);
    expect(companyEntityPlatformAssigned('company_local'), isFalse);
  });

  test('companyEntitySourceLabel maps canonical and legacy sources', () {
    final l10n = lookupAppLocalizations(const Locale('en'));

    expect(companyEntitySourceLabel(l10n, 'platform_assigned'), l10n.companyKeyPlatformBound);
    expect(companyEntitySourceLabel(l10n, 'platform_bound'), l10n.companyKeyPlatformBound);
    expect(companyEntitySourceLabel(l10n, 'company_local'), l10n.companyKeySourceLocal);
    expect(companyEntitySourceLabel(l10n, 'company'), l10n.companyKeySourceLocal);
  });

  test('companyModuleBoundToCabinet checks scoped cabinet_ids', () {
    const module = {
      'cabinet_ids': ['cab_1', 'cab_2'],
    };
    expect(companyModuleBoundToCabinet(module, 'cab_1'), isTrue);
    expect(companyModuleBoundToCabinet(module, 'cab_9'), isFalse);
  });
}
