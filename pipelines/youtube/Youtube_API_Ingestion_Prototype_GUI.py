from flask import Flask, render_template_string, request, redirect, url_for, jsonify
import requests
import json
import os
import re
from dotenv import load_dotenv
from pymongo import MongoClient
from datetime import datetime
import mysql.connector

# Load env from repo root
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env'))

def _env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    return v if v not in (None, "") else default

def get_mysql_conn():
    host = _env("DB_HOST", "")
    port = int(_env("DB_PORT", ""))
    return mysql.connector.connect(
        host=host,
        port=port,
        user=_env("DB_USER", ""),
        password=_env("DB_PASSWORD", ""),
        database=_env("DB_NAME", ""),
        autocommit=False,
    )

def get_mongo_client():
    mongo_host = _env("MONGO_HOST", "")
    mongo_port = _env("MONGO_PORT", "")
    mongo_user = _env("MONGO_ROOT_USERNAME", "")
    mongo_pass = _env("MONGO_ROOT_PASSWORD", "")
    mongo_db = _env("MONGO_DATABASE", "")

    if mongo_user and mongo_pass and mongo_host != "localhost":
        uri = f"mongodb://{mongo_user}:{mongo_pass}@{mongo_host}:{mongo_port}/{mongo_db}"
    else:
        uri = f"mongodb://{mongo_host}:{mongo_port}/{mongo_db}"

    return MongoClient(uri)

def parse_duration(duration_str):
    if not duration_str:
        return "0:00"
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str)
    if not match:
        return "0:00"
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    else:
        return f"{minutes}:{seconds:02d}"

def fetch_sectors_and_skills():
    try:
        conn = get_mysql_conn()
        cur = conn.cursor()
        query = """
            SELECT DISTINCT sector_name_raw, source_skill_title
            FROM map_sf_to_cat_skill
            WHERE sector_name_raw IS NOT NULL AND source_skill_title IS NOT NULL
            ORDER BY sector_name_raw, source_skill_title
        """
        cur.execute(query)
        rows = cur.fetchall()
        cur.close()
        conn.close()

        sectors = {}
        for sector, skill in rows:
            if sector not in sectors:
                sectors[sector] = []
            sectors[sector].append(skill)
        return sectors
    except Exception as e:
        return {}

def search_skills(sector, search_term):
    try:
        conn = get_mysql_conn()
        cur = conn.cursor()
        query = """
            SELECT DISTINCT source_skill_title
            FROM map_sf_to_cat_skill
            WHERE sector_name_raw = %s AND source_skill_title LIKE %s
            ORDER BY source_skill_title
        """
        cur.execute(query, (sector, f'%{search_term}%'))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [row[0] for row in rows]
    except Exception as e:
        return []

app = Flask(__name__)

@app.route('/')
def index():
    try:
        sectors = fetch_sectors_and_skills()
        sectors_list = list(sectors.keys())
    except Exception as e:
        sectors_list = []
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>YouTube API Scraper</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 20px; }
            .sector { display: none; }
            .sector.active { display: block; }
            .skills { max-height: 300px; overflow-y: auto; border: 1px solid #ccc; padding: 10px; }
            input[type="checkbox"] { margin: 5px; }
        </style>
    </head>
    <body>
        <h1>YouTube API Scraper</h1>
        <form method="post" action="/fetch">
            <label>Sector:</label>
            <select name="sector" id="sector_select">
                {% for sector in sectors_list %}
                <option value="{{ sector }}">{{ sector }}</option>
                {% endfor %}
            </select><br><br>

            <label>Search Skills:</label>
            <input type="text" id="search_skills" placeholder="Type to search skills"><br><br>

            <div class="skills" id="skills_container">
                <!-- Skills will be loaded dynamically -->
            </div><br>

            <button type="button" onclick="selectAll()">Select All</button>
            <button type="button" onclick="deselectAll()">Deselect All</button><br><br>

            <label>API Key:</label>
            <input name="api_key" required><br><br>

            <label>Search Max Results:</label>
            <input name="search_max_results" value="10" type="number"><br><br>

            <label>Search Order:</label>
            <select name="search_order">
                <option value="relevance">relevance</option>
                <option value="date">date</option>
                <option value="rating">rating</option>
                <option value="viewCount">viewCount</option>
                <option value="title">title</option>
                <option value="videoCount">videoCount</option>
            </select><br><br>

            <label>Comments Max Results:</label>
            <input name="comments_max_results" value="10" type="number"><br><br>

            <button type="submit">Fetch and Upsert Data</button>
        </form>

        <script>
            const sectorSelect = document.getElementById('sector_select');
            const searchInput = document.getElementById('search_skills');
            const skillsContainer = document.getElementById('skills_container');
            let selectedSkills = new Set();

            function loadSkills(sector, searchTerm = '') {
                fetch('/search_skills', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ sector: sector, search_term: searchTerm }),
                })
                .then(response => {
                    if (!response.ok) {
                        throw new Error('Network response was not ok');
                    }
                    return response.json();
                })
                .then(skills => {
                    skillsContainer.innerHTML = skills.map(skill => 
                        `<input type="checkbox" name="skills" value="${skill}" ${selectedSkills.has(skill) ? 'checked' : ''} onchange="toggleSkill('${skill}')"> ${skill}<br>`
                    ).join('');
                })
                .catch(error => {
                    console.error('Error loading skills:', error);
                    skillsContainer.innerHTML = 'Error loading skills.';
                });
            }

            function toggleSkill(skill) {
                if (selectedSkills.has(skill)) {
                    selectedSkills.delete(skill);
                } else {
                    selectedSkills.add(skill);
                }
            }

            function updateSkills() {
                const selectedSector = sectorSelect.value;
                const searchText = searchInput.value;
                loadSkills(selectedSector, searchText);
            }

            sectorSelect.addEventListener('change', () => {
                selectedSkills.clear(); // Clear selections when sector changes
                updateSkills();
            });
            searchInput.addEventListener('input', updateSkills);

            // Initial load
            updateSkills();

            function selectAll() {
                const checkboxes = skillsContainer.querySelectorAll('input[type="checkbox"]');
                checkboxes.forEach(cb => {
                    cb.checked = true;
                    selectedSkills.add(cb.value);
                });
            }

            function deselectAll() {
                const checkboxes = skillsContainer.querySelectorAll('input[type="checkbox"]');
                checkboxes.forEach(cb => {
                    cb.checked = false;
                    selectedSkills.delete(cb.value);
                });
            }
        </script>
    </body>
    </html>
    """
    return render_template_string(html, sectors_list=sectors_list)

@app.route('/search_skills', methods=['POST'])
def search_skills_route():
    data = request.get_json()
    sector = data.get('sector')
    search_term = data.get('search_term', '')
    skills = search_skills(sector, search_term)
    return jsonify(skills)

@app.route('/fetch', methods=['POST'])
def fetch():
    sector = request.form['sector']
    api_key = request.form['api_key']
    search_max_results = int(request.form['search_max_results'])
    search_order = request.form['search_order']
    comments_max_results = int(request.form['comments_max_results'])
    selected_skills = request.form.getlist('skills')

    if not selected_skills:
        return "Please select at least one skill."

    try:
        client = get_mongo_client()
        db = client[_env("MONGO_DATABASE", "")]
        collection = db['videos']

        for skill in selected_skills:
            # Search for videos
            search_url = "https://www.googleapis.com/youtube/v3/search"
            search_params = {
                "part": "snippet",
                "maxResults": search_max_results,
                "order": search_order,
                "key": api_key,
                "type": "video",
                "q": sector + " " + skill
            }
            search_response = requests.get(search_url, params=search_params)
            search_data = search_response.json()

            if "items" not in search_data:
                continue

            for item in search_data["items"]:
                video_id = item["id"]["videoId"]

                # Fetch video statistics
                videos_url = "https://www.googleapis.com/youtube/v3/videos"
                videos_params = {
                    "key": api_key,
                    "part": "snippet,statistics,contentDetails",
                    "id": video_id
                }
                videos_response = requests.get(videos_url, params=videos_params)
                videos_json = videos_response.json()

                statistics = {}
                tags = []
                duration = ""
                if "items" in videos_json and videos_json["items"]:
                    video_item = videos_json["items"][0]
                    statistics = video_item.get("statistics", {})
                    tags = video_item["snippet"].get("tags", [])
                    duration = video_item.get("contentDetails", {}).get("duration", "")

                # Fetch comments
                comments_url = "https://www.googleapis.com/youtube/v3/commentThreads"
                comments_params = {
                    "key": api_key,
                    "part": "snippet",
                    "videoId": video_id,
                    "maxResults": comments_max_results,
                    "textFormat": "plaintext",
                    "order": "relevance"
                }
                comments_response = requests.get(comments_url, params=comments_params)
                comments_json = comments_response.json()
                comments = comments_json.get("items", [])

                doc = {
                    'sector': sector,
                    'skill_name': skill,
                    'videoId': video_id,
                    'publishedAt': item['snippet']['publishedAt'],
                    'title': item['snippet']['title'],
                    'description': item['snippet']['description'],
                    'viewCount': int(statistics.get('viewCount', '0')),
                    'likeCount': int(statistics.get('likeCount', '0')),
                    'tags': tags,
                    'comments': [
                        {
                            'textDisplay': comment['snippet']['topLevelComment']['snippet']['textDisplay'],
                            'textOriginal': comment['snippet']['topLevelComment']['snippet']['textOriginal'],
                        }
                        for comment in comments
                    ],
                    'duration': parse_duration(duration),
                    'ingested_timing': datetime.now()
                }

                # Upsert
                collection.update_one({'videoId': video_id, 'skill_name': skill}, {'$set': doc}, upsert=True)

        return "Data upserted successfully!"

    except Exception as e:
        return f"Error: {str(e)}"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)