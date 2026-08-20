import 'package:flutter/foundation.dart';

import 'package:prodavan/core/network/api_client.dart';
import 'package:prodavan/core/network/api_exception.dart';
import 'package:prodavan/core/session/session_context.dart';
import 'package:prodavan/core/session/session_store.dart';
import 'package:prodavan/shell/models.dart';

/// Application session and workspace state (cabinet → project chain).
class AppState extends ChangeNotifier {
  AppState({
    required SessionStore store,
    required SessionContext sessionContext,
    required ApiClient api,
    required AuthApi authApi,
    required CabinetsApi cabinetsApi,
    required ProjectsApi projectsApi,
    required CatalogsApi catalogsApi,
    required SpecsApi specsApi,
  })  : _store = store,
        _session = sessionContext,
        _api = api,
        _authApi = authApi,
        _cabinetsApi = cabinetsApi,
        _projectsApi = projectsApi,
        _catalogsApi = catalogsApi,
        _specsApi = specsApi;

  final SessionStore _store;
  final SessionContext _session;
  final ApiClient _api;
  final AuthApi _authApi;
  final CabinetsApi _cabinetsApi;
  final ProjectsApi _projectsApi;
  final CatalogsApi _catalogsApi;
  final SpecsApi _specsApi;

  bool _bootstrapped = false;
  bool _busy = false;
  String? _error;
  String? _userEmail;
  String? _tenantName;
  List<String> _cabinetIdsFromToken = [];
  List<CabinetItem> _cabinets = [];
  CabinetItem? _activeCabinet;
  Map<String, dynamic>? _manifestUi;
  Map<String, dynamic>? _capabilities;
  List<ProjectItem> _projects = [];
  ProjectItem? _activeProject;
  Map<String, dynamic>? _projectStats;
  String? _statusMessage;
  String? _lastRunId;
  String? _lastExportPath;
  String? _s4bState;

  bool get bootstrapped => _bootstrapped;
  bool get busy => _busy;
  String? get error => _error;
  String? get statusMessage => _statusMessage;
  bool get isAuthenticated => _session.accessToken != null && _session.accessToken!.isNotEmpty;
  String? get userEmail => _userEmail;
  String? get tenantName => _tenantName;
  List<CabinetItem> get cabinets => List.unmodifiable(_cabinets);
  CabinetItem? get activeCabinet => _activeCabinet;
  Map<String, dynamic>? get manifestUi => _manifestUi;
  Map<String, dynamic>? get capabilities => _capabilities;
  List<ProjectItem> get projects => List.unmodifiable(_projects);
  ProjectItem? get activeProject => _activeProject;
  Map<String, dynamic>? get projectStats => _projectStats;
  String? get lastRunId => _lastRunId;
  String? get lastExportPath => _lastExportPath;
  String? get s4bState => _s4bState;

  Future<void> bootstrap() async {
    _session.accessToken = _store.accessToken;
    _session.activeCabinetId = _store.activeCabinetId;
    if (_session.accessToken == null) {
      _bootstrapped = true;
      notifyListeners();
      return;
    }
    await _loadSession();
    _bootstrapped = true;
    notifyListeners();
  }

  Future<void> login({required String email, required String password}) async {
    await _run(() async {
      final data = await _authApi.login({'email': email, 'password': password});
      await _applyAuthResponse(data);
    });
  }

  Future<void> register({
    required String email,
    required String password,
    required String displayName,
    required String tenantSlug,
    required String tenantDisplayName,
  }) async {
    await _run(() async {
      final data = await _authApi.register({
        'email': email,
        'password': password,
        'display_name': displayName,
        'tenant_slug': tenantSlug,
        'tenant_display_name': tenantDisplayName,
      });
      await _applyAuthResponse(data);
    });
  }

  Future<void> logout() async {
    await _store.clear();
    _session.accessToken = null;
    _session.activeCabinetId = null;
    _userEmail = null;
    _tenantName = null;
    _cabinetIdsFromToken = [];
    _cabinets = [];
    _activeCabinet = null;
    _manifestUi = null;
    _capabilities = null;
    _projects = [];
    _activeProject = null;
    _projectStats = null;
    _statusMessage = null;
    _lastRunId = null;
    _lastExportPath = null;
    _s4bState = null;
    _error = null;
    notifyListeners();
  }

  Future<void> switchCabinet(CabinetItem cabinet) async {
    await _run(() async {
      await _setActiveCabinet(cabinet.id);
      final data = await _cabinetsApi.switchCabinet(cabinet.id);
      await _setAccessToken(data['access_token'] as String);
      _activeCabinet = cabinet;
      await _loadCabinetContext(cabinet.id);
      _activeProject = null;
      _projectStats = null;
      await _store.saveActiveProject(null);
      await _loadProjects();
    });
  }

  Future<void> createCabinet({
    required String slug,
    required String displayName,
    String profileId = 'electronics-procurement',
  }) async {
    await _run(() async {
      final data = await _cabinetsApi.create(
        slug: slug,
        displayName: displayName,
        profileId: profileId,
      );
      final cabinet = CabinetItem.fromJson(data);
      await _setActiveCabinet(cabinet.id);
      final switchData = await _cabinetsApi.switchCabinet(cabinet.id);
      await _setAccessToken(switchData['access_token'] as String);
      _activeCabinet = cabinet;
      await _loadCabinetContext(cabinet.id);
      _activeProject = null;
      _projectStats = null;
      await _store.saveActiveProject(null);
      await refreshCabinets();
      await _loadProjects();
    });
  }

  Future<void> refreshCabinets() async {
    if (!isAuthenticated || _session.activeCabinetId == null) return;
    final data = await _cabinetsApi.list();
    _cabinets = (data['items'] as List<dynamic>)
        .map((item) => CabinetItem.fromJson(item as Map<String, dynamic>))
        .toList();
    _activeCabinet = _cabinets.cast<CabinetItem?>().firstWhere(
          (c) => c?.id == _session.activeCabinetId,
          orElse: () => _cabinets.isNotEmpty ? _cabinets.first : null,
        );
    notifyListeners();
  }

  Future<void> createProject({
    required String slug,
    required String displayName,
  }) async {
    await _run(() async {
      final data = await _projectsApi.create(slug: slug, displayName: displayName);
      final project = ProjectItem.fromJson(data);
      await openProject(project);
      await _loadProjects();
    });
  }

  Future<void> openProject(ProjectItem project) async {
    await _run(() async {
      final data = await _projectsApi.open(project.id);
      await _setAccessToken(data['access_token'] as String);
      await _store.saveActiveProject(project.id);
      _activeProject = project;
      await _loadProjectStats(project.id);
    });
  }

  Future<void> uploadCatalog({
    required String filename,
    required List<int> bytes,
    required String slug,
    required String displayName,
  }) async {
    final cabinet = _activeCabinet;
    if (cabinet == null) {
      _error = 'Нет активного кабинета';
      notifyListeners();
      return;
    }
    await _run(() async {
      final data = await _catalogsApi.upload(
        cabinetId: cabinet.id,
        filename: filename,
        bytes: bytes,
        slug: slug,
        displayName: displayName,
      );
      _statusMessage =
          'Каталог ${data['status']}: ${data['stats']?['rows'] ?? 0} строк (on_order отброшены)';
    });
  }

  Future<void> uploadSpecAndRun({
    required String filename,
    required List<int> bytes,
  }) async {
    final project = _activeProject;
    if (project == null) {
      _error = 'Нет активного проекта';
      notifyListeners();
      return;
    }
    await _run(() async {
      final uploaded = await _specsApi.uploadInbox(
        projectId: project.id,
        filename: filename,
        bytes: bytes,
      );
      final runId = uploaded['run_id'] as String?;
      if (runId == null) {
        _statusMessage = 'Файл в inbox, прогон не создан';
        return;
      }
      _lastRunId = runId;
      for (final phase in SpecsApi.pipelineToReview) {
        await _specsApi.advance(
          projectId: project.id,
          runId: runId,
          targetPhase: phase,
        );
      }
      _statusMessage = 'Прогон $runId до review. Цены только из каталога/S4B in_stock.';
      await _loadProjectStats(project.id);
    });
  }

  Future<void> refreshS4bStatus() async {
    if (!isCapabilityEnabled('procurement.s4b')) {
      _s4bState = null;
      return;
    }
    try {
      final data = await _catalogsApi.s4bStatus();
      _s4bState = data['state'] as String?;
    } catch (_) {
      _s4bState = null;
    }
    notifyListeners();
  }

  Future<void> saveS4bCredentials({
    required String username,
    required String password,
  }) async {
    await _run(() async {
      final data = await _catalogsApi.putS4bCredentials(
        username: username,
        password: password,
      );
      _s4bState = data['state'] as String?;
      _statusMessage = _s4bState == 'credentials_valid'
          ? 'S4B подключён (ping ok). Пароль в ответах не возвращается.'
          : 'S4B не принял креды: ${data['last_error'] ?? data['state']}';
    });
  }

  Future<void> exportKp() async {
    final project = _activeProject;
    final runId = _lastRunId;
    if (project == null || runId == null) {
      _error = 'Нет прогона для КП';
      notifyListeners();
      return;
    }
    await _run(() async {
      final data = await _specsApi.exportKp(projectId: project.id, runId: runId);
      _lastExportPath = data['export_path'] as String?;
      _statusMessage =
          'КП: ${data['lines_filled']} строк с ценой, review ${data['lines_review']} · ${data['export_path']}';
    });
  }

  bool isCapabilityEnabled(String capability) {
    final caps = _capabilities;
    if (caps == null) return false;
    switch (capability) {
      case 'procurement.s4b':
      case 's4b':
        return caps['integrations']?['s4b']?['enabled'] == true;
      case 'procurement.kp':
      case 'specs_kp':
        return caps['modules']?['specs_kp']?['enabled'] == true;
      case 'procurement.equipment':
      case 'equipment_cards':
        return caps['modules']?['equipment_cards']?['enabled'] == true;
      default:
        return false;
    }
  }

  Future<void> _applyAuthResponse(Map<String, dynamic> data) async {
    await _setAccessToken(data['access_token'] as String);
    _userEmail = data['user']?['email'] as String?;
    final tenants = data['tenants'] as List<dynamic>? ?? [];
    if (tenants.isNotEmpty) {
      _tenantName = tenants.first['display_name'] as String?;
    }
    await _loadSession();
  }

  Future<void> _loadSession() async {
    final me = await _authApi.me();
    _userEmail = me['user']?['email'] as String?;
    _tenantName = me['tenant']?['display_name'] as String?;
    _cabinetIdsFromToken =
        (me['cabinet_ids'] as List<dynamic>? ?? []).map((id) => id.toString()).toList();

    var cabinetId = _store.activeCabinetId ?? _session.activeCabinetId;
    if (cabinetId == null && _cabinetIdsFromToken.isNotEmpty) {
      cabinetId = _cabinetIdsFromToken.first;
    }
    if (cabinetId != null) {
      await _setActiveCabinet(cabinetId);
      try {
        final switchData = await _cabinetsApi.switchCabinet(cabinetId);
        await _setAccessToken(switchData['access_token'] as String);
      } catch (_) {
        // keep token; header still set
      }
      await refreshCabinets();
      await _loadCabinetContext(cabinetId);
      await _loadProjects();
      final savedProject = _store.activeProjectId;
      if (savedProject != null) {
        final match = _projects.where((p) => p.id == savedProject).toList();
        if (match.isNotEmpty) {
          _activeProject = match.first;
          await _loadProjectStats(savedProject);
        }
      }
    }
  }

  Future<void> _loadCabinetContext(String cabinetId) async {
    final manifest = await _cabinetsApi.manifest(cabinetId);
    _manifestUi = manifest['ui'] as Map<String, dynamic>?;
    _capabilities = manifest['capabilities'] as Map<String, dynamic>?;
    _activeCabinet = _cabinets.cast<CabinetItem?>().firstWhere(
          (c) => c?.id == cabinetId,
          orElse: () => _activeCabinet,
        );
    await refreshS4bStatus();
  }

  Future<void> _loadProjects() async {
    final data = await _projectsApi.list();
    _projects = (data['items'] as List<dynamic>)
        .map((item) => ProjectItem.fromJson(item as Map<String, dynamic>))
        .toList();
    notifyListeners();
  }

  Future<void> _loadProjectStats(String projectId) async {
    _projectStats = await _projectsApi.stats(projectId);
    notifyListeners();
  }

  Future<void> _setAccessToken(String token) async {
    _session.accessToken = token;
    await _store.saveToken(token);
  }

  Future<void> _setActiveCabinet(String cabinetId) async {
    _session.activeCabinetId = cabinetId;
    await _store.saveActiveCabinet(cabinetId);
  }

  Future<void> _run(Future<void> Function() action) async {
    _busy = true;
    _error = null;
    _statusMessage = null;
    notifyListeners();
    try {
      await action();
    } on ApiException catch (exc) {
      _error = exc.message;
    } catch (exc) {
      _error = exc.toString();
    } finally {
      _busy = false;
      notifyListeners();
    }
  }

  @override
  void dispose() {
    _api.dispose();
    super.dispose();
  }
}

Future<AppState> createAppState() async {
  final store = await SessionStore.open();
  final session = SessionContext()
    ..accessToken = store.accessToken
    ..activeCabinetId = store.activeCabinetId;

  final api = ApiClient(
    tokenProvider: () => session.accessToken,
    cabinetIdProvider: () => session.activeCabinetId,
  );

  return AppState(
    store: store,
    sessionContext: session,
    api: api,
    authApi: AuthApi(api),
    cabinetsApi: CabinetsApi(api),
    projectsApi: ProjectsApi(api),
    catalogsApi: CatalogsApi(api),
    specsApi: SpecsApi(api),
  );
}
