import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/features/containers/container_workspace_api.dart';
import 'package:prodavan/features/containers/container_workspace_files_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

class _UploadWorkspaceApi implements ContainerWorkspaceApi, WorkspaceFileUploadApi {
  int listCalls = 0;
  final List<String> listedPaths = [];
  String? uploadedPath;
  Uint8List? uploadedBytes;

  @override
  Future<Map<String, dynamic>> listWorkspaceEntries({required String path}) async {
    listCalls++;
    listedPaths.add(path);
    return {
      'entries': [
        {'kind': 'file', 'name': 'existing.txt', 'path': 'existing.txt', 'size': 1},
      ],
    };
  }

  @override
  Future<Map<String, dynamic>> previewWorkspaceFile({required String path}) async => {};

  @override
  Future<WorkspaceFileDownload> downloadWorkspaceFile({required String path}) async =>
      WorkspaceFileDownload(bytes: Uint8List(0));

  Future<Map<String, dynamic>> uploadWorkspaceFile({
    required String path,
    required Uint8List bytes,
  }) async {
    uploadedPath = path;
    uploadedBytes = bytes;
    return {'path': 'other/notes.txt', 'size': bytes.length};
  }
}

void main() {
  test('acceptance: workspace download preserves normalized headers and filename', () {
    final download = WorkspaceFileDownload.fromResponse(
      http.Response.bytes(
        [1, 2, 3],
        200,
        headers: {
          'X-Workspace-Path': 'normalized/файл.bin',
          'X-Workspace-Size': '3',
          'Content-Disposition': 'attachment; filename="файл.bin"',
        },
      ),
      requestedPath: './normalized/файл.bin',
    );

    expect(download.bytes, orderedEquals([1, 2, 3]));
    expect(download.normalizedPath, 'normalized/файл.bin');
    expect(download.size, 3);
    expect(download.filename, 'файл.bin');
    expect(workspaceDownloadFilename(download, 'fallback.bin'), 'файл.bin');
  });

  testWidgets('acceptance: employee workspace follows server normalized upload directory', (tester) async {
    final api = _UploadWorkspaceApi();
    await tester.pumpWidget(
      MaterialApp(
        localizationsDelegates: const [
          AppLocalizations.delegate,
          ...GlobalMaterialLocalizations.delegates,
        ],
        supportedLocales: AppLocalizations.supportedLocales,
        home: ContainerWorkspaceFilesPage(
          title: 'Files',
          api: api,
          initialPath: 'docs',
          filePicker: () async => [
            PlatformFile(name: 'notes.txt', size: 3, bytes: Uint8List.fromList([1, 2, 3])),
          ],
        ),
      ),
    );
    await tester.pumpAndSettle();
    final initialCalls = api.listCalls;

    await tester.tap(find.byIcon(Icons.upload_file_outlined));
    await tester.pumpAndSettle();

    expect(api.uploadedPath, 'docs/notes.txt');
    expect(api.uploadedBytes, orderedEquals([1, 2, 3]));
    expect(api.listCalls, initialCalls + 1);
    expect(api.listedPaths.last, 'other');
  });
}
