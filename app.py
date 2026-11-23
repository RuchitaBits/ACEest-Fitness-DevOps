import math
import os
import sqlite3
from datetime import date
from functools import wraps
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from flask import Flask, flash, g, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


BASE_DIR = Path(__file__).resolve().parent
DATABASE = Path(
    os.environ.get("DATABASE_PATH")
    or os.environ.get("ACEEST_DB_PATH")
    or BASE_DIR / "instance" / "aceest.db"
)

PROGRAMS = {
    "Fat Loss": {"factor": 22, "workout": "Strength and conditioning", "nutrition": "Protein-rich meals with a measured calorie deficit."},
    "Muscle Gain": {"factor": 35, "workout": "Progressive resistance training", "nutrition": "Protein-rich meals with a measured calorie surplus."},
    "Beginner": {"factor": 26, "workout": "Full-body fundamentals", "nutrition": "Balanced meals focused on consistency and recovery."},
}
EXERCISES = {
    "beginner": ["Push-Up", "Goblet Squat", "Dumbbell Row", "Plank"],
    "intermediate": ["Bench Press", "Barbell Row", "Leg Press", "Lateral Raise"],
    "advanced": ["Deadlift", "Overhead Press", "Pull-Up", "Kettlebell Swings"],
}


def _as_bool(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def get_app_version():
    version = os.environ.get("APP_VERSION")
    version_file = BASE_DIR / "VERSION"
    if version:
        return version
    return version_file.read_text(encoding="utf-8").strip() if version_file.is_file() else ""


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY") or os.environ.get("FLASK_SECRET_KEY"),
        DATABASE=str(
            os.environ.get("DATABASE_PATH")
            or os.environ.get("ACEEST_DB_PATH")
            or DATABASE
        ),
        DEMO_USERNAME=os.environ.get("DEMO_USERNAME") or os.environ.get("ADMIN_USERNAME"),
        DEMO_PASSWORD=os.environ.get("DEMO_PASSWORD") or os.environ.get("ADMIN_PASSWORD"),
        APP_VERSION=os.environ.get("APP_VERSION") or get_app_version(),
        SHADOW_MODE=_as_bool(os.environ.get("SHADOW_MODE")),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=_as_bool(os.environ.get("SESSION_COOKIE_SECURE")),
    )
    if test_config:
        app.config.update(test_config)
    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)

    @app.teardown_appcontext
    def close_db(_error=None):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    @app.before_request
    def ignore_shadow_writes():
        if app.config["SHADOW_MODE"] and request.method not in {"GET", "HEAD", "OPTIONS"}:
            app.logger.info("Ignored state-changing request received by shadow deployment")
            return jsonify(status="shadow request ignored"), 202
        return None

    @app.after_request
    def add_version_header(response):
        if app.config["APP_VERSION"]:
            response.headers["X-Application-Version"] = app.config["APP_VERSION"]
        return response

    with app.app_context():
        init_db(app.config["DEMO_USERNAME"], app.config["DEMO_PASSWORD"])

    @app.route("/")
    def index():
        return render_template("index.html", programs=PROGRAMS)

    @app.route("/health")
    def health():
        query_db("SELECT 1", one=True)
        return jsonify(status="healthy")

    @app.route("/login", methods=("GET", "POST"))
    def login():
        if request.method == "POST":
            if not all(
                app.config.get(key)
                for key in ("SECRET_KEY", "DEMO_USERNAME", "DEMO_PASSWORD")
            ):
                return render_template(
                    "error.html",
                    message="Authentication is not configured.",
                    error_code=503,
                ), 503
            username = request.form.get("username", "").strip()
            user = query_db("SELECT * FROM users WHERE username = ?", (username,), one=True)
            if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
                session.clear()
                session["user_id"] = user["id"]
                return redirect(url_for("dashboard"))
            flash("Invalid username or password.", "error")
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("index"))

    @app.route("/dashboard")
    @login_required
    def dashboard():
        clients = query_db("SELECT * FROM clients ORDER BY name")
        workouts = query_db("SELECT workouts.*, clients.name AS client_name FROM workouts JOIN clients ON clients.id = workouts.client_id ORDER BY workout_date DESC LIMIT 5")
        active = sum(client["membership_status"] == "Active" for client in clients)
        return render_template("dashboard.html", clients=clients, workouts=workouts, active=active, programs=PROGRAMS)

    @app.route("/clients")
    @login_required
    def clients():
        return render_template("clients.html", clients=query_db("SELECT * FROM clients ORDER BY name"), programs=PROGRAMS)

    @app.route("/clients/add", methods=("POST",))
    @login_required
    def add_client():
        form = request.form
        try:
            name = form.get("name", "").strip()
            age, height, weight, target_weight = (
                float(form.get(key, 0))
                for key in ("age", "height", "weight", "target_weight")
            )
            target_adherence = float(form.get("target_adherence", 80))
            program = form.get("program", "")
            measurements = (age, height, weight, target_weight, target_adherence)
            if (
                not name
                or program not in PROGRAMS
                or not all(math.isfinite(value) for value in measurements)
                or min(age, height, weight, target_weight) <= 0
                or not 0 <= target_adherence <= 100
            ):
                raise ValueError
            execute_db(
                "INSERT INTO clients (name, age, height, weight, program, estimated_calories, target_weight, target_adherence, membership_status, membership_end) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    name,
                    age,
                    height,
                    weight,
                    program,
                    calculate_calories(weight, program),
                    target_weight,
                    target_adherence,
                    form.get("membership_status", "Active"),
                    form.get("membership_end", ""),
                ),
            )
            flash("Client added.", "success")
        except (TypeError, ValueError):
            flash("Please provide valid client details.", "error")
        return redirect(url_for("clients"))

    @app.route("/clients/<int:client_id>")
    @login_required
    def client_detail(client_id):
        client = query_db("SELECT * FROM clients WHERE id = ?", (client_id,), one=True)
        if client is None:
            return render_template(
                "error.html", message="Client not found.", error_code=404
            ), 404
        progress = query_db("SELECT * FROM progress WHERE client_id = ? ORDER BY week_date DESC", (client_id,))
        metrics = query_db("SELECT * FROM metrics WHERE client_id = ? ORDER BY metric_date DESC", (client_id,))
        bmi = round(
            float(client["weight"]) / (float(client["height"]) / 100) ** 2, 1
        )
        return render_template(
            "client_detail.html",
            client=client,
            progress=progress,
            metrics=metrics,
            bmi=bmi,
            weight_chart=_chart_points(metrics, "weight"),
            adherence_chart=_chart_points(progress, "adherence", 0, 100),
        )

    @app.route("/clients/<int:client_id>/metrics", methods=("POST",))
    @login_required
    def add_metric(client_id):
        client = query_db("SELECT id FROM clients WHERE id = ?", (client_id,), one=True)
        if client is None:
            return render_template(
                "error.html", message="Client not found.", error_code=404
            ), 404

        form = request.form
        try:
            metric_date = form.get("metric_date") or date.today().isoformat()
            date.fromisoformat(metric_date)
            measurements = {}
            for field in ("weight", "waist", "body_fat"):
                raw_value = form.get(field, "").strip()
                if raw_value:
                    value = float(raw_value)
                    if not math.isfinite(value) or value <= 0:
                        raise ValueError
                    if field == "body_fat" and value > 100:
                        raise ValueError
                    measurements[field] = value
            if not measurements:
                raise ValueError

            execute_db(
                "INSERT INTO metrics (client_id, metric_date, weight, waist, body_fat) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    client_id,
                    metric_date,
                    measurements.get("weight"),
                    measurements.get("waist"),
                    measurements.get("body_fat"),
                ),
            )
            flash("Body metrics saved.", "success")
        except (TypeError, ValueError):
            flash("Enter a valid date and at least one valid measurement.", "error")
        return redirect(url_for("client_detail", client_id=client_id))

    @app.route("/clients/<int:client_id>/report.pdf")
    @login_required
    def client_report(client_id):
        client = query_db("SELECT * FROM clients WHERE id = ?", (client_id,), one=True)
        if client is None:
            return render_template(
                "error.html", message="Client not found.", error_code=404
            ), 404

        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

        metrics = query_db(
            "SELECT metric_date, weight, waist, body_fat FROM metrics "
            "WHERE client_id = ? ORDER BY metric_date DESC",
            (client_id,),
        )
        progress = query_db(
            "SELECT week_date, adherence FROM progress "
            "WHERE client_id = ? ORDER BY week_date DESC",
            (client_id,),
        )
        workouts = query_db(
            "SELECT workouts.workout_date, workouts.workout_type, workouts.duration, "
            "GROUP_CONCAT(exercises.name, ', ') AS exercise_names "
            "FROM workouts LEFT JOIN exercises ON exercises.workout_id = workouts.id "
            "WHERE workouts.client_id = ? "
            "GROUP BY workouts.id ORDER BY workouts.workout_date DESC",
            (client_id,),
        )

        buffer = BytesIO()
        document = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=0.65 * inch,
            leftMargin=0.65 * inch,
            title=f"Fitness report for {client['name']}",
        )
        styles = getSampleStyleSheet()
        bmi = float(client["weight"]) / (float(client["height"]) / 100) ** 2
        story = [
            Paragraph(f"Fitness report: {escape(client['name'])}", styles["Title"]),
            Spacer(1, 0.15 * inch),
            Paragraph(
                f"Program: {escape(client['program'])} | "
                f"Membership: {escape(client['membership_status'])}",
                styles["BodyText"],
            ),
            Paragraph(
                f"Current weight: {client['weight']:g} kg | "
                f"Target weight: {client['target_weight']:g} kg | "
                f"Estimated calories: {client['estimated_calories']:g} | "
                f"BMI estimate: {bmi:.1f}",
                styles["BodyText"],
            ),
            Paragraph("BMI is a screening estimate, not a medical diagnosis.", styles["Italic"]),
            Spacer(1, 0.2 * inch),
            Paragraph("Body metrics", styles["Heading2"]),
        ]
        story.append(
            _report_table(
                [["Date", "Weight (kg)", "Waist", "Body fat (%)"]]
                + [
                    [
                        row["metric_date"],
                        _report_value(row["weight"]),
                        _report_value(row["waist"]),
                        _report_value(row["body_fat"]),
                    ]
                    for row in metrics
                ]
            )
        )
        story.extend([Spacer(1, 0.15 * inch), Paragraph("Weekly progress", styles["Heading2"])])
        story.append(
            _report_table(
                [["Week", "Adherence (%)"]]
                + [[row["week_date"], _report_value(row["adherence"])] for row in progress]
            )
        )
        story.extend([Spacer(1, 0.15 * inch), Paragraph("Workout history", styles["Heading2"])])
        story.append(
            _report_table(
                [["Date", "Workout", "Duration", "Exercises"]]
                + [
                    [
                        row["workout_date"],
                        escape(row["workout_type"]),
                        f"{row['duration']} min",
                        escape(row["exercise_names"] or ""),
                    ]
                    for row in workouts
                ]
            )
        )
        document.build(story)
        buffer.seek(0)
        return send_file(
            buffer,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"client-{client_id}-report.pdf",
        )

    @app.route("/clients/<int:client_id>/update", methods=("POST",))
    @login_required
    def update_client(client_id):
        form = request.form
        try:
            weight = float(form["weight"])
            program = form["program"]
            if not math.isfinite(weight) or weight <= 0 or program not in PROGRAMS:
                raise ValueError
            execute_db("UPDATE clients SET weight = ?, program = ?, estimated_calories = ?, membership_status = ?, membership_end = ? WHERE id = ?", (weight, program, calculate_calories(weight, program), form.get("membership_status", "Active"), form.get("membership_end", ""), client_id))
            flash("Client updated.", "success")
        except (KeyError, TypeError, ValueError):
            flash("Invalid update details.", "error")
        return redirect(url_for("client_detail", client_id=client_id))

    @app.route("/clients/<int:client_id>/delete", methods=("POST",))
    @login_required
    def delete_client(client_id):
        execute_db("DELETE FROM clients WHERE id = ?", (client_id,))
        flash("Client deleted.", "success")
        return redirect(url_for("clients"))

    @app.route("/programs")
    @login_required
    def programs():
        return render_template("programs.html", programs=PROGRAMS)

    @app.route("/programs/generate", methods=("POST",))
    @login_required
    def generate_program():
        program = request.form.get("program", "Beginner")
        level = request.form.get("experience", "beginner").lower()
        if program not in PROGRAMS or level not in EXERCISES:
            flash("Select a valid program and experience level.", "error")
            return redirect(url_for("programs"))
        generated = list(EXERCISES[level])
        session["generated_program"] = {"program": program, "experience": level.title(), "exercises": generated}
        return render_template("programs.html", programs=PROGRAMS, generated=session["generated_program"])

    @app.route("/workouts")
    @login_required
    def workouts():
        clients = query_db("SELECT id, name FROM clients ORDER BY name")
        records = query_db(
            "SELECT workouts.*, clients.name AS client_name, "
            "GROUP_CONCAT(exercises.name, ', ') AS exercise_names "
            "FROM workouts JOIN clients ON clients.id = workouts.client_id "
            "LEFT JOIN exercises ON exercises.workout_id = workouts.id "
            "GROUP BY workouts.id ORDER BY workout_date DESC"
        )
        return render_template("workouts.html", clients=clients, workouts=records)

    @app.route("/workouts/add", methods=("POST",))
    @login_required
    def add_workout():
        form = request.form
        try:
            client_id = int(form["client_id"])
            duration = int(form["duration"])
            if duration <= 0 or not query_db("SELECT id FROM clients WHERE id = ?", (client_id,), one=True):
                raise ValueError
            exercise_name = form.get("exercise_name", "").strip()
            exercise_values = None
            if exercise_name:
                sets = int(form["sets"])
                reps = int(form["reps"])
                raw_weight = form.get("exercise_weight", "").strip()
                exercise_weight = float(raw_weight) if raw_weight else None
                if (
                    sets <= 0
                    or reps <= 0
                    or (exercise_weight is not None and (
                        not math.isfinite(exercise_weight) or exercise_weight < 0
                    ))
                ):
                    raise ValueError
                exercise_values = (exercise_name, sets, reps, exercise_weight)
            elif any(form.get(field, "").strip() for field in ("sets", "reps", "exercise_weight")):
                raise ValueError

            db = get_db()
            cursor = db.execute(
                "INSERT INTO workouts (client_id, workout_date, workout_type, duration, notes) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    client_id,
                    form.get("workout_date") or date.today().isoformat(),
                    form.get("workout_type", "Strength"),
                    duration,
                    form.get("notes", "").strip(),
                ),
            )
            if exercise_values:
                db.execute(
                    "INSERT INTO exercises (workout_id, name, sets, reps, weight) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (cursor.lastrowid, *exercise_values),
                )
            db.commit()
            flash("Workout recorded.", "success")
        except (KeyError, TypeError, ValueError):
            flash("Please provide valid workout details.", "error")
        return redirect(url_for("workouts"))

    @app.route("/clients/<int:client_id>/progress", methods=("POST",))
    @login_required
    def add_progress(client_id):
        client = query_db("SELECT id FROM clients WHERE id = ?", (client_id,), one=True)
        if client is None:
            return render_template(
                "error.html", message="Client not found.", error_code=404
            ), 404

        try:
            adherence = float(request.form["adherence"])
            week_date = request.form.get("week_date") or date.today().isoformat()
            date.fromisoformat(week_date)
            if not math.isfinite(adherence) or not 0 <= adherence <= 100:
                raise ValueError
            execute_db(
                "INSERT INTO progress (client_id, week_date, adherence) "
                "VALUES (?, ?, ?)",
                (client_id, week_date, adherence),
            )
            flash("Progress saved.", "success")
        except (KeyError, TypeError, ValueError):
            flash("Enter a valid date and adherence between 0 and 100.", "error")
        return redirect(url_for("client_detail", client_id=client_id))

    return app


def login_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view(**kwargs)
    return wrapped_view


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app().config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def current_app():
    from flask import current_app as flask_current_app
    return flask_current_app


def query_db(query, args=(), one=False):
    cursor = get_db().execute(query, args)
    rows = cursor.fetchall()
    cursor.close()
    return (rows[0] if rows else None) if one else rows


def execute_db(query, args=()):
    db = get_db()
    cursor = db.execute(query, args)
    db.commit()
    return cursor.lastrowid


def init_db(username=None, password=None):
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS clients (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, age REAL NOT NULL, height REAL NOT NULL, weight REAL NOT NULL, program TEXT NOT NULL, estimated_calories REAL NOT NULL, target_weight REAL NOT NULL, target_adherence REAL NOT NULL, membership_status TEXT NOT NULL, membership_end TEXT);
        CREATE TABLE IF NOT EXISTS progress (id INTEGER PRIMARY KEY AUTOINCREMENT, client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, week_date TEXT NOT NULL, adherence REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS workouts (id INTEGER PRIMARY KEY AUTOINCREMENT, client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, workout_date TEXT NOT NULL, workout_type TEXT NOT NULL, duration INTEGER NOT NULL, notes TEXT);
        CREATE TABLE IF NOT EXISTS exercises (id INTEGER PRIMARY KEY AUTOINCREMENT, workout_id INTEGER NOT NULL REFERENCES workouts(id) ON DELETE CASCADE, name TEXT NOT NULL, sets INTEGER, reps INTEGER, weight REAL);
        CREATE TABLE IF NOT EXISTS metrics (id INTEGER PRIMARY KEY AUTOINCREMENT, client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, metric_date TEXT NOT NULL, weight REAL, waist REAL, body_fat REAL);
    """)
    if username and password:
        password_hash = generate_password_hash(password)
        db.execute(
            "INSERT OR IGNORE INTO users (username, password_hash) VALUES (?, ?)",
            (username, password_hash),
        )
    db.commit()


def calculate_calories(weight, program):
    weight = float(weight)
    if program not in PROGRAMS or not math.isfinite(weight) or weight <= 0:
        raise ValueError("Weight and program must be valid")
    return round(weight * PROGRAMS[program]["factor"], 2)


def _report_value(value):
    return "" if value is None else f"{value:g}"


def _chart_points(rows, field, minimum=None, maximum=None):
    values = [float(row[field]) for row in reversed(rows) if row[field] is not None]
    if not values:
        return ""
    low = min(values) if minimum is None else minimum
    high = max(values) if maximum is None else maximum
    if high == low:
        low -= 1
        high += 1

    width, height, margin = 600, 180, 20
    x_span = width - 2 * margin
    y_span = height - 2 * margin
    points = []
    for index, value in enumerate(values):
        x = margin if len(values) == 1 else margin + index * x_span / (len(values) - 1)
        y = height - margin - (value - low) * y_span / (high - low)
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)


def _report_table(rows):
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    if len(rows) == 1:
        rows.append(["No records"] + [""] * (len(rows[0]) - 1))
    table = Table(rows, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#183b35")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#d6dfdc")),
                ("PADDING", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return table


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))