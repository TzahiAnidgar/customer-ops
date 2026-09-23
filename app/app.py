import base64
import os
import sqlite3
import shutil
import mimetypes
from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from flask import Flask, Response, flash, redirect, render_template, render_template_string, request, send_file, url_for
from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader, TemplateNotFound
from werkzeug.utils import secure_filename

try:
    from azure.storage.blob import BlobServiceClient
except ImportError:
    BlobServiceClient = None

try:
    import runtime_assets
except ImportError:
    runtime_assets = None


BASE_DIR = Path(__file__).resolve().parent
if os.name == "nt":
    DEFAULT_DATA_DIR = BASE_DIR.parent / "data"
    DEFAULT_UPLOAD_DIR = BASE_DIR.parent / "uploads"
else:
    DEFAULT_DATA_DIR = Path("/home/data")
    DEFAULT_UPLOAD_DIR = Path("/home/uploads")

DATA_DIR = Path(os.getenv("CUSTOMER_OPS_DATA_DIR", str(DEFAULT_DATA_DIR)))
UPLOAD_DIR = Path(os.getenv("CUSTOMER_OPS_UPLOAD_DIR", str(DEFAULT_UPLOAD_DIR)))
DB_PATH = Path(os.getenv("CUSTOMER_OPS_DB_PATH", str(DATA_DIR / "customer_ops.db")))
SEED_DIR = Path(os.getenv("CUSTOMER_OPS_SEED_DIR", str(BASE_DIR.parent / "seed-data")))
ALLOWED_DOC_TYPES = {"LLD", "HLD", "SOW"}
TASK_WORKFLOW_STATUSES = ("todo", "in_progress", "blocked", "done")
TASK_STATUS_LABELS = {
    "todo": "To Do",
    "in_progress": "In Progress",
    "blocked": "Blocked",
    "done": "Done",
}
TASK_STATUS_TRANSITIONS = {
    "todo": ("in_progress", "blocked"),
    "in_progress": ("blocked", "done", "todo"),
    "blocked": ("in_progress", "todo"),
    "done": ("in_progress",),
}
PROJECT_STATUS_OPTIONS = (
    "In progress",
    "Waiting for costumer",
    "Done",
    "Hold",
    "Waiting for Supplier",
    "Not Started yet",
)
DEFAULT_PROJECT_STATUS = "Not Started yet"
PROJECT_FILTER_ALL = "all"
PROJECT_FILTER_NOT_DONE = "not_done"
WORKSPACES = ("Ziv", "Yossi")
WORKSPACE_TABS = (("Ziv", "Ziv"), ("Yossi", "Yossi"))
DEFAULT_WORKSPACE = "Ziv"


def sanitize_workspace(value: str) -> str:
    return value if value in WORKSPACES else DEFAULT_WORKSPACE


def first_existing_path(*candidates: Path) -> Path:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def database_needs_seed_restore(db_path: Path) -> bool:
    if not db_path.exists():
        return True
    try:
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'customers'"
            ).fetchone()
            if not row:
                return True
            count = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
            return count == 0
        finally:
            conn.close()
    except sqlite3.Error:
        return True


def create_app() -> Flask:
    template_root = first_existing_path(BASE_DIR / "templates", BASE_DIR / "app" / "templates")
    static_root = first_existing_path(BASE_DIR / "static", BASE_DIR / "app" / "static")
    app = Flask(
        __name__,
        template_folder=str(template_root),
        static_folder=str(static_root),
        static_url_path="/static",
    )
    template_sources = getattr(runtime_assets, "TEMPLATES", {}) if runtime_assets is not None else {}
    app.jinja_loader = ChoiceLoader(
        [
            DictLoader(template_sources),
            FileSystemLoader([str(BASE_DIR / "templates"), str(BASE_DIR / "app" / "templates")]),
        ]
    )
    app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")
    app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024

    @app.template_filter("simple_date")
    def simple_date(value):
        return format_simple_date(value)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    
    try:
        restore_seed_data()
    except Exception as e:
        print(f"[WARNING] Seed data restoration failed (continuing): {e}")
    
    try:
        init_db()
    except Exception as e:
        print(f"[ERROR] Database initialization failed: {e}")
        raise


    @app.get("/static/<path:filename>")
    def serve_static(filename):
        static_assets = getattr(runtime_assets, "STATIC_ASSETS", {}) if runtime_assets is not None else {}
        print(f"[DEBUG] serve_static called with filename={filename}, available keys={list(static_assets.keys())}")
        if filename in static_assets:
            content = static_assets[filename]
            if filename.endswith(".css"):
                return Response(content, mimetype="text/css")
            elif filename.endswith(".js"):
                return Response(content, mimetype="application/javascript")
            else:
                try:
                    binary_content = base64.b64decode(content)
                    mimetype, _ = mimetypes.guess_type(filename)
                    return Response(binary_content, mimetype=mimetype or "application/octet-stream")
                except Exception as e:
                    print(f"[DEBUG] Error decoding static asset {filename}: {e}")
                    return "Not found", 404
        print(f"[DEBUG] Static asset not found: {filename}")
        return "Not found", 404

    @app.get("/")
    def index():
        selected_project_status_filter = sanitize_project_status_filter(
            request.args.get("status_filter", PROJECT_FILTER_ALL)
        )
        workspace = sanitize_workspace(request.args.get("workspace", DEFAULT_WORKSPACE))

        customer_query_params = [workspace]
        if selected_project_status_filter == PROJECT_FILTER_NOT_DONE:
            customer_filter_clause = "WHERE lower(COALESCE(c.project_status, 'Not Started yet')) != 'done' AND c.workspace = ?"
        elif selected_project_status_filter != PROJECT_FILTER_ALL:
            customer_filter_clause = "WHERE COALESCE(c.project_status, 'Not Started yet') = ? AND c.workspace = ?"
            customer_query_params = [selected_project_status_filter, workspace]
        else:
            customer_filter_clause = "WHERE c.workspace = ?"

        conn = get_conn()
        customers = conn.execute(
            f"""
            SELECT
                c.id,
                c.name,
                c.contact_email,
                c.notes,
                c.created_at,
                c.logo_file_name,
                c.logo_storage_path,
                COALESCE(c.project_status, 'Not Started yet') AS project_status,
                COALESCE(c.completion_pct, 0) AS completion_pct,
                COALESCE(c.shmil_notes, '') AS shmil_notes,
                COALESCE(
                    (
                    SELECT group_concat(DISTINCT d.doc_type)
                    FROM documents d
                    WHERE d.customer_id = c.id
                    ),
                    '-'
                ) AS document_types
            FROM customers c
            {customer_filter_clause}
            ORDER BY datetime(created_at) DESC
            """,
            tuple(customer_query_params),
        ).fetchall()
        dashboard = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM customers WHERE workspace = ?) AS projects_total,
                (SELECT COUNT(*) FROM tasks t JOIN projects p ON p.id = t.project_id JOIN customers c ON c.id = p.customer_id WHERE c.workspace = ?) AS tasks_total,
                (SELECT COUNT(*) FROM tasks t JOIN projects p ON p.id = t.project_id JOIN customers c ON c.id = p.customer_id WHERE lower(t.status) = 'done' AND c.workspace = ?) AS tasks_done,
                (SELECT COUNT(*) FROM tasks t JOIN projects p ON p.id = t.project_id JOIN customers c ON c.id = p.customer_id WHERE lower(t.status) = 'blocked' AND c.workspace = ?) AS tasks_blocked,
                (
                SELECT COUNT(*)
                FROM tasks t JOIN projects p ON p.id = t.project_id JOIN customers c ON c.id = p.customer_id
                WHERE t.due_date IS NOT NULL
                  AND t.due_date != ''
                  AND date(t.due_date) < date('now')
                  AND lower(t.status) != 'done'
                  AND c.workspace = ?
                ) AS tasks_overdue,
                (SELECT COUNT(*) FROM documents d JOIN customers c ON c.id = d.customer_id WHERE c.workspace = ?) AS documents_total
            """,
            (workspace, workspace, workspace, workspace, workspace, workspace),
        ).fetchone()
        risk_tasks = conn.execute(
            """
            SELECT
                t.title,
                t.status,
                t.due_date,
                p.name AS sub_project_name,
                c.name AS project_name
            FROM tasks t
            JOIN projects p ON p.id = t.project_id
            JOIN customers c ON c.id = p.customer_id
            WHERE c.workspace = ?
              AND (
                lower(t.status) = 'blocked'
                OR (
                t.due_date IS NOT NULL
                AND t.due_date != ''
                AND date(t.due_date) < date('now')
                AND lower(t.status) != 'done'
                )
              )
            ORDER BY
                CASE WHEN lower(t.status) = 'blocked' THEN 0 ELSE 1 END,
                date(t.due_date) ASC
            LIMIT 10
            """,
            (workspace,),
        ).fetchall()
        conn.close()
        template_context = dict(
            customers=customers,
            dashboard=dashboard,
            risk_tasks=risk_tasks,
            task_status_labels=TASK_STATUS_LABELS,
            project_status_options=PROJECT_STATUS_OPTIONS,
            project_status_filter_all=PROJECT_FILTER_ALL,
            project_status_filter_not_done=PROJECT_FILTER_NOT_DONE,
            selected_project_status_filter=selected_project_status_filter,
            workspace=workspace,
            workspace_tabs=WORKSPACE_TABS,
        )
        try:
            return render_template("index.html", **template_context)
        except TemplateNotFound:
            return render_template_string(
                """<!doctype html>
                <html>
                <head><meta charset="utf-8"><title>Customer Ops</title></head>
                <body>
                  <h1>Customer Ops</h1>
                  <p>Template fallback is active.</p>
                  <p>Workspace: {{ workspace }}</p>
                  <p>Projects: {{ customers|length }}</p>
                  <ul>
                    {% for customer in customers %}
                      <li>{{ customer.name }}</li>
                    {% endfor %}
                  </ul>
                </body>
                </html>""",
                **template_context,
            )

    @app.post("/customers")
    def create_customer():
        selected_project_status_filter = sanitize_project_status_filter(
            request.form.get("status_filter", PROJECT_FILTER_ALL)
        )
        workspace = sanitize_workspace(request.form.get("workspace", DEFAULT_WORKSPACE))
        name = request.form.get("name", "").strip()
        if not name:
            flash("Project name is required.", "error")
            return redirect(url_for("index", status_filter=selected_project_status_filter, workspace=workspace))

        contact_email = request.form.get("contact_email", "").strip()
        notes = request.form.get("notes", "").strip()
        requested_project_status = request.form.get("project_status", DEFAULT_PROJECT_STATUS).strip()
        project_status = (
            requested_project_status
            if requested_project_status in PROJECT_STATUS_OPTIONS
            else DEFAULT_PROJECT_STATUS
        )
        now = utc_now()
        conn = get_conn()
        conn.execute(
            """
            INSERT INTO customers (name, contact_email, notes, created_at, project_status, workspace)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name, contact_email, notes, now, project_status, workspace),
        )
        conn.commit()
        conn.close()
        flash("Project created.", "success")
        return redirect(url_for("index", status_filter=selected_project_status_filter, workspace=workspace))

    @app.post("/customers/<int:customer_id>/completion")
    def update_customer_completion(customer_id: int):
        selected_project_status_filter = sanitize_project_status_filter(
            request.form.get("status_filter", PROJECT_FILTER_ALL)
        )
        workspace = sanitize_workspace(request.form.get("workspace", DEFAULT_WORKSPACE))
        try:
            pct = int(request.form.get("completion_pct", 0))
        except (ValueError, TypeError):
            pct = 0
        pct = max(0, min(100, pct))

        conn = get_conn()
        customer = conn.execute("SELECT id FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if not customer:
            conn.close()
            flash("Project not found.", "error")
            return redirect(url_for("index", status_filter=selected_project_status_filter, workspace=workspace))

        conn.execute("UPDATE customers SET completion_pct = ? WHERE id = ?", (pct, customer_id))
        conn.commit()
        conn.close()
        return redirect(url_for("index", status_filter=selected_project_status_filter, workspace=workspace))

    @app.post("/customers/<int:customer_id>/shmil-notes")
    def update_customer_shmil_notes(customer_id: int):
        selected_project_status_filter = sanitize_project_status_filter(
            request.form.get("status_filter", PROJECT_FILTER_ALL)
        )
        workspace = sanitize_workspace(request.form.get("workspace", DEFAULT_WORKSPACE))
        shmil_notes = request.form.get("shmil_notes", "").strip()
        if len(shmil_notes) > 140:
            flash("Shmil's Notes must be 140 characters or less.", "error")
            return redirect(url_for("index", status_filter=selected_project_status_filter, workspace=workspace))

        conn = get_conn()
        customer = conn.execute("SELECT id FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if not customer:
            conn.close()
            flash("Project not found.", "error")
            return redirect(url_for("index", status_filter=selected_project_status_filter, workspace=workspace))

        conn.execute("UPDATE customers SET shmil_notes = ? WHERE id = ?", (shmil_notes, customer_id))
        conn.commit()
        conn.close()
        return redirect(url_for("index", status_filter=selected_project_status_filter, workspace=workspace))

    @app.post("/customers/<int:customer_id>/name")
    def update_customer_name(customer_id: int):
        selected_project_status_filter = sanitize_project_status_filter(
            request.form.get("status_filter", PROJECT_FILTER_ALL)
        )
        workspace = sanitize_workspace(request.form.get("workspace", DEFAULT_WORKSPACE))
        new_name = request.form.get("name", "").strip()
        if not new_name:
            flash("Project name is required.", "error")
            return redirect(url_for("index", status_filter=selected_project_status_filter, workspace=workspace))

        if len(new_name) > 255:
            flash("Project name must be 255 characters or less.", "error")
            return redirect(url_for("index", status_filter=selected_project_status_filter, workspace=workspace))

        conn = get_conn()
        customer = conn.execute("SELECT id, name FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if not customer:
            conn.close()
            flash("Project not found.", "error")
            return redirect(url_for("index", status_filter=selected_project_status_filter, workspace=workspace))

        old_name = customer["name"]
        conn.execute("UPDATE customers SET name = ? WHERE id = ?", (new_name, customer_id))
        conn.commit()
        conn.close()
        flash(f"Project renamed from '{old_name}' to '{new_name}'.", "success")
        return redirect(url_for("index", status_filter=selected_project_status_filter, workspace=workspace))

    @app.post("/customers/<int:customer_id>/contact-email")
    def update_customer_contact_email(customer_id: int):
        selected_project_status_filter = sanitize_project_status_filter(
            request.form.get("status_filter", PROJECT_FILTER_ALL)
        )
        workspace = sanitize_workspace(request.form.get("workspace", DEFAULT_WORKSPACE))
        contact_email = request.form.get("contact_email", "").strip()
        if not contact_email:
            flash("Contact email is required.", "error")
            return redirect(url_for("index", status_filter=selected_project_status_filter, workspace=workspace))
        if "@" not in contact_email or "." not in contact_email.split("@")[-1]:
            flash("Please enter a valid contact email.", "error")
            return redirect(url_for("index", status_filter=selected_project_status_filter, workspace=workspace))

        conn = get_conn()
        customer = conn.execute(
            "SELECT id FROM customers WHERE id = ?",
            (customer_id,),
        ).fetchone()
        if not customer:
            conn.close()
            flash("Project not found.", "error")
            return redirect(url_for("index", status_filter=selected_project_status_filter, workspace=workspace))

        conn.execute(
            "UPDATE customers SET contact_email = ? WHERE id = ?",
            (contact_email, customer_id),
        )
        conn.commit()
        conn.close()
        flash("Contact email saved.", "success")
        return redirect(url_for("index", status_filter=selected_project_status_filter, workspace=workspace))

    @app.post("/customers/<int:customer_id>/status")
    def update_customer_status(customer_id: int):
        selected_project_status_filter = sanitize_project_status_filter(
            request.form.get("status_filter", PROJECT_FILTER_ALL)
        )
        workspace = sanitize_workspace(request.form.get("workspace", DEFAULT_WORKSPACE))
        requested_project_status = request.form.get("project_status", "").strip()
        if requested_project_status not in PROJECT_STATUS_OPTIONS:
            flash("Invalid project status.", "error")
            return redirect(url_for("index", status_filter=selected_project_status_filter, workspace=workspace))

        conn = get_conn()
        customer = conn.execute(
            "SELECT id FROM customers WHERE id = ?",
            (customer_id,),
        ).fetchone()
        if not customer:
            conn.close()
            flash("Project not found.", "error")
            return redirect(url_for("index", status_filter=selected_project_status_filter, workspace=workspace))

        conn.execute(
            "UPDATE customers SET project_status = ? WHERE id = ?",
            (requested_project_status, customer_id),
        )
        conn.commit()
        conn.close()
        flash("Project status updated.", "success")
        return redirect(url_for("index", status_filter=selected_project_status_filter, workspace=workspace))

    @app.post("/customers/<int:customer_id>/logo")
    def upload_customer_logo(customer_id: int):
        selected_project_status_filter = sanitize_project_status_filter(
            request.form.get("status_filter", PROJECT_FILTER_ALL)
        )
        workspace = sanitize_workspace(request.form.get("workspace", DEFAULT_WORKSPACE))
        file = request.files.get("logo")
        if file is None or file.filename == "":
            flash("Please choose a logo file.", "error")
            return redirect(url_for("index", status_filter=selected_project_status_filter, workspace=workspace))

        safe_name = secure_filename(file.filename)
        logo_storage_path = store_customer_logo(file=file, customer_id=customer_id, safe_name=safe_name)

        conn = get_conn()
        customer = conn.execute("SELECT id FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if not customer:
            conn.close()
            flash("Project not found.", "error")
            return redirect(url_for("index", status_filter=selected_project_status_filter, workspace=workspace))

        conn.execute(
            """
            UPDATE customers
            SET logo_file_name = ?, logo_storage_path = ?
            WHERE id = ?
            """,
            (safe_name, logo_storage_path, customer_id),
        )
        conn.commit()
        conn.close()
        flash("Project logo uploaded.", "success")
        return redirect(url_for("index", status_filter=selected_project_status_filter, workspace=workspace))

    @app.get("/customers/<int:customer_id>/email-contact")
    def email_customer_contact(customer_id: int):
        selected_project_status_filter = sanitize_project_status_filter(
            request.args.get("status_filter", PROJECT_FILTER_ALL)
        )
        workspace = sanitize_workspace(request.args.get("workspace", DEFAULT_WORKSPACE))
        conn = get_conn()
        customer = conn.execute(
            "SELECT id, name, contact_email FROM customers WHERE id = ?",
            (customer_id,),
        ).fetchone()
        conn.close()
        if not customer:
            flash("Project not found.", "error")
            return redirect(url_for("index", status_filter=selected_project_status_filter, workspace=workspace))

        contact_email = (customer["contact_email"] or "").strip()
        if not contact_email:
            flash("No contact email defined for this project.", "error")
            return redirect(url_for("index", status_filter=selected_project_status_filter, workspace=workspace))

        project_name = customer["name"] or "Project"
        subject = f"Project update - {project_name}"
        body = (
            "Hi,\n\n"
            f"I wanted to share a quick update regarding the project \"{project_name}\".\n\n"
            "Best regards,"
        )
        mailto_url = (
            f"mailto:{quote(contact_email)}"
            f"?subject={quote(subject)}&body={quote(body)}"
        )
        return redirect(mailto_url)

    @app.get("/customers/<int:customer_id>/logo")
    def get_customer_logo(customer_id: int):
        conn = get_conn()
        customer = conn.execute(
            """
            SELECT logo_file_name, logo_storage_path
            FROM customers
            WHERE id = ?
            """,
            (customer_id,),
        ).fetchone()
        conn.close()

        if not customer or not customer["logo_storage_path"]:
            # Return a simple placeholder SVG instead of 404
            placeholder_svg = (
                '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">'
                '<rect width="100" height="100" fill="#e0e0e0"/>'
                '<text x="50" y="50" text-anchor="middle" dy=".3em" font-family="Arial" font-size="12" fill="#999">Logo</text>'
                '</svg>'
            )
            return Response(placeholder_svg, mimetype="image/svg+xml")

        return serve_stored_file(
            storage_path=customer["logo_storage_path"],
            file_name=customer["logo_file_name"] or "logo",
            as_attachment=False,
        )

    @app.post("/customers/<int:customer_id>/delete")
    def delete_customer(customer_id: int):
        selected_project_status_filter = sanitize_project_status_filter(
            request.form.get("status_filter", PROJECT_FILTER_ALL)
        )
        workspace = sanitize_workspace(request.form.get("workspace", DEFAULT_WORKSPACE))
        conn = get_conn()
        customer = conn.execute("SELECT id, name FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if not customer:
            conn.close()
            flash("Project not found.", "error")
            return redirect(url_for("index", status_filter=selected_project_status_filter, workspace=workspace))

        conn.execute("DELETE FROM tasks WHERE project_id IN (SELECT id FROM projects WHERE customer_id = ?)", (customer_id,))
        conn.execute("DELETE FROM time_windows WHERE customer_id = ?", (customer_id,))
        conn.execute("DELETE FROM documents WHERE customer_id = ?", (customer_id,))
        conn.execute("DELETE FROM projects WHERE customer_id = ?", (customer_id,))
        conn.execute("DELETE FROM customers WHERE id = ?", (customer_id,))
        conn.commit()
        conn.close()

        flash(f"Project '{customer['name']}' deleted.", "success")
        return redirect(url_for("index", status_filter=selected_project_status_filter, workspace=workspace))

    @app.get("/customers/<int:customer_id>")
    def customer_details(customer_id: int):
        conn = get_conn()
        customer = conn.execute(
            "SELECT id, name, contact_email, notes, created_at FROM customers WHERE id = ?",
            (customer_id,),
        ).fetchone()
        if not customer:
            conn.close()
            flash("Project not found.", "error")
            return redirect(url_for("index"))

        projects = conn.execute(
            """
            SELECT id, customer_id, name, status, start_date, end_date, description
            FROM projects
            WHERE customer_id = ?
            ORDER BY id DESC
            """,
            (customer_id,),
        ).fetchall()
        tasks = conn.execute(
            """
            SELECT t.id, t.project_id, t.title, t.status, t.due_date, t.owner
            FROM tasks t
            JOIN projects p ON p.id = t.project_id
            WHERE p.customer_id = ?
            ORDER BY t.id DESC
            """,
            (customer_id,),
        ).fetchall()
        time_windows = conn.execute(
            """
            SELECT id, customer_id, project_id, label, start_at, end_at, notes
            FROM time_windows
            WHERE customer_id = ?
            ORDER BY datetime(start_at) DESC
            """,
            (customer_id,),
        ).fetchall()
        documents = conn.execute(
            """
            SELECT id, customer_id, project_id, doc_type, file_name, storage_path, uploaded_at
            FROM documents
            WHERE customer_id = ?
            ORDER BY datetime(uploaded_at) DESC
            """,
            (customer_id,),
        ).fetchall()
        conn.close()

        tasks_by_project = {}
        next_statuses_by_task = {}
        for task in tasks:
            normalized_status = normalize_task_status(task["status"])
            task_data = dict(task)
            task_data["status"] = normalized_status
            tasks_by_project.setdefault(task["project_id"], []).append(task_data)
            next_statuses_by_task[task["id"]] = next_workflow_statuses(normalized_status)
        timeline = build_timeline_data(projects=projects, tasks=tasks, time_windows=time_windows)

        return render_template(
            "customer_details.html",
            customer=customer,
            projects=projects,
            tasks_by_project=tasks_by_project,
            next_statuses_by_task=next_statuses_by_task,
            time_windows=time_windows,
            documents=documents,
            allowed_doc_types=sorted(ALLOWED_DOC_TYPES),
            task_workflow_statuses=TASK_WORKFLOW_STATUSES,
            task_status_labels=TASK_STATUS_LABELS,
            timeline=timeline,
        )

    @app.post("/customers/<int:customer_id>/projects")
    def create_project(customer_id: int):
        name = request.form.get("name", "").strip()
        if not name:
            flash("Project name is required.", "error")
            return redirect(url_for("customer_details", customer_id=customer_id))

        status = request.form.get("status", "planned").strip() or "planned"
        start_date = request.form.get("start_date", "").strip() or None
        end_date = request.form.get("end_date", "").strip() or None
        description = request.form.get("description", "").strip()

        conn = get_conn()
        customer = conn.execute("SELECT id FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if not customer:
            conn.close()
            flash("Project not found.", "error")
            return redirect(url_for("index"))

        conn.execute(
            """
            INSERT INTO projects (customer_id, name, status, start_date, end_date, description)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (customer_id, name, status, start_date, end_date, description),
        )
        conn.commit()
        conn.close()
        flash("Project created.", "success")
        return redirect(url_for("customer_details", customer_id=customer_id))

    @app.post("/projects/<int:project_id>/name")
    def update_project_name(project_id: int):
        new_name = request.form.get("name", "").strip()
        if not new_name:
            flash("Sub-project name is required.", "error")
            return redirect(url_for("index"))

        if len(new_name) > 255:
            flash("Sub-project name must be 255 characters or less.", "error")
            return redirect(url_for("index"))

        conn = get_conn()
        project = conn.execute(
            "SELECT id, customer_id, name FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        if not project:
            conn.close()
            flash("Sub-project not found.", "error")
            return redirect(url_for("index"))

        old_name = project["name"]
        conn.execute("UPDATE projects SET name = ? WHERE id = ?", (new_name, project_id))
        conn.commit()
        conn.close()
        flash(f"Sub-project renamed from '{old_name}' to '{new_name}'.", "success")
        return redirect(url_for("customer_details", customer_id=project["customer_id"]))

    @app.post("/projects/<int:project_id>/tasks")
    def create_task(project_id: int):
        title = request.form.get("title", "").strip()
        customer_id = request.form.get("customer_id", type=int)
        if not title or not customer_id:
            flash("Task title and project are required.", "error")
            return redirect(url_for("index"))

        status = parse_requested_task_status(request.form.get("status", "todo")) or "todo"
        due_date = request.form.get("due_date", "").strip() or None
        owner = request.form.get("owner", "").strip()

        conn = get_conn()
        project = conn.execute(
            "SELECT id, customer_id FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        if not project:
            conn.close()
            flash("Project not found.", "error")
            return redirect(url_for("customer_details", customer_id=customer_id))

        conn.execute(
            """
            INSERT INTO tasks (project_id, title, status, due_date, owner)
            VALUES (?, ?, ?, ?, ?)
            """,
            (project_id, title, status, due_date, owner),
        )
        task_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """
            INSERT INTO task_status_history (task_id, old_status, new_status, changed_at)
            VALUES (?, ?, ?, ?)
            """,
            (task_id, None, status, utc_now()),
        )
        conn.commit()
        conn.close()
        flash("Task created.", "success")
        return redirect(url_for("customer_details", customer_id=project["customer_id"]))

    @app.post("/tasks/<int:task_id>/status")
    def update_task_status(task_id: int):
        next_status = parse_requested_task_status(request.form.get("status", ""))
        if not next_status:
            flash("Invalid status value.", "error")
            return redirect(url_for("index"))

        conn = get_conn()
        task = conn.execute(
            """
            SELECT t.id, t.status, p.customer_id
            FROM tasks t
            JOIN projects p ON p.id = t.project_id
            WHERE t.id = ?
            """,
            (task_id,),
        ).fetchone()
        if not task:
            conn.close()
            flash("Task not found.", "error")
            return redirect(url_for("index"))

        current_status = normalize_task_status(task["status"])
        allowed_next_statuses = next_workflow_statuses(current_status)
        if next_status not in allowed_next_statuses:
            conn.close()
            flash("Invalid workflow transition.", "error")
            return redirect(url_for("customer_details", customer_id=task["customer_id"]))

        conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (next_status, task_id))
        conn.execute(
            """
            INSERT INTO task_status_history (task_id, old_status, new_status, changed_at)
            VALUES (?, ?, ?, ?)
            """,
            (task_id, current_status, next_status, utc_now()),
        )
        conn.commit()
        conn.close()

        flash("Task status updated.", "success")
        return redirect(url_for("customer_details", customer_id=task["customer_id"]))

    @app.post("/customers/<int:customer_id>/time-windows")
    def create_time_window(customer_id: int):
        label = request.form.get("label", "").strip()
        start_at = request.form.get("start_at", "").strip()
        end_at = request.form.get("end_at", "").strip()
        project_id = request.form.get("project_id", type=int)
        notes = request.form.get("notes", "").strip()

        if not label or not start_at or not end_at:
            flash("Label, start time and end time are required.", "error")
            return redirect(url_for("customer_details", customer_id=customer_id))

        conn = get_conn()
        conn.execute(
            """
            INSERT INTO time_windows (customer_id, project_id, label, start_at, end_at, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (customer_id, project_id, label, start_at, end_at, notes),
        )
        conn.commit()
        conn.close()
        flash("Time window created.", "success")
        return redirect(url_for("customer_details", customer_id=customer_id))

    @app.post("/customers/<int:customer_id>/documents")
    def upload_document(customer_id: int):
        project_id = request.form.get("project_id", type=int)
        doc_type = request.form.get("doc_type", "").strip().upper()
        file = request.files.get("document")
        if doc_type not in ALLOWED_DOC_TYPES:
            flash("Document type must be LLD, HLD or SOW.", "error")
            return redirect(url_for("customer_details", customer_id=customer_id))
        if file is None or file.filename == "":
            flash("Please choose a file.", "error")
            return redirect(url_for("customer_details", customer_id=customer_id))

        safe_name = secure_filename(file.filename)
        storage_path = store_file(file=file, customer_id=customer_id, doc_type=doc_type, safe_name=safe_name)

        conn = get_conn()
        conn.execute(
            """
            INSERT INTO documents (customer_id, project_id, doc_type, file_name, storage_path, uploaded_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (customer_id, project_id, doc_type, safe_name, storage_path, utc_now()),
        )
        conn.commit()
        conn.close()
        flash("Document uploaded.", "success")
        return redirect(url_for("customer_details", customer_id=customer_id))

    @app.get("/documents/<int:document_id>/preview")
    def preview_document(document_id: int):
        return serve_document(document_id=document_id, as_attachment=False)

    @app.get("/documents/<int:document_id>/download")
    def download_document(document_id: int):
        return serve_document(document_id=document_id, as_attachment=True)

    return app


def store_file(file, customer_id: int, doc_type: str, safe_name: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    object_name = f"{customer_id}/{doc_type}/{timestamp}_{safe_name}"
    storage_connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "").strip()
    blob_container = os.getenv("AZURE_BLOB_CONTAINER", "customer-documents").strip()

    if storage_connection_string and BlobServiceClient is not None:
        blob_service = BlobServiceClient.from_connection_string(storage_connection_string)
        container_client = blob_service.get_container_client(blob_container)
        if not container_client.exists():
            container_client.create_container()
        blob_client = container_client.get_blob_client(object_name)
        blob_client.upload_blob(file.stream.read(), overwrite=True)
        return f"azure://{blob_container}/{object_name}"

    local_target = UPLOAD_DIR / object_name
    local_target.parent.mkdir(parents=True, exist_ok=True)
    file.save(local_target)
    return str(local_target)


def store_customer_logo(file, customer_id: int, safe_name: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    object_name = f"{customer_id}/logos/{timestamp}_{safe_name}"
    storage_connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "").strip()
    blob_container = os.getenv("AZURE_BLOB_CONTAINER", "customer-documents").strip()

    if storage_connection_string and BlobServiceClient is not None:
        blob_service = BlobServiceClient.from_connection_string(storage_connection_string)
        container_client = blob_service.get_container_client(blob_container)
        if not container_client.exists():
            container_client.create_container()
        blob_client = container_client.get_blob_client(object_name)
        blob_client.upload_blob(file.stream.read(), overwrite=True)
        return f"azure://{blob_container}/{object_name}"

    local_target = UPLOAD_DIR / object_name
    local_target.parent.mkdir(parents=True, exist_ok=True)
    file.save(local_target)
    return str(local_target)


def serve_stored_file(storage_path: str, file_name: str, as_attachment: bool):
    mime_type = guess_mime_type(file_name)
    if storage_path.startswith("azure://"):
        if BlobServiceClient is None:
            return Response("Azure Blob client is not installed.", status=500)
        storage_connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "").strip()
        if not storage_connection_string:
            return Response("Azure storage is not configured.", status=500)

        container_name, blob_name = parse_azure_storage_path(storage_path)
        if not container_name or not blob_name:
            return Response("Invalid Azure storage path.", status=500)

        blob_service = BlobServiceClient.from_connection_string(storage_connection_string)
        blob_client = blob_service.get_blob_client(container=container_name, blob=blob_name)
        if not blob_client.exists():
            return Response("File was not found in storage.", status=404)

        data = blob_client.download_blob().readall()
        return send_file(
            BytesIO(data),
            mimetype=mime_type,
            as_attachment=as_attachment,
            download_name=file_name,
        )

    local_path = Path(storage_path)
    if not local_path.exists():
        return Response("File was not found.", status=404)

    return send_file(
        local_path,
        mimetype=mime_type,
        as_attachment=as_attachment,
        download_name=file_name,
    )


def serve_document(document_id: int, as_attachment: bool):
    conn = get_conn()
    document = conn.execute(
        """
        SELECT id, file_name, storage_path
        FROM documents
        WHERE id = ?
        """,
        (document_id,),
    ).fetchone()
    conn.close()

    if not document:
        return Response("Document not found.", status=404)

    return serve_stored_file(
        storage_path=document["storage_path"],
        file_name=document["file_name"],
        as_attachment=as_attachment,
    )


def parse_azure_storage_path(storage_path: str):
    raw = storage_path.replace("azure://", "", 1)
    parts = raw.split("/", 1)
    if len(parts) != 2:
        return None, None
    return parts[0], parts[1]


def guess_mime_type(file_name: str) -> str:
    guessed, _ = mimetypes.guess_type(file_name)
    return guessed or "application/octet-stream"


def ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, column_type: str):
    existing_columns = {
        row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name not in existing_columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            contact_email TEXT,
            notes TEXT,
            shmil_notes TEXT,
            created_at TEXT NOT NULL,
            logo_file_name TEXT,
            logo_storage_path TEXT,
            project_status TEXT NOT NULL DEFAULT 'Not Started yet'
        );

        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'planned',
            start_date TEXT,
            end_date TEXT,
            description TEXT,
            FOREIGN KEY(customer_id) REFERENCES customers(id)
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'todo',
            due_date TEXT,
            owner TEXT,
            FOREIGN KEY(project_id) REFERENCES projects(id)
        );

        CREATE TABLE IF NOT EXISTS task_status_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            old_status TEXT,
            new_status TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            FOREIGN KEY(task_id) REFERENCES tasks(id)
        );

        CREATE TABLE IF NOT EXISTS time_windows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            project_id INTEGER,
            label TEXT NOT NULL,
            start_at TEXT NOT NULL,
            end_at TEXT NOT NULL,
            notes TEXT,
            FOREIGN KEY(customer_id) REFERENCES customers(id),
            FOREIGN KEY(project_id) REFERENCES projects(id)
        );

        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            project_id INTEGER,
            doc_type TEXT NOT NULL,
            file_name TEXT NOT NULL,
            storage_path TEXT NOT NULL,
            uploaded_at TEXT NOT NULL,
            FOREIGN KEY(customer_id) REFERENCES customers(id),
            FOREIGN KEY(project_id) REFERENCES projects(id)
        );
        """
    )
    ensure_column(conn, "customers", "logo_file_name", "TEXT")
    ensure_column(conn, "customers", "logo_storage_path", "TEXT")
    ensure_column(conn, "customers", "project_status", "TEXT")
    ensure_column(conn, "customers", "completion_pct", "INTEGER")
    ensure_column(conn, "customers", "workspace", "TEXT")
    ensure_column(conn, "customers", "shmil_notes", "TEXT")
    conn.execute(
        "UPDATE customers SET workspace = ? WHERE workspace IS NULL OR trim(workspace) = ''",
        (DEFAULT_WORKSPACE,),
    )
    conn.execute(
        """
        UPDATE customers
        SET project_status = ?
        WHERE project_status IS NULL OR trim(project_status) = ''
        """,
        (DEFAULT_PROJECT_STATUS,),
    )
    conn.commit()
    conn.close()


def restore_seed_data() -> None:
    runtime_seed_b64 = getattr(runtime_assets, "SEED_DB_B64", "") if runtime_assets is not None else ""
    if database_needs_seed_restore(DB_PATH):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        if runtime_seed_b64:
            DB_PATH.write_bytes(base64.b64decode(runtime_seed_b64))
        else:
            db_seed_sources = [
                SEED_DIR / "customer_ops.db",
                BASE_DIR.parent / "customer_ops.db",
                BASE_DIR.parent / "data" / "customer_ops.db",
            ]
            for seed_db in db_seed_sources:
                if seed_db.exists():
                    shutil.copy2(seed_db, DB_PATH)
                    break

    upload_seed_sources = [
        SEED_DIR / "uploads",
        BASE_DIR.parent / "uploads",
        BASE_DIR.parent / "seed-data" / "uploads",
    ]
    for seed_uploads in upload_seed_sources:
        if not seed_uploads.exists():
            continue
        for source_path in seed_uploads.rglob("*"):
            if not source_path.is_file():
                continue
            relative_path = source_path.relative_to(seed_uploads)
            destination_path = UPLOAD_DIR / relative_path
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            if not destination_path.exists():
                shutil.copy2(source_path, destination_path)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def format_simple_date(value) -> str:
    if not value:
        return "-"
    if isinstance(value, datetime):
        return value.strftime("%d-%m-%Y")
    raw = str(value).strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.strftime("%d-%m-%Y")
    except ValueError:
        return raw[:10] if len(raw) >= 10 else raw


def parse_datetime_value(value):
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    candidates = (raw, raw.replace("Z", "+00:00"))
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed
        except ValueError:
            continue
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def format_timeline_date(value: datetime, include_time: bool) -> str:
    if include_time:
        return value.strftime("%d-%m-%Y %H:%M")
    return value.strftime("%d-%m-%Y")


def build_timeline_data(projects, tasks, time_windows):
    project_names = {project["id"]: project["name"] for project in projects}
    entries = []

    for tw in time_windows:
        start_dt = parse_datetime_value(tw["start_at"])
        end_dt = parse_datetime_value(tw["end_at"])
        if not start_dt or not end_dt:
            continue
        if end_dt < start_dt:
            start_dt, end_dt = end_dt, start_dt
        project_name = project_names.get(tw["project_id"], "General")
        entries.append(
            {
                "kind": "time-window",
                "title": tw["label"] or "Time Window",
                "meta": f"{project_name} | Time Window",
                "project_name": project_name,
                "status": None,
                "start": start_dt,
                "end": end_dt,
                "range_label": f"{format_timeline_date(start_dt, include_time=True)} - {format_timeline_date(end_dt, include_time=True)}",
            }
        )

    for task in tasks:
        due_dt = parse_datetime_value(task["due_date"])
        if not due_dt:
            continue
        status = normalize_task_status(task["status"])
        project_name = project_names.get(task["project_id"], "General")
        entries.append(
            {
                "kind": "task",
                "title": task["title"],
                "meta": f"{project_name} | {TASK_STATUS_LABELS.get(status, status)}",
                "project_name": project_name,
                "status": status,
                "status_label": TASK_STATUS_LABELS.get(status, status),
                "start": due_dt,
                "end": due_dt,
                "range_label": f"Due: {format_timeline_date(due_dt, include_time=False)}",
            }
        )

    if not entries:
        return {
            "rows": [],
            "start_label": "",
            "end_label": "",
            "start_iso": "",
            "end_iso": "",
        }

    timeline_start = min(entry["start"] for entry in entries)
    timeline_end = max(entry["end"] for entry in entries)
    if timeline_end <= timeline_start:
        timeline_end = timeline_start.replace(hour=23, minute=59, second=59)
        if timeline_end <= timeline_start:
            return {
                "rows": [],
                "start_label": "",
                "end_label": "",
                "start_iso": "",
                "end_iso": "",
            }

    total_seconds = (timeline_end - timeline_start).total_seconds()
    rows = []
    sorted_entries = sorted(entries, key=lambda item: (item["start"], item["kind"], item["title"]))
    for entry in sorted_entries:
        start_offset = (entry["start"] - timeline_start).total_seconds()
        end_offset = (entry["end"] - timeline_start).total_seconds()
        left_pct = (start_offset / total_seconds) * 100
        right_pct = (end_offset / total_seconds) * 100
        min_width = 1.2 if entry["kind"] == "task" else 2.0
        width_pct = max(right_pct - left_pct, min_width)
        width_pct = min(width_pct, 100 - left_pct)
        row = dict(entry)
        row["left_pct"] = left_pct
        row["width_pct"] = width_pct
        row["start_iso"] = entry["start"].isoformat()
        row["end_iso"] = entry["end"].isoformat()
        rows.append(row)

    return {
        "rows": rows,
        "start_label": format_timeline_date(timeline_start, include_time=False),
        "end_label": format_timeline_date(timeline_end, include_time=False),
        "start_iso": timeline_start.isoformat(),
        "end_iso": timeline_end.isoformat(),
    }


def normalize_task_status(status: str) -> str:
    normalized = (status or "").strip().lower()
    alias_map = {
        "to do": "todo",
        "to-do": "todo",
        "in progress": "in_progress",
        "in-progress": "in_progress",
    }
    normalized = alias_map.get(normalized, normalized)
    return normalized if normalized in TASK_WORKFLOW_STATUSES else "todo"


def parse_requested_task_status(status: str):
    normalized = (status or "").strip().lower()
    alias_map = {
        "to do": "todo",
        "to-do": "todo",
        "in progress": "in_progress",
        "in-progress": "in_progress",
    }
    normalized = alias_map.get(normalized, normalized)
    return normalized if normalized in TASK_WORKFLOW_STATUSES else None


def sanitize_project_status_filter(raw_value: str) -> str:
    value = (raw_value or "").strip()
    valid_values = {PROJECT_FILTER_ALL, PROJECT_FILTER_NOT_DONE, *PROJECT_STATUS_OPTIONS}
    return value if value in valid_values else PROJECT_FILTER_ALL


def next_workflow_statuses(current_status: str):
    return TASK_STATUS_TRANSITIONS.get(current_status, TASK_WORKFLOW_STATUSES)


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)

