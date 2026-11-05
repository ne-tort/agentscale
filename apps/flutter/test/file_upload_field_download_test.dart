import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:prodavan/features/meta/runtime/module_runtime_scope.dart';
import 'package:prodavan/features/meta/widgets/file_upload_field.dart';

void main() {
  testWidgets('stored FileRef can be downloaded using asset and version ids',
      (tester) async {
    String? requestedAsset;
    String? requestedVersion;
    String? requestedBlobVersion;
    // The scope is built first with a placeholder child: FileUploadField takes the scope
    // as a constructor argument, so passing it a value that is still being constructed
    // was a self-reference and did not compile.
    final scope = ModuleRuntimeScope(
      moduleId: 'mod_files',
      uploadKind: ModuleContentUploadKind.platform,
      uploadContentFn: ({required filename, required bytes, mime}) async => {},
      uploadSecretFn: ({required secret, label}) async => {},
      downloadContentFn: ({
        required assetId,
        versionId,
        blobVersionId,
      }) async {
        requestedAsset = assetId;
        requestedVersion = versionId;
        requestedBlobVersion = blobVersionId;
        return Uint8List.fromList([1, 2, 3]);
      },
      child: const SizedBox.shrink(),
    );
    final field = FileUploadField(
      label: 'Шаблон документа',
      value: const {
        'asset_id': 'asset-42',
        'version_id': 'version-3',
        'blob_version_id': 'blob-3',
        'filename': 'template.docx',
      },
      scope: scope,
      readOnly: true,
      onChanged: (_) {},
    );
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: field)));

    await tester.tap(find.byTooltip('Скачать файл'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 50));

    expect(requestedAsset, 'asset-42');
    expect(requestedVersion, 'version-3');
    expect(requestedBlobVersion, 'blob-3');
  });
}
