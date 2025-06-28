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

@app.route('/api/match_team_lists/<int:match_id>')
def match_team_lists(match_id):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # Get home/away team IDs and names for the match
    cur.execute("""
        SELECT m.home_team_id, m.away_team_id, t1.name as home_team, t2.name as away_team
        FROM matches m
        JOIN teams t1 ON m.home_team_id = t1.id
        JOIN teams t2 ON m.away_team_id = t2.id
        WHERE m.id = %s
    """, (match_id,))
    match = cur.fetchone()
    if not match:
        cur.close()
        conn.close()
        return jsonify({"error": "Match not found"}), 404

    # Get all players for the team (excluding 'Replacement')
    def get_team_players(team_id):
        cur.execute("""
            SELECT p.id, p.name, tl.position, tl.starter, tl.jersey_number
            FROM team_list tl
            JOIN players p ON tl.player_id = p.id
            WHERE tl.match_id = %s AND tl.team_id = %s AND tl.position <> 'Replacement'
        """, (match_id, team_id))
        players = cur.fetchall()
        return players

    # Helper to select team list in NRL order
    def order_nrl_team_list(players):
        # Normalize and group players
        pos_map = {
            'FB': 'Fullback', 'Fullback': 'Fullback',
            'WG': 'Wing', 'Wing': 'Wing',
            'CE': 'Centre', 'Centre': 'Centre',
            'FE': 'Five-eighth', 'Five-eighth': 'Five-eighth',
            'HB': 'Halfback', 'Halfback': 'Halfback',
            'PR': 'Front row', 'Front row': 'Front row', 
            'HK': 'Hooker', 'Hooker': 'Hooker',
            'SR': 'Second row', 'Second row': 'Second row',
            'LK': 'Lock', 'Lock': 'Lock',
            'Interchange': 'Bench', 'Bench': 'Bench', 'Reserve': 'Bench'
        }

        # Normalize positions
        for p in players:
            p['norm_pos'] = pos_map.get(p['position'], p['position'])
        used_players = set()
        result = []

        # Helper to pick from group and mark used
        def pick(players, norm_pos, pick='min'):
            # pick: 'min' (lowest jersey), 'max' (highest jersey)
            candidates = [p for p in players if p['norm_pos'] == norm_pos and p['id'] not in used_players]
            if not candidates:
                return None
            candidates = [p for p in candidates if p['jersey_number'] is not None]
            if not candidates:
                return None
            target = min(candidates, key=lambda x: x['jersey_number']) if pick == 'min' else max(candidates, key=lambda x: x['jersey_number'])
            used_players.add(target['id'])
            return target

        # 1. Fullback
        fb = pick(players, 'Fullback')
        if fb: result.append(fb)
        # 2. Wing (lowest jersey)
        wing1 = pick(players, 'Wing', pick='min')
        if wing1: result.append(wing1)
        # 3. Centre (lowest jersey)
        centre1 = pick(players, 'Centre', pick='min')
        if centre1: result.append(centre1)
        # 4. Centre (highest jersey)
        centre2 = pick(players, 'Centre', pick='max')
        if centre2: result.append(centre2)
        # 5. Wing (highest jersey)
        wing2 = pick(players, 'Wing', pick='max')
        if wing2: result.append(wing2)
        # 6. Five-eighth
        fe = pick(players, 'Five-eighth')
        if fe: result.append(fe)
        # 7. Halfback
        hb = pick(players, 'Halfback')
        if hb: result.append(hb)
        # 8. Prop (lowest jersey)
        prop1 = pick(players, 'Front row', pick='min')
        if prop1: result.append(prop1)
        # 9. Hooker
        hk = pick(players, 'Hooker')
        if hk: result.append(hk)
        # 10. Prop (highest jersey)
        prop2 = pick(players, 'Front row', pick='max')
        if prop2: result.append(prop2)
        # 11. Second Row (lowest jersey)
        sr1 = pick(players, 'Second row', pick='min')
        if sr1: result.append(sr1)
        # 12. Second Row (highest jersey)
        sr2 = pick(players, 'Second row', pick='max')
        if sr2: result.append(sr2)
        # 13. Lock
        lk = pick(players, 'Lock')
        if lk: result.append(lk)
        # 14–17. Bench (lowest jerseys)
        bench = [p for p in players if p['norm_pos'] == 'Bench' and p['id'] not in used_players]
        bench = [p for p in bench if p['jersey_number'] is not None]
        bench.sort(key=lambda x: x['jersey_number'])
        for p in bench[:4]:
            used_players.add(p['id'])
            result.append(p)
        return result

    # Get and order home and away teams
    home_players = order_nrl_team_list(get_team_players(match['home_team_id']))
    away_players = order_nrl_team_list(get_team_players(match['away_team_id']))

    cur.close()
    conn.close()
    return jsonify({
        "home_team": match['home_team'],
        "home_players": home_players,
        "away_team": match['away_team'],
        "away_players": away_players
    })

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
