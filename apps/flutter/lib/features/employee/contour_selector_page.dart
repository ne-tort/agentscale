import 'package:flutter/material.dart';

import 'package:prodavan/core/auth/session_store.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/features/employee/cabinet_list_page.dart';

/// Pick active company when employee has multiple memberships (L05).
class ContourSelectorPage extends StatelessWidget {
  const ContourSelectorPage({super.key, required this.me});

  final Map<String, dynamic> me;

  Future<void> _select(BuildContext context, String companyId) async {
    workContext.companyId = companyId;
    await sessionStore.save(
      baseUrl: workContext.baseUrl,
      bearerToken: workContext.bearerToken,
      companyId: companyId,
    );
    if (!context.mounted) return;
    Navigator.of(context).pushReplacement(
      MaterialPageRoute<void>(builder: (_) => const CabinetListPage()),
    );
  }

  @override
  Widget build(BuildContext context) {
    final memberships = me['employee']?['memberships'];
    final items = memberships is List ? memberships.cast<Map<String, dynamic>>() : const <Map<String, dynamic>>[];

    return AppScaffold(
      title: const Text('Select company'),
      body: ListView.separated(
        padding: const EdgeInsets.all(AppSpacing.lg),
        itemCount: items.length,
        separatorBuilder: (_, __) => const Divider(height: 1),
        itemBuilder: (context, index) {
          final m = items[index];
          final companyId = m['company_id'] as String? ?? '';
          final companyName = (m['company_name'] as String?)?.trim();
          final role = m['role'] as String? ?? '';
          return ListTile(
            title: Text(
              (companyName != null && companyName.isNotEmpty) ? companyName : companyId,
            ),
            subtitle: Text(role.isEmpty ? companyId : '$role · $companyId'),
            onTap: companyId.isEmpty ? null : () => _select(context, companyId),
          );
        },
      ),
    );
  }
}
