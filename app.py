from flask import Flask, jsonify, Response
import requests
from bs4 import BeautifulSoup
import os
from flask_cors import CORS
import json
import time
from datetime import datetime
import re
import psycopg2
from psycopg2.extras import RealDictCursor

def get_db_connection():
    return psycopg2.connect(
        host=os.environ.get('PGHOST'),
        dbname=os.environ.get('PGDATABASE'),
        user=os.environ.get('PGUSER'),
        password=os.environ.get('PGPASSWORD')
    )

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

# map NRL nicknames to your site’s naming convention
TEAM_NAME_MAP = {
    'Sea Eagles': 'Manly',
    'Wests Tigers': 'Tigers',
    # Add others if needed in future
}

def extract_round(match):
    if "roundTitle" in match and match["roundTitle"].startswith("Round"):
        return int(match["roundTitle"].split()[-1])
    elif "matchCentreUrl" in match:
        m = re.search(r'round-(\d+)', match["matchCentreUrl"])
        if m:
            return int(m.group(1))
    return None

def get_current_season_and_round():
    now = time.time()
    cache_duration = 3600  # 1 hour

    # Use cached round if still valid
    if round_cache["season"] and (now - round_cache["timestamp"]) < cache_duration:
        print(f"[CACHE] Returning cached round: {round_cache['season']}, {round_cache['round']}")
        return round_cache["season"], round_cache["round"]

    try:
        url = "https://www.nrl.com/draw/data"
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers)
        res.raise_for_status()
        data = res.json()

        fixtures = data.get("fixtures", [])
        current_year = datetime.now().year
        selected = data.get("selectedRoundId", 1)
        fixtures = data.get("fixtures", [])

        any_played = any(
            (m.get("homeTeam", {}).get("score") is not None or
             m.get("awayTeam", {}).get("score") is not None)
            for m in fixtures
        )

        current_round = selected if any_played else max(1, selected - 1)
        
        round_cache["season"] = current_year
        round_cache["round"] = current_round
        round_cache["timestamp"] = now

        print(f"[DEBUG] Returning current round: {current_year}, {current_round}")
        
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
    season, round_num = get_current_season_and_round()
    url = f'https://www.nrl.com/draw/data?competition=111&season={season}&round={round_num}'
    #url = f'https://www.nrl.com/draw/data?competition=111&season=2025&round=15'
    headers = {'User-Agent': 'Mozilla/5.0'}
    res = requests.get(url, headers=headers)
    data = res.json()

    results = []
    for match in data.get("fixtures", []):
        home_team = TEAM_NAME_MAP.get(match['homeTeam']['nickName'], match['homeTeam']['nickName'])
        away_team = TEAM_NAME_MAP.get(match['awayTeam']['nickName'], match['awayTeam']['nickName'])
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
    
@app.route('/api/upcoming_matches')
def upcoming_matches():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT
            m.id as match_id,
            m.date,
            r.round_number,
            s.year as season_year,
            t_home.name as home_team,
            t_away.name as away_team,
            m.venue
        FROM matches m
        JOIN rounds r ON m.round_id = r.id
        JOIN seasons s ON r.season_id = s.id
        JOIN teams t_home ON m.home_team_id = t_home.id
        JOIN teams t_away ON m.away_team_id = t_away.id
        WHERE m.is_finished = FALSE
          AND m.date >= CURRENT_DATE
        ORDER BY m.date ASC, r.round_number ASC
    """)
    matches = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(matches)

@app.route('/api/current_round_matches')
def current_round_matches():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # 1. Get the current round_id (whose start_date <= today)
    cur.execute("""
        SELECT id, round_number, season_id
        FROM rounds
        WHERE start_date <= CURRENT_DATE
        ORDER BY start_date DESC
        LIMIT 1
    """)
    round_row = cur.fetchone()
    if not round_row:
        cur.close()
        conn.close()
        return jsonify({"error": "No round found"}), 404

    round_id = round_row["id"]

    # 2. Get all matches for that round
    cur.execute("""
        SELECT
            m.id as match_id,
            m.date,
            r.round_number,
            s.year as season_year,
            t_home.name as home_team,
            t_away.name as away_team,
            m.venue,
            m.is_finished,
            m.home_score,
            m.away_score
        FROM matches m
        JOIN rounds r ON m.round_id = r.id
        JOIN seasons s ON r.season_id = s.id
        JOIN teams t_home ON m.home_team_id = t_home.id
        JOIN teams t_away ON m.away_team_id = t_away.id
        WHERE m.round_id = %s
        ORDER BY m.date ASC
    """, (round_id,))

    matches = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(matches)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
