from io import BytesIO

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS

from .service import build_schedule_workbook, get_default_schedule_request, solve_schedule

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "http://localhost:3000"}})


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


if __name__ == "__main__":
    app.run(debug=True)
