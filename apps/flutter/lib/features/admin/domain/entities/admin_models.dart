class AdminUser {
  const AdminUser({
    required this.id,
    required this.loginId,
    required this.companyName,
    required this.status,
    this.contactPerson,
    this.phone,
    this.email,
    this.tenantSlug,
  });

  final String id;
  final String loginId;
  final String companyName;
  final String status;
  final String? contactPerson;
  final String? phone;
  final String? email;
  final String? tenantSlug;

  factory AdminUser.fromJson(Map<String, dynamic> json) {
    return AdminUser(
      id: json['id'].toString(),
      loginId: json['login_id'] as String? ?? '',
      companyName: json['company_name'] as String? ?? '',
      status: json['status'] as String? ?? 'active',
      contactPerson: json['contact_person'] as String?,
      phone: json['phone'] as String?,
      email: json['email'] as String?,
      tenantSlug: json['tenant_slug'] as String?,
    );
  }
}

class AdminStats {
  const AdminStats({
    required this.usersTotal,
    required this.usersByStatus,
    required this.tenantsTotal,
    required this.cabinetsTotal,
    required this.projectsTotal,
    required this.runsTotal,
  });

  final int usersTotal;
  final Map<String, int> usersByStatus;
  final int tenantsTotal;
  final int cabinetsTotal;
  final int projectsTotal;
  final int runsTotal;

  factory AdminStats.fromJson(Map<String, dynamic> json) {
    final raw = json['users_by_status'] as Map<String, dynamic>? ?? {};
    return AdminStats(
      usersTotal: json['users_total'] as int? ?? 0,
      usersByStatus: raw.map((k, v) => MapEntry(k, (v as num).toInt())),
      tenantsTotal: json['tenants_total'] as int? ?? 0,
      cabinetsTotal: json['cabinets_total'] as int? ?? 0,
      projectsTotal: json['projects_total'] as int? ?? 0,
      runsTotal: json['runs_total'] as int? ?? 0,
    );
  }
}
