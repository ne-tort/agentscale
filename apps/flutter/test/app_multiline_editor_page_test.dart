import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/core/widgets/app_multiline_editor_page.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('multiline editor commits on blur and back, not per keystroke',
      (tester) async {
    final commits = <String>[];
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light,
        home: AppMultilineEditorPage(
          title: 'Edit',
          initial: 'hello',
          onCommit: commits.add,
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField), 'hello world');
    await tester.pump();
    expect(commits, isEmpty);

    FocusManager.instance.primaryFocus?.unfocus();
    await tester.pump();
    expect(commits, ['hello world']);

    commits.clear();
    await tester.enterText(find.byType(TextField), 'final text');
    await tester.pump();
    expect(commits, isEmpty);

    final navigator = tester.state<NavigatorState>(find.byType(Navigator));
    await navigator.maybePop();
    await tester.pumpAndSettle();
    // PopScope _commit plus focus-loss onEditingComplete may both fire — ok.
    expect(commits, isNotEmpty);
    expect(commits.last, 'final text');
  });
}
