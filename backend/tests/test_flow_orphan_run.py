def test_orphan_run_id_is_skipped_without_error(monkeypatch):
    import app.orchestration.flows as flows
    import app.services.runs.sqlalchemy_repository as repo
    import app.services.workbench_service as wb

    class _DB:
        def rollback(self):
            pass

        def close(self):
            pass

        def commit(self):
            raise AssertionError("must not commit")

    monkeypatch.setattr("app.db.session.SessionLocal", lambda: _DB())
    monkeypatch.setattr(repo.SQLAlchemyRunRepository, "get_run", lambda self, rid: None)
    monkeypatch.setattr(wb, "_mark_run_dispatched", lambda *a: (_ for _ in ()).throw(AssertionError("dispatched")))
    monkeypatch.setattr(wb, "_mark_run_failed", lambda *a: (_ for _ in ()).throw(AssertionError("failed")))
    flows.execute_recommendation_run.fn("run_queued_deadbeef")
