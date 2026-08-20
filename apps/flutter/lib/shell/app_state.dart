import 'package:flutter/material.dart';

import 'package:prodavan/core/errors/error_mapper.dart';
import 'package:prodavan/core/errors/ui_messenger.dart';
import 'package:prodavan/core/network/api_client.dart';
import 'package:prodavan/core/network/api_exception.dart';
import 'package:prodavan/core/session/session_context.dart';
import 'package:prodavan/core/session/session_store.dart';
import 'package:prodavan/features/admin/data/datasources/admin_remote_datasource.dart';
import 'package:prodavan/features/admin/domain/entities/admin_models.dart';
import 'package:prodavan/features/auth/data/datasources/auth_remote_datasource.dart';
import 'package:prodavan/features/auth/data/repositories/auth_repository_impl.dart';
import 'package:prodavan/features/auth/domain/entities/user_session.dart';
import 'package:prodavan/shell/models.dart';

/// Application session and workspace state (cabinet → project chain).
class AppState extends ChangeNotifier {
  AppState({
    required SessionStore store,
    required SessionContext sessionContext,
    required ApiClient api,
    required AuthApi authApi,
    required AuthRepositoryImpl authRepository,
    required AdminRemoteDataSource adminApi,
    required CabinetsApi cabinetsApi,
    required ProjectsApi projectsApi,
    required CatalogsApi catalogsApi,
    required SpecsApi specsApi,
    UiMessenger? uiMessenger,
  })  : _store = store,
        _session = sessionContext,
        _api = api,
        _authApi = authApi,
        _authRepository = authRepository,
        _adminApi = adminApi,
        _cabinetsApi = cabinetsApi,
        _projectsApi = projectsApi,
        _catalogsApi = catalogsApi,
        _specsApi = specsApi,
        uiMessenger = uiMessenger ?? UiMessenger();

  final SessionStore _store;
  final SessionContext _session;
  final ApiClient _api;
  final AuthApi _authApi;
  final AuthRepositoryImpl _authRepository;
  final AdminRemoteDataSource _adminApi;
  final CabinetsApi _cabinetsApi;
  final ProjectsApi _projectsApi;
  final CatalogsApi _catalogsApi;
  final SpecsApi _specsApi;
  final UiMessenger uiMessenger;
  final GlobalKey<ScaffoldMessengerState> scaffoldMessengerKey =
      GlobalKey<ScaffoldMessengerState>();

  /// Exposed for debug screens (runs / variants).
  SpecsApi get specsApi => _specsApi;

  bool _bootstrapped = false;
  bool _busy = false;
  bool _refreshInFlight = false;
  String? _error;
  UserSession? _currentUser;
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
  UserSession? get currentUser => _currentUser;
  bool get isPlatformAdmin => _currentUser?.isPlatformAdmin ?? false;
  @Deprecated('Use currentUser.email')
  String? get userEmail => _currentUser?.email;
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
    _session.refreshToken = _store.refreshToken;
    _session.activeCabinetId = _store.activeCabinetId;
    if (_session.accessToken == null) {
      _bootstrapped = true;
      notifyListeners();
      return;
    }
    try {
      await _loadSession();
    } on ApiException catch (_) {
      final ok = await tryRefreshTokens();
      if (ok) {
        await _loadSession();
      } else {
        await logout();
      }
    }
    _bootstrapped = true;
    notifyListeners();
  }

  /// Exchange refresh_token → new access (+ rotated refresh). Returns false if logged out.
  Future<bool> tryRefreshTokens() async {
    final refresh = _session.refreshToken ?? _store.refreshToken;
    if (refresh == null || refresh.isEmpty || _refreshInFlight) {
      return false;
    }
    _refreshInFlight = true;
    try {
      final data = await _authApi.refresh(refresh);
      await _setTokens(
        accessToken: data['access_token'] as String,
        refreshToken: data['refresh_token'] as String?,
      );
      return true;
    } catch (_) {
      return false;
    } finally {
      _refreshInFlight = false;
    }
  }

  Future<void> login({required String loginId, required String password}) async {
    await _run(() async {
      await _authRepository.login(loginId: loginId, password: password);
      final data = _authRepository.lastAuthResponse!;
      await _applyAuthResponse(data);
    });
  }

  Future<void> updateProfile({
    String? contactPerson,
    String? phone,
    String? email,
  }) async {
    await _run(() async {
      _currentUser = await _authRepository.updateProfile(
        contactPerson: contactPerson,
        phone: phone,
        email: email,
      );
      uiMessenger.showSuccess('Профиль сохранён');
    });
  }

  Future<void> changePassword({
    required String currentPassword,
    required String newPassword,
  }) async {
    await _run(() async {
      await _authRepository.changePassword(
        currentPassword: currentPassword,
        newPassword: newPassword,
      );
      uiMessenger.showSuccess('Пароль изменён');
    });
  }

  Future<AdminStats> loadAdminStats() async {
    final data = await _adminApi.stats();
    return AdminStats.fromJson(data);
  }

  Future<List<AdminUser>> loadAdminUsers() async {
    final rows = await _adminApi.listUsers();
    return rows
        .map((e) => AdminUser.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<AdminUser> loadAdminUser(String id) async {
    final data = await _adminApi.getUser(id);
    return AdminUser.fromJson(data);
  }

  Future<bool> createAdminUser({
    required String loginId,
    required String companyName,
    required String password,
    String? contactPerson,
    String? phone,
    String? email,
  }) async {
    var ok = false;
    await _run(() async {
      await _adminApi.createUser({
        'login_id': loginId,
        'company_name': companyName,
        'password': password,
        if (contactPerson != null) 'contact_person': contactPerson,
        if (phone != null) 'phone': phone,
        if (email != null) 'email': email,
      });
      uiMessenger.showSuccess('Пользователь создан');
      ok = true;
    });
    return ok;
  }

  Future<void> updateAdminUser(String id, {required String status}) async {
    await _run(() async {
      await _adminApi.updateUser(id, {'status': status});
    });
  }

  Future<void> deleteAdminUser(String id) async {
    await _run(() async {
      await _adminApi.deleteUser(id);
    });
  }

  Future<void> logout() async {
    await _store.clear();
    _session.accessToken = null;
    _session.refreshToken = null;
    _session.activeCabinetId = null;
    _currentUser = null;
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
    await _setTokens(
      accessToken: data['access_token'] as String,
      refreshToken: data['refresh_token'] as String?,
    );
    final userJson = data['user'] as Map<String, dynamic>?;
    if (userJson != null) {
      _currentUser = UserSession.fromJson(userJson);
    }
    final tenants = data['tenants'] as List<dynamic>? ?? [];
    if (tenants.isNotEmpty) {
      _tenantName = tenants.first['display_name'] as String?;
    }
    await _loadSession();
  }

  Future<void> _loadSession() async {
    final me = await _authApi.me();
    final userJson = me['user'] as Map<String, dynamic>?;
    if (userJson != null) {
      _currentUser = UserSession.fromJson(userJson);
    }
    _tenantName = me['tenant']?['display_name'] as String?;
    _cabinetIdsFromToken =
        (me['cabinet_ids'] as List<dynamic>? ?? []).map((id) => id.toString()).toList();

    if (_currentUser?.isPlatformAdmin == true) {
      return;
    }

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

  Future<void> _setTokens({required String accessToken, String? refreshToken}) async {
    _session.accessToken = accessToken;
    if (refreshToken != null) {
      _session.refreshToken = refreshToken;
    }
    await _store.saveTokens(accessToken: accessToken, refreshToken: refreshToken);
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
      final failure = ErrorMapper.fromApiException(exc);
      _error = failure.message;
      uiMessenger.showFailure(failure);
    } catch (exc) {
      final failure = ErrorMapper.fromUnknown(exc);
      _error = failure.message;
      uiMessenger.showFailure(failure);
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
    ..refreshToken = store.refreshToken
    ..activeCabinetId = store.activeCabinetId;

  final api = ApiClient(
    tokenProvider: () => session.accessToken,
    cabinetIdProvider: () => session.activeCabinetId,
  );
  final authApi = AuthApi(api);
  final authRepository = AuthRepositoryImpl(AuthRemoteDataSource(authApi, api));

  final state = AppState(
    store: store,
    sessionContext: session,
    api: api,
    authApi: authApi,
    authRepository: authRepository,
    adminApi: AdminRemoteDataSource(api),
    cabinetsApi: CabinetsApi(api),
    projectsApi: ProjectsApi(api),
    catalogsApi: CatalogsApi(api),
    specsApi: SpecsApi(api),
  );
  api.onUnauthorized = state.tryRefreshTokens;
  return state;
}
