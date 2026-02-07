import json
import os
import re
from dotenv import load_dotenv
from pymongo import MongoClient
from datetime import datetime
from tkinter import filedialog, Tk

# Hide the main Tk window
root = Tk()
root.withdraw()

def parse_duration(duration_str):
    if not duration_str:
        return "0:00"
    # Match PT[H]H[H]M[M]S[S]
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

# Load env from repo root if present (works both locally and in the seed container)
import os
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env'))

def _env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    return v if v not in (None, "") else default

# MongoDB connection
mongo_host = _env("MONGO_HOST", "")
mongo_port = _env("MONGO_PORT", "")
mongo_user = _env("MONGO_ROOT_USERNAME", "")
mongo_pass = _env("MONGO_ROOT_PASSWORD", "")
mongo_db = _env("MONGO_DATABASE", "")

if mongo_user and mongo_pass and mongo_host != "localhost":
    uri = f"mongodb://{mongo_user}:{mongo_pass}@{mongo_host}:{mongo_port}/{mongo_db}"
else:
    uri = f"mongodb://{mongo_host}:{mongo_port}/{mongo_db}"

client = MongoClient(uri)
db = client[mongo_db]  # Database name
collection = db['videos']  # Collection name

# Select the JSON file
json_file = filedialog.askopenfilename(title="Select JSON file", filetypes=[("JSON files", "*.json")])
if not json_file:
    print("No file selected.")
    exit(1)

with open(json_file, 'r') as f:
    data = json.load(f)

# Get sector from JSON
sector = data.get('sector', 'Unknown')

# Process and insert data
for skill, videos in data['videos'].items():
    for video in videos:
        doc = {
            'sector': sector,
            'skill_name': skill,
            'videoId': video['video_id'],
            'publishedAt': video['search_data']['snippet']['publishedAt'],
            'title': video['search_data']['snippet']['title'],
            'description': video['search_data']['snippet']['description'],
            'viewCount': int(video['statistics'].get('statistics', {}).get('viewCount', '0')),
            'likeCount': int(video['statistics'].get('statistics', {}).get('likeCount', '0')),
            'tags': video.get('tags', []),
            'comments': [
                {
                    'textDisplay': comment['snippet']['topLevelComment']['snippet']['textDisplay'],
                    'textOriginal': comment['snippet']['topLevelComment']['snippet']['textOriginal'],
                    # Add more fields if needed
                }
                for comment in video['comments']
            ],
            'duration': parse_duration(video.get('duration', '')),
            'ingested_timing': datetime.now()
        }
        collection.insert_one(doc)

print("Data ingested successfully!")