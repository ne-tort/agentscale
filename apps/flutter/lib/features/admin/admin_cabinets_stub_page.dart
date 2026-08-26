import 'package:flutter/material.dart';

import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Admin Cabinets tab — stub until cabinets admin surface ships.
class AdminCabinetsStubPage extends StatelessWidget {
  const AdminCabinetsStubPage({super.key, this.embedded = false});

  final bool embedded;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      body: EmptyPlaceholder(
        title: l10n.adminCabinetsComingSoonTitle,
        subtitle: l10n.adminCabinetsComingSoonHint,
      ),
    );
  }
}
