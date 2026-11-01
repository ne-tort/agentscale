import 'dart:typed_data';

import 'package:prodavan/features/containers/workspace_file_utils.dart';
import 'package:prodavan/features/meta/runtime/cabinet_data_controller.dart';
import 'package:prodavan/features/meta/runtime/owner_module_data_controller.dart';

/// Downloads the `file_ref` returned by a module action (budget xlsx /
/// КП PDF export) and saves it through the desktop save dialog.
///
/// True when a file was saved, false when the result carried nothing to
/// download.
Future<bool> saveModuleActionFile(
  CabinetDataController controller,
  Map<String, dynamic> result,
) async {
  return saveModuleActionFileWithDownloader(
    result,
    (ref) => controller.api.downloadContentAsset(
      assetId: ref['asset_id']!.toString(),
      versionId: ref['version_id']?.toString(),
      blobVersionId: ref['blob_version_id']?.toString(),
    ),
  );
}

Future<bool> saveOwnerModuleActionFile(
  OwnerModuleDataController controller,
  Map<String, dynamic> result,
) {
  return saveModuleActionFileWithDownloader(
    result,
    (ref) => controller.downloadContentAsset(
      assetId: ref['asset_id']!.toString(),
      versionId: ref['version_id']?.toString(),
      blobVersionId: ref['blob_version_id']?.toString(),
    ),
  );
}

Future<bool> saveModuleActionFileWithDownloader(
  Map<String, dynamic> result,
  Future<Uint8List> Function(Map<String, dynamic> ref) download,
) async {
  final rawRef = result['file_ref'];
  if (rawRef is! Map) return false;
  final ref = Map<String, dynamic>.from(rawRef);
  final assetId = ref['asset_id']?.toString() ?? '';
  if (assetId.isEmpty) return false;
  var filename = ref['filename']?.toString() ?? '';
  if (filename.isEmpty) filename = 'export';
  final bytes = await download(ref);
  return saveWorkspaceFileBytes(filename: filename, bytes: bytes);
}
