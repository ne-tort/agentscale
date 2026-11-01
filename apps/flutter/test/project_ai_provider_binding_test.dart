import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/features/employee/project_ai_provider_binding.dart';

void main() {
  test('stale resolved provider binding is unavailable for launch', () {
    expect(
      isProjectAiProviderBindingAvailable(
        'key-removed',
        const [
          {'id': 'key-current'},
        ],
      ),
      isFalse,
    );
  });

  test('resolved provider binding is available when API returns the key', () {
    expect(
      isProjectAiProviderBindingAvailable(
        'key-current',
        const [
          {'id': 'key-current'},
        ],
      ),
      isTrue,
    );
  });

  test('missing resolved provider binding is unavailable for launch', () {
    expect(
      isProjectAiProviderBindingAvailable(null, const []),
      isFalse,
    );
  });
}
