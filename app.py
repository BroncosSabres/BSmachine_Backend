from flask import Flask, jsonify, Response
import requests
from bs4 import BeautifulSoup
import os
from flask_cors import CORS
import json
import time

app = Flask(__name__)
CORS(app)  

cache = {
    "data": None,
    "timestamp": 0
}

@app.route('/latest-results')
def latest_results():
    now = time.time()
    cache_duration = 300  # seconds = 5 minutes

    # Use cache if still valid
    if cache["data"] and (now - cache["timestamp"]) < cache_duration:
        return app.response_class(
            response=json.dumps(cache["data"]),
            status=200,
            mimetype='application/json'
        )

    # Otherwise fetch fresh data
    url = 'https://www.nrl.com/draw/data?competition=111&season=2025&round=8'
    headers = {'User-Agent': 'Mozilla/5.0'}
    res = requests.get(url, headers=headers)
    data = res.json()

    results = []
    for match in data.get("fixtures", []):
        home_team = match['homeTeam']['nickName']
        away_team = match['awayTeam']['nickName']
        home_score = match['homeTeam'].get('score')
        away_score = match['awayTeam'].get('score')

        if home_score is not None and away_score is not None:
            winner = home_score > away_score and home_team or away_team
            results.append({
                'home': home_team,
                'away': away_team,
                'home_score': home_score,
                'away_score': away_score,
                'winner': winner
            })

    # Save to cache
    cache["data"] = results
    cache["timestamp"] = now

    return app.response_class(
        response=json.dumps(results),
        status=200,
        mimetype='application/json'
    )

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
