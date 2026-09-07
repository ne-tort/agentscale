/// Format `project_ids` JSON column for collection cells.
/// Empty / null → «Все» / «All»; otherwise count of ids.
String formatProjectIdsCell(dynamic value, {bool english = false}) {
  final allLabel = english ? 'All' : 'Все';
  if (value == null) return allLabel;
  if (value is! List) return value.toString();
  if (value.isEmpty) return allLabel;
  return '${value.length}';
}

bool isProjectIdsColumn(String field) => field == 'project_ids';
