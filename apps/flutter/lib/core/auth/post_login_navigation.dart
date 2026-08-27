import 'package:flutter/material.dart';

import 'package:prodavan/core/auth/token_session.dart';
import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/features/admin/admin_shell.dart';
import 'package:prodavan/features/company/company_shell.dart';
import 'package:prodavan/features/employee/cabinet_list_page.dart';
import 'package:prodavan/features/employee/contour_selector_page.dart';

/// Route after successful `/me` based on Keycloak roles / contours.
Future<void> navigateAfterMe(BuildContext context, Map<String, dynamic> me) async {
  if (!context.mounted) return;

  final contours = me['contours'];
  final memberships = me['employee']?['memberships'];
  final company = me['company'];
  final employee = me['employee'];
  final isPlatformAdmin =
      contours is List && contours.contains('platform_admin');
  final hasMemberships = memberships is List && memberships.isNotEmpty;
  final isCompanyPrincipal =
      company is Map && employee == null && contours is List && contours.contains('company');

  if (isPlatformAdmin && !hasMemberships) {
    adminContext.setSession(
      baseUrl: workContext.baseUrl,
      bearerToken: workContext.bearerToken,
    );
    if (!context.mounted) return;
    Navigator.of(context).pushReplacement(
      MaterialPageRoute<void>(builder: (_) => const AdminShell()),
    );
    return;
  }

  if (isCompanyPrincipal) {
    final companyId = company['id'] as String?;
    final companyName = company['name'] as String? ?? companyId ?? '';
    if (companyId != null) {
      companyContext.setSession(
        baseUrl: workContext.baseUrl,
        bearerToken: workContext.bearerToken,
      );
      companyContext.selectCompany(id: companyId, name: companyName);
      await tokenSession.setCompanyId(companyId);
    }
    if (!context.mounted) return;
    Navigator.of(context).pushReplacement(
      MaterialPageRoute<void>(builder: (_) => const CompanyShell()),
    );
    return;
  }

  if (memberships is List && memberships.length == 1) {
    final companyId = memberships.first['company_id'] as String?;
    if (companyId != null) {
      await tokenSession.setCompanyId(companyId);
    }
  }

  if (!context.mounted) return;
  final companyId = tokenSession.companyId;
  if (companyId == null && memberships is List && memberships.length > 1) {
    Navigator.of(context).pushReplacement(
      MaterialPageRoute<void>(builder: (_) => ContourSelectorPage(me: me)),
    );
    return;
  }

  Navigator.of(context).pushReplacement(
    MaterialPageRoute<void>(builder: (_) => const CabinetListPage()),
  );
}
