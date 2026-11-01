import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:prodavan/features/meta/module_action_file_download.dart';

void main() {
  group('saveModuleActionFileWithDownloader', () {
    test('returns false without a FileRef and does not download', () async {
      var downloads = 0;

      final saved = await saveModuleActionFileWithDownloader(
        const {'ok': true},
        (_) async {
          downloads++;
          return Uint8List(0);
        },
      );

      expect(saved, isFalse);
      expect(downloads, 0);
    });

    test('returns false when FileRef has no asset id', () async {
      var downloads = 0;

      final saved = await saveModuleActionFileWithDownloader(
        const {
          'file_ref': {'filename': 'report.xlsx'},
        },
        (_) async {
          downloads++;
          return Uint8List(0);
        },
      );

      expect(saved, isFalse);
      expect(downloads, 0);
    });
  });
}
