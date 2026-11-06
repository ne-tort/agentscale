import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:prodavan/features/containers/container_workspace_api.dart';

void main() {
  test('acceptance: workspace download preserves metadata and prefers server filename', () {
    final download = WorkspaceFileDownload(
      bytes: Uint8List.fromList([1, 2, 3]),
      normalizedPath: 'reports/normalized-name.txt',
      size: 3,
      filename: 'download-name.txt',
    );

    expect(download.bytes, orderedEquals([1, 2, 3]));
    expect(download.normalizedPath, 'reports/normalized-name.txt');
    expect(download.size, 3);
    expect(workspaceDownloadFilename(download, 'requested-name.txt'), 'download-name.txt');
  });

  test('acceptance: normalized path supplies filename when content disposition is absent', () {
    final download = WorkspaceFileDownload(
      bytes: Uint8List(0),
      normalizedPath: 'reports/normalized-name.txt',
      size: 0,
    );

    expect(workspaceDownloadFilename(download, 'requested-name.txt'), 'normalized-name.txt');
  });
}
