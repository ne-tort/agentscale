import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/features/employee/widgets/attachment_image_viewer.dart';
import 'package:prodavan/features/employee/widgets/attachment_preview_chip.dart';
import 'package:prodavan/features/employee/widgets/attachment_preview_kinds.dart';
import 'package:prodavan/features/employee/widgets/project_status_banner.dart';
import 'package:prodavan/features/employee/widgets/project_status_chip.dart';

Widget themed(Widget child) {
  return MaterialApp(theme: AppTheme.light, home: Scaffold(body: child));
}

/// Minimal valid 1x1 PNG.
Uint8List get _png => Uint8List.fromList([
      0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d,
      0x49, 0x48, 0x44, 0x52, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
      0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53, 0xde, 0x00, 0x00, 0x00,
      0x0c, 0x49, 0x44, 0x41, 0x54, 0x08, 0xd7, 0x63, 0xf8, 0xcf, 0xc0, 0x00,
      0x00, 0x00, 0x03, 0x00, 0x01, 0x00, 0x05, 0xfe, 0xd4, 0xef, 0x00, 0x00,
      0x00, 0x00, 0x49, 0x45, 0x4e, 0x44, 0xae, 0x42, 0x60, 0x82,
    ]);

void main() {
  test('preview kind helpers', () {
    expect(attachmentCanPreview('image/png'), isTrue);
    expect(attachmentCanPreview('text/plain'), isTrue);
    expect(attachmentCanPreview('application/json'), isTrue);
    expect(attachmentCanPreview('application/pdf'), isTrue);
    expect(attachmentCanPreview('application/zip'), isFalse);
    expect(attachmentIsTextContentType('text/csv'), isTrue);
  });

  testWidgets('project status chip marks paused', (tester) async {
    await tester.pumpWidget(themed(const ProjectStatusChip(status: 'paused')));
    expect(find.text('paused'), findsOneWidget);
    expect(ProjectStatusChip.isPaused('paused'), isTrue);
    expect(ProjectStatusChip.isPaused('active'), isFalse);
  });

  testWidgets('project status chip marks active', (tester) async {
    await tester.pumpWidget(themed(const ProjectStatusChip(status: 'active')));
    expect(find.text('active'), findsOneWidget);
  });

  testWidgets('project paused banner renders', (tester) async {
    await tester.pumpWidget(
      themed(
        Builder(
          builder: (context) => Column(
            children: ProjectStatusBanner.build(
              context,
              companySuspended: false,
              projectPaused: true,
            ),
          ),
        ),
      ),
    );
    expect(find.text('Project is paused — chat and uploads are disabled'), findsOneWidget);
    expect(find.text('Resume'), findsNothing);
  });

  testWidgets('project paused banner resume action', (tester) async {
    var resumed = false;
    await tester.pumpWidget(
      themed(
        Builder(
          builder: (context) => Column(
            children: ProjectStatusBanner.build(
              context,
              companySuspended: false,
              projectPaused: true,
              onResume: () => resumed = true,
            ),
          ),
        ),
      ),
    );
    expect(find.text('Resume'), findsOneWidget);
    await tester.tap(find.text('Resume'));
    expect(resumed, isTrue);
  });

  testWidgets('company suspend banner takes priority over paused', (tester) async {
    await tester.pumpWidget(
      themed(
        Builder(
          builder: (context) => Column(
            children: ProjectStatusBanner.build(
              context,
              companySuspended: true,
              projectPaused: true,
            ),
          ),
        ),
      ),
    );
    expect(find.text('Company subscription expired — chat and uploads are disabled'), findsOneWidget);
    expect(find.text('Project is paused — chat and uploads are disabled'), findsNothing);
  });

  testWidgets('attachment preview chip shows image thumbnail', (tester) async {
    await tester.pumpWidget(
      themed(
        AttachmentPreviewChip(
          projectId: 'p1',
          attachmentId: 'a1',
          label: 'dot.png',
          contentType: 'image/png',
          loadBytes: () async => _png,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('dot.png'), findsOneWidget);
    expect(find.byType(Image), findsOneWidget);
  });

  testWidgets('tapping image chip opens full-screen viewer', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light,
        home: Scaffold(
          body: AttachmentPreviewChip(
            projectId: 'p1',
            attachmentId: 'a1',
            label: 'dot.png',
            contentType: 'image/png',
            loadBytes: () async => _png,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('dot.png'));
    await tester.pumpAndSettle();
    expect(find.byType(AttachmentViewerPage), findsOneWidget);
    expect(find.byType(InteractiveViewer), findsOneWidget);
    expect(find.text('dot.png'), findsWidgets);
  });

  testWidgets('tapping text chip opens selectable text preview', (tester) async {
    final bytes = Uint8List.fromList(utf8.encode('hello preview\nline 2'));
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light,
        home: Scaffold(
          body: AttachmentPreviewChip(
            projectId: 'p1',
            attachmentId: 'a2',
            label: 'note.txt',
            contentType: 'text/plain',
            loadBytes: () async => bytes,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('note.txt'));
    await tester.pumpAndSettle();
    expect(find.byType(AttachmentViewerPage), findsOneWidget);
    expect(find.textContaining('hello preview'), findsOneWidget);
    expect(find.byType(SelectableText), findsOneWidget);
  });

  testWidgets('tapping pdf chip opens stub viewer', (tester) async {
    final bytes = Uint8List.fromList(utf8.encode('%PDF-1.4 stub'));
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light,
        home: Scaffold(
          body: AttachmentPreviewChip(
            projectId: 'p1',
            attachmentId: 'a3',
            label: 'doc.pdf',
            contentType: 'application/pdf',
            loadBytes: () async => bytes,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('doc.pdf'));
    await tester.pumpAndSettle();
    expect(find.byType(AttachmentViewerPage), findsOneWidget);
    expect(find.textContaining('PDF inline preview is not available yet'), findsOneWidget);
  });

  testWidgets('zip chip does not open viewer', (tester) async {
    await tester.pumpWidget(
      themed(
        AttachmentPreviewChip(
          projectId: 'p1',
          attachmentId: 'a4',
          label: 'pack.zip',
          contentType: 'application/zip',
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byType(ActionChip), findsNothing);
    expect(find.byType(Chip), findsOneWidget);
  });
}
