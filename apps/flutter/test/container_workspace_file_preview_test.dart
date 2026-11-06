import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:prodavan/features/containers/container_workspace_api.dart';
import 'package:prodavan/features/containers/container_workspace_file_preview_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';
import 'package:prodavan/l10n/app_localizations_en.dart';

class _PreviewApi implements ContainerWorkspaceApi {
  _PreviewApi(this.truncated);

  final bool truncated;

  @override
  Future<WorkspaceFileDownload> downloadWorkspaceFile({required String path}) async =>
      WorkspaceFileDownload(bytes: Uint8List(0));

  @override
  Future<Map<String, dynamic>> listWorkspaceEntries({required String path}) async =>
      const {'entries': []};

  @override
  Future<Map<String, dynamic>> previewWorkspaceFile({required String path}) async => {
        'path': path,
        'content': 'visible text',
        'truncated': truncated,
      };
}

void main() {
  Future<void> pumpPreview(WidgetTester tester, {required bool truncated}) async {
    await tester.pumpWidget(
      MaterialApp(
        localizationsDelegates: const [
          AppLocalizations.delegate,
          ...GlobalMaterialLocalizations.delegates,
        ],
        supportedLocales: AppLocalizations.supportedLocales,
        home: ContainerWorkspaceFilePreviewPage(
          title: 'large.txt',
          path: 'large.txt',
          api: _PreviewApi(truncated),
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  testWidgets('live preview labels content truncated by backend', (tester) async {
    await pumpPreview(tester, truncated: true);

    expect(find.text(AppLocalizationsEn().projectPreviewTruncated), findsOneWidget);
    expect(find.textContaining('256 KB'), findsOneWidget);
    expect(find.byIcon(Icons.warning_amber_outlined), findsOneWidget);
    expect(find.text('visible text'), findsOneWidget);
  });

  testWidgets('live preview does not show truncation label for complete content', (tester) async {
    await pumpPreview(tester, truncated: false);

    expect(find.text(AppLocalizationsEn().projectPreviewTruncated), findsNothing);
    expect(find.text('visible text'), findsOneWidget);
  });
}
