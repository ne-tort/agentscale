import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';

import 'package:prodavan/core/jobs/app_job_scope.dart';
import 'package:prodavan/core/jobs/app_job_store.dart';
import 'package:prodavan/core/settings/app_settings_controller.dart';
import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/features/auth/session_gate_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Platform entry — session restore → Sign in; locale + theme from settings.
class ProdavanApp extends StatefulWidget {
  const ProdavanApp({super.key});

  @override
  State<ProdavanApp> createState() => _ProdavanAppState();
}

class _ProdavanAppState extends State<ProdavanApp> {
  @override
  void initState() {
    super.initState();
    appSettings.load();
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: appSettings,
      builder: (context, _) {
        if (!appSettings.isLoaded) {
          return const MaterialApp(
            debugShowCheckedModeBanner: false,
            home: Scaffold(
              body: Center(child: CircularProgressIndicator()),
            ),
          );
        }
        return MaterialApp(
          onGenerateTitle: (context) => 'Prodavan',
          theme: AppTheme.forMode(appSettings.themeMode),
          locale: appSettings.locale,
          supportedLocales: AppLocalizations.supportedLocales,
          localizationsDelegates: const [
            AppLocalizations.delegate,
            GlobalMaterialLocalizations.delegate,
            GlobalWidgetsLocalizations.delegate,
            GlobalCupertinoLocalizations.delegate,
          ],
          home: const SessionGatePage(),
          debugShowCheckedModeBanner: kDebugMode,
          builder: (context, child) {
            return AppJobScope(
              store: appJobStore,
              child: child ?? const SizedBox.shrink(),
            );
          },
        );
      },
    );
  }
}
