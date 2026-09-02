/// Typed chat block from transcript API or live SSE projection.
class ChatBlock {
  ChatBlock({required this.kind, required this.raw});

  final String kind;
  final Map<String, dynamic> raw;

  String get id => raw['id'] as String? ?? '';

  String get text => raw['text'] as String? ?? '';

  bool get isStreaming => raw['_streaming'] == true;

  ChatBlock copyWithRaw(Map<String, dynamic> patch) {
    return ChatBlock(kind: kind, raw: {...raw, ...patch});
  }

  static ChatBlock fromJson(Map<String, dynamic> json) {
    return ChatBlock(
      kind: json['kind'] as String? ?? 'unknown',
      raw: Map<String, dynamic>.from(json),
    );
  }

  Map<String, dynamic> toJson() => Map<String, dynamic>.from(raw)..['kind'] = kind;
}

/// In-flight assistant stream state for a single turn.
class ChatTurnState {
  ChatTurnState({this.userBlock, this.blocks = const [], this.cancelled = false});

  ChatBlock? userBlock;
  List<ChatBlock> blocks;
  bool cancelled;

  ChatTurnState copyWith({
    ChatBlock? userBlock,
    List<ChatBlock>? blocks,
    bool? cancelled,
  }) {
    return ChatTurnState(
      userBlock: userBlock ?? this.userBlock,
      blocks: blocks ?? this.blocks,
      cancelled: cancelled ?? this.cancelled,
    );
  }
}
