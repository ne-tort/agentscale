import 'dart:typed_data';

import 'package:prodavan/core/api/admin_api.dart';
import 'package:prodavan/core/api/company_api.dart';
import 'package:prodavan/core/api/prodavan_api.dart';

/// Scoped API for live Pod workspace filesystem (employee / admin / company).
abstract class ContainerWorkspaceApi {
  Future<Map<String, dynamic>> listWorkspaceEntries({required String path});

  Future<Map<String, dynamic>> previewWorkspaceFile({required String path});

  Future<Uint8List> downloadWorkspaceFile({required String path});
}

class EmployeeContainerWorkspaceApi implements ContainerWorkspaceApi {
  EmployeeContainerWorkspaceApi(this._api, this.projectId);

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
  Future<Uint8List> downloadWorkspaceFile({required String path}) {
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
  Future<Uint8List> downloadWorkspaceFile({required String path}) {
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
  Future<Uint8List> downloadWorkspaceFile({required String path}) {
    return _api.downloadWorkspaceFile(companyId: companyId, projectId: projectId, path: path);
  }
}
