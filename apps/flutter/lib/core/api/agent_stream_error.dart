/// Agent-runtime SSE error payload from `/chat/stream`.
class AgentStreamError implements Exception {
  const AgentStreamError(this.data);

  final Map<String, dynamic> data;

  String? get code => data['code']?.toString();
  String? get message => data['message']?.toString();

  @override
  String toString() {
    final c = code;
    final m = message;
    if (c != null && c.isNotEmpty && m != null && m.isNotEmpty) {
      return 'AgentStreamError($c): $m';
    }
    if (c != null && c.isNotEmpty) return 'AgentStreamError($c)';
    if (m != null && m.isNotEmpty) return 'AgentStreamError: $m';
    return 'AgentStreamError';
  }
}
