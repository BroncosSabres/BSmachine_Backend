from flask import Flask, jsonify, Response
import requests
from bs4 import BeautifulSoup
import os
from flask_cors import CORS
import json

app = Flask(__name__)
CORS(app)  

@app.route('/latest-results')
def latest_results():
    url = 'https://www.nrl.com/draw/data?competition=111&season=2025&round=8'
    headers = {'User-Agent': 'Mozilla/5.0'}  # Required to avoid 403 errors
    res = requests.get(url, headers=headers)
    data = res.json()  # Now safe

    results = []
    for match in data.get("fixtures", []):
        home_team = match['homeTeam']['nickName']
        away_team = match['awayTeam']['nickName']
        home_score = match['homeTeam'].get('score')
        away_score = match['awayTeam'].get('score')

        if home_score is not None and away_score is not None:
            winner = home_team if home_score > away_score else away_team
            results.append({
                'home': home_team,
                'away': away_team,
                'home_score': home_score,
                'away_score': away_score,
                'winner': winner
            })

    return Response(json.dumps(results), mimetype='application/json')


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
