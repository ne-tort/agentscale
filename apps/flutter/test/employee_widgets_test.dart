import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/features/employee/widgets/attachment_preview_chip.dart';
import 'package:prodavan/features/employee/widgets/project_status_banner.dart';

Widget themed(Widget child) {
  return MaterialApp(theme: AppTheme.light, home: Scaffold(body: child));
}

void main() {
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
    final png = Uint8List.fromList([
      0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
      0, 0, 0, 0,
    ]);
    await tester.pumpWidget(
      themed(
        AttachmentPreviewChip(
          projectId: 'p1',
          attachmentId: 'a1',
          label: 'dot.png',
          contentType: 'image/png',
          loadBytes: () async => png,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('dot.png'), findsOneWidget);
    expect(find.byType(Image), findsOneWidget);
  });
}
