from flask import Flask, jsonify, Response, request
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
import numpy as np
from scipy.special import comb, factorial

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
    
def multinomial_at_least(n, probs, mins):
    """
    Compute P(X1 >= mins[0], X2 >= mins[1], ..., XK >= mins[K-1]) 
    where (X1,...,XK, X_other) ~ Multinomial(n, [p1,...,pK, p_other]).
    Uses inclusion-exclusion principle.
    """
    K = len(probs)
    p_other = 1.0 - sum(probs)
    all_probs = list(probs) + [p_other]
    all_mins = list(mins) + [0] # 'other' has no lower limit
    
    total = 0.0
    subsets = []
    for mask in range(1, 1 << K):
        subset = [i for i in range(K) if (mask & (1 << i))]
        subsets.append(subset)

    for subset in subsets:
        sign = (-1) ** (len(subset) + 1)
        upper_limits = [mins[i] - 1 if i in subset else n for i in range(K)]
        allocs = []
        def gen(idx, remaining, cur):
            if idx == K:
                cur_other = remaining
                if cur_other >= 0:
                    allocs.append(cur + [cur_other])
                return
            max_k = min(upper_limits[idx], remaining)
            for x in range(0, max_k + 1):
                gen(idx + 1, remaining - x, cur + [x])
        gen(0, n, [])
        prob = 0.0
        for alloc in allocs:
            multinom = factorial(n)
            for x in alloc:
                multinom /= factorial(x)
            p = 1.0
            for i, x in enumerate(alloc):
                p *= all_probs[i] ** x
            prob += multinom * p
        total += sign * prob
    return 1.0 - total

def joint_min_tries_probability(try_dist, player_probs, min_tries):
    """
    try_dist: dict of {n_tries: prob}
    player_probs: list of per-try probabilities for selected players
    min_tries: list of minimum required tries for each selected player
    Returns: joint probability
    """
    result = 0.0
    for n_tries_str, p_n in try_dist.items():
        n_tries = int(n_tries_str)
        if n_tries < sum(min_tries):
            continue
        joint_prob = multinomial_at_least(n_tries, player_probs, min_tries)
        result += p_n * joint_prob
    return result

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
            SELECT p.id, p.name, tl.position, tl.starter, tl.jersey_number, tl.team_id
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
    
@app.route('/api/player_try_probabilities/<int:match_id>/<int:team_id>')
def player_try_probabilities(match_id, team_id):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # Get last 3 seasons' IDs
    cur.execute("SELECT id FROM seasons ORDER BY year DESC LIMIT 2")
    recent_season_ids = [row['id'] for row in cur.fetchall()]

    # Get all players named for this match/team, with position
    cur.execute("""
        SELECT p.id, tl.position
        FROM team_list tl
        JOIN players p ON tl.player_id = p.id
        WHERE tl.match_id = %s AND tl.team_id = %s AND tl.position <> 'Replacement'
    """, (match_id, team_id))
    player_rows = cur.fetchall()

    try_probs = {}
    pos_try_rates = {}  # position: list of (prob, matches_played)

    # Calculate probability and store (prob, matches_played) for position
    for row in player_rows:
        pid = row['id']
        position = row['position']
        cur.execute("""
            SELECT 
                SUM(COALESCE(ps.tries, 0)) AS tries,
                COUNT(*) AS matches_played
            FROM player_stats ps
            JOIN matches m ON ps.match_id = m.id
            JOIN rounds r ON m.round_id = r.id
            WHERE ps.player_id = %s
              AND r.season_id = ANY(%s)
              AND ps.position = %s
        """, (pid, recent_season_ids, position))
        stats = cur.fetchone()
        tries = stats['tries'] or 0
        matches_played = stats['matches_played'] or 0
        if matches_played >= 5:
            effective_tries = tries if tries > 0 else 1
            prob = effective_tries / matches_played
            try_probs[pid] = prob
            pos_try_rates.setdefault(position, []).append((prob, matches_played))
        else:
            try_probs[pid] = None  # flag for fallback

    # Now set fallback probabilities for low-sample players
    for row in player_rows:
        pid = row['id']
        position = row['position']
        if try_probs[pid] is None:
            # Get all position rates with >= 5 matches
            pos_list = [prob for prob, matches in pos_try_rates.get(position, []) if matches >= 5]
            if pos_list:
                avg = sum(pos_list) / len(pos_list)
            else:
                # Fallback to overall mean (players with >=5 matches)
                all_probs = [prob for pid2, prob in try_probs.items() if prob is not None]
                avg = sum(all_probs) / len(all_probs) if all_probs else 0.05
            try_probs[pid] = avg
            
    # 5. NORMALIZE: so each value is the probability any given try by this team is scored by that player
    total_rate = sum(try_probs.values())
    if total_rate > 0:
        norm_try_probs = {str(pid): prob / total_rate for pid, prob in try_probs.items()}
    else:
        n = len(try_probs)
        norm_try_probs = {str(pid): 1 / n for pid in try_probs} if n > 0 else {}

    cur.close()
    conn.close()
    return jsonify(norm_try_probs)

@app.route('/api/match_try_distribution/<int:match_id>/<int:team_id>')
def match_try_distribution(match_id, team_id):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT distribution
        FROM match_try_distributions
        WHERE match_id = %s AND team_id = %s
        ORDER BY generated_at DESC
        LIMIT 1
    """, (match_id, team_id))
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row or not row['distribution']:
        # If there is no data, return an empty dict or a default distribution
        return jsonify({})
    return jsonify(row['distribution'])

@app.route('/api/match_sgm_bins_range/<int:match_id>')
def match_sgm_bins_range(match_id):
    # Get filters from query parameters
    margin_gte = request.args.get('margin_gte', type=int)
    margin_lte = request.args.get('margin_lte', type=int)
    total_gte = request.args.get('total_gte', type=int)
    total_lte = request.args.get('total_lte', type=int)

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT margin, total_points, home_try_dist, away_try_dist, count
        FROM match_sgm_bins
        WHERE match_id = %s
    """, (match_id,))
    bins = cur.fetchall()
    cur.close()
    conn.close()

    # Filter bins according to query params
    filtered_bins = []
    for row in bins:
        m, t = row['margin'], row['total_points']
        if margin_gte is not None and m < margin_gte:
            continue
        if margin_lte is not None and m > margin_lte:
            continue
        if total_gte is not None and t < total_gte:
            continue
        if total_lte is not None and t > total_lte:
            continue
        filtered_bins.append(row)

    if not filtered_bins:
        return jsonify({"error": "No bins found for selection"}), 404

    # Aggregate distributions weighted by count
    total_count = sum(row['count'] for row in filtered_bins) or 1
    agg_home_dist = {}
    agg_away_dist = {}
    for row in filtered_bins:
        c = row['count']
        # Home tries
        for k, v in row['home_try_dist'].items():
            agg_home_dist[k] = agg_home_dist.get(k, 0) + v * c
        # Away tries
        for k, v in row['away_try_dist'].items():
            agg_away_dist[k] = agg_away_dist.get(k, 0) + v * c

    # Normalize
    for k in agg_home_dist:
        agg_home_dist[k] /= total_count
    for k in agg_away_dist:
        agg_away_dist[k] /= total_count

    # Probability of being in the selected bins
    total_bins_count = sum(row['count'] for row in bins) or 1
    selection_prob = total_count / total_bins_count

    return jsonify({
        "match_id": match_id,
        "margin_filter": {"gte": margin_gte, "lte": margin_lte},
        "total_points_filter": {"gte": total_gte, "lte": total_lte},
        "home_try_dist": agg_home_dist,
        "away_try_dist": agg_away_dist,
        "total_count": total_count,
        "prob": selection_prob
    })
    
@app.route('/api/match_sgm_bins_lines/<int:match_id>')
def match_sgm_bins_lines(match_id):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT DISTINCT margin, total_points
        FROM match_sgm_bins
        WHERE match_id = %s
        ORDER BY margin, total_points
    """, (match_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    margins = sorted(set(row['margin'] for row in rows))
    totals = sorted(set(row['total_points'] for row in rows))
    return jsonify({
        "margins": margins,
        "totals": totals
    })

@app.route('/api/sgm_probability', methods=['POST'])
def sgm_probability():
    """
    Expects JSON body:
    {
        "try_dist": {"0":0.05,"1":0.10,"2":0.20,...},
        "player_probs": [0.22, 0.15, ...],
        "min_tries": [1, 1, ...]
    }
    """
    data = request.get_json()
    try_dist = data.get('try_dist', {})
    player_probs = data.get('player_probs', [])
    min_tries = data.get('min_tries', [])

    # Validation
    if not try_dist or not player_probs or not min_tries or len(player_probs) != len(min_tries):
        return jsonify({"error": "Invalid input"}), 400

    prob = joint_min_tries_probability(try_dist, player_probs, min_tries)
    return jsonify({"probability": prob})


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
