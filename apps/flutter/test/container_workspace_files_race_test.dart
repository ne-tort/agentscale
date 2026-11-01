import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:prodavan/features/containers/container_workspace_api.dart';
import 'package:prodavan/features/containers/container_workspace_files_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

class _RacingWorkspaceApi implements ContainerWorkspaceApi {
  final Completer<Map<String, dynamic>> folderResponse = Completer();
  final Completer<Map<String, dynamic>> rootRefreshResponse = Completer();
  int rootCalls = 0;

  @override
  Future<Map<String, dynamic>> listWorkspaceEntries({required String path}) {
    if (path == 'folder') return folderResponse.future;
    rootCalls++;
    if (rootCalls == 1) {
      return Future.value({
        'entries': [
          {'kind': 'dir', 'name': 'folder', 'path': 'folder'},
        ],
      });
    }
    return rootRefreshResponse.future;
  }

  @override
  Future<Map<String, dynamic>> previewWorkspaceFile({required String path}) async => {};

  @override
  Future<WorkspaceFileDownload> downloadWorkspaceFile({required String path}) async =>
      WorkspaceFileDownload(bytes: Uint8List(0));
}

void main() {
  testWidgets('late response from previous directory cannot replace current tree', (tester) async {
    final api = _RacingWorkspaceApi();
    await tester.pumpWidget(
      MaterialApp(
        localizationsDelegates: const [
          AppLocalizations.delegate,
          ...GlobalMaterialLocalizations.delegates,
        ],
        supportedLocales: AppLocalizations.supportedLocales,
        home: ContainerWorkspaceFilesPage(title: 'Files', api: api),
      ),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.text('folder'));
    await tester.pump();
    expect(api.folderResponse.isCompleted, isFalse);

    await tester.tap(find.text('/'));
    await tester.pump();
    api.rootRefreshResponse.complete({
      'entries': [
        {'kind': 'file', 'name': 'current.txt', 'path': 'current.txt', 'size': 7},
      ],
    });
    await tester.pump();

    api.folderResponse.complete({
      'entries': [
        {'kind': 'file', 'name': 'stale.txt', 'path': 'folder/stale.txt', 'size': 5},
      ],
    });
    await tester.pump();

    expect(find.text('current.txt'), findsOneWidget);
    expect(find.text('stale.txt'), findsNothing);
  });
}
