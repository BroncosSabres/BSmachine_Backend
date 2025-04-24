from flask import Flask, jsonify
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)

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

    return jsonify(results)


if __name__ == '__main__':
    app.run(debug=True)
