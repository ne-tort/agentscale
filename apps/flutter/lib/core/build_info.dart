/// Build identity injected at compile time for Flutter web cache-bust / support.
///
/// CI / Dockerfile pass:
///   --dart-define=BUILD_ID=<gitsha12>
///   --build-name=1.0.0 --build-number=<run_or_sha>
library;

const String buildId = String.fromEnvironment('BUILD_ID', defaultValue: 'dev');
const String buildName = String.fromEnvironment(
  'BUILD_NAME',
  defaultValue: '1.0.0',
);
const String buildNumber = String.fromEnvironment(
  'BUILD_NUMBER',
  defaultValue: '0',
);

String get buildLabel {
  final id = buildId.length > 12 ? buildId.substring(0, 12) : buildId;
  return '$buildName+$buildNumber · $id';
}
