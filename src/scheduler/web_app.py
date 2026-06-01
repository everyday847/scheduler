from io import BytesIO
import json
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_file
from flask_cors import CORS

from .service import build_schedule_workbook, get_default_schedule_request, solve_schedule

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "http://localhost:3000"}})


@app.route("/api/configs", methods=["GET"])
def list_configs():
    """List available annual and standing config files."""
    from .solver_bridge import list_configs
    return jsonify(list_configs())


@app.route("/api/config/<config_type>/<filename>", methods=["GET"])
def get_config(config_type: str, filename: str):
    """Load and return a config file as JSON."""
    from .solver_bridge import CONFIG_DIR
    import yaml
    from datetime import date as date_type

    if config_type not in ("annual", "standing"):
        return jsonify({"error": "config_type must be 'annual' or 'standing'"}), 400

    path = CONFIG_DIR / config_type / filename
    if not path.exists() or not path.suffix == ".yaml":
        return jsonify({"error": f"Config not found: {filename}"}), 404

    data = yaml.safe_load(path.read_text())
    _convert_dates(data)
    return jsonify(data)


def _convert_dates(obj):
    """Recursively convert datetime.date objects to ISO strings."""
    from datetime import date as date_type
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, date_type):
                obj[k] = v.isoformat()
            else:
                _convert_dates(v)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, date_type):
                obj[i] = v.isoformat()
            else:
                _convert_dates(v)


@app.route("/api/config/annual/<filename>/draft", methods=["GET"])
def get_draft(filename: str):
    """Load draft config (falls back to published)."""
    from .solver_bridge import CONFIG_DIR
    from .draft_persistence import load_draft_or_published, has_draft
    from datetime import date as date_type

    try:
        data = load_draft_or_published(CONFIG_DIR / "annual", filename)
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404

    _convert_dates(data)
    data["_has_draft"] = has_draft(CONFIG_DIR / "annual", filename)
    return jsonify(data)


@app.route("/api/config/annual/<filename>/draft", methods=["PUT"])
def save_draft_endpoint(filename: str):
    """Save draft config."""
    from .solver_bridge import CONFIG_DIR
    from .draft_persistence import save_draft

    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "Request body must be JSON"}), 400

    save_draft(CONFIG_DIR / "annual", filename, body)
    return jsonify({"status": "saved"})


@app.route("/api/config/annual/<filename>/draft", methods=["DELETE"])
def discard_draft_endpoint(filename: str):
    """Discard draft config."""
    from .solver_bridge import CONFIG_DIR
    from .draft_persistence import discard_draft

    discard_draft(CONFIG_DIR / "annual", filename)
    return jsonify({"status": "discarded"})


@app.route("/api/config/annual/<filename>/publish", methods=["POST"])
def publish_draft_endpoint(filename: str):
    """Publish draft to replace published config."""
    from .solver_bridge import CONFIG_DIR
    from .draft_persistence import publish_draft

    try:
        publish_draft(CONFIG_DIR / "annual", filename)
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404

    return jsonify({"status": "published"})


@app.route("/api/default-schedule", methods=["GET"])
def default_schedule():
    return jsonify(get_default_schedule_request())


@app.route("/api/schedule", methods=["POST"])
def schedule():
    try:
        return jsonify(solve_schedule(request.get_json(silent=True)))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/schedule.xlsx", methods=["POST"])
def schedule_workbook():
    try:
        result = solve_schedule(request.get_json(silent=True))
        workbook = build_schedule_workbook(result)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return send_file(
        BytesIO(workbook),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="optimized_schedule.xlsx",
    )


@app.route("/api/schedule/stream", methods=["POST"])
def schedule_stream():
    """SSE endpoint: progressively solves and streams improving schedules."""
    body = request.get_json(silent=True)

    def generate():
        from .solver_bridge import build_solver_config_from_request, get_runner
        from parafrost_scheduler.schedule_solver import solve_full_schedule_progressive

        try:
            config = build_solver_config_from_request(body)
            runner = get_runner()
        except Exception as exc:
            yield _sse("error", {"message": str(exc)})
            return

        try:
            for event in solve_full_schedule_progressive(config, runner):
                yield _sse(event["type"], event)
        except GeneratorExit:
            pass
        except Exception as exc:
            yield _sse("error", {"message": str(exc)})

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _sse(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


if __name__ == "__main__":
    app.run(debug=True)
