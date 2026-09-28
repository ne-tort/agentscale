/// Typed chat block from transcript API or live SSE projection.
class ChatBlock {
  ChatBlock({required this.kind, required this.raw});

  final String kind;
  final Map<String, dynamic> raw;

  String get id => raw['id'] as String? ?? '';

  /// Stable widget-key identity.
  ///
  /// Blocks created by the live projection ([applyStreamEvent]) or the
  /// transcript loader ([chatBlocksFromTranscript]) always carry a unique
  /// `_key`. Anything else falls back to its wire id, then kind — callers
  /// building ad-hoc blocks (tests) may collide there, so list keying in
  /// production must rely on the assigned `_key` only.
  String get key => raw['_key'] as String? ?? (id.isNotEmpty ? id : kind);

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
