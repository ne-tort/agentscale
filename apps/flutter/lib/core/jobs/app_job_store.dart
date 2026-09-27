import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// In-process + persisted job tracker (survives navigation and page reload).
class AppJobStore extends ChangeNotifier {
  AppJobStore({SharedPreferences? prefs}) : _prefsOverride = prefs;

  static const _prefsKey = 'app_jobs_v1';

  final SharedPreferences? _prefsOverride;
  final Map<String, AppJob> _jobs = <String, AppJob>{};
  final Map<String, Completer<AppJob>> _completers = <String, Completer<AppJob>>{};
  final Set<String> _activeRunners = <String>{};
  Future<void>? _hydrateFuture;
  bool _hydrated = false;

  /// Persist debounce: poll subtitles tick every 1-2s and must not hammer
  /// SharedPreferences on every notify. Phase changes flush immediately.
  static const _persistMinGap = Duration(seconds: 2);
  Timer? _persistTimer;
  DateTime _lastPersistAt = DateTime.fromMillisecondsSinceEpoch(0);

  List<AppJob> get jobs => List<AppJob>.unmodifiable(_jobs.values);

  bool get isHydrated => _hydrated;

  @override
  void dispose() {
    _persistTimer?.cancel();
    _persistTimer = null;
    super.dispose();
  }

  AppJob? byId(String id) => _jobs[id];

  AppJob? bySubject({required String kind, required String subjectId}) {
    for (final job in _jobs.values) {
      if (job.kind == kind && job.subjectId == subjectId) return job;
    }
    return null;
  }

  /// Any lifecycle job (launch/reload/resume/sync) for [subjectId].
  AppJob? lifecycleFor(String subjectId) {
    AppJob? best;
    for (final job in _jobs.values) {
      if (job.subjectId != subjectId) continue;
      if (!AppJobKinds.isLifecycle(job.kind)) continue;
      if (job.status != AppJobStatus.running) continue;
      best = job;
      break;
    }
    return best;
  }

  bool isActive({required String kind, required String subjectId}) {
    final job = bySubject(kind: kind, subjectId: subjectId);
    return job != null && job.status == AppJobStatus.running && !job.isExpired;
  }

  bool isLifecycleActive(String subjectId) {
    final job = lifecycleFor(subjectId);
    return job != null && !job.isExpired;
  }

  Future<void> ensureHydrated() {
    return _hydrateFuture ??= _hydrate();
  }

  Future<void> _hydrate() async {
    try {
      final prefs = _prefsOverride ?? await SharedPreferences.getInstance();
      final raw = prefs.getString(_prefsKey);
      if (raw != null && raw.isNotEmpty) {
        final decoded = jsonDecode(raw);
        if (decoded is List) {
          for (final item in decoded) {
            if (item is! Map) continue;
            final job = AppJob.fromJson(Map<String, dynamic>.from(item));
            if (job.status != AppJobStatus.running) continue;
            if (job.isExpired) {
              _jobs[job.id] = job.copyWith(
                status: AppJobStatus.failed,
                error: 'timeout',
                updatedAt: DateTime.now(),
              );
            } else {
              _jobs[job.id] = job;
              _completers.putIfAbsent(job.id, Completer<AppJob>.new);
            }
          }
        }
      }
    } catch (e, st) {
      debugPrint('AppJobStore hydrate failed: $e\n$st');
    }
    _hydrated = true;
    notifyListeners();
    await _persist();
  }

  /// Start a job, or attach [run] to a restored running job without a runner.
  ///
  /// Always returns a Future that completes when the job finishes (succeeded/failed).
  Future<AppJob> start({
    required String kind,
    required String subjectId,
    required String title,
    String? subtitle,
    Duration timeout = const Duration(minutes: 12),
    required Future<void> Function(AppJobController ctrl) run,
  }) async {
    await ensureHydrated();

    final existing = bySubject(kind: kind, subjectId: subjectId);
    if (existing != null && existing.status == AppJobStatus.running) {
      if (existing.isExpired) {
        _finish(existing.id, AppJobStatus.failed, error: 'timeout');
      } else if (_activeRunners.contains(existing.id)) {
        return _completers[existing.id]!.future;
      } else {
        return _attachRunner(existing, run);
      }
    }

    final id = '${kind}_$subjectId';
    final now = DateTime.now();
    final job = AppJob(
      id: id,
      kind: kind,
      subjectId: subjectId,
      title: title,
      subtitle: subtitle ?? '',
      status: AppJobStatus.running,
      startedAt: now,
      deadlineAt: now.add(timeout),
      updatedAt: now,
    );
    _jobs[id] = job;
    final completer = Completer<AppJob>();
    _completers[id] = completer;
    notifyListeners();
    _schedulePersist();
    return _attachRunner(job, run);
  }

  Future<AppJob> _attachRunner(
    AppJob job,
    Future<void> Function(AppJobController ctrl) run,
  ) {
    final id = job.id;
    final completer = _completers.putIfAbsent(id, Completer<AppJob>.new);
    if (_activeRunners.contains(id)) {
      return completer.future;
    }
    _activeRunners.add(id);
    final ctrl = AppJobController._(this, id);

    unawaited(() async {
      try {
        if (job.isExpired) {
          throw TimeoutException('job deadline exceeded', job.deadlineAt.difference(job.startedAt));
        }
        await run(ctrl).timeout(job.remaining);
        _finish(id, AppJobStatus.succeeded);
      } on TimeoutException catch (e) {
        _finish(id, AppJobStatus.failed, error: e.message ?? 'timeout');
      } catch (e) {
        _finish(id, AppJobStatus.failed, error: e.toString());
      } finally {
        _activeRunners.remove(id);
      }
    }());

    return completer.future;
  }

  void updateSubtitle(String id, String subtitle) {
    final job = _jobs[id];
    if (job == null || job.status != AppJobStatus.running) return;
    if (job.subtitle == subtitle) return;
    _jobs[id] = job.copyWith(subtitle: subtitle, updatedAt: DateTime.now());
    notifyListeners();
    _schedulePersist();
  }

  void clear(String id) {
    _jobs.remove(id);
    final c = _completers.remove(id);
    if (c != null && !c.isCompleted) {
      c.completeError(StateError('job cleared'));
    }
    notifyListeners();
    _schedulePersist(immediate: true);
  }

  void clearSubject({required String kind, required String subjectId}) {
    final job = bySubject(kind: kind, subjectId: subjectId);
    if (job != null) clear(job.id);
  }

  void _finish(String id, AppJobStatus status, {String? error}) {
    final job = _jobs[id];
    if (job == null) return;
    final next = job.copyWith(
      status: status,
      error: error,
      updatedAt: DateTime.now(),
    );
    _jobs[id] = next;
    final completer = _completers[id];
    if (completer != null && !completer.isCompleted) {
      completer.complete(next);
    }
    notifyListeners();
    _schedulePersist(immediate: true);
  }

  /// Debounced persist: at most one write per [_persistMinGap] unless
  /// [immediate] (phase change: start / finish / clear).
  void _schedulePersist({bool immediate = false}) {
    _persistTimer?.cancel();
    _persistTimer = null;
    if (immediate ||
        DateTime.now().difference(_lastPersistAt) >= _persistMinGap) {
      unawaited(_persist());
      return;
    }
    _persistTimer = Timer(_persistMinGap, () {
      _persistTimer = null;
      unawaited(_persist());
    });
  }

  Future<void> _persist() async {
    _lastPersistAt = DateTime.now();
    try {
      final prefs = _prefsOverride ?? await SharedPreferences.getInstance();
      final running = _jobs.values
          .where((j) => j.status == AppJobStatus.running && !j.isExpired)
          .map((j) => j.toJson())
          .toList();
      await prefs.setString(_prefsKey, jsonEncode(running));
    } catch (e, st) {
      debugPrint('AppJobStore persist failed: $e\n$st');
    }
  }
}

enum AppJobStatus { running, succeeded, failed }

class AppJob {
  const AppJob({
    required this.id,
    required this.kind,
    required this.subjectId,
    required this.title,
    required this.subtitle,
    required this.status,
    required this.startedAt,
    required this.deadlineAt,
    required this.updatedAt,
    this.error,
  });

  final String id;
  final String kind;
  final String subjectId;
  final String title;
  final String subtitle;
  final AppJobStatus status;
  final DateTime startedAt;
  final DateTime deadlineAt;
  final DateTime updatedAt;
  final String? error;

  bool get isExpired => DateTime.now().isAfter(deadlineAt);

  Duration get remaining {
    final left = deadlineAt.difference(DateTime.now());
    return left.isNegative ? Duration.zero : left;
  }

  AppJob copyWith({
    String? title,
    String? subtitle,
    AppJobStatus? status,
    DateTime? updatedAt,
    String? error,
  }) {
    return AppJob(
      id: id,
      kind: kind,
      subjectId: subjectId,
      title: title ?? this.title,
      subtitle: subtitle ?? this.subtitle,
      status: status ?? this.status,
      startedAt: startedAt,
      deadlineAt: deadlineAt,
      updatedAt: updatedAt ?? this.updatedAt,
      error: error,
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'kind': kind,
        'subjectId': subjectId,
        'title': title,
        'subtitle': subtitle,
        'status': status.name,
        'startedAt': startedAt.toIso8601String(),
        'deadlineAt': deadlineAt.toIso8601String(),
        'updatedAt': updatedAt.toIso8601String(),
        if (error != null) 'error': error,
      };

  factory AppJob.fromJson(Map<String, dynamic> json) {
    return AppJob(
      id: json['id'] as String,
      kind: json['kind'] as String,
      subjectId: json['subjectId'] as String,
      title: json['title'] as String? ?? '',
      subtitle: json['subtitle'] as String? ?? '',
      status: AppJobStatus.values.firstWhere(
        (s) => s.name == json['status'],
        orElse: () => AppJobStatus.running,
      ),
      startedAt: DateTime.tryParse(json['startedAt'] as String? ?? '') ?? DateTime.now(),
      deadlineAt: DateTime.tryParse(json['deadlineAt'] as String? ?? '') ??
          DateTime.now().add(const Duration(minutes: 12)),
      updatedAt: DateTime.tryParse(json['updatedAt'] as String? ?? '') ?? DateTime.now(),
      error: json['error'] as String?,
    );
  }
}

class AppJobController {
  AppJobController._(this._store, this.jobId);
  final AppJobStore _store;
  final String jobId;

  Duration get remaining =>
      _store.byId(jobId)?.remaining ?? Duration.zero;

  void setSubtitle(String subtitle) => _store.updateSubtitle(jobId, subtitle);
}

abstract final class AppJobKinds {
  static const projectLaunch = 'project.launch';
  static const projectReload = 'project.reload';
  static const projectResume = 'project.resume';
  static const projectSync = 'project.sync';

  static const lifecycle = {
    projectLaunch,
    projectReload,
    projectResume,
    projectSync,
  };

  static bool isLifecycle(String kind) => lifecycle.contains(kind);
}

/// Default wall-clock budget: image pull (600s) + Ready (20s) + client slack.
const appJobDefaultTimeout = Duration(minutes: 12);

final appJobStore = AppJobStore();
