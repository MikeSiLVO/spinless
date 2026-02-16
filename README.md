# Spinless

Prevent Kodi from spinning up your drives during library browsing.

If you find Spinless useful, [buy me a coffee](https://ko-fi.com/mikesilvo) ☕

## The Problem

Kodi checks local artwork files **daily** to detect if they've changed. This causes hard drives to spin up every time you browse your library - frustrating for users with media on external drives or NAS storage.

There's no built-in setting to disable this behavior.

## The Solution

Spinless sets the `lasthashcheck` date in Kodi's texture database to a far future date (2099), preventing these daily checks.

**By default, only updates artwork for items with NFO files** - the assumption being that if you've created an NFO, you've curated that item's artwork and don't need Kodi constantly re-checking it.

## Features

- **Cross-platform**: Windows, Linux, macOS
- **GUI and CLI modes**: Use whichever you prefer
- **Video library**: Movies, sets, TV shows (seasons, episodes), music videos, and actors
- **Music library**: Artists and albums
- **Safe**: Dry-run by default, preview changes before applying
- **Selective**: Only affects items with NFO files (or all local artwork with advanced option)
- **Auto-detection**: Finds Kodi databases automatically
- **Settings persistence**: Remembers your preferences

## Requirements

- Python 3.7+
- tkinter (for GUI mode - included on Windows/macOS, `python3-tk` package on Linux)

## Usage

### GUI Mode (Default)

```bash
python spinless.py
```

1. Databases are auto-detected (or browse to select manually)
2. Configure content types and options
3. Click **Scan (Dry Run)** to preview changes
4. Click **Apply Changes** to update the database

### CLI Mode

```bash
# Dry run - movies and actors (default)
python spinless.py --cli

# Include TV shows
python spinless.py --cli --tvshows

# Apply changes
python spinless.py --cli --apply

# Update ALL local artwork (ignore NFO requirement)
python spinless.py --cli --all-local --apply

# Include music library
python spinless.py --cli --music-artists --music-albums

# Specify database paths manually
python spinless.py --cli --video-db /path/to/MyVideos131.db --texture-db /path/to/Textures14.db --apply
```

### CLI Options

| Option | Description |
|--------|-------------|
| `--cli` | Run in command-line mode (no GUI) |
| `--apply` | Apply changes (default is dry-run) |
| `--movies` / `--no-movies` | Include/exclude movies (default: include) |
| `--sets` / `--no-sets` | Include/exclude movie sets (default: include) |
| `--tvshows` | Include TV shows |
| `--no-seasons` | Exclude seasons when processing TV shows |
| `--no-episodes` | Exclude episodes when processing TV shows |
| `--musicvideos` | Include music videos |
| `--actors` / `--no-actors` | Include/exclude actors (default: include) |
| `--music-artists` | Include music artists |
| `--music-albums` | Include music albums |
| `--nfo-logic` | Episode NFO logic: `per_item` (default) or `require_show_nfo` |
| `--all-local` | Update ALL local artwork (ignore NFO requirement) |
| `--path-sub FROM=TO` | Path substitution for remote DB access (repeatable) |
| `--video-db PATH` | Path to MyVideos*.db |
| `--music-db PATH` | Path to MyMusic*.db |
| `--texture-db PATH` | Path to Textures*.db |

## TV Show NFO Logic

For TV shows, Spinless uses these NFO files:

- **tvshow.nfo** - For the show itself (and inherited by seasons)
- **Episode NFO** - Named to match the episode file (e.g., `S01E01.nfo` for `S01E01.mkv`)

**Per-item mode** (default): Each episode needs its own NFO file.

**Require show NFO mode**: Episodes only updated if their parent show has `tvshow.nfo` AND the episode has its own NFO.

Note: Kodi doesn't use `season.nfo` files - season artwork is managed through the show.

## Important Notes

1. **Close Kodi** before running with `--apply` to avoid database locking issues

2. **Re-run after curating new items** - When you add NFOs to more items, run the tool again to update those textures

3. **Manual artwork changes reset the date** - If you change artwork via Kodi's "Manage Artwork" dialog, the `lasthashcheck` will be reset. Just re-run this tool afterward.

4. **Remote artwork unaffected** - This only matters for local artwork files. HTTP/HTTPS artwork URLs (TMDB, Fanart.tv, etc.) are never hash-checked.

## Actors

Actor artwork (images stored in `.actors` folders alongside your media) requires the Actors option enabled along with at least one of Movies or TV Shows. In NFO mode, actors are included if they appear in any selected content item that has an NFO file. In `--all-local` mode, all actors are included.

## Music

Music artwork is sourced from a separate database (`MyMusic*.db`) but cached in the same texture database as video artwork. Enable with `--music-artists` and/or `--music-albums` in CLI, or the checkboxes in GUI.

Music always processes all local artwork — there's no NFO filtering. The `--all-local` flag has no effect on music content types.

## How It Works

1. Scans your video and/or music databases for selected content types
2. Gets local artwork URLs for those items
3. Finds matching entries in the texture cache database
4. Updates `lasthashcheck` to `2099-01-01 00:00:00`

Kodi's hash check logic:
- If `lasthashcheck` is older than 1 day → re-check the file (spins up drive)
- If `lasthashcheck` is in the future → skip the check (no drive access)

## Settings

Settings are saved to:

| Platform | Path |
|----------|------|
| Windows | `%APPDATA%\spinless\settings.json` |
| Linux | `~/.config/spinless/settings.json` |
| macOS | `~/Library/Application Support/spinless/settings.json` |

## Logging

Spinless automatically logs to a rotating log file in the same directory as settings. No configuration needed.

| Platform | Log file |
|----------|----------|
| Windows | `%APPDATA%\spinless\spinless.log` |
| Linux | `~/.config/spinless/spinless.log` |
| macOS | `~/Library/Application Support/spinless/spinless.log` |

- **Rotation**: 1 MB max per file, 3 backups kept (`spinless.log.1`, `.2`, `.3`) — 4 MB total cap
- **Verbosity**: Always logs at DEBUG level for full diagnostic detail
- **Log file path** is shown in the CLI output and in the GUI results pane on startup

The log captures:

- Settings, database paths, and path substitutions used for each run
- Scan progress: item counts, artwork found, textures matched
- Per-item NFO check results — which directories weren't found and which were missing NFO files
- Path conversions (WSL drive mapping, UNC paths, user-defined substitutions)
- Database auto-detection: which paths were searched and what was found
- Apply operations: how many textures were updated
- Errors with full tracebacks

The log is the first place to check when results are unexpected (e.g., fewer items found than expected). Run a scan, then check the log for `"directory not found"` or `"not found in"` entries to see exactly which items were skipped and why.

## Database Locations

The tool auto-detects these Kodi database locations:

| Platform | Path |
|----------|------|
| Windows | `%APPDATA%\Kodi\userdata\Database\` |
| Linux | `~/.kodi/userdata/Database/` |
| macOS | `~/Library/Application Support/Kodi/userdata/Database/` |
| LibreELEC | `/storage/.kodi/userdata/Database/` |
| Flatpak | `~/.var/app/tv.kodi.Kodi/data/userdata/Database/` |

## FAQ

**Q: Is this safe?**
A: Yes. It only modifies the `lasthashcheck` timestamp - it doesn't delete anything or change your artwork. If you ever want to revert, you can set `lasthashcheck` back to a past date or clear your texture cache.

**Q: Will Kodi still detect if I change my artwork?**
A: Not automatically. If you replace a local artwork file, you'll need to either:
- Manually refresh the item in Kodi
- Re-run this tool (which will process newly-modified textures)
- Clear the texture cache for that item

**Q: Why only items with NFOs?**
A: NFO presence indicates you've manually curated that item. Items without NFOs might still have artwork from automatic scraping that you haven't reviewed yet. Use `--all-local` to update everything regardless.

## License

MIT License - See [LICENSE](LICENSE) file.
