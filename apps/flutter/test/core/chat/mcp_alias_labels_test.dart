import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/models/chat_projection.dart';
import 'package:prodavan/core/chat/tool_activity_labels.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// MCP display aliases (mcp_aliases module meta) + standard agent utility
/// labels + permission_denial projection.
void main() {
  Future<AppLocalizations> l10n(WidgetTester tester) async {
    late AppLocalizations captured;
    await tester.pumpWidget(
      MaterialApp(
        localizationsDelegates: AppLocalizations.localizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        locale: const Locale('ru'),
        home: Builder(
          builder: (context) {
            captured = AppLocalizations.of(context);
            return const SizedBox.shrink();
          },
        ),
      ),
    );
    return captured;
  }

  final aliases = <String, String>{
    'mcp.prodavan-equipment.equipment_catalog_search': 'Поиск товара',
    'equipment_catalog_sources': 'Источники каталога',
  };

  group('splitMcpToolName', () {
    test('canonical mcp.server.tool', () {
      final r = splitMcpToolName('mcp.prodavan-equipment.equipment_catalog_search');
      expect(r.server, 'prodavan-equipment');
      expect(r.tool, 'equipment_catalog_search');
    });

    test('legacy mcp__server__tool', () {
      final r = splitMcpToolName('mcp__prodavan-equipment__equipment_catalog_search');
      expect(r.server, 'prodavan-equipment');
      expect(r.tool, 'equipment_catalog_search');
    });

    test('single-segment name (no server part)', () {
      final r = splitMcpToolName('mcp.list_servers');
      expect(r.server, isNull);
      expect(r.tool, 'list_servers');
    });

    test('openclaw builtin', () {
      final r = splitMcpToolName('mcp.openclaw.fs.read');
      expect(r.server, 'openclaw');
      expect(r.tool, 'fs.read');
    });
  });

  group('resolveMcpAlias', () {
    test('full canonical name wins', () {
      expect(
        resolveMcpAlias(aliases, 'mcp.prodavan-equipment.equipment_catalog_search'),
        'Поиск товара',
      );
    });

    test('bare tool name fallback', () {
      expect(
        resolveMcpAlias(aliases, 'mcp.any-server.equipment_catalog_sources'),
        'Источники каталога',
      );
    });

    test('no alias → null', () {
      expect(resolveMcpAlias(aliases, 'mcp.x.unknown_tool'), isNull);
      expect(resolveMcpAlias(const {}, 'mcp.x.y'), isNull);
    });
  });

  group('normalizeToolKind openclaw builtins', () {
    test('mcp.openclaw.<builtin> unwraps to the builtin kind', () {
      expect(normalizeToolKind('mcp.openclaw.fs.read'), ToolKind.fileRead);
      expect(normalizeToolKind('mcp.openclaw.shell.exec'), ToolKind.shell);
      expect(normalizeToolKind('mcp.openclaw.search.glob'), ToolKind.searchGlob);
      // Module MCP tools stay MCP.
      expect(normalizeToolKind('mcp.prodavan-equipment.equipment_catalog_search'), ToolKind.mcp);
    });
  });

  testWidgets('formatToolActivityLabel uses MCP alias', (tester) async {
    final l = await l10n(tester);
    final p = formatToolActivityLabel(
      l,
      name: 'mcp.prodavan-equipment.equipment_catalog_search',
      mcpAliases: aliases,
    );
    expect(p.label, 'Поиск товара');
  });

  testWidgets('formatToolActivityLabel: pending appends dots', (tester) async {
    final l = await l10n(tester);
    final p = formatToolActivityLabel(
      l,
      name: 'mcp.prodavan-equipment.equipment_catalog_search',
      pending: true,
      mcpAliases: aliases,
    );
    expect(p.label, 'Поиск товара…');
  });

  testWidgets('formatToolActivityLabel: unaliased MCP → server · tool (neutral)', (tester) async {
    final l = await l10n(tester);
    final p = formatToolActivityLabel(
      l,
      name: 'mcp.some-server.some_tool',
    );
    expect(p.label, contains('some-server'));
    expect(p.label, contains('some_tool'));
    // Neutral labels: no tech "MCP:" prefix.
    expect(p.label.startsWith('MCP'), isFalse);
    expect(p.label, l.projectChatToolMcpServer('some-server', 'some_tool'));
  });

  testWidgets('formatToolActivityLabel: standard utilities localized', (tester) async {
    final l = await l10n(tester);
    expect(
      formatToolActivityLabel(l, name: 'web.search').label,
      l.projectChatToolWebSearch,
    );
    expect(
      formatToolActivityLabel(l, name: 'todo.write').label,
      l.projectChatToolTodoWrite,
    );
    // mcp.list_servers is a functional utility, not a module tool — no raw
    // "MCP: .list_servers" label.
    expect(
      formatToolActivityLabel(l, name: 'mcp.list_servers').label,
      l.projectChatToolMcpServers,
    );
  });

  test('applyStreamEvent: permission_denial → block', () {
    var blocks = <ChatBlock>[];
    blocks = applyStreamEvent(blocks, {
      'type': 'permission_denial',
      'data': {'name': 'mcp.prodavan-equipment.found_groups_upsert', 'reason': 'denied by policy'},
    });
    expect(blocks.length, 1);
    expect(blocks.first.kind, 'permission_denial');
    expect(blocks.first.raw['name'], 'mcp.prodavan-equipment.found_groups_upsert');
  });
}
