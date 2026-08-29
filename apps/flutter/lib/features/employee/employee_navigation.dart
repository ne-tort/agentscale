import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/features/employee/cabinet_picker_page.dart';
import 'package:prodavan/features/employee/cabinet_shell.dart';

/// Route employee to cabinet picker or auto-enter single cabinet.
Future<void> navigateToEmployeeCabinets(BuildContext context) async {
  final cabinets = await workContext.api.listCabinets();
  if (!context.mounted) return;
  if (cabinets.length == 1) {
    final c = cabinets.first;
    final id = c['id'] as String;
    final name = c['name'] as String? ?? id;
    workContext.enterCabinet(id);
    Navigator.of(context).pushReplacement(
      MaterialPageRoute<void>(
        builder: (_) => CabinetShell(cabinetId: id, cabinetName: name),
      ),
    );
    return;
  }
  Navigator.of(context).pushReplacement(
    MaterialPageRoute<void>(builder: (_) => const CabinetPickerPage()),
  );
}

/// Open cabinet shell (switch cabinet from settings).
void openCabinetShell(
  BuildContext context, {
  required String cabinetId,
  required String cabinetName,
}) {
  workContext.enterCabinet(cabinetId);
  Navigator.of(context).pushAndRemoveUntil(
    MaterialPageRoute<void>(
      builder: (_) => CabinetShell(cabinetId: cabinetId, cabinetName: cabinetName),
    ),
    (_) => false,
  );
}
