#!/usr/bin/env python3
"""
Spinless - Kodi Texture Hash Check Updater

Prevents Kodi from spinning up drives during library browsing by setting
lasthashcheck to a future date for local artwork textures.

Kodi checks local artwork files daily to detect changes. This causes drive
spinup for users with media on external/NAS drives. This tool sets the
lasthashcheck date far into the future, preventing these checks.

By default, only updates textures for items that have NFO files, indicating
the user has curated that item's metadata and artwork.

Usage:
    GUI mode:   python spinless.py
    CLI mode:   python spinless.py --cli [--apply]

Supports Windows, Linux, and macOS.
"""

import argparse
import glob
import json
import os
import platform
import sqlite3
import sys
import urllib.parse
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional, Tuple

__version__ = "1.2.0"

FUTURE_DATE = "2099-01-01 00:00:00"


@dataclass
class Settings:
    """Application settings with persistence."""
    include_movies: bool = True
    include_tvshows: bool = False
    include_seasons: bool = True
    include_episodes: bool = True
    tvshow_nfo_logic: str = "per_item"
    update_all_local: bool = False

    # Database paths (not saved)
    video_db: str = ""
    texture_db: str = ""

    @classmethod
    def load(cls) -> "Settings":
        """Load settings from config file."""
        config_path = cls._config_path()
        if config_path.exists():
            try:
                with open(config_path) as f:
                    data = json.load(f)
                return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
            except Exception:
                pass
        return cls()

    def save(self):
        """Save settings to config file."""
        config_path = self._config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        data = {k: v for k, v in asdict(self).items() if k not in ("video_db", "texture_db")}
        with open(config_path, "w") as f:
            json.dump(data, f, indent=2)

    @staticmethod
    def _config_path() -> Path:
        """Get platform-specific config file path."""
        system = platform.system()
        if system == "Windows":
            base = Path(os.environ.get("APPDATA", Path.home()))
        elif system == "Darwin":
            base = Path.home() / "Library" / "Application Support"
        else:
            base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        return base / "spinless" / "settings.json"


# --- Path utilities ---

def get_kodi_database_paths() -> List[Path]:
    """Return possible Kodi database folder paths for the current platform."""
    system = platform.system()
    paths = []

    if system == "Windows":
        appdata = os.environ.get("APPDATA")
        localappdata = os.environ.get("LOCALAPPDATA")
        if appdata:
            paths.append(Path(appdata) / "Kodi" / "userdata" / "Database")
        if localappdata:
            paths.append(Path(localappdata) / "Kodi" / "userdata" / "Database")
        paths.append(Path.home() / "AppData" / "Roaming" / "Kodi" / "userdata" / "Database")

    elif system == "Darwin":
        paths.append(Path.home() / "Library" / "Application Support" / "Kodi" / "userdata" / "Database")

    elif system == "Linux":
        paths.append(Path.home() / ".kodi" / "userdata" / "Database")
        paths.append(Path.home() / ".var" / "app" / "tv.kodi.Kodi" / "data" / "userdata" / "Database")
        paths.append(Path("/storage") / ".kodi" / "userdata" / "Database")

    return paths


def find_database(pattern: str) -> Optional[Path]:
    """Find the most recent database matching pattern."""
    for db_path in get_kodi_database_paths():
        if db_path.exists():
            matches = sorted(db_path.glob(pattern), reverse=True)
            if matches:
                return matches[0]
    return None


def convert_path_for_access(path: str) -> str:
    """Convert database path to accessible filesystem path (handles WSL)."""
    if not path:
        return path

    is_wsl = "microsoft" in platform.uname().release.lower()

    if is_wsl and len(path) >= 2 and path[1] == ':':
        drive = path[0].lower()
        if 'a' <= drive <= 'z':
            return f"/mnt/{drive}" + path[2:].replace('\\', '/')

    return path


def has_nfo_file(folder_path: str, nfo_name: Optional[str] = None) -> bool:
    """Check if folder contains an NFO file. If nfo_name specified, check that exact file."""
    converted = convert_path_for_access(folder_path)
    if not os.path.isdir(converted):
        return False
    if nfo_name:
        return os.path.exists(os.path.join(converted, nfo_name))
    return len(glob.glob(os.path.join(glob.escape(converted), "*.nfo"))) > 0


def has_episode_nfo(folder_path: str, episode_filename: str) -> bool:
    """Check if episode has matching NFO file (same name, .nfo extension)."""
    converted = convert_path_for_access(folder_path)
    if not os.path.isdir(converted):
        return False
    base_name = os.path.splitext(episode_filename)[0]
    nfo_path = os.path.join(converted, base_name + ".nfo")
    return os.path.exists(nfo_path)


def normalize_url_for_texture(url: str) -> str:
    """Convert artwork URL to texture database storage format."""
    if url.startswith('image://'):
        return url
    encoded = urllib.parse.quote(url, safe='')
    return f'image://{encoded}/'


# --- Database queries ---

_VALID_TABLE_COLUMNS = {
    ("movie", "idMovie"),
    ("tvshow", "idShow"),
    ("seasons", "idSeason"),
    ("episode", "idEpisode"),
}


def query_all_ids(video_db: Path, table: str, id_column: str) -> List[int]:
    """Get all IDs from a table."""
    if (table, id_column) not in _VALID_TABLE_COLUMNS:
        raise ValueError(f"Invalid table/column: {table}.{id_column}")
    conn = sqlite3.connect(str(video_db))
    try:
        cursor = conn.cursor()
        cursor.execute(f"SELECT {id_column} FROM {table}")
        return [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()


def query_local_artwork(video_db: Path, media_type: str, media_ids: List[int]) -> List[Tuple[int, str, str]]:
    """Get local artwork URLs for specified media items."""
    if not media_ids:
        return []

    conn = sqlite3.connect(str(video_db))
    try:
        cursor = conn.cursor()
        placeholders = ','.join('?' * len(media_ids))
        cursor.execute(f"""
            SELECT media_id, type, url
            FROM art
            WHERE media_type = ?
              AND media_id IN ({placeholders})
              AND url NOT LIKE 'http%'
              AND url NOT LIKE 'image://%'
        """, [media_type] + list(media_ids))
        return cursor.fetchall()
    finally:
        conn.close()


def get_movies_with_nfo(video_db: Path, progress_callback=None) -> List[int]:
    """Get movie IDs that have NFO files."""
    conn = sqlite3.connect(str(video_db))
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT m.idMovie, p.strPath
            FROM movie m
            JOIN files f ON m.idFile = f.idFile
            JOIN path p ON f.idPath = p.idPath
        """)

        result = []
        rows = cursor.fetchall()
        total = len(rows)

        for i, (movie_id, folder_path) in enumerate(rows):
            if has_nfo_file(folder_path):
                result.append(movie_id)
            if progress_callback and i % 100 == 0:
                progress_callback(i, total, f"Scanning movies for NFOs... ({i}/{total})")

        return result
    finally:
        conn.close()


def get_tvshows_with_nfo(video_db: Path, progress_callback=None) -> List[int]:
    """Get TV show IDs that have tvshow.nfo files."""
    conn = sqlite3.connect(str(video_db))
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ts.idShow, p.strPath
            FROM tvshow ts
            JOIN tvshowlinkpath tsl ON ts.idShow = tsl.idShow
            JOIN path p ON tsl.idPath = p.idPath
        """)

        result = []
        rows = cursor.fetchall()
        total = len(rows)

        for i, (show_id, folder_path) in enumerate(rows):
            if has_nfo_file(folder_path, "tvshow.nfo"):
                result.append(show_id)
            if progress_callback and i % 50 == 0:
                progress_callback(i, total, f"Scanning TV shows for NFOs... ({i}/{total})")

        return result
    finally:
        conn.close()


def get_seasons_for_shows(video_db: Path, show_ids: List[int]) -> List[int]:
    """Get season IDs for specified TV shows."""
    if not show_ids:
        return []

    conn = sqlite3.connect(str(video_db))
    try:
        cursor = conn.cursor()
        placeholders = ','.join('?' * len(show_ids))
        cursor.execute(f"SELECT idSeason FROM seasons WHERE idShow IN ({placeholders})", show_ids)
        return [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()


def get_episodes_with_nfo(video_db: Path, show_ids: Optional[List[int]] = None,
                          progress_callback=None) -> List[int]:
    """Get episode IDs that have NFO files. Optionally filter by show IDs."""
    conn = sqlite3.connect(str(video_db))
    try:
        cursor = conn.cursor()

        if show_ids:
            placeholders = ','.join('?' * len(show_ids))
            cursor.execute(f"""
                SELECT e.idEpisode, p.strPath, f.strFilename
                FROM episode e
                JOIN files f ON e.idFile = f.idFile
                JOIN path p ON f.idPath = p.idPath
                WHERE e.idShow IN ({placeholders})
            """, show_ids)
        else:
            cursor.execute("""
                SELECT e.idEpisode, p.strPath, f.strFilename
                FROM episode e
                JOIN files f ON e.idFile = f.idFile
                JOIN path p ON f.idPath = p.idPath
            """)

        result = []
        rows = cursor.fetchall()
        total = len(rows)

        for i, (episode_id, folder_path, filename) in enumerate(rows):
            if has_episode_nfo(folder_path, filename):
                result.append(episode_id)
            if progress_callback and i % 200 == 0:
                progress_callback(i, total, f"Scanning episodes for NFOs... ({i}/{total})")

        return result
    finally:
        conn.close()


# --- Texture processing ---

def find_textures_to_update(
    texture_db: Path,
    artwork_urls: List[Tuple[int, str, str]]
) -> Tuple[List[Tuple[int, str, str]], int]:
    """Find textures that need lasthashcheck updated."""
    conn = sqlite3.connect(str(texture_db))
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, url, lasthashcheck FROM texture")
        all_textures = {row[1]: (row[0], row[2]) for row in cursor.fetchall()}
    finally:
        conn.close()

    textures_to_update = []
    urls_not_found = 0

    for _, _, url in artwork_urls:
        texture_url = normalize_url_for_texture(url)

        match = all_textures.get(texture_url) or all_textures.get(url)
        if match:
            texture_id, current_hashcheck = match
            if not current_hashcheck or current_hashcheck < FUTURE_DATE:
                textures_to_update.append((texture_id, url, current_hashcheck))
        else:
            urls_not_found += 1

    return textures_to_update, urls_not_found


def check_database_writable(texture_db: Path):
    """Verify texture database is not locked by Kodi."""
    try:
        conn = sqlite3.connect(str(texture_db), timeout=1.0)
        conn.execute("BEGIN IMMEDIATE")
        conn.rollback()
        conn.close()
    except sqlite3.OperationalError:
        raise RuntimeError(
            f"Texture database is locked: {texture_db}\n"
            "Close Kodi and try again."
        )


def apply_updates(texture_db: Path, textures: List[Tuple[int, str, str]]) -> int:
    """Apply lasthashcheck updates to texture database."""
    check_database_writable(texture_db)

    conn = sqlite3.connect(str(texture_db))
    try:
        cursor = conn.cursor()
        for texture_id, _, _ in textures:
            cursor.execute(
                "UPDATE texture SET lasthashcheck = ? WHERE id = ?",
                (FUTURE_DATE, texture_id)
            )
        conn.commit()
        return len(textures)
    finally:
        conn.close()


# --- Scanning ---

@dataclass
class ScanResult:
    """Results from scanning for artwork to update."""
    movie_count: int = 0
    tvshow_count: int = 0
    season_count: int = 0
    episode_count: int = 0
    artwork_count: int = 0
    textures_to_update: List[Tuple[int, str, str]] = field(default_factory=list)
    not_cached: int = 0


def scan_for_updates(video_db: Path, texture_db: Path, settings: Settings,
                     log_callback=None, progress_callback=None) -> ScanResult:
    """Scan databases and find textures needing updates based on settings."""
    result = ScanResult()
    all_artwork: List[Tuple[int, str, str]] = []

    def log(msg):
        if log_callback:
            log_callback(msg)

    # Movies
    if settings.include_movies:
        log("Scanning movies...")
        if settings.update_all_local:
            movie_ids = query_all_ids(video_db, "movie", "idMovie")
            log(f"  Found {len(movie_ids)} movies (all)")
        else:
            movie_ids = get_movies_with_nfo(video_db, progress_callback)
            log(f"  Found {len(movie_ids)} movies with NFO files")

        result.movie_count = len(movie_ids)
        if movie_ids:
            artwork = query_local_artwork(video_db, "movie", movie_ids)
            log(f"  Found {len(artwork)} local movie artwork entries")
            all_artwork.extend(artwork)

    # TV Shows
    if settings.include_tvshows:
        log("\nScanning TV shows...")

        if settings.update_all_local:
            show_ids = query_all_ids(video_db, "tvshow", "idShow")
            log(f"  Found {len(show_ids)} TV shows (all)")
        else:
            show_ids = get_tvshows_with_nfo(video_db, progress_callback)
            log(f"  Found {len(show_ids)} TV shows with tvshow.nfo")

        result.tvshow_count = len(show_ids)
        if show_ids:
            artwork = query_local_artwork(video_db, "tvshow", show_ids)
            log(f"  Found {len(artwork)} local TV show artwork entries")
            all_artwork.extend(artwork)

        # Seasons
        if settings.include_seasons:
            log("\nScanning seasons...")
            if settings.update_all_local:
                season_ids = query_all_ids(video_db, "seasons", "idSeason")
                log(f"  Found {len(season_ids)} seasons (all)")
            else:
                season_ids = get_seasons_for_shows(video_db, show_ids)
                log(f"  Found {len(season_ids)} seasons for shows with tvshow.nfo")

            result.season_count = len(season_ids)
            if season_ids:
                artwork = query_local_artwork(video_db, "season", season_ids)
                log(f"  Found {len(artwork)} local season artwork entries")
                all_artwork.extend(artwork)

        # Episodes
        if settings.include_episodes:
            log("\nScanning episodes...")
            if settings.update_all_local:
                episode_ids = query_all_ids(video_db, "episode", "idEpisode")
                log(f"  Found {len(episode_ids)} episodes (all)")
            elif settings.tvshow_nfo_logic == "require_show_nfo":
                episode_ids = get_episodes_with_nfo(video_db, show_ids, progress_callback)
                log(f"  Found {len(episode_ids)} episodes with NFO (in shows with tvshow.nfo)")
            else:
                episode_ids = get_episodes_with_nfo(video_db, None, progress_callback)
                log(f"  Found {len(episode_ids)} episodes with NFO files")

            result.episode_count = len(episode_ids)
            if episode_ids:
                artwork = query_local_artwork(video_db, "episode", episode_ids)
                log(f"  Found {len(artwork)} local episode artwork entries")
                all_artwork.extend(artwork)

    result.artwork_count = len(all_artwork)

    if all_artwork:
        log("\nFinding textures to update...")
        result.textures_to_update, result.not_cached = find_textures_to_update(texture_db, all_artwork)
        log(f"  Textures needing update: {len(result.textures_to_update)}")
        log(f"  Artwork not yet cached: {result.not_cached}")

    return result


# --- CLI ---

def run_cli(video_db: Path, texture_db: Path, settings: Settings, apply: bool = False) -> int:
    """Run in command-line mode."""
    print(f"Spinless v{__version__}")
    print("=" * 50)
    print()

    if not apply:
        print("DRY RUN - No changes will be made")
        print("Use --apply to actually update the database")
        print()

    print(f"Video DB:   {video_db}")
    print(f"Texture DB: {texture_db}")
    print()

    print("Settings:")
    print(f"  Movies: {'Yes' if settings.include_movies else 'No'}")
    print(f"  TV Shows: {'Yes' if settings.include_tvshows else 'No'}")
    if settings.include_tvshows:
        print(f"    Seasons: {'Yes' if settings.include_seasons else 'No'}")
        print(f"    Episodes: {'Yes' if settings.include_episodes else 'No'}")
        print(f"    NFO Logic: {settings.tvshow_nfo_logic}")
    print(f"  Update All Local: {'Yes' if settings.update_all_local else 'No (NFO only)'}")
    print()

    result = scan_for_updates(video_db, texture_db, settings, log_callback=print)
    print()

    if not result.textures_to_update:
        print("All textures already up to date. Nothing to do.")
        return 0

    print("Preview (first 10):")
    for texture_id, url, current in result.textures_to_update[:10]:
        short_url = url if len(url) < 50 else "..." + url[-47:]
        print(f"  [{texture_id}] {short_url}")
        print(f"       {current or 'NULL'} -> {FUTURE_DATE}")

    if len(result.textures_to_update) > 10:
        print(f"  ... and {len(result.textures_to_update) - 10} more")
    print()

    if apply:
        print("Applying updates...")
        count = apply_updates(texture_db, result.textures_to_update)
        print(f"  Updated {count} textures")
        print()
        print("Done!")
    else:
        print("=" * 50)
        print("DRY RUN COMPLETE - No changes made")
        print("Run with --apply to update the database")
        print("=" * 50)

    return len(result.textures_to_update)


# --- GUI ---

class SpinlessApp:
    """Tkinter GUI application."""

    def __init__(self, root, video_db: Optional[Path], texture_db: Optional[Path], settings: Settings):
        import tkinter as tk
        from tkinter import ttk, scrolledtext

        self.tk = tk
        self.ttk = ttk
        self.filedialog = None  # Lazy import on use
        self.messagebox = None

        self.root = root
        self.root.title(f"Spinless v{__version__}")
        self.root.geometry("800x700")
        self.root.resizable(True, True)

        self.settings = settings
        self.video_db_path = tk.StringVar(value=str(video_db) if video_db else "")
        self.texture_db_path = tk.StringVar(value=str(texture_db) if texture_db else "")
        self.textures_to_update: List[Tuple[int, str, str]] = []

        self.include_movies = tk.BooleanVar(value=settings.include_movies)
        self.include_tvshows = tk.BooleanVar(value=settings.include_tvshows)
        self.include_seasons = tk.BooleanVar(value=settings.include_seasons)
        self.include_episodes = tk.BooleanVar(value=settings.include_episodes)
        self.tvshow_nfo_logic = tk.StringVar(value=settings.tvshow_nfo_logic)
        self.update_all_local = tk.BooleanVar(value=settings.update_all_local)

        self._create_widgets(scrolledtext)
        self._update_tvshow_state()

    def _get_filedialog(self):
        if self.filedialog is None:
            from tkinter import filedialog
            self.filedialog = filedialog
        return self.filedialog

    def _get_messagebox(self):
        if self.messagebox is None:
            from tkinter import messagebox
            self.messagebox = messagebox
        return self.messagebox

    def _create_widgets(self, scrolledtext):
        tk, ttk = self.tk, self.ttk

        main = ttk.Frame(self.root, padding="10")
        main.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main.columnconfigure(1, weight=1)

        row = 0

        # Info
        info = ttk.Label(main, text=(
            "Prevents Kodi from spinning up drives during library browsing by updating\n"
            "lasthashcheck for local artwork textures."
        ), wraplength=750, justify="left")
        info.grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 10))
        row += 1

        # Database paths
        ttk.Label(main, text="Video Database:").grid(row=row, column=0, sticky="w", pady=3)
        vf = ttk.Frame(main)
        vf.grid(row=row, column=1, sticky="ew", pady=3)
        vf.columnconfigure(0, weight=1)
        ttk.Entry(vf, textvariable=self.video_db_path, width=70).grid(row=0, column=0, sticky="ew")
        ttk.Button(vf, text="Browse", command=self._browse_video).grid(row=0, column=1, padx=(5, 0))
        row += 1

        ttk.Label(main, text="Texture Database:").grid(row=row, column=0, sticky="w", pady=3)
        tf = ttk.Frame(main)
        tf.grid(row=row, column=1, sticky="ew", pady=3)
        tf.columnconfigure(0, weight=1)
        ttk.Entry(tf, textvariable=self.texture_db_path, width=70).grid(row=0, column=0, sticky="ew")
        ttk.Button(tf, text="Browse", command=self._browse_texture).grid(row=0, column=1, padx=(5, 0))
        row += 1

        # Separator
        ttk.Separator(main, orient="horizontal").grid(row=row, column=0, columnspan=2, sticky="ew", pady=10)
        row += 1

        # Settings Frame
        settings_frame = ttk.LabelFrame(main, text="Settings", padding="10")
        settings_frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=5)
        settings_frame.columnconfigure(1, weight=1)
        row += 1

        # Content Types
        ttk.Label(settings_frame, text="Content Types:").grid(row=0, column=0, sticky="w", pady=2)
        content_frame = ttk.Frame(settings_frame)
        content_frame.grid(row=0, column=1, sticky="w", pady=2)

        ttk.Checkbutton(content_frame, text="Movies", variable=self.include_movies).pack(side="left", padx=(0, 15))
        ttk.Checkbutton(content_frame, text="TV Shows", variable=self.include_tvshows,
                       command=self._update_tvshow_state).pack(side="left")

        # TV Show Options
        ttk.Label(settings_frame, text="TV Show Options:").grid(row=1, column=0, sticky="nw", pady=2)
        tv_frame = ttk.Frame(settings_frame)
        tv_frame.grid(row=1, column=1, sticky="w", pady=2)

        self.seasons_cb = ttk.Checkbutton(tv_frame, text="Include Seasons", variable=self.include_seasons)
        self.seasons_cb.pack(anchor="w")
        self.episodes_cb = ttk.Checkbutton(tv_frame, text="Include Episodes", variable=self.include_episodes)
        self.episodes_cb.pack(anchor="w")

        # NFO Logic
        ttk.Label(settings_frame, text="Episode NFO Logic:").grid(row=2, column=0, sticky="nw", pady=2)
        nfo_frame = ttk.Frame(settings_frame)
        nfo_frame.grid(row=2, column=1, sticky="w", pady=2)

        self.per_item_rb = ttk.Radiobutton(nfo_frame, text="Per-item (episode has own NFO)",
                                            variable=self.tvshow_nfo_logic, value="per_item")
        self.per_item_rb.pack(anchor="w")
        self.require_show_rb = ttk.Radiobutton(nfo_frame, text="Require show NFO (episode NFO + tvshow.nfo)",
                                                variable=self.tvshow_nfo_logic, value="require_show_nfo")
        self.require_show_rb.pack(anchor="w")

        # Advanced
        ttk.Separator(settings_frame, orient="horizontal").grid(row=3, column=0, columnspan=2, sticky="ew", pady=8)
        ttk.Label(settings_frame, text="Advanced:").grid(row=4, column=0, sticky="w", pady=2)
        ttk.Checkbutton(settings_frame, text="Update ALL local artwork (ignore NFO requirement)",
                       variable=self.update_all_local).grid(row=4, column=1, sticky="w", pady=2)

        # Buttons
        bf = ttk.Frame(main)
        bf.grid(row=row, column=0, columnspan=2, pady=10)
        self.scan_btn = ttk.Button(bf, text="Scan (Dry Run)", command=self._scan)
        self.scan_btn.grid(row=0, column=0, padx=5)
        self.apply_btn = ttk.Button(bf, text="Apply Changes", command=self._apply, state="disabled")
        self.apply_btn.grid(row=0, column=1, padx=5)
        row += 1

        # Progress
        self.progress = ttk.Progressbar(main, mode="indeterminate")
        self.progress.grid(row=row, column=0, columnspan=2, sticky="ew", pady=5)
        row += 1

        self.status = tk.StringVar(value="Ready - Configure settings and click Scan")
        ttk.Label(main, textvariable=self.status).grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1

        # Results
        ttk.Label(main, text="Results:").grid(row=row, column=0, columnspan=2, sticky="w", pady=(10, 5))
        row += 1
        self.results = scrolledtext.ScrolledText(main, height=14, font=("Consolas", 9))
        self.results.grid(row=row, column=0, columnspan=2, sticky="nsew", pady=5)
        main.rowconfigure(row, weight=1)

    def _update_tvshow_state(self):
        """Enable/disable TV show options based on checkbox."""
        state = "normal" if self.include_tvshows.get() else "disabled"
        self.seasons_cb.config(state=state)
        self.episodes_cb.config(state=state)
        self.per_item_rb.config(state=state)
        self.require_show_rb.config(state=state)

    def _browse_video(self):
        p = self._get_filedialog().askopenfilename(
            title="Select Video Database",
            filetypes=[("SQLite Database", "MyVideos*.db"), ("All", "*.*")]
        )
        if p:
            self.video_db_path.set(p)

    def _browse_texture(self):
        p = self._get_filedialog().askopenfilename(
            title="Select Texture Database",
            filetypes=[("SQLite Database", "Textures*.db"), ("All", "*.*")]
        )
        if p:
            self.texture_db_path.set(p)

    def _log(self, msg):
        self.results.insert(self.tk.END, msg + "\n")
        self.results.see(self.tk.END)
        self.root.update_idletasks()

    def _clear_log(self):
        self.results.delete(1.0, self.tk.END)

    def _set_status(self, msg):
        self.status.set(msg)
        self.root.update_idletasks()

    def _set_buttons(self, scanning=False, can_apply=False):
        self.scan_btn.config(state="disabled" if scanning else "normal")
        self.apply_btn.config(state="normal" if can_apply and not scanning else "disabled")

    def _get_current_settings(self) -> Settings:
        """Get current settings from GUI."""
        return Settings(
            include_movies=self.include_movies.get(),
            include_tvshows=self.include_tvshows.get(),
            include_seasons=self.include_seasons.get(),
            include_episodes=self.include_episodes.get(),
            tvshow_nfo_logic=self.tvshow_nfo_logic.get(),
            update_all_local=self.update_all_local.get(),
            video_db=self.video_db_path.get(),
            texture_db=self.texture_db_path.get()
        )

    def _save_settings(self):
        """Save current settings."""
        self._get_current_settings().save()

    def _scan(self):
        import threading

        video_db = self.video_db_path.get()
        texture_db = self.texture_db_path.get()
        messagebox = self._get_messagebox()

        if not video_db or not os.path.exists(video_db):
            messagebox.showerror("Error", "Video database not found")
            return
        if not texture_db or not os.path.exists(texture_db):
            messagebox.showerror("Error", "Texture database not found")
            return

        if not self.include_movies.get() and not self.include_tvshows.get():
            messagebox.showerror("Error", "Select at least one content type")
            return

        self._save_settings()
        self._set_buttons(scanning=True)
        self._clear_log()
        self.textures_to_update = []
        self.progress.start()

        def worker():
            try:
                self._do_scan(Path(video_db), Path(texture_db))
            except Exception as e:
                msg = str(e)
                self.root.after(0, lambda m=msg: self._log(f"\nERROR: {m}"))
            finally:
                self.root.after(0, self._scan_done)

        threading.Thread(target=worker, daemon=True).start()

    def _scan_done(self):
        self.progress.stop()
        can_apply = len(self.textures_to_update) > 0
        self._set_buttons(scanning=False, can_apply=can_apply)
        self._set_status("Scan complete" if can_apply else "Nothing to update")

    def _do_scan(self, video_db: Path, texture_db: Path):
        settings = self._get_current_settings()

        def log(msg):
            self.root.after(0, lambda m=msg: self._log(m))

        result = scan_for_updates(video_db, texture_db, settings, log_callback=log)

        if not result.textures_to_update:
            log("\nAll textures already up to date.")
            return

        log("\nPreview (first 15):")
        for tid, url, cur in result.textures_to_update[:15]:
            short = url if len(url) < 55 else "..." + url[-52:]
            log(f"  [{tid}] {short}")
            log(f"       {cur or 'NULL'} -> {FUTURE_DATE}")

        if len(result.textures_to_update) > 15:
            log(f"  ... and {len(result.textures_to_update) - 15} more")

        log("\n" + "=" * 50)
        log("Summary:")
        if result.movie_count:
            log(f"  Movies: {result.movie_count}")
        if result.tvshow_count:
            log(f"  TV Shows: {result.tvshow_count}")
        if result.season_count:
            log(f"  Seasons: {result.season_count}")
        if result.episode_count:
            log(f"  Episodes: {result.episode_count}")
        log(f"  Total artwork: {result.artwork_count}")
        log(f"  Textures to update: {len(result.textures_to_update)}")
        log("\nClick 'Apply Changes' to proceed")
        log("=" * 50)

        self.textures_to_update = result.textures_to_update

    def _apply(self):
        import threading

        messagebox = self._get_messagebox()

        if not self.textures_to_update:
            return

        if not messagebox.askyesno(
            "Confirm",
            f"Update {len(self.textures_to_update)} textures?\n\n"
            "Make sure Kodi is closed."
        ):
            return

        self._set_buttons(scanning=True)
        self.progress.start()

        def worker():
            try:
                texture_db = Path(self.texture_db_path.get())
                count = apply_updates(texture_db, self.textures_to_update)
                self.root.after(0, lambda c=count: self._log(f"\nUpdated {c} textures!"))
                self.root.after(0, lambda c=count: messagebox.showinfo("Done", f"Updated {c} textures"))
            except Exception as e:
                msg = str(e)
                self.root.after(0, lambda m=msg: self._log(f"\nERROR: {m}"))
                self.root.after(0, lambda m=msg: messagebox.showerror("Error", m))
            finally:
                self.root.after(0, self._apply_done)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_done(self):
        self.progress.stop()
        self._set_buttons(scanning=False, can_apply=False)
        self._set_status("Done")
        self.textures_to_update = []


def run_gui(video_db: Optional[Path], texture_db: Optional[Path], settings: Settings):
    """Run in GUI mode."""
    try:
        import tkinter as tk
    except ImportError:
        print("ERROR: tkinter not available.")
        print("On Linux, install with: sudo apt install python3-tk")
        print("Or run in CLI mode: python spinless.py --cli")
        sys.exit(1)

    root = tk.Tk()
    SpinlessApp(root, video_db, texture_db, settings)
    root.mainloop()


# --- Entry point ---

def main():
    parser = argparse.ArgumentParser(
        description="Prevent Kodi drive spinup by updating texture lasthashcheck dates.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                     Launch GUI
  %(prog)s --cli               Dry run in terminal (movies only)
  %(prog)s --cli --apply       Apply changes via terminal
  %(prog)s --cli --tvshows     Include TV shows
  %(prog)s --cli --all-local   Update all local artwork (ignore NFO)
        """
    )
    parser.add_argument("--cli", action="store_true", help="Run in command-line mode (no GUI)")
    parser.add_argument("--apply", action="store_true", help="Apply changes (CLI mode only)")
    parser.add_argument("--video-db", type=Path, help="Path to MyVideos*.db")
    parser.add_argument("--texture-db", type=Path, help="Path to Textures*.db")

    parser.add_argument("--movies", action="store_true", dest="movies", default=None,
                       help="Include movies (default: yes)")
    parser.add_argument("--no-movies", action="store_false", dest="movies",
                       help="Exclude movies")
    parser.add_argument("--tvshows", action="store_true", help="Include TV shows")
    parser.add_argument("--no-seasons", action="store_true", help="Exclude seasons")
    parser.add_argument("--no-episodes", action="store_true", help="Exclude episodes")

    parser.add_argument("--nfo-logic", choices=["per_item", "require_show_nfo"],
                       default="per_item", help="Episode NFO logic (default: per_item)")

    parser.add_argument("--all-local", action="store_true",
                       help="Update all local artwork (ignore NFO requirement)")

    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    args = parser.parse_args()

    settings = Settings.load()

    if args.movies is not None:
        settings.include_movies = args.movies
    if args.tvshows:
        settings.include_tvshows = True
    if args.no_seasons:
        settings.include_seasons = False
    if args.no_episodes:
        settings.include_episodes = False
    if args.nfo_logic:
        settings.tvshow_nfo_logic = args.nfo_logic
    if args.all_local:
        settings.update_all_local = True

    video_db = args.video_db or find_database("MyVideos*.db")
    texture_db = args.texture_db or find_database("Textures*.db")

    if args.cli:
        if not video_db or not video_db.exists():
            print("ERROR: Video database not found. Use --video-db to specify path.")
            sys.exit(1)
        if not texture_db or not texture_db.exists():
            print("ERROR: Texture database not found. Use --texture-db to specify path.")
            sys.exit(1)
        run_cli(video_db, texture_db, settings, args.apply)
    else:
        run_gui(video_db, texture_db, settings)


if __name__ == "__main__":
    main()
