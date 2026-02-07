import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import requests
import json
import os
import difflib
import mysql.connector
from dotenv import load_dotenv

# Load env from repo root if present (works both locally and in the seed container)
import os
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env'))

def _env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    return v if v not in (None, "") else default

def get_conn():
    
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

class YouTubeAPIScraper:
    def __init__(self, root):
        self.root = root
        self.root.title("YouTube API Scraper")
        self.root.geometry("900x700")
        self.root.resizable(True, True)

        # Make the window scrollable
        self.main_canvas = tk.Canvas(self.root)
        self.main_scrollbar = ttk.Scrollbar(self.root, orient="vertical", command=self.main_canvas.yview)
        self.main_canvas.configure(yscrollcommand=self.main_scrollbar.set)
        self.main_frame = ttk.Frame(self.main_canvas)
        self.main_canvas.create_window((0, 0), window=self.main_frame, anchor="nw")
        self.main_frame.bind("<Configure>", lambda e: self.main_canvas.configure(scrollregion=self.main_canvas.bbox("all")))
        self.main_canvas.pack(side="left", fill="both", expand=True)
        self.main_scrollbar.pack(side="right", fill="y")

        # Bind mouse wheel to root for global handling
        self.root.bind("<MouseWheel>", self._on_mousewheel_global)

        self.root.resizable(True, True)

        # Fetch sectors and skills from database
        self.sectors = self.fetch_sectors_and_skills()
        self.skill_to_sector = {}
        for sector, skills in self.sectors.items():
            for skill in skills:
                self.skill_to_sector[skill] = sector
        self.skills = list(self.skill_to_sector.keys())
        self.skill_vars = {}  # To hold checkbox variables
        self.skill_checkboxes = {}  # To hold checkbox widgets
        self.filtered_skills = self.sectors[list(self.sectors.keys())[0]] if self.sectors else []  # Default to first sector
        self.checked_states = {skill: False for skill in self.skills}  # To preserve checked states

        # Search variable
        self.search_var = tk.StringVar()

        # API Parameters
        self.api_key = tk.StringVar(value="")
        self.search_max_results = tk.StringVar(value="10")
        self.search_order = tk.StringVar(value="relevance")
        self.search_type = tk.StringVar(value="video")
        self.comments_max_results = tk.StringVar(value="10")

        # Sector variable
        self.sector_var = tk.StringVar(value=list(self.sectors.keys())[0] if self.sectors else "")

        # Search type variables
        self.create_widgets()

    # Fetch sectors and skills from the database FROM map_sf_to_cat_skill
    def fetch_sectors_and_skills(self):
        try:
            conn = get_conn()
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
            messagebox.showerror("Database Error", f"Failed to fetch sectors and skills: {str(e)}")

    def _on_mousewheel_main(self, event):
        self.main_canvas.yview_scroll(int(-1*(event.delta/120)), "units")

    def _on_mousewheel_skills(self, event):
        self.skills_canvas.yview_scroll(int(-1*(event.delta/120)), "units")

    def _on_mousewheel_global(self, event):
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        if self._is_descendant(widget, self.skills_frame):
            self._on_mousewheel_skills(event)
        else:
            self._on_mousewheel_main(event)

    def _is_descendant(self, widget, ancestor):
        while widget:
            if widget == ancestor:
                return True
            widget = widget.master
        return False

    def create_widgets(self):
        # Sector Selection
        ttk.Label(self.main_frame, text="Select Sector:").grid(row=0, column=0, padx=10, pady=10, sticky="w")
        sector_combo = ttk.Combobox(self.main_frame, textvariable=self.sector_var, values=list(self.sectors.keys()), state="readonly", width=50)
        sector_combo.grid(row=0, column=1, padx=10, pady=10)
        sector_combo.bind("<<ComboboxSelected>>", self.on_sector_change)

        # Skill Selection with Search
        ttk.Label(self.main_frame, text="Search Skills:").grid(row=1, column=0, padx=10, pady=10, sticky="w")
        search_entry = ttk.Entry(self.main_frame, textvariable=self.search_var)
        search_entry.grid(row=1, column=1, padx=10, pady=10)
        search_entry.bind("<KeyRelease>", self.filter_skills)

        # Select All/Deselect All buttons
        select_frame = ttk.Frame(self.main_frame)
        select_frame.grid(row=2, column=0, columnspan=2, padx=10, pady=10, sticky="w")
        ttk.Button(select_frame, text="Select All", command=self.select_all).pack(side="left", padx=5)
        ttk.Button(select_frame, text="Deselect All", command=self.deselect_all).pack(side="left", padx=5)

        # Skills Frame with Scrollbar
        self.skills_canvas = tk.Canvas(self.main_frame, height=100)  # Height for about 4-5 items to force scrolling
        self.skills_scrollbar = ttk.Scrollbar(self.main_frame, orient="vertical", command=self.skills_canvas.yview)
        self.skills_canvas.configure(yscrollcommand=self.skills_scrollbar.set)
        self.skills_frame = ttk.Frame(self.skills_canvas)
        self.skills_canvas.create_window((0, 0), window=self.skills_frame, anchor="nw")
        self.skills_frame.bind("<Configure>", lambda e: self.skills_canvas.configure(scrollregion=self.skills_canvas.bbox("all")))
        self.skills_canvas.grid(row=3, column=0, padx=10, pady=10, sticky="nsew")
        self.skills_scrollbar.grid(row=3, column=1, sticky="ns")

        # Create checkboxes for skills
        self.create_skill_checkboxes(self.skills_frame)

        # Separator after skills
        ttk.Separator(self.main_frame, orient='horizontal').grid(row=4, column=0, columnspan=3, sticky='ew', pady=10)

        # API Key
        ttk.Label(self.main_frame, text="API Key:").grid(row=5, column=0, padx=10, pady=10, sticky="w")
        ttk.Entry(self.main_frame, textvariable=self.api_key, width=50).grid(row=5, column=1, columnspan=2, padx=10, pady=10)

        # Separator after API Key
        ttk.Separator(self.main_frame, orient='horizontal').grid(row=6, column=0, columnspan=3, sticky='ew', pady=10)

        # Search Parameters
        ttk.Label(self.main_frame, text="Search Max Results:").grid(row=7, column=0, padx=10, pady=10, sticky="w")
        vcmd = (self.root.register(self.validate_integer), '%P')
        ttk.Entry(self.main_frame, textvariable=self.search_max_results, validate="key", validatecommand=vcmd).grid(row=7, column=1, padx=10, pady=10)
        ttk.Label(self.main_frame, text="(integer only)", foreground="gray").grid(row=7, column=2, padx=5, pady=10)

        ttk.Label(self.main_frame, text="Search Order:").grid(row=8, column=0, padx=10, pady=10, sticky="w")
        order_options = ["relevance", "date", "rating", "viewCount", "title", "videoCount"]
        self.search_order_combo = ttk.Combobox(self.main_frame, textvariable=self.search_order, values=order_options, state="readonly")
        self.search_order_combo.grid(row=8, column=1, padx=10, pady=10)
        self.search_order_combo.current(0)  # Set default to relevance

        ttk.Label(self.main_frame, text="Comments Max Results:").grid(row=9, column=0, padx=10, pady=10, sticky="w")
        vcmd_comments = (self.root.register(self.validate_integer), '%P')
        ttk.Entry(self.main_frame, textvariable=self.comments_max_results, validate="key", validatecommand=vcmd_comments).grid(row=9, column=1, padx=10, pady=10)
        ttk.Label(self.main_frame, text="(integer only)", foreground="gray").grid(row=9, column=2, padx=5, pady=10)

        #Separator after search params
        ttk.Separator(self.main_frame, orient='horizontal').grid(row=10, column=0, columnspan=3, sticky='ew', pady=10)

        ttk.Label(self.main_frame, text="Search Type:").grid(row=11, column=0, padx=10, pady=10, sticky="w")
        ttk.Label(self.main_frame, text="Video", foreground="blue").grid(row=11, column=1, padx=10, pady=10, sticky="w")

        #Videos Parameters
        ttk.Label(self.main_frame, text="Videos Part:").grid(row=12, column=0, padx=10, pady=10, sticky="w")
        ttk.Label(self.main_frame, text="snippet, statistics, contentDetails", foreground="blue").grid(row=12, column=1, padx=10, pady=10, sticky="w")

        #Comments Parameters
        ttk.Label(self.main_frame, text="Comments Part:").grid(row=13, column=0, padx=10, pady=10, sticky="w")
        ttk.Label(self.main_frame, text="snippet", foreground="blue").grid(row=13, column=1, padx=10, pady=10, sticky="w")
        ttk.Label(self.main_frame, text="Comments Text Format:").grid(row=14, column=0, padx=10, pady=10, sticky="w")
        ttk.Label(self.main_frame, text="plaintext", foreground="blue").grid(row=14, column=1, padx=10, pady=10, sticky="w")

        # Buttons
        ttk.Button(self.main_frame, text="Fetch Selected Skills", command=self.fetch_data).grid(row=15, column=0, columnspan=3, padx=10, pady=20, sticky="ew")

        # Status
        self.status_label = ttk.Label(self.main_frame, text="")
        self.status_label.grid(row=16, column=0, columnspan=3, padx=10, pady=10)

        # Configure grid weights for proper resizing
        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_columnconfigure(1, weight=1)
        self.main_frame.grid_columnconfigure(2, weight=1)
        self.main_frame.grid_rowconfigure(3, weight=1)  # For skills canvas expansion

    def validate_integer(self, P):
        if P == "" or P.isdigit():
            return True
        else:
            return False

    def get_search_type(self):
        return "video"

    def create_skill_checkboxes(self, frame):
        # Clear existing checkboxes
        for widget in frame.winfo_children():
            widget.destroy()
        self.skill_vars.clear()
        self.skill_checkboxes.clear()

        for skill in self.filtered_skills:
            var = tk.BooleanVar(value=self.checked_states[skill])
            cb = ttk.Checkbutton(frame, text=skill, variable=var, command=lambda s=skill: self.update_checked_state(s))
            cb.pack(anchor="w")
            self.skill_vars[skill] = var
            self.skill_checkboxes[skill] = cb

    def update_checked_state(self, skill):
        self.checked_states[skill] = self.skill_vars[skill].get()

    def select_all(self):
        for skill in self.filtered_skills:
            self.checked_states[skill] = True
        self.create_skill_checkboxes(self.skills_frame)

    def deselect_all(self):
        for skill in self.filtered_skills:
            self.checked_states[skill] = False
        self.create_skill_checkboxes(self.skills_frame)

    def on_sector_change(self, event=None):
        selected_sector = self.sector_var.get()
        self.filtered_skills = self.sectors[selected_sector]
        # Reset checked states for new skills
        for skill in self.filtered_skills:
            self.checked_states[skill] = False
        self.create_skill_checkboxes(self.skills_frame)

    def filter_skills(self, event=None):
        search_text = self.search_var.get().lower()
        current_sector_skills = self.sectors[self.sector_var.get()]
        if not search_text:
            self.filtered_skills = current_sector_skills
        else:
            self.filtered_skills = [skill for skill in current_sector_skills if skill.lower().startswith(search_text)]
        self.create_skill_checkboxes(self.skills_frame)

    def fetch_data(self):
        selected_skills = [skill for skill, var in self.skill_vars.items() if var.get()]
        if not selected_skills:
            messagebox.showerror("Error", "Please select at least one skill.")
            return

        try:
            max_results = int(self.search_max_results.get())
        except ValueError:
            messagebox.showerror("Error", "Search Max Results must be an integer.")
            return

        try:
            comments_max = int(self.comments_max_results.get())
        except ValueError:
            messagebox.showerror("Error", "Comments Max Results must be an integer.")
            return

        type_str = self.get_search_type()
        if not type_str:
            messagebox.showerror("Error", "Please select at least one search type.")
            return

        self.status_label.config(text="Fetching data...")

        try:
            all_videos_data = {}
            for skill in selected_skills:
                self.status_label.config(text=f"Fetching data for {skill}...")

                # Search for videos
                search_url = "https://www.googleapis.com/youtube/v3/search"
                search_params = {
                    "part": "snippet",
                    "maxResults": max_results,
                    "order": self.search_order.get(),
                    "key": self.api_key.get(),
                    "type": type_str,
                    "q": self.skill_to_sector[skill] + " " + skill
                }
                search_response = requests.get(search_url, params=search_params)
                search_data = search_response.json()

                if "items" not in search_data:
                    messagebox.showerror("Error", f"Failed to fetch search results for {skill}.")
                    continue

                videos_data = []
                for item in search_data["items"]:
                    video_id = item["id"]["videoId"]
                    video_info = {
                        "search_data": item,
                        "video_id": video_id
                    }

                    # Fetch video statistics
                    videos_url = "https://www.googleapis.com/youtube/v3/videos"
                    videos_params = {
                        "key": self.api_key.get(),
                        "part": "snippet,statistics,contentDetails",
                        "id": video_id
                    }
                    videos_response = requests.get(videos_url, params=videos_params)
                    videos_json = videos_response.json()
                    if "items" in videos_json and videos_json["items"]:
                        video_info["statistics"] = videos_json["items"][0]
                        video_info["tags"] = videos_json["items"][0]["snippet"].get("tags", [])
                        video_info["duration"] = videos_json["items"][0].get("contentDetails", {}).get("duration", "")
                    else:
                        video_info["statistics"] = {}
                        video_info["tags"] = []
                        video_info["duration"] = ""

                    # Fetch comments
                    comments_url = "https://www.googleapis.com/youtube/v3/commentThreads"
                    comments_params = {
                        "key": self.api_key.get(),
                        "part": "snippet",
                        "videoId": video_id,
                        "maxResults": comments_max,
                        "textFormat": "plaintext",
                        "order": "relevance"
                    }
                    comments_response = requests.get(comments_url, params=comments_params)
                    comments_json = comments_response.json()
                    if "items" in comments_json:
                        video_info["comments"] = comments_json["items"]
                    else:
                        video_info["comments"] = []

                    videos_data.append(video_info)

                all_videos_data[skill] = videos_data

            # Save to JSON
            output_data = {
                "sector": self.sector_var.get(),
                "selected_skills": selected_skills,
                "search_order": self.search_order.get(),
                "videos": all_videos_data
            }
            file_path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON files", "*.json")])
            if file_path:
                with open(file_path, "w") as f:
                    json.dump(output_data, f, indent=4)
                self.status_label.config(text=f"Data saved to {file_path}")
            else:
                self.status_label.config(text="Save cancelled")

        except Exception as e:
            messagebox.showerror("Error", str(e))
            self.status_label.config(text="Error occurred")

if __name__ == "__main__":
    root = tk.Tk()
    app = YouTubeAPIScraper(root)
    root.mainloop()