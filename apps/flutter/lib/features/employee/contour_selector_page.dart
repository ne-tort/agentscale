import 'package:flutter/material.dart';

import 'package:prodavan/core/auth/token_session.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_icon_button.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/employee/cabinet_list_page.dart';
import 'package:prodavan/features/settings/open_app_settings.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Pick active company when employee has multiple memberships (L05).
class ContourSelectorPage extends StatelessWidget {
  const ContourSelectorPage({super.key, required this.me});

  final Map<String, dynamic> me;

  Future<void> _select(BuildContext context, String companyId) async {
    await tokenSession.setCompanyId(companyId);
    if (!context.mounted) return;
    Navigator.of(context).pushReplacement(
      MaterialPageRoute<void>(builder: (_) => const CabinetListPage()),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final memberships = me['employee']?['memberships'];
    final items = memberships is List ? memberships.cast<Map<String, dynamic>>() : <Map<String, dynamic>>[];

    return AppScaffold(
      title: Text(l10n.commonSelectCompany),
      actions: [
        AppIconButton(
          tooltip: l10n.settings,
          icon: Icons.settings_outlined,
          onPressed: () => openAppSettings(context),
        ),
      ],
      body: items.isEmpty
          ? EmptyPlaceholder(title: l10n.commonEmpty, icon: Icons.business_outlined)
          : ListView.separated(
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
