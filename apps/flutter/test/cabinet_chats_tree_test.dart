import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:prodavan/core/preferences/collapsed_projects_store.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/features/employee/cabinet_chats_rail.dart';
import 'package:prodavan/l10n/app_localizations.dart';

Widget _app(Widget home) {
  return MaterialApp(
    theme: AppTheme.light,
    locale: const Locale('ru'),
    supportedLocales: AppLocalizations.supportedLocales,
    localizationsDelegates: const [
      AppLocalizations.delegate,
      GlobalMaterialLocalizations.delegate,
      GlobalWidgetsLocalizations.delegate,
      GlobalCupertinoLocalizations.delegate,
    ],
    home: Scaffold(body: home),
  );
}

Map<String, dynamic> _group(
  String id,
  String name, {
  bool newChatEnabled = false,
  List<Map<String, dynamic>> chats = const [],
}) {
  return <String, dynamic>{
    'project_id': id,
    'project_name': name,
    'status': 'active',
    'new_chat_enabled': newChatEnabled,
    'chats': chats,
  };
}

Map<String, dynamic> _chat(
  String sid,
  String title, {
  bool pinned = false,
  String projectId = 'proj_1',
  String projectName = 'Alpha',
}) {
  return <String, dynamic>{
    'session_id': sid,
    'project_id': projectId,
    'project_name': projectName,
    'title': title,
    'pinned': pinned,
    'has_draft': false,
    'has_messages': true,
    'last_message_at': '2026-06-01T00:00:00+00:00',
    'status': 'active',
    'created_at': '2026-06-01T00:00:00+00:00',
  };
}

void main() {
  testWidgets('renders project headers with chats under expanded branches', (tester) async {
    await tester.pumpWidget(
      _app(
        CabinetChatsRail(
          extended: true,
          newChatEnabled: false,
          projectGroups: [
            _group('proj_a', 'Alpha', chats: [
              _chat('ags_pin', 'Pinned A', pinned: true),
              _chat('ags_a2', 'A2'),
            ]),
            _group('proj_b', 'Beta', chats: [
              _chat('ags_b1', 'B1', projectId: 'proj_b', projectName: 'Beta'),
            ]),
          ],
          activeSessionId: null,
          onNewChat: null,
          onOpenChat: (_) {},
        ),
      ),
    );

    expect(find.text('Alpha'), findsOneWidget);
    expect(find.text('Beta'), findsOneWidget);
    // Chats of every project render under their expanded branch.
    expect(find.text('Pinned A'), findsOneWidget);
    expect(find.text('A2'), findsOneWidget);
    expect(find.text('B1'), findsOneWidget);
    // Both branches expanded → chevrons point down.
    expect(find.byIcon(Icons.expand_more), findsNWidgets(2));
    expect(find.byIcon(Icons.chevron_right), findsNothing);
    // No chat count badges on project headers.
    expect(find.text('· 2'), findsNothing);
    expect(find.text('· 1'), findsNothing);
    // Chat rows are text-only — no leading icons.
    expect(find.byIcon(Icons.chat_bubble_outline), findsNothing);
    expect(find.byIcon(Icons.push_pin), findsNothing);
    expect(find.byIcon(Icons.edit_note_outlined), findsNothing);
    // Compact tree fonts: chats labelMedium (12), project names 13.
    expect(tester.widget<Text>(find.text('A2')).style?.fontSize, 12);
    expect(tester.widget<Text>(find.text('Alpha')).style?.fontSize, 13);
  });

  testWidgets('tapping a header collapses the branch and hides its chats', (tester) async {
    var collapsed = <String>{};
    await tester.pumpWidget(
      _app(
        StatefulBuilder(
          builder: (context, setState) => CabinetChatsRail(
            extended: true,
            newChatEnabled: false,
            projectGroups: [
              _group('proj_a', 'Alpha', chats: [_chat('ags_a1', 'A1')]),
              _group('proj_b', 'Beta', chats: [
                _chat('ags_b1', 'B1', projectId: 'proj_b', projectName: 'Beta'),
              ]),
            ],
            collapsedProjectIds: collapsed,
            onToggleProjectCollapsed: (id) => setState(() {
              final next = Set<String>.of(collapsed);
              if (!next.add(id)) {
                next.remove(id);
              }
              collapsed = next;
            }),
            activeSessionId: null,
            onNewChat: null,
            onOpenChat: (_) {},
          ),
        ),
      ),
    );
    expect(find.text('A1'), findsOneWidget);
    expect(find.byIcon(Icons.expand_more), findsNWidgets(2));

    await tester.tap(find.text('Alpha'));
    await tester.pumpAndSettle();

    // Alpha collapsed: only its chats disappear, header stays.
    expect(find.text('A1'), findsNothing);
    expect(find.text('Alpha'), findsOneWidget);
    expect(find.text('B1'), findsOneWidget);
    expect(find.byIcon(Icons.chevron_right), findsOneWidget);

    // Tapping again re-expands.
    await tester.tap(find.text('Alpha'));
    await tester.pumpAndSettle();
    expect(find.text('A1'), findsOneWidget);
    expect(find.byIcon(Icons.expand_more), findsNWidgets(2));
  });

  testWidgets('pinned chat renders first within its branch', (tester) async {
    await tester.pumpWidget(
      _app(
        CabinetChatsRail(
          extended: true,
          newChatEnabled: false,
          projectGroups: [
            _group('proj_a', 'Alpha', chats: [
              _chat('ags_pin', 'Pinned A', pinned: true),
              _chat('ags_new', 'Newer A'),
              _chat('ags_mid', 'Mid A'),
            ]),
          ],
          activeSessionId: null,
          onNewChat: null,
          onOpenChat: (_) {},
        ),
      ),
    );

    // No pin icons anymore — order is the only pinned signal.
    expect(find.byIcon(Icons.push_pin), findsNothing);
    final pinnedTop = tester.getTopLeft(find.text('Pinned A')).dy;
    final newerTop = tester.getTopLeft(find.text('Newer A')).dy;
    final midTop = tester.getTopLeft(find.text('Mid A')).dy;
    expect(pinnedTop, lessThan(newerTop));
    expect(newerTop, lessThan(midTop));
  });

  testWidgets('selected chat row shows a persistent hover-colored highlight', (tester) async {
    await tester.pumpWidget(
      _app(
        CabinetChatsRail(
          extended: true,
          newChatEnabled: false,
          projectGroups: [
            _group('proj_a', 'Alpha', chats: [
              _chat('ags_1', 'Selected one'),
              _chat('ags_2', 'Plain one'),
            ]),
          ],
          activeSessionId: 'ags_1',
          onNewChat: null,
          onOpenChat: (_) {},
        ),
      ),
    );

    final selected = tester.widget<Text>(find.text('Selected one'));
    final plain = tester.widget<Text>(find.text('Plain one'));
    expect(selected.style?.fontWeight, FontWeight.w600);
    expect(plain.style?.fontWeight, isNot(FontWeight.w600));

    // Persistent highlight: a container with the hover color behind the text.
    final label = find.text('Selected one');
    final expected = tester
        .element(label)
        .appColors
        .onSurface
        .withValues(alpha: 0.08);
    final box = tester.widget<DecoratedBox>(
      find.ancestor(of: label, matching: find.byType(DecoratedBox)),
    );
    expect((box.decoration as BoxDecoration).color, expected);
    // Plain rows have no highlight container.
    expect(
      find.ancestor(of: find.text('Plain one'), matching: find.byType(DecoratedBox)),
      findsNothing,
    );
  });

  testWidgets('per-project new-chat button appears when enabled + extended', (tester) async {
    final started = <String>[];
    await tester.pumpWidget(
      _app(
        CabinetChatsRail(
          extended: true,
          newChatEnabled: false,
          projectGroups: [
            _group('proj_a', 'Alpha', newChatEnabled: true, chats: [
              _chat('ags_a1', 'A1'),
            ]),
            _group('proj_b', 'Beta', newChatEnabled: false, chats: [
              _chat('ags_b1', 'B1', projectId: 'proj_b', projectName: 'Beta'),
            ]),
          ],
          activeSessionId: null,
          onNewChat: null,
          onNewChatForProject: started.add,
          onOpenChat: (_) {},
        ),
      ),
    );

    // Only the enabled branch has an add button.
    expect(find.byIcon(Icons.add), findsOneWidget);

    await tester.tap(find.byIcon(Icons.add));
    await tester.pumpAndSettle();
    expect(started, ['proj_a']);
  });

  testWidgets('empty branch renders a draft row that starts a chat there', (tester) async {
    final started = <String>[];
    await tester.pumpWidget(
      _app(
        CabinetChatsRail(
          extended: true,
          newChatEnabled: false,
          projectGroups: [
            _group('proj_a', 'Alpha', newChatEnabled: true, chats: const []),
          ],
          activeSessionId: null,
          onNewChat: null,
          onNewChatForProject: started.add,
          onOpenChat: (_) {},
        ),
      ),
    );

    expect(find.text('Alpha'), findsOneWidget);
    // Exactly one synthetic draft row inside the empty branch.
    expect(find.text('Новый диалог'), findsOneWidget);
    // Draft row has no leading icon (unlike the legacy global tile).
    expect(find.byIcon(Icons.add_comment_outlined), findsNothing);

    await tester.tap(find.text('Новый диалог'));
    await tester.pumpAndSettle();
    expect(started, ['proj_a']);
  });

  testWidgets('draft row disappears once a chat exists, returns when empty', (tester) async {
    Widget railFor(List<Map<String, dynamic>> groups) => _app(
          CabinetChatsRail(
            extended: true,
            newChatEnabled: false,
            projectGroups: groups,
            activeSessionId: null,
            onNewChat: null,
            onNewChatForProject: (_) {},
            onOpenChat: (_) {},
          ),
        );

    // Branch with a chat: no synthetic draft row.
    await tester.pumpWidget(railFor([
      _group('proj_a', 'Alpha', newChatEnabled: true, chats: [_chat('ags_a1', 'A1')]),
    ]));
    expect(find.text('A1'), findsOneWidget);
    expect(find.text('Новый диалог'), findsNothing);

    // All chats gone → the branch is empty → the draft row returns.
    await tester.pumpWidget(railFor([
      _group('proj_a', 'Alpha', newChatEnabled: true, chats: const []),
    ]));
    expect(find.text('Новый диалог'), findsOneWidget);
  });

  testWidgets('draft row is dim and untappable when new chats are disabled', (tester) async {
    final started = <String>[];
    await tester.pumpWidget(
      _app(
        CabinetChatsRail(
          extended: true,
          newChatEnabled: false,
          projectGroups: [
            _group('proj_a', 'Alpha', newChatEnabled: false, chats: const []),
          ],
          activeSessionId: null,
          onNewChat: null,
          onNewChatForProject: started.add,
          onOpenChat: (_) {},
        ),
      ),
    );

    final row = find.text('Новый диалог');
    expect(row, findsOneWidget);
    final style = tester.widget<Text>(row).style;
    expect(style?.color, tester.element(row).appColors.muted.withValues(alpha: 0.45));

    await tester.tap(row);
    await tester.pumpAndSettle();
    expect(started, isEmpty);
  });

  testWidgets('global new chat tile is absent in tree mode', (tester) async {
    var globalTapped = false;
    await tester.pumpWidget(
      _app(
        CabinetChatsRail(
          extended: true,
          newChatEnabled: true,
          projectGroups: [
            _group('proj_a', 'Alpha', newChatEnabled: true, chats: [
              _chat('ags_a1', 'A1'),
            ]),
          ],
          activeSessionId: null,
          onNewChat: () => globalTapped = true,
          onOpenChat: (_) {},
        ),
      ),
    );

    expect(find.byIcon(Icons.add_comment_outlined), findsNothing);
    expect(find.text('Новый диалог'), findsNothing);
    expect(globalTapped, isFalse);
  });

  testWidgets('legacy fallback keeps the global new chat tile', (tester) async {
    var globalTapped = false;
    await tester.pumpWidget(
      _app(
        CabinetChatsRail(
          extended: true,
          legacyLayout: true,
          newChatEnabled: true,
          projectGroups: [
            _group('proj_a', 'Alpha', newChatEnabled: false, chats: [
              _chat('ags_a1', 'A1'),
            ]),
          ],
          activeSessionId: null,
          onNewChat: () => globalTapped = true,
          onOpenChat: (_) {},
        ),
      ),
    );

    expect(find.text('Новый диалог'), findsOneWidget);

    await tester.tap(find.text('Новый диалог'));
    await tester.pumpAndSettle();
    expect(globalTapped, isTrue);
  });

  testWidgets('icon-only rail renders chats flat without project headers', (tester) async {
    await tester.pumpWidget(
      _app(
        CabinetChatsRail(
          extended: false,
          newChatEnabled: false,
          projectGroups: [
            _group('proj_a', 'Alpha', chats: [_chat('ags_a1', 'A1')]),
            _group('proj_b', 'Beta', chats: [
              _chat('ags_b1', 'B1', projectId: 'proj_b', projectName: 'Beta'),
            ]),
          ],
          activeSessionId: null,
          onNewChat: null,
          onOpenChat: (_) {},
        ),
      ),
    );

    // No per-project icons exist — chats of all projects render flat,
    // no headers (and no count badges) in icon-only mode; text-only rows.
    expect(find.text('Alpha'), findsNothing);
    expect(find.text('Beta'), findsNothing);
    expect(find.text('· 1'), findsNothing);
    expect(find.text('A1'), findsOneWidget);
    expect(find.text('B1'), findsOneWidget);
    expect(find.byIcon(Icons.chat_bubble_outline), findsNothing);
  });

  test('CollapsedProjectsStore load/save round-trip', () async {
    SharedPreferences.setMockInitialValues(<String, Object>{
      'prodavan.chats.rail.collapsedProjects': ['proj_b'],
    });
    expect(await CollapsedProjectsStore.load(), {'proj_b'});

    await CollapsedProjectsStore.save({'proj_a', 'proj_b'});
    expect(await CollapsedProjectsStore.load(), {'proj_a', 'proj_b'});

    // Nothing persisted → empty set (all branches expanded).
    SharedPreferences.setMockInitialValues(<String, Object>{});
    expect(await CollapsedProjectsStore.load(), isEmpty);
  });

  testWidgets('collapse state persists after reload', (tester) async {
    SharedPreferences.setMockInitialValues(<String, Object>{});
    final groups = [
      _group('proj_a', 'Alpha', chats: [_chat('ags_a1', 'A1')]),
      _group('proj_b', 'Beta', chats: [
        _chat('ags_b1', 'B1', projectId: 'proj_b', projectName: 'Beta'),
      ]),
    ];

    // Session 1: collapse Alpha (harness persists via CollapsedProjectsStore,
    // same wiring as CabinetShell).
    await tester.pumpWidget(_app(_RailHarness(groups: groups)));
    await tester.pump();
    expect(find.text('A1'), findsOneWidget);

    await tester.tap(find.text('Alpha'));
    await tester.pumpAndSettle();
    expect(find.text('A1'), findsNothing);

    // Session 2 (fresh state = shell reload): persisted collapsed set applies.
    await tester.pumpWidget(_app(_RailHarness(groups: groups)));
    await tester.pump();
    expect(find.text('A1'), findsNothing);
    expect(find.text('B1'), findsOneWidget);
  });
}

/// Store-backed harness mirroring CabinetShell's collapse wiring:
/// initState loads the persisted set, toggle updates state + saves.
class _RailHarness extends StatefulWidget {
  const _RailHarness({required this.groups});

  final List<Map<String, dynamic>> groups;

  @override
  State<_RailHarness> createState() => _RailHarnessState();
}

class _RailHarnessState extends State<_RailHarness> {
  Set<String> _collapsed = const <String>{};

  @override
  void initState() {
    super.initState();
    CollapsedProjectsStore.load().then((ids) {
      if (mounted) {
        setState(() => _collapsed = ids);
      }
    });
  }

  void _toggle(String projectId) {
    final next = Set<String>.of(_collapsed);
    if (!next.add(projectId)) {
      next.remove(projectId);
    }
    setState(() => _collapsed = next);
    CollapsedProjectsStore.save(next);
  }

  @override
  Widget build(BuildContext context) {
    return CabinetChatsRail(
      extended: true,
      newChatEnabled: false,
      projectGroups: widget.groups,
      collapsedProjectIds: _collapsed,
      activeSessionId: null,
      onNewChat: null,
      onNewChatForProject: null,
      onToggleProjectCollapsed: _toggle,
      onOpenChat: (_) {},
    );
  }
}
