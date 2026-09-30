import 'package:prodavan/features/containers/workspace_file_utils.dart';
import 'package:prodavan/features/meta/runtime/cabinet_data_controller.dart';

/// Downloads the `file_ref` returned by a module action (budget xlsx /
/// КП PDF export) and saves it through the desktop save dialog.
///
/// True when a file was saved, false when the result carried nothing to
/// download.
Future<bool> saveModuleActionFile(
  CabinetDataController controller,
  Map<String, dynamic> result,
) async {
  final ref = result['file_ref'];
  if (ref is! Map) return false;
  final assetId = ref['asset_id']?.toString() ?? '';
  if (assetId.isEmpty) return false;
  var filename = ref['filename']?.toString() ?? '';
  if (filename.isEmpty) filename = 'export';
  final bytes = await controller.api.downloadContentAsset(
    assetId: assetId,
    versionId: ref['version_id']?.toString(),
    blobVersionId: ref['blob_version_id']?.toString(),
  );
  return saveWorkspaceFileBytes(filename: filename, bytes: bytes);
}
