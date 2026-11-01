import 'dart:typed_data';

import 'package:prodavan/core/api/admin_api.dart';
import 'package:prodavan/core/api/company_api.dart';
import 'package:prodavan/core/api/prodavan_api.dart';

export 'package:prodavan/core/api/prodavan_api.dart' show WorkspaceFileDownload;

/// Upload capability is intentionally separate: only employee project workspaces
/// expose the live write endpoint in this page.
abstract interface class WorkspaceFileUploadApi {
  Future<Map<String, dynamic>> uploadWorkspaceFile({
    required String path,
    required Uint8List bytes,
  });
}

/// Scoped API for live Pod workspace filesystem (employee / admin / company).
String workspaceDownloadFilename(WorkspaceFileDownload download, String fallback) {
  final filename = download.filename?.trim();
  if (filename != null && filename.isNotEmpty) return filename;
  final normalizedPath = download.normalizedPath?.trim();
  if (normalizedPath == null || normalizedPath.isEmpty) return fallback;
  final separator = normalizedPath.lastIndexOf('/');
  final pathFilename = normalizedPath.substring(separator + 1).trim();
  return pathFilename.isEmpty ? fallback : pathFilename;
}

abstract class ContainerWorkspaceApi {
  Future<Map<String, dynamic>> listWorkspaceEntries({required String path});

  Future<Map<String, dynamic>> previewWorkspaceFile({required String path});

  Future<WorkspaceFileDownload> downloadWorkspaceFile({required String path});
}

class EmployeeContainerWorkspaceApi implements ContainerWorkspaceApi, WorkspaceFileUploadApi {
  EmployeeContainerWorkspaceApi(this._api, this.projectId);

  Future<Map<String, dynamic>> uploadWorkspaceFile({
    required String path,
    required Uint8List bytes,
  }) {
    return _api.uploadWorkspaceFile(projectId: projectId, path: path, bytes: bytes);
  }

  final ProdavanApi _api;
  final String projectId;

  @override
  Future<Map<String, dynamic>> listWorkspaceEntries({required String path}) {
    return _api.listWorkspaceEntries(projectId: projectId, path: path);
  }

  @override
  Future<Map<String, dynamic>> previewWorkspaceFile({required String path}) {
    return _api.previewWorkspaceFile(projectId: projectId, path: path);
  }

  @override
  Future<WorkspaceFileDownload> downloadWorkspaceFile({required String path}) {
    return _api.downloadWorkspaceFile(projectId: projectId, path: path);
  }
}

class AdminContainerWorkspaceApi implements ContainerWorkspaceApi {
  AdminContainerWorkspaceApi(this._api, this.projectId);

  final AdminApi _api;
  final String projectId;

  @override
  Future<Map<String, dynamic>> listWorkspaceEntries({required String path}) {
    return _api.listWorkspaceEntries(projectId: projectId, path: path);
  }

  @override
  Future<Map<String, dynamic>> previewWorkspaceFile({required String path}) {
    return _api.previewWorkspaceFile(projectId: projectId, path: path);
  }

  @override
  Future<WorkspaceFileDownload> downloadWorkspaceFile({required String path}) {
    return _api.downloadWorkspaceFile(projectId: projectId, path: path);
  }
}

class CompanyContainerWorkspaceApi implements ContainerWorkspaceApi {
  CompanyContainerWorkspaceApi(this._api, this.companyId, this.projectId);

  final CompanyApi _api;
  final String companyId;
  final String projectId;

  @override
  Future<Map<String, dynamic>> listWorkspaceEntries({required String path}) {
    return _api.listWorkspaceEntries(companyId: companyId, projectId: projectId, path: path);
  }

  @override
  Future<Map<String, dynamic>> previewWorkspaceFile({required String path}) {
    return _api.previewWorkspaceFile(companyId: companyId, projectId: projectId, path: path);
  }

  @override
  Future<WorkspaceFileDownload> downloadWorkspaceFile({required String path}) {
    return _api.downloadWorkspaceFile(companyId: companyId, projectId: projectId, path: path);
  }
}
