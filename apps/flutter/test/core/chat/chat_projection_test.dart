import 'package:flutter_test/flutter_test.dart';
import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/models/chat_projection.dart';

void main() {
  test('applyStreamEvent accumulates incremental text_delta into streaming assistant block', () {
    var blocks = <ChatBlock>[];
    blocks = applyStreamEvent(blocks, {'type': 'text_delta', 'data': {'text': 'Hel'}});
    blocks = applyStreamEvent(blocks, {'type': 'text_delta', 'data': {'text': 'lo'}});
    expect(blocks.length, 1);
    expect(blocks.first.kind, 'assistant_markdown');
    expect(blocks.first.text, 'Hello');
    expect(blocks.first.isStreaming, isTrue);
  });

  test('stall_retry user_message не рендерится вторым пузырём', () {
    var blocks = applyStreamEvent([], {
      'type': 'user_message',
      'data': {'text': 'подбери SSD'},
    });
    blocks = applyStreamEvent(blocks, {
      'type': 'user_message',
      'data': {'text': 'подбери SSD', 'stall_retry': true},
    });
    final userBlocks = blocks.where((b) => b.kind == 'user').toList();
    expect(userBlocks.length, 1);
  });

  test('reconnect-статусы сторожа невидимы в транскрипте', () {
    var blocks = applyStreamEvent([], {
      'type': 'status',
      'data': {'phase': 'reconnect', 'attempt': 1, 'stall': true},
    });
    blocks = applyStreamEvent(blocks, {
      'type': 'done',
      'data': {'reason': 'stalled', 'stalled': true},
    });
    expect(blocks, isEmpty);
  });

  test('легаси error CHAT_RUN_STALLED не рендерится', () {
    final blocks = applyStreamEvent([], {
      'type': 'error',
      'data': {
        'code': 'CHAT_RUN_STALLED',
        'message': 'Ответ агента прервался без завершения…',
      },
    });
    expect(blocks, isEmpty);
    // остальные ошибки — как раньше
    final other = applyStreamEvent([], {
      'type': 'error',
      'data': {'code': 'PROVIDER_TIMEOUT', 'message': 'provider request timed out'},
    });
    expect(other.single.kind, 'error');
  });

  test('applyStreamEvent does not swallow suffix-overlap incremental chunks', () {
    // Wire is already incremental; "lo" after "Hello" must append, not overlap-merge away.
    var blocks = applyStreamEvent([], {
      'type': 'text_delta',
      'data': {'text': 'Hello'},
    });
    blocks = applyStreamEvent(blocks, {
      'type': 'text_delta',
      'data': {'text': 'lo'},
    });
    expect(blocks.single.text, 'Hellolo');
  });

  test('applyStreamEvent does not swallow cyrillic suffix overlap', () {
    var blocks = applyStreamEvent([], {
      'type': 'text_delta',
      'data': {'text': 'Найденные'},
    });
    blocks = applyStreamEvent(blocks, {
      'type': 'text_delta',
      'data': {'text': 'денные'},
    });
    expect(blocks.single.text, 'Найденныеденные');
  });

  test('normalizeTextDelta still merges cumulative SDK chunks (API/transcript)', () {
    var cum = '';
    var r = normalizeTextDelta(cum, 'Hel');
    cum = r.cumulative;
    r = normalizeTextDelta(cum, 'lo');
    // Incremental "lo" after "Hel" appends (not cumulative "Hello").
    expect(r.cumulative, 'Hello');
    r = normalizeTextDelta('Проверка', 'роверка прошла');
    // No overlap merge: verbatim append.
    expect(r.cumulative, 'Проверкароверка прошла');
    r = normalizeTextDelta('конф', 'конфигурацию');
    expect(r.cumulative, 'конфигурацию');
    expect(r.incremental, 'игурацию');
  });

  test('finalizeTurnBlocks clears streaming flag', () {
    final blocks = [
      ChatBlock(kind: 'assistant_markdown', raw: {'text': 'Hi', '_streaming': true}),
    ];
    final done = finalizeTurnBlocks(blocks);
    expect(done.first.isStreaming, isFalse);
  });

  test('tool_call closes streaming assistant so later text is a new block', () {
    var blocks = applyStreamEvent([], {
      'type': 'text_delta',
      'data': {'text': 'Before '},
    });
    blocks = applyStreamEvent(blocks, {
      'type': 'tool_call',
      'data': {'id': 't1', 'name': 'Read', 'input': {'path': 'a.md'}},
    });
    blocks = applyStreamEvent(blocks, {
      'type': 'text_delta',
      'data': {'text': 'After'},
    });
    expect(blocks.length, 3);
    expect(blocks[0].kind, 'assistant_markdown');
    expect(blocks[0].text, 'Before ');
    expect(blocks[0].isStreaming, isFalse);
    expect(blocks[1].kind, 'tool_call');
    expect(blocks[2].kind, 'assistant_markdown');
    expect(blocks[2].text, 'After');
    expect(blocks[2].isStreaming, isTrue);
  });

  group('usage attach', () {
    test('applyStreamEvent attaches usage to the nearest preceding assistant block', () {
      var blocks = applyStreamEvent([], {
        'type': 'text_delta',
        'data': {'text': 'Hi'},
      });
      blocks = applyStreamEvent(blocks, {
        'type': 'usage',
        'data': {'input_tokens': 10, 'model': 'm1', 'cost_usd': 0.01},
      });
      expect(blocks.length, 1);
      expect(blocks.single.kind, 'assistant_markdown');
      final usage = blocks.single.raw['usage'] as Map<String, dynamic>;
      expect(usage['input_tokens'], 10);
      expect(usage['model'], 'm1');
      expect(usage['cost_usd'], 0.01);
    });

    test('multiple usage events in a turn accumulate on the assistant block', () {
      var blocks = applyStreamEvent([], {
        'type': 'text_delta',
        'data': {'text': 'Hi'},
      });
      blocks = applyStreamEvent(blocks, {
        'type': 'usage',
        'data': {'input_tokens': 10, 'output_tokens': 5, 'model': 'm1', 'cost_usd': 0.02},
      });
      blocks = applyStreamEvent(blocks, {
        'type': 'usage',
        'data': {
          'input_tokens': 7,
          'cache_read_tokens': 100,
          'model': 'm2',
        },
      });
      expect(blocks.single.kind, 'assistant_markdown');
      final usage = blocks.single.raw['usage'] as Map<String, dynamic>;
      expect(usage['input_tokens'], 17);
      expect(usage['output_tokens'], 5);
      expect(usage['cache_read_tokens'], 100);
      expect(usage['cost_usd'], 0.02);
      expect(usage['model'], 'm2');
    });

    test('usage without a preceding assistant home is dropped', () {
      var blocks = applyStreamEvent([], {
        'type': 'usage',
        'data': {'input_tokens': 10},
      });
      expect(blocks, isEmpty);

      final out = attachUsageToAssistant([
        ChatBlock(kind: 'tool_call', raw: {'id': 't1', 'name': 'Read'}),
        ChatBlock(kind: 'usage', raw: {'input_tokens': 3}),
      ]);
      expect(out.where((b) => b.kind == 'usage'), isEmpty);
      expect(out.length, 1);
    });

    test('attachUsageToAssistant keeps late usage blocks from transcripts', () {
      final out = attachUsageToAssistant([
        ChatBlock(kind: 'user', raw: {'text': 'q'}),
        ChatBlock(kind: 'assistant_markdown', raw: {'text': 'a1'}),
        ChatBlock(kind: 'usage', raw: {'input_tokens': 4}),
        ChatBlock(kind: 'assistant_markdown', raw: {'text': 'a2'}),
        ChatBlock(kind: 'usage', raw: {'output_tokens': 9}),
      ]);
      expect(out.length, 3);
      expect(out.where((b) => b.kind == 'usage'), isEmpty);
      expect((out[1].raw['usage'] as Map)['input_tokens'], 4);
      expect((out[2].raw['usage'] as Map)['output_tokens'], 9);
    });

    test('chatBlocksFromTranscript attaches usage and assigns stable keys', () {
      final blocks = chatBlocksFromTranscript([
        {'kind': 'user', 'text': 'q'},
        {'kind': 'assistant_markdown', 'text': 'a'},
        {'kind': 'usage', 'input_tokens': 4, 'output_tokens': 2, 'cost_usd': 0.05, 'model': 'm'},
      ]);
      expect(blocks.length, 2);
      expect(blocks.where((b) => b.kind == 'usage'), isEmpty);
      final usage = blocks[1].raw['usage'] as Map<String, dynamic>;
      expect(usage['input_tokens'], 4);
      expect(usage['output_tokens'], 2);
      expect(usage['cost_usd'], 0.05);
      expect(usage['model'], 'm');
      expect(blocks[1].key, 'h1');
    });
  });

  group('subagent matching', () {
    test('subagent_event matches by agent_id when ids disagree', () {
      var blocks = applyStreamEvent([], {
        'type': 'subagent_start',
        'data': {'agent_id': 'ag_1', 'parent_tool_use_id': 'tu_1', 'type': 'researcher'},
      });
      // Block id is agent_id; event carries only agent_id.
      blocks = applyStreamEvent(blocks, {
        'type': 'subagent_event',
        'data': {
          'agent_id': 'ag_1',
          'child_event': {'kind': 'text', 'text': 'working'},
        },
      });
      final sub = blocks.where((b) => b.kind == 'subagent').single;
      expect((sub.raw['events'] as List).length, 1);
    });

    test('subagent_event matches by parent_tool_use_id when agent_id differs', () {
      var blocks = applyStreamEvent([], {
        'type': 'subagent_start',
        'data': {'agent_id': 'ag_1', 'parent_tool_use_id': 'tu_1'},
      });
      // Block id is agent_id; event carries only parent_tool_use_id.
      blocks = applyStreamEvent(blocks, {
        'type': 'subagent_event',
        'data': {
          'parent_tool_use_id': 'tu_1',
          'child_event': {'text': 'x'},
        },
      });
      final sub = blocks.where((b) => b.kind == 'subagent').single;
      expect((sub.raw['events'] as List).length, 1);
    });

    test('subagent_stop matches by either id kind', () {
      var blocks = applyStreamEvent([], {
        'type': 'subagent_start',
        'data': {'agent_id': 'ag_1', 'parent_tool_use_id': 'tu_1'},
      });
      blocks = applyStreamEvent(blocks, {
        'type': 'subagent_stop',
        'data': {'parent_tool_use_id': 'tu_1', 'result_summary': 'done'},
      });
      expect(blocks.single.raw['result_summary'], 'done');

      var blocks2 = applyStreamEvent([], {
        'type': 'subagent_start',
        'data': {'agent_id': 'ag_2', 'parent_tool_use_id': 'tu_2'},
      });
      blocks2 = applyStreamEvent(blocks2, {
        'type': 'subagent_stop',
        'data': {'agent_id': 'ag_2', 'result_summary': 'ok'},
      });
      expect(blocks2.single.raw['result_summary'], 'ok');
    });

    test('subagent events do not leak into other subagent blocks', () {
      var blocks = applyStreamEvent([], {
        'type': 'subagent_start',
        'data': {'agent_id': 'ag_1', 'parent_tool_use_id': 'tu_1'},
      });
      blocks = applyStreamEvent(blocks, {
        'type': 'subagent_start',
        'data': {'agent_id': 'ag_2', 'parent_tool_use_id': 'tu_2'},
      });
      blocks = applyStreamEvent(blocks, {
        'type': 'subagent_event',
        'data': {
          'parent_tool_use_id': 'tu_2',
          'child_event': {'text': 'only for ag_2'},
        },
      });
      expect(((blocks[0].raw['events'] as List?) ?? []).length, 0);
      expect((blocks[1].raw['events'] as List).length, 1);
    });
  });

  group('block identity', () {
    test('live projection assigns a unique _key to every block', () {
      var blocks = <ChatBlock>[];
      for (var i = 0; i < 12; i++) {
        blocks = applyStreamEvent(blocks, {'type': 'text_delta', 'data': {'text': 'a'}});
        blocks = applyStreamEvent(blocks, {
          'type': 'tool_call',
          'data': {'id': 't$i', 'name': 'Read', 'input': {}},
        });
        blocks = applyStreamEvent(blocks, {
          'type': 'tool_result',
          'data': {'id': 't$i', 'name': 'Read', 'output': 'x'},
        });
        blocks = applyStreamEvent(blocks, {'type': 'usage', 'data': {'input_tokens': 1}});
        blocks = applyStreamEvent(blocks, {
          'type': 'task_progress',
          'data': {
            'tasks': [
              {'id': 'a', 'title': 't'},
            ],
            'message': 'm',
          },
        });
        blocks = applyStreamEvent(blocks, {
          'type': 'error',
          'data': {'code': 'X', 'message': 'y'},
        });
      }
      final keys = blocks.map((b) => b.key).toList();
      expect(keys.toSet().length, keys.length, reason: 'keys must not collide: $keys');
      expect(blocks.where((b) => b.kind == 'usage'), isEmpty);
      // Keyed blocks keep their key across streaming text updates.
      var stream = applyStreamEvent(<ChatBlock>[], {'type': 'text_delta', 'data': {'text': 'x'}});
      final keyBefore = stream.single.key;
      stream = applyStreamEvent(stream, {'type': 'text_delta', 'data': {'text': 'y'}});
      expect(stream.single.text, 'xy');
      expect(stream.single.key, keyBefore);
    });

    test('chatBlocksFromTranscript keys are unique (same text, no ids)', () {
      final blocks = chatBlocksFromTranscript([
        {'kind': 'user', 'text': 'hi'},
        {'kind': 'user', 'text': 'hi'},
        {'kind': 'assistant_markdown', 'text': ''},
      ]);
      final keys = blocks.map((b) => b.key).toList();
      expect(keys.toSet().length, keys.length);
      expect(keys[0], 'h0');
      expect(keys[1], 'h1');
    });

    test('user_message echo preserves the local block identity', () {
      var blocks = <ChatBlock>[
        createLiveBlock('user', {'text': 'hi'}),
      ];
      final keyBefore = blocks.single.key;
      blocks = applyStreamEvent(blocks, {
        'type': 'user_message',
        'data': {'text': 'hi', 'id': 'srv_1'},
      });
      expect(blocks.single.kind, 'user');
      expect(blocks.single.key, keyBefore);
    });
  });

  test('tool_call_delta показывает placeholder вызова сразу (id-матчинг)', () {
    var blocks = <ChatBlock>[];
    blocks = applyStreamEvent(blocks, {
      'type': 'text_delta',
      'data': {'text': 'Распараллелю'},
    });
    blocks = applyStreamEvent(blocks, {
      'type': 'tool_call_delta',
      'data': {'id': 'call_1', 'name': 'agent_spawn', 'partial_json': ''},
    });
    // placeholder появился сразу — не ждём полного tool_call
    expect(blocks.where((b) => b.kind == 'tool_call'), hasLength(1));
    final placeholder = blocks.firstWhere((b) => b.kind == 'tool_call');
    // wire-имя agent_spawn → каноническое agent.spawn (лейблы «Субагент …»)
    expect(placeholder.raw['name'], 'agent.spawn');

    // повторные дельты не плодят блоки
    blocks = applyStreamEvent(blocks, {
      'type': 'tool_call_delta',
      'data': {'id': 'call_1', 'name': 'agent_spawn', 'partial_json': '{"task":"a'},
    });
    expect(blocks.where((b) => b.kind == 'tool_call'), hasLength(1));

    // полный tool_call с тем же id ЗАМЕНЯЕТ placeholder, не дублирует
    blocks = applyStreamEvent(blocks, {
      'type': 'tool_call',
      'data': {'id': 'call_1', 'name': 'agent.spawn', 'input': {'task': 'подбор', 'agent_type': 'default'}},
    });
    final calls = blocks.where((b) => b.kind == 'tool_call').toList();
    expect(calls, hasLength(1));
    expect(calls.first.raw['name'], 'agent.spawn');
    expect(calls.first.raw['input'], isNotEmpty);
  });

  test('tool_call без дельты работает как раньше (новый блок)', () {
    var blocks = applyStreamEvent([], {
      'type': 'tool_call',
      'data': {'id': 'call_9', 'name': 'mcp.openclaw.fs.list', 'input': {'path': '/tmp'}},
    });
    expect(blocks.where((b) => b.kind == 'tool_call'), hasLength(1));
  });
}
