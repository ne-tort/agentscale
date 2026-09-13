import 'dart:async';

import 'package:flutter/foundation.dart';

/// In-process background job tracker that survives page navigation.
///
/// First consumer: project launch/reload/resume. Other long ops can reuse the
/// same [kind]/[subjectId] keys later.
class AppJobStore extends ChangeNotifier {
  final Map<String, AppJob> _jobs = <String, AppJob>{};

  List<AppJob> get jobs => List<AppJob>.unmodifiable(_jobs.values);

  AppJob? byId(String id) => _jobs[id];

  AppJob? bySubject({required String kind, required String subjectId}) {
    for (final job in _jobs.values) {
      if (job.kind == kind && job.subjectId == subjectId) return job;
    }
    return null;
  }

  bool isActive({required String kind, required String subjectId}) {
    final job = bySubject(kind: kind, subjectId: subjectId);
    return job != null && job.status == AppJobStatus.running;
  }

  /// Start or replace a job for [kind]+[subjectId]. Runner must complete.
  Future<AppJob> start({
    required String kind,
    required String subjectId,
    required String label,
    required Future<void> Function(AppJobController ctrl) run,
  }) {
    final existing = bySubject(kind: kind, subjectId: subjectId);
    if (existing != null && existing.status == AppJobStatus.running) {
      return Future<AppJob>.value(existing);
    }

    final id = '${kind}_$subjectId';
    final job = AppJob(
      id: id,
      kind: kind,
      subjectId: subjectId,
      label: label,
      status: AppJobStatus.running,
      updatedAt: DateTime.now(),
    );
    _jobs[id] = job;
    notifyListeners();

    final ctrl = AppJobController._(this, id);
    return () async {
      try {
        await run(ctrl);
        _finish(id, AppJobStatus.succeeded);
      } catch (e) {
        _finish(id, AppJobStatus.failed, error: e.toString());
        rethrow;
      }
      return _jobs[id]!;
    }();
  }

  void updateLabel(String id, String label) {
    final job = _jobs[id];
    if (job == null || job.status != AppJobStatus.running) return;
    _jobs[id] = job.copyWith(label: label, updatedAt: DateTime.now());
    notifyListeners();
  }

  void clear(String id) {
    _jobs.remove(id);
    notifyListeners();
  }

  void clearSubject({required String kind, required String subjectId}) {
    final job = bySubject(kind: kind, subjectId: subjectId);
    if (job != null) clear(job.id);
  }

  void _finish(String id, AppJobStatus status, {String? error}) {
    final job = _jobs[id];
    if (job == null) return;
    _jobs[id] = job.copyWith(
      status: status,
      error: error,
      updatedAt: DateTime.now(),
    );
    notifyListeners();
  }
}

enum AppJobStatus { running, succeeded, failed }

class AppJob {
  const AppJob({
    required this.id,
    required this.kind,
    required this.subjectId,
    required this.label,
    required this.status,
    required this.updatedAt,
    this.error,
  });

  final String id;
  final String kind;
  final String subjectId;
  final String label;
  final AppJobStatus status;
  final DateTime updatedAt;
  final String? error;

  AppJob copyWith({
    String? label,
    AppJobStatus? status,
    DateTime? updatedAt,
    String? error,
  }) {
    return AppJob(
      id: id,
      kind: kind,
      subjectId: subjectId,
      label: label ?? this.label,
      status: status ?? this.status,
      updatedAt: updatedAt ?? this.updatedAt,
      error: error,
    );
  }
}

class AppJobController {
  AppJobController._(this._store, this.jobId);
  final AppJobStore _store;
  final String jobId;

  void setLabel(String label) => _store.updateLabel(jobId, label);
}

/// Job kinds used by project lifecycle.
abstract final class AppJobKinds {
  static const projectLaunch = 'project.launch';
  static const projectReload = 'project.reload';
  static const projectResume = 'project.resume';
}

final appJobStore = AppJobStore();
