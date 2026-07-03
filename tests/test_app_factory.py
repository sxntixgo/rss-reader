def test_app_has_routes(app):
    rules = {r.rule for r in app.url_map.iter_rules()}
    for path in (
        "/",
        "/articles",
        "/feeds",
        "/feeds/opml",
        "/preferences",
        "/preferences/regenerate",
        "/status",
        "/settings",
        "/settings/models",
        "/settings/embeds",
        "/poll",
        "/count",
        "/manage-feeds",
        "/sidebar/feeds",
        "/rescore-hidden",
        "/dismiss-all",
        "/search",
        "/article/<int:article_id>/save",
        "/feeds/<int:feed_id>/pause",
        "/feeds/<int:feed_id>/resume",
        "/feeds/<int:feed_id>/threshold",
        "/feeds/<int:feed_id>/tags",
        "/article/<int:article_id>/dismiss",
    ):
        assert path in rules, f"missing route: {path}"


def test_scheduler_init_does_not_raise(app):
    from app.scheduler import init_scheduler
    sched = init_scheduler(app)
    assert sched is not None
    job_ids = {j.id for j in sched.get_jobs()}
    assert {"poll_feeds", "run_pipeline", "regen_prefs"}.issubset(job_ids)


def test_scheduler_error_listener(app, caplog):
    from app.scheduler import _on_job_error
    event = type("E", (), {"job_id": "x", "exception": RuntimeError("nope")})()
    _on_job_error(event)
    assert "raised" in caplog.text
