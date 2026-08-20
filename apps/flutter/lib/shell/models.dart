class CabinetItem {
  const CabinetItem({
    required this.id,
    required this.slug,
    required this.displayName,
    required this.profileId,
    required this.status,
    required this.capabilities,
  });

  final String id;
  final String slug;
  final String displayName;
  final String? profileId;
  final String status;
  final Map<String, dynamic> capabilities;

  factory CabinetItem.fromJson(Map<String, dynamic> json) {
    return CabinetItem(
      id: json['id'] as String,
      slug: json['slug'] as String,
      displayName: json['display_name'] as String,
      profileId: json['profile_id'] as String?,
      status: json['status'] as String,
      capabilities: (json['capabilities'] as Map<String, dynamic>?) ?? {},
    );
  }

  bool get hasS4b =>
      capabilities['integrations']?['s4b']?['enabled'] == true;
}

class ProjectItem {
  const ProjectItem({
    required this.id,
    required this.slug,
    required this.displayName,
    required this.status,
    required this.workspaceKey,
    this.stats,
  });

  final String id;
  final String slug;
  final String displayName;
  final String status;
  final String workspaceKey;
  final Map<String, dynamic>? stats;

  factory ProjectItem.fromJson(Map<String, dynamic> json) {
    return ProjectItem(
      id: json['id'] as String,
      slug: json['slug'] as String,
      displayName: json['display_name'] as String,
      status: json['status'] as String,
      workspaceKey: json['workspace_key'] as String,
      stats: json['stats'] as Map<String, dynamic>?,
    );
  }

  int get inboxPending => (stats?['inbox_pending'] as num?)?.toInt() ?? 0;
}
