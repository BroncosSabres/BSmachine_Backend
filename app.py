from flask import Flask, jsonify, Response
import requests
from bs4 import BeautifulSoup
import os
from flask_cors import CORS
import json
import time
from datetime import datetime

app = Flask(__name__)
CORS(app)  

cache = {
    "data": None,
    "timestamp": 0
}

# Round lookup cache (1 hr)
round_cache = {
    "season": None,
    "round": None,
    "timestamp": 0
}

def get_current_season_and_round():
    now = time.time()
    cache_duration = 3600  # 1 hour

    # Use cached round if still valid
    if round_cache["season"] and (now - round_cache["timestamp"]) < cache_duration:
        return round_cache["season"], round_cache["round"]

    try:
        url = "https://www.nrl.com/draw/data"
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers)
        res.raise_for_status()
        data = res.json()

        current_year = datetime.now().year
        matches = [m for m in data if m.get("season") == current_year and m.get("round")]
        rounds = [m["round"] for m in matches]
        current_round = max(rounds) if rounds else 1

        round_cache["season"] = current_year
        round_cache["round"] = current_round
        round_cache["timestamp"] = now

        return current_year, current_round
    except Exception as e:
        print(f"Error getting current round: {e}")
        return datetime.now().year, 1  # fallback


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
    # Get current season and round
    #season, round_num = get_current_season_and_round()
    #url = f'https://www.nrl.com/draw/data?competition=111&season={season}&round={round_num}'
    url = f'https://www.nrl.com/draw/data?competition=111&season=2025&round=14'
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
