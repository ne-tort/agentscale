import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/features/containers/workspace_download_error.dart';

void main() {
  test('parses PAYLOAD_TOO_LARGE metadata for live workspace download', () {
    final limit = workspaceDownloadLimit(
      ProdavanApiException(
        413,
        '{"code":"PAYLOAD_TOO_LARGE","size":12582912,"max_bytes":10485760}',
      ),
    );

    expect(limit, isNotNull);
    expect(limit!.size, 12582912);
    expect(limit.maxBytes, 10485760);
  });

  test('parses nested PAYLOAD_TOO_LARGE metadata for compatible responses', () {
    final limit = workspaceDownloadLimit(
      ProdavanApiException(
        413,
        '{"code":"PAYLOAD_TOO_LARGE","extra":{"size":12582912,"max_bytes":10485760}}',
      ),
    );

    expect(limit, isNotNull);
    expect(limit!.size, 12582912);
    expect(limit.maxBytes, 10485760);
  });

  test('ignores unrelated 413 responses', () {
    expect(
      workspaceDownloadLimit(
        ProdavanApiException(413, '{"code":"ATTACHMENT_TOO_LARGE"}'),
      ),
      isNull,
    );
  });
}
