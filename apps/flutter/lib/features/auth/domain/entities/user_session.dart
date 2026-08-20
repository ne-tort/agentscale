enum UserRole {
  platformAdmin,
  user;

  static UserRole fromApi(String? raw) {
    if (raw == 'platform.admin') return UserRole.platformAdmin;
    return UserRole.user;
  }

  String get apiValue =>
      this == UserRole.platformAdmin ? 'platform.admin' : 'user';
}

class UserSession {
  const UserSession({
    required this.id,
    required this.loginId,
    required this.companyName,
    required this.role,
    required this.status,
    this.contactPerson,
    this.phone,
    this.email,
  });

  final String id;
  final String loginId;
  final String companyName;
  final String? contactPerson;
  final String? phone;
  final String? email;
  final UserRole role;
  final String status;

  factory UserSession.fromJson(Map<String, dynamic> json) {
    return UserSession(
      id: json['id'].toString(),
      loginId: json['login_id'] as String? ?? '',
      companyName: json['company_name'] as String? ?? '',
      contactPerson: json['contact_person'] as String?,
      phone: json['phone'] as String?,
      email: json['email'] as String?,
      role: UserRole.fromApi(json['role'] as String?),
      status: json['status'] as String? ?? 'active',
    );
  }

  bool get isPlatformAdmin => role == UserRole.platformAdmin;
}
