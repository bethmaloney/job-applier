import logging
import math
import threading

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify

import config
import database
import scraper
import ranker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = Flask(__name__)
app.secret_key = config.SECRET_KEY

# Track background fetch status
_fetch_lock = threading.Lock()
_fetch_status = {"running": False, "message": "", "current": 0, "total": 0, "stage": ""}


def _make_progress(stage):
    """Return a callback that updates _fetch_status progress for the given stage."""
    def _on_progress(current, total):
        _fetch_status["stage"] = stage
        _fetch_status["current"] = current
        _fetch_status["total"] = total
        _fetch_status["message"] = f"{stage} ({current + 1}/{total})"
    return _on_progress


@app.before_request
def ensure_db():
    database.init_db()


@app.route("/")
def dashboard():
    conn = database.get_db()
    source_filter = request.args.get("source")
    sort_by = request.args.get("sort", "relevance")
    jobs = database.get_new_jobs(conn, source_filter=source_filter, sort_by=sort_by)
    stats = database.get_dashboard_stats(conn)
    conn.close()
    return render_template(
        "dashboard.html",
        jobs=jobs,
        stats=stats,
        source_filter=source_filter,
        sort_by=sort_by,
    )


@app.route("/all")
def all_jobs():
    conn = database.get_db()
    page = request.args.get("page", 1, type=int)
    show_dismissed = request.args.get("dismissed", "0") == "1"
    jobs, total = database.get_all_jobs(conn, page=page, show_dismissed=show_dismissed)
    total_pages = max(1, math.ceil(total / 20))
    stats = database.get_dashboard_stats(conn)
    conn.close()
    return render_template(
        "all_jobs.html",
        jobs=jobs,
        page=page,
        total_pages=total_pages,
        total=total,
        show_dismissed=show_dismissed,
        stats=stats,
    )


@app.route("/jobs/<int:job_id>/seen", methods=["POST"])
def mark_job_seen(job_id):
    conn = database.get_db()
    database.mark_seen(conn, job_id)
    conn.close()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": True})
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/jobs/<int:job_id>/dismiss", methods=["POST"])
def dismiss_job(job_id):
    conn = database.get_db()
    database.mark_dismissed(conn, job_id)
    conn.close()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": True})
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/jobs/<int:job_id>/applied", methods=["POST"])
def mark_job_applied(job_id):
    conn = database.get_db()
    database.mark_applied(conn, job_id)
    conn.close()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": True})
    flash("Marked as applied!", "success")
    return redirect(request.referrer or url_for("dashboard"))


def _run_fetch():
    """Background fetch + rank."""
    global _fetch_status
    try:
        _fetch_status["message"] = "Scraping jobs..."
        results = scraper.fetch_all_jobs(on_progress=_make_progress("Fetching job details"))

        total_new = sum(r["new"] for r in results)
        _fetch_status["message"] = f"Found {total_new} new jobs. Ranking..."

        ranked = ranker.rank_new_jobs(on_progress=_make_progress("Ranking jobs"))
        _fetch_status["message"] = f"Done! {total_new} new jobs, {ranked} ranked."
    except Exception as e:
        logging.error(f"Fetch error: {e}")
        _fetch_status["message"] = f"Error: {e}"
    finally:
        _fetch_status["running"] = False


@app.route("/fetch", methods=["POST"])
def fetch_jobs():
    global _fetch_status
    if _fetch_status["running"]:
        flash("A fetch is already running.", "warning")
        return redirect(url_for("dashboard"))

    with _fetch_lock:
        _fetch_status["running"] = True
        _fetch_status["message"] = "Starting..."

    thread = threading.Thread(target=_run_fetch, daemon=True)
    thread.start()

    flash("Fetching new jobs in the background... refresh in a minute.", "info")
    return redirect(url_for("dashboard"))


@app.route("/rank", methods=["POST"])
def rank_jobs():
    global _fetch_status
    if _fetch_status["running"]:
        flash("A fetch/rank is already running.", "warning")
        return redirect(url_for("dashboard"))

    def _run_rank():
        try:
            _fetch_status["running"] = True
            _fetch_status["message"] = "Ranking unranked jobs..."
            ranked = ranker.rank_new_jobs(on_progress=_make_progress("Ranking jobs"))
            _fetch_status["message"] = f"Done! {ranked} jobs ranked."
        except Exception as e:
            logging.error(f"Rank error: {e}")
            _fetch_status["message"] = f"Error: {e}"
        finally:
            _fetch_status["running"] = False

    with _fetch_lock:
        _fetch_status["running"] = True
        _fetch_status["message"] = "Starting ranking..."

    thread = threading.Thread(target=_run_rank, daemon=True)
    thread.start()

    flash("Ranking jobs in the background... refresh in a minute.", "info")
    return redirect(url_for("dashboard"))


@app.route("/refresh", methods=["POST"])
def refresh_jobs():
    """Re-fetch Seek job details and re-rank them."""
    global _fetch_status
    if _fetch_status["running"]:
        flash("A fetch/rank is already running.", "warning")
        return redirect(url_for("dashboard"))

    def _run_refresh():
        try:
            _fetch_status["message"] = "Re-fetching job details..."
            updated, errors = scraper.refresh_job_details(on_progress=_make_progress("Refreshing job details"))
            _fetch_status["message"] = f"Updated {updated} jobs. Ranking..."
            ranked = ranker.rank_new_jobs(on_progress=_make_progress("Ranking jobs"))
            _fetch_status["message"] = f"Done! {updated} descriptions updated, {ranked} jobs ranked."
        except Exception as e:
            logging.error(f"Refresh error: {e}")
            _fetch_status["message"] = f"Error: {e}"
        finally:
            _fetch_status["running"] = False

    with _fetch_lock:
        _fetch_status["running"] = True
        _fetch_status["message"] = "Starting refresh..."

    thread = threading.Thread(target=_run_refresh, daemon=True)
    thread.start()

    flash("Refreshing job details in the background... this will take a while.", "info")
    return redirect(url_for("dashboard"))


@app.route("/fetch/status")
def fetch_status():
    return jsonify(_fetch_status)


@app.route("/settings", methods=["GET", "POST"])
def settings():
    conn = database.get_db()

    if request.method == "POST":
        database.save_profile(
            conn,
            skills=request.form.get("skills", ""),
            preferences=request.form.get("preferences", ""),
            resume_text=request.form.get("resume_text", ""),
            target_titles=request.form.get("target_titles", ""),
            min_salary=request.form.get("min_salary", type=int),
            location=request.form.get("location", ""),
        )
        flash("Profile saved!", "success")
        conn.close()
        return redirect(url_for("settings"))

    profile = database.get_profile(conn)
    logs = database.get_fetch_logs(conn)
    conn.close()
    return render_template("settings.html", profile=profile, logs=logs)


if __name__ == "__main__":
    database.init_db()
    app.run(debug=True, port=5000)
