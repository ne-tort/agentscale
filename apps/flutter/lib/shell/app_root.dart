import 'package:flutter/material.dart';

import 'package:prodavan/features/admin/presentation/screens/admin_shell_screen.dart';
import 'package:prodavan/features/auth/presentation/screens/login_screen.dart';
import 'package:prodavan/shell/app_state.dart';
import 'package:prodavan/shell/cabinet_shell_screen.dart';

class AppRoot extends StatefulWidget {
  const AppRoot({super.key, required this.appState});

  final AppState appState;

  @override
  State<AppRoot> createState() => _AppRootState();
}

class _AppRootState extends State<AppRoot> {
  @override
  void initState() {
    super.initState();
    widget.appState.addListener(_onStateChanged);
  }

  @override
  void dispose() {
    widget.appState.removeListener(_onStateChanged);
    super.dispose();
  }

  void _onStateChanged() => setState(() {});

  @override
  Widget build(BuildContext context) {
    final state = widget.appState;
    if (!state.bootstrapped) {
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }
    if (!state.isAuthenticated) {
      return const LoginScreen();
    }
    if (state.isPlatformAdmin) {
      return const AdminShellScreen();
    }
    return const CabinetShellScreen();
  }
}
