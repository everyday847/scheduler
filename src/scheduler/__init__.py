from flask import Flask, request, jsonify
from flask_cors import CORS
from .main import optimize_schedule

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "http://localhost:3000"}})

@app.route('/api/schedule', methods=['POST'])
def your_function():
    data = request.json

    shifts_for_fellows, fellows_for_shifts = optimize_schedule(
        jr_fellows=data['jr_fellows'],
        sr_fellows=data['sr_fellows'],
        stroke_fellows=data['stroke_fellows'],
        CCM_fellows=data['CCM_fellows'],
        NH_fellows=data['NH_fellows'],
        # shifts=data['shifts'],
        fellow_week_pairs=data['fellow_week_pairs']
    )

    result = {"message": "Data received!", "shifts_for_fellows": shifts_for_fellows, "fellows_for_shifts": fellows_for_shifts}
    return jsonify(result)

if __name__ == '__main__':
    app.run(debug=True)