# smclipy

> [!IMPORTANT]
> **Disclaimer: this project is vibecoded.** Most of the code, tests, and documentation were written by AI assistants under human direction. It is provided as-is, with no warranty and no guarantee of correctness, security, or fitness for any purpose. Review the code before trusting it with your data, and use it at your own risk.

**simple music cli py** is a Python script designed to streamline downloading, tagging, and organizing music from **YouTube and SoundCloud**. It allows you to batch-download audio, manually set metadata (including multiple artists, album, and cover art).

## Features

- **Batch Downloading:** Queue up as many YouTube or SoundCloud URLs as you want before processing. Recognizes `youtube.com` watch/shorts/live/embed links, `youtu.be` links, and `soundcloud.com` tracks. Unrecognizable pastes are warned about and duplicate URLs are collapsed.
- **Interactive Tagging:** For each track you set the Title, Album, and Artists (with autocompletion). The title defaults from the video's title, and the artists from the channel/uploader — corrected against your known authors (ignoring case and spaces).
- **MusicBrainz Re-tagging (`tag`):** List every song already in your library and look it up on [MusicBrainz](https://musicbrainz.org/) (free, no API key) to fix title, artists, album, release date, genre, track number, album artist, and cover art — reviewing each candidate and the exact diff before applying. Narrow the run with `--search TEXT` (title, album, or artist) or `--specific FIELD` (autocomplete an existing title, album, or artist).
- **Source Finding (`findsource`):** Search YouTube and SoundCloud for songs already in your library and record the matching URL in the tracking database. Providers are tried in the order set by `source_preferences` (default: YouTube, then SoundCloud); by default you pick from the candidates, while `--auto` saves the best plausible result. A result only counts when it names the song's artist (uploader or an `Artist - Title feat. Artist2` title) or repeats its title, so unrelated videos that merely share a duration are skipped. Narrow the run with `--search TEXT` or `--specific FIELD`, use `--force` to replace an existing source, and combine `--auto --all` for a headless run.
- **Playlists (`playlist`):** Build playlists of your library songs (create/rename/delete/list/show), add/remove/move songs interactively, sort by metadata, and export them to `.m3u`, `.m3u-link` (one URL per line with artist/title labels), or `.json` — or import `.m3u`/`.json` playlists by resolving the entries against your library (UUID first, then each path relative to the file, your music folder, or a unique matching filename). Playlists are stored in `smclipy.db` and reference songs by their tracking UUID, so renaming or relocating a song never breaks its playlists. When a JSON import leaves songs unmatched, it reports how many are missing and points to `smclipy download --file`, which downloads each missing song from its recorded `source_url` and applies the playlist metadata automatically (`--auto` also skips the cover-crop question). Use `smclipy findsource --file playlist.json` to search for and fill missing source URLs, and `smclipy modify --file playlist.json` to edit the exported entries by hand.
- **Video Info Preview:** Before tagging, the video's title, artist/s, and the first `description_max_lines` lines of its description are shown for context.
- **Cover Art Cropping:** Crop cover art to a perfect 1:1 square ratio during tagging, with a terminal preview of the art.
- **Cover Rescan (`crop`):** Re-extracts covers from your existing audio files, detects pillarboxed art, lets you crop it to 1:1, and re-embeds it into the matching files. Images you decline to crop are remembered so you're not asked twice.
- **Manual Editing (`modify`):** Edit the tags of any library song by hand — title, artists, album, release date, genre, album artist, track number, cover art, and source URL. Pick a range to bulk-apply one set of values (e.g., the songs of an album) or a single number to edit one song; a blank answer keeps a field, and typing `/clear` empties it. The source URL is tracking metadata stored in `smclipy.db` (not written into the audio file). Narrow the list first with `--search TEXT` (matches title, album, or any artist) or `--specific FIELD` (autocomplete an existing title, album, or artist), then pick among the matches.
- **Name Deduplication (`dedupe`):** Find artist and album names that differ only in capitalization or spacing — `Artist`, `artist`, `a r t i s t` — pick the spelling to keep for each group, and smclipy rewrites the tags, renames the `artist-title` files and their cover images, and updates the tracking database. Every group is reviewed before anything is written; use `--artists`, `--albums`, or `--dry-run` to narrow or preview the run.
- **Interrupt & Retry Friendly:** Queues are saved as you paste them — pressing Ctrl+C saves what you've entered, queues survive interruptions, and `download` detects an unfinished queue and offers to resume. Videos that fail (e.g., HTTP errors) are kept pending for a later retry, with up to `max_download_attempts` total attempts per URL, after which they're marked permanently failed and reported; already-processed ones are never re-downloaded.
- **Overwrite Protection:** Asks before overwriting an existing audio file with the same `artist-title` name, with a per-track `[i/N]` progress line while processing. A choice to keep your existing copy is remembered, so that track isn't re-asked on a later resume. In headless `--no-prompt` mode every prompt is skipped (title, album, artists, cover-crop, overwrite), so an existing file is simply kept and skipped.
- **Clean Cleanup:** Uses a `.temp` directory (inside your music folder's `smclipy` subfolder) during the download and tagging process to keep your main library clean.

## Requirements

- **Python 3.12+** (handled automatically if you use `uv`).
- **FFmpeg** — required by yt-dlp to extract the audio to your chosen format and embed the cover art.
- **[Deno](https://docs.deno.com/runtime/getting_started/installation/)** — required by yt-dlp (2025.11+) to solve YouTube's JavaScript challenges during extraction. Without it, downloads may fail or have limited format availability.

## Installation

```bash
pip install smclipy
```

## Configuration

smclipy relies on a configuration file to know where to organize your files.

On the first run of `download`, `crop`, `tag`, `findsource`, `modify`, `dedupe`, `restore`, `update`, `stats`, or any `playlist` action, a default config file is created at `~/.config/smclipy/config.json` and the program exits so you can edit it to your liking. The `-d`/`--directories` and `-v`/`--version` options work immediately using defaults, so you can preview where everything will live before a config exists. To store the config somewhere else, set the `SMCLIPY_CONFIG` environment variable to your preferred path (respects `$XDG_CONFIG_HOME`). If a key is missing from your existing config, it is re-added with its default value on the next command (except `-d`/`--directories`, which never rewrites the file).

```json
{
  "name": "smclipy",
  "path_to_music_folder": "./Music",
  "description_max_lines": 5,
  "write_album_if_same_as_title": false,
  "clean_unwanted_tags": false,
  "audio_format": "mp3",
  "max_download_attempts": 3,
  "max_backups": -1,
  "cookies": "",
  "cookies_from_browser": "",
  "source_preferences": ["youtube", "soundcloud"],
  "tag_fields": ["title", "artists", "album", "date", "genre", "album_artist", "track_number", "cover"]
}
```

### Config Breakdown

- `name`: The name of the smclipy state folder (`smclipy`) created inside your music folder to hold the tracking database, covers, backups, and temp files. Song files themselves are saved directly in the music folder (or its subfolders).
- `path_to_music_folder`: The base directory where your music library lives (default is `./Music`, relative to wherever you run the command from; a leading `~` is expanded).
- `description_max_lines`: How many lines of the video description to show while tagging (default is 5).
- `write_album_if_same_as_title`: When `true`, the `tag` command writes the album even if it equals the song title. When `false` (default), an album that matches the title is left empty.
- `clean_unwanted_tags`: When `true`, MP3 downloads strip every tag except title, artists, album, cover art, and the tracking UUID before embedding new tags (FLAC, M4A, OGG, and OPUS encode their own minimal tag sets, so there is nothing extra to strip there). When `false` (default), any tags the source provided (genre, date, lyrics, comments, ...) are kept untouched; the `tag` command still overwrites the fields you confirm.
- `audio_format`: The format new downloads are saved in. Valid values are `mp3` (default), `m4a`, `flac`, `opus`, and `ogg` (Ogg Vorbis). Existing files in other supported formats are still recognized and re-tagged, so changing this never orphans your library.
- `max_download_attempts`: How many times `download` tries a URL before giving up on it permanently. After this many failed attempts the URL is marked failed, reported at the start of the next run, and not re-queued again (default is 3).
- `max_backups`: How many tag snapshots to keep per song in `smclipy/backups/<uuid>/`. The default `-1` keeps every snapshot; a positive number keeps only the newest N and deletes older snapshots (and their saved cover art) whenever a new one is recorded. `0` and values below `-1` are invalid and fall back to `-1` with a warning. Pre-crop cover copies in `backups/covers/` are not affected.
- `cookies`: Path to a Netscape-format `cookies.txt` file passed to yt-dlp. Needed for age-restricted or sign-in-required videos (see the [yt-dlp cookie guide](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies)). `~` is expanded; a missing or unreadable file is ignored with a warning. Leave empty to disable.
- `cookies_from_browser`: Browser name to extract cookies from (e.g. `firefox`, `chrome`) — must be one of the browsers yt-dlp supports, otherwise it is ignored with a warning. Lower priority than `cookies` — if both are set, the cookies file wins. Leave empty to disable.
- `source_preferences`: The order in which `findsource` searches providers for each song. Valid values are `youtube` (alias `yt`) and `soundcloud` (alias `sc`); unknown or duplicate entries are ignored, and an empty/invalid list falls back to `["youtube", "soundcloud"]`. The first provider that returns results wins, so put your preferred source first.
- `tag_fields`: Which metadata fields the `tag` command may apply to your library songs. Valid values are `title`, `artists`, `album`, `date`, `genre`, `album_artist`, `track_number`, and `cover`. Genres come from MusicBrainz and, when several are listed, are written most-voted first and joined with `; ` (e.g. `progressive rock; art rock; rock`).

## Usage

Run it from your music library's parent directory:

```bash
smclipy <command>
```

You can also invoke it as a module: `python -m smclipy <command>`. Note that `download`, `crop`, `tag`, `findsource`, `modify`, `dedupe`, `restore`, and the interactive playlist actions (`add`, `remove`, `move`) require an interactive terminal, with four headless exceptions: `download --batch FILE --no-prompt`, `download --file FILE --auto` (or `--no-prompt`), `tag --auto --all`, and `findsource --auto --all`. `tag --specific` and `findsource --specific` always need a terminal (they autocomplete existing values), even alongside `--auto --all`; `--search` does not break the headless mode (`tag --auto --all --search TEXT`, `findsource --auto --all --search TEXT`). `update`, the JSON modes, and the remaining playlist actions never require a terminal, so they can be scripted or scheduled.

### Commands

- **`smclipy download`** — Batch download and tag songs from YouTube or SoundCloud. Pass `--batch FILE` to read URLs from a file and `--no-prompt` to accept the default title, album, and artists, which together make it safe for cron/scripts. Pass `--file FILE` with a v2 playlist JSON to download only its missing songs and apply each entry's title, artists, album, date, genre, album artist, and track number automatically; `--auto` is a synonym for `--no-prompt` that also skips the cover-crop question. Downloaded songs are not added to the playlist automatically — re-run `smclipy playlist import` afterwards.
- **`smclipy crop`** — Re-extract covers from your audio files, find pillarboxed art, crop to 1:1 interactively, and re-embed into the matching files.
- **`smclipy tag`** — Retag songs already in your library using MusicBrainz metadata. Songs you already tagged (recorded in the tracking database) are automatically skipped with a warning, so re-running over `all`, a range, or a single number only processes what's new. Songs imported from the older text-file tracking format are *not* treated as already tagged, so they are offered on the first run.
  - `smclipy tag --auto` (`-a`) — **Full-auto:** after you pick the song range, the top MusicBrainz match is applied to each selected song automatically with no further prompts, writing every enabled `tag_fields` value (including the title). Shows a per-song progress line instead of the match picker and confirm dialog.
  - `smclipy tag --semi` (`-s`) — **Semi-auto:** the top match is picked automatically per song, but you still review and confirm each change set (checkbox dialog) before it is applied.
  - `smclipy tag --all` — Skip the range prompt and select every untagged song. Combine with `--auto` (`smclipy tag --auto --all`) to retag the whole library without an interactive terminal.
  - `smclipy tag --json` (`-j`) — Print a machine-readable JSON report to stdout (per-song outcome, recording used, fields written) while human progress goes to stderr. Requires `--auto --all`; use it for a scriptable headless retag report. Exits with status 1 when any song crashed, even though the report is still printed.
  - `smclipy tag --skip-not-found` — Skip songs whose last MusicBrainz lookup found no match instead of retrying them. Run without it to retry those songs.
  - `smclipy tag --force` — Re-process songs that were already tagged by a previous `tag` run instead of skipping them. Combine with `--all` (or `--auto --all`) to retag the whole library. A song whose current tags already match MusicBrainz is reported as already matching rather than rewritten.
  - Transient lookup failures are retried automatically: if the MusicBrainz search fails, nothing is applied and the song stays untagged; if the genre or cover-art request fails (network error, rate limit, server error), the fields that did resolve are still written but the song stays untagged, so the next `tag` run retries it. A release that genuinely has no cover art is final and is not retried.
  - `smclipy tag --max-age AGE` — Only process songs added to the library within the last `AGE` (e.g. `30m`, `24h`, `7d`). Useful with `--auto --all` to retag only recent downloads.
  - `smclipy tag --search TEXT` — Only consider songs whose title, album, or any artist contains `TEXT` (case-insensitive). Combine with `--auto --all` for a headless retag of the matches.
  - `smclipy tag --specific FIELD` — Autocomplete the library's existing `title`, `album`, or `artists` values, pick one, and only retag songs that match it. Requires an interactive terminal. `--search` and `--specific` are mutually exclusive.
- **`smclipy findsource`** — Search for download sources of songs already in your library (or, with `--file`, the entries of a v2 playlist JSON) and save the matching URL in the tracking database (`source_url`). Providers are tried in the order from `source_preferences` (default `youtube`, then `soundcloud`); the first provider with results wins unless `--all-providers` is set. Candidates are ranked so the most plausible result is first: a matching length (within two seconds of the library song), then artist similarity, then provider preference, then the provider's own relevance order. Artist names are taken from the uploader, the prefix of an `Artist - Title` title, and `feat.`/`ft.`/`featuring`/`, with` credits, and are compared against every artist on the song (exact or fuzzy, ignoring case, punctuation, and common `- Topic`/`VEVO`/`Official`/`Music` suffixes). A candidate is only accepted when it names the song's artist or repeats its title; unrelated results are ignored even when their duration matches. By default you pick from the ranked candidates (or skip the song), with unmatched results shown in red and Skip preselected when nothing matches; auto mode skips the song when no candidate matches. Songs that already have a source are skipped. Nothing is downloaded and song tags are not changed (the library scan may still add a tracking UUID to a file that has none yet).
  - Interactive mode shows the library song's length in the header (e.g. `=== Title - Artist (1:40) ===`) and lists the candidates in ranked order. Candidates whose length is within two seconds are highlighted in bold green with a `★ ... [same length]` marker.
  - `smclipy findsource --auto` (`-a`) — **Full-auto:** after you pick the song range, the best-ranked plausible result is saved with no candidate picker; if no result names the artist or repeats the title, the song is skipped and recorded as `rejected`.
  - `smclipy findsource --all-providers` — With `--auto`, search every provider in `source_preferences` instead of stopping at the first one that returns results, then rank the combined candidates. Useful when the preferred provider does not carry a song or has a poor match; it can roughly double the number of searches per song. Requires `--auto`.
  - `smclipy findsource --all` — Skip the range prompt and select every song. Combine with `--auto` (`smclipy findsource --auto --all`) to run without an interactive terminal.
  - `smclipy findsource --file FILE` (`-f`) — Search the entries of a smclipy v2 playlist JSON instead of the library. Every entry is listed for selection; existing `source_url` values are kept unless `--force`. Found URLs are written back to the playlist atomically, and for entries already downloaded/tracked the URL is saved in the database too. Entries that are not in the library only update the JSON. When the DB already knows a URL for a downloaded entry, it is copied into the playlist without a search.
  - `smclipy findsource --json` (`-j`) — Print a machine-readable JSON report to stdout (per-song outcome, provider, and URL) while human progress goes to stderr. Requires `--auto --all`; exits with status 1 when any song crashed.
  - `smclipy findsource --force` — Search songs that already have a recorded source URL and replace it when a new source is found. If the search returns the same URL again, the song is reported as `unchanged`. Without `--force`, already-sourced songs are skipped (and reported separately).
  - `smclipy findsource --skip-skipped` — Skip songs whose last search was skipped (you chose Skip), found no match, or was rejected (no artist/title match), instead of searching them again. Useful with `--auto --all` so repeated runs only work on songs that still need a source. Transient outcomes (`unavailable`, errors) are never skipped this way; run without the flag to retry everything.
  - `smclipy findsource --search TEXT` — Only search songs whose title, album, or any artist contains `TEXT` (case-insensitive). Combine with `--auto --all` for a headless run.
  - `smclipy findsource --specific FIELD` — Autocomplete the library's existing `title`, `album`, or `artists` values, pick one, and only search songs that match it. Requires an interactive terminal. `--search` and `--specific` are mutually exclusive.
  - Provider failures (network errors, rate limits) are warned about and the next configured provider is tried. If no candidate is found and every provider failed, the song is reported as `unavailable`; if some providers failed and others returned no results — or returned only results that did not match — it is also `unavailable` because the search was incomplete. The outcome of every search is recorded in the tracking database (`source_status`); `skipped`, `not_found`, and `rejected` are what `--skip-skipped` filters, while `unavailable` and per-song errors stay retryable. A provider that simply has no results falls through to the next provider.
- **`smclipy modify`** — Pick songs from the library list (numbers, ranges, or `all`) and edit their tags by hand, or use `--file` to edit one audio file or the entries of a v2 playlist JSON: title, artists, album, release date, genre, album artist, track number, cover art, and source URL. You choose which fields to touch in a checkbox dialog, then type one value per field that's applied to every selected song — so a range bulk-edits an album and a single number edits just that song. A blank answer keeps the field, typing `/clear` empties it (e.g., to remove a bad album). For cover art, give a path to an image file to embed it, or `/clear` to remove the current image. Source URL is database-only tracking metadata, not an audio tag, so editing it never rewrites the file and it is not included in `restore` snapshots.
  - `smclipy modify --search TEXT` (`-s`) — Only consider songs whose title, album, or any artist contains `TEXT` (case-insensitive), then pick among the matches as usual.
  - `smclipy modify --specific FIELD` — Autocomplete the library's existing `title`, `album`, or `artists` values, pick one, and only consider songs that match it. `--search` and `--specific` are mutually exclusive.
  - `smclipy modify --file FILE` (`-f`) — Edit a single audio file directly (inside the music folder it is tracked first; outside it tags are still written, with a warning), or edit the entries of a v2 playlist JSON. Playlist mode supports every field except cover art and changes only the JSON file; source URL stays a JSON field there. `--reset-mb` and cover editing apply only to audio files.
  - `smclipy modify --reset-mb` — Also forget the MusicBrainz match of every selected song, so a later `smclipy tag` run offers it again. This happens even when you leave every field unchanged, so it also works to retry a song that was tagged without cover art.
- **`smclipy dedupe`** — Find artist and album names whose only difference is capitalization or spacing (for example `Artist`, `artist`, and `a r t i s t`). Each group gets a single-choice dialog to pick the spelling to keep; the program then rewrites the tags, renames the affected `artist-title` audio files and cover images, and updates the tracking database. Nothing is written until every group has been reviewed.
  - `smclipy dedupe --artists` / `--albums` — Only look for artist or only for album spellings (default: both).
  - `smclipy dedupe --dry-run` — Print the planned tag, filename, cover, and database changes without writing anything.
- **`smclipy restore`** — Browse one list of restore points, newest first, and undo a mistaken `tag`, `modify`, `crop`, or `dedupe` change. Every mutation records a backup of the song's previous tags and cover art first; each row names the song, the operation that followed the backup, when it was taken, and the values it would restore, and a full before/after preview (including cover changes) is shown before anything is written. The restore itself is backed up too. Filename renames made by `dedupe` are not undone. Also handy after a bad `tag --auto` run caught in time.
  - `smclipy restore --all` — Show every restore point instead of only the newest 25.
  - `smclipy restore --limit N` — Show at most N restore points (default: 25).
- **`smclipy playlist`** — Create, rename, delete, list, show, fill, reorder, sort, and import/export playlists of your library songs. Every action is documented below in the CLI reference. `list` and `show` accept `--json` (`-j`) for machine-readable output; `export` rescans the library first so paths are current (use `--no-scan` to skip the rescan).
- **`smclipy update`** — Rescan the music folder and sync the tracking database: new files get registered, renames are recorded, and vanished files are marked missing. Prints a summary of what changed, or a JSON report with `--json`; safe to run anytime, including from scripts or cron.
- **`smclipy stats`** — Summarize the library: total songs, albums, and artists, total reproduction (playback) time, and the albums and artists with the most songs. Rescans first so the numbers are current; use `--no-scan` to report the database as-is, `--top N` to change how many albums/artists are listed (default 10), and `--json` for a machine-readable report. Safe to run from scripts or cron.
- **`smclipy -d` / `--directories`** — Print the directories and files smclipy uses (config, music, library, temp, covers, backups, database) and exit; it cannot be combined with a command.
- **`smclipy -v` / `--version`** — Print the version and exit.

**MusicBrainz attribution:** the `tag` command searches [MusicBrainz](https://musicbrainz.org), a community-maintained music encyclopedia run by the [MetaBrainz Foundation](https://metabrainz.org). MusicBrainz core data is released as CC0; supplementary data and the MusicBrainz documentation are licensed under [CC BY-NC-SA 3.0](https://creativecommons.org/licenses/by-nc-sa/3.0/). See [MusicBrainz's data license page](https://musicbrainz.org/doc/About/Data_License) for the full breakdown. Cover art is served by the [Cover Art Archive](https://coverartarchive.org/), a joint project of the Internet Archive and MusicBrainz; the images are copyrighted by their respective copyright holders.

### Download flow

1. **Queue URLs:** Paste your URLs one by one (enter an empty line to finish).
2. **Resume or restart:** If a previous run left an unfinished queue, you're asked whether to resume from where it stopped or start a fresh queue.
3. **Tagging Flow (Per Track):**
   - The track is downloaded to `Music/smclipy/.temp`.
   - If a cover image is found, it's previewed; if it isn't already 1:1 you're prompted to crop it.
   - A preview shows the video's title, artist/s, and its description (truncated to `description_max_lines` lines).
   - Enter the **Title** of the track (prefilled with the video's title).
   - Enter the **Album** (prefilled when the video provides one).
   - Enter the **Artist/s** (prefilled from the channel name; if it matches an author already known — ignoring case and spaces — your existing spelling is kept). To tag multiple artists, separate them using a backslash `\` or a comma (e.g., `Artist 1\Artist 2\Artist 3` or `Artist 1, Artist 2`). A name that itself contains a comma (or a backslash) must escape it, e.g. `Earth\, Wind & Fire`, so it stays one artist.
4. **Save / Skip:** Once tagged, the file is moved to your music folder as `artist-title.<audio_format>` (e.g., `Rick Astley-Never Gonna Give You Up.mp3`). If a file with that name already exists, you're asked whether to overwrite it or keep your existing copy; keeping it is remembered so you aren't asked about that track again. Overwriting a tracked song keeps its existing tracking UUID, so the database row is updated in place and no phantom duplicate is created. Successfully processed videos are recorded so they're skipped on resume; failed videos stay pending and are reported at the end so you can retry them later — once `max_download_attempts` total attempts have been made, a dead URL is reported as permanently failed instead of being re-queued.

## Folder Structure Example

After running the script and tagging a few songs, your output directory will look something like this:

```text
📁 ./Music
├── 🎵 artist1-title1.mp3
├── 🎵 artist1-title2.mp3
├── 🎵 artist2-title1.mp3
├── 🎵 artist3-title1.mp3
└── 📁 smclipy
    ├── 📁 .temp                       <-- Temp files during processing
    ├── 📁 covers                      <-- Cover art extracted from your audio files
    ├── 📁 backups                     <-- Tag snapshots used by `smclipy restore`
    └── 📄 smclipy.db                  <-- Tracking database (songs, history, queue, authors)
```

> **Snapshots & undo:** before every tag, modify, crop, or dedupe change, smclipy records the song's current tags (and cover art) as a snapshot in `smclipy/backups/<uuid>/`; set `max_backups` to a positive number to keep only the newest N snapshots per song (the default `-1` keeps all of them). A mistaken `tag --auto` run, a bad manual edit, a wrongly-cropped cover, or a mis-merged artist/album name can therefore be rolled back at any time with `smclipy restore`, which lists every backup newest first with the values it would restore, shows a before/after preview (including cover changes), and then rewrites the file's tags (and — where a backup contains cover art — re-embeds it) and re-syncs the database; filename renames made by `dedupe` are not undone. Every write to the audio files is atomic (a sibling temp file renamed over the original), so an interrupted run never leaves a half-written song, and leftovers are swept by the next scan. Original cover files are also copied into `smclipy/backups/covers/` before a crop, so a false-positive crop is never permanently destructive.

> **Subfolders are scanned too:** `update`, `tag`, `findsource`, `modify`, `dedupe`, and `crop` no longer look only at files in the root of the music folder — albums organized into subdirectories are registered and tracked the same way (paths are recorded relative to the music folder). The smclipy state folder, its subtrees (`.temp`, `covers`, `backups`) and any hidden directories (`.stfolder`, `.stversions`, etc.) are skipped, so scanning never mistakes the script's own data for music. Songs in hidden folders are intentionally not scanned.

> **Tracker:** every song is identified by a UUID stored in its tags, so renaming a file keeps it tracked (the rename is recorded). The database keeps the download URL, MusicBrainz match status (tagged / skipped / not found), cover status (not pillarbox / false positive / cropped), and an append-only event history per song, all inspectable directly in `smclipy.db`.
>
> **Re-process a song:** to re-tag a song that was already tagged, or be offered a cover you previously declined, reset its MusicBrainz status with `smclipy modify --reset-mb` or edit `smclipy.db` directly (or delete the row to forget it entirely). To re-process every already-tagged song in bulk without resetting status, run `smclipy tag --force` (add `--all` for the whole library). A song that is missing from the music folder is marked missing but keeps its history; put the file back and it re-appears under the same tracking ID.
>
> **Upgrading:** the old text-file tracking (`authors.txt`, `tagged_files.txt`, the queue files, and `cropping_tool_false_positives.txt`) is imported once into `smclipy.db` on the first run of a version that has the database, after which those files are ignored. The database schema is versioned, so future releases upgrade it in place without losing history. Legacy rows are imported as untagged, so the first `tag` run offers every existing library song for MusicBrainz metadata.
>
> **Syncthing & other synced shares:** if your music folder is shared between devices, the smclipy state that lives inside it (`smclipy.db`, `.temp`, `covers`, `backups`) should not sync — two devices writing the SQLite database independently with last-writer-wins whole-file copies will corrupt it, and half-downloaded temp files replicate mid-write. An `.stignore` template is provided in the repo (`stignore.example`): copy it to `.stignore` in the shared folder's root (or un-ignore just the `backups/` directory if you want restores mirrored).

## Development

Clone the repo and run it from the project root with uv (set `SMCLIPY_CONFIG` to use your existing `config.json`):

```bash
uv run smclipy <command>
```

Check your changes with the same tooling used in CI:

```bash
uv run pytest          # tests
uv run ruff check      # lint
uv run ruff format --check  # format
uv run mypy            # type checks
```

## Roadmap

See [roadmap.md](roadmap.md) for planned work: async/concurrent downloads, a configurable library folder structure, and a full TUI upgrade.

## CLI reference

The exact `--help` output of the CLI and of every command. This is always current: run any command with `--help` (or `smclipy <cmd> <action> --help` for playlist actions) to see the live version.

#### `smclipy`

```text
usage: smclipy [-h] [-v] [-d] command ...

Download, tag, and organize music from YouTube and SoundCloud.

positional arguments:
  command
    download         Batch download songs from YouTube or SoundCloud and tag
                     them.
    crop             Crop pillarboxed cover images and re-embed them.
    tag              Retag library songs using MusicBrainz metadata.
    findsource       Find and record download sources for library songs.
    modify           Manually edit the tags of one or more library songs.
    dedupe           Merge differently formatted artist and album names.
    restore          Undo tag changes from recorded backups.
    playlist         Create and manage library playlists, and import/export
                     them.
    update           Rescan the library and sync the tracking database.
    stats            Report library totals: songs, albums, artists, playtime.

options:
  -h, --help         show this help message and exit
  -v, --version      Print the version and exit.
  -d, --directories  Print the directories smclipy uses and exit (cannot be
                     combined with a command).

examples:
  smclipy download   Batch download songs and tag them interactively
  smclipy download --batch urls.txt --no-prompt  Headless batch download
  smclipy download --file playlist.json --auto  Fetch missing songs
  smclipy tag        Retag library songs using MusicBrainz metadata
  smclipy tag --auto Full-auto retag: top match applied to each song
  smclipy tag --semi Auto-pick the top match, confirm each change
  smclipy tag --auto --all  Headless retag of every untagged song
  smclipy tag --auto --all --json  Same, with a JSON report on stdout
  smclipy tag --search TEXT  Only retag songs matching TEXT
  smclipy tag --specific album  Autocomplete an album, then retag it
  smclipy findsource  Find and record sources, picking candidates
  smclipy findsource --auto --all  Headless source search for all songs
  smclipy findsource --auto --all-providers  Rank across all providers
  smclipy findsource --skip-skipped  Skip songs skipped before
  smclipy findsource --search TEXT  Only search songs matching TEXT
  smclipy findsource --specific artists  Search one artist's songs
  smclipy findsource --file playlist.json  Fill missing playlist URLs
  smclipy modify     Manually edit the tags of one or more songs
  smclipy modify --search TEXT  Find songs by text, then edit them
  smclipy modify --specific artists  Autocomplete an artist, then edit
  smclipy modify --file playlist.json  Edit exported playlist entries
  smclipy dedupe     Merge differently styled artist/album names
  smclipy dedupe --albums  Only merge album spellings
  smclipy dedupe --dry-run  Preview a merge without writing
  smclipy restore    Roll back a song to an earlier tag backup
  smclipy restore --all  Show every recorded restore point
  smclipy restore --limit 5  Show only the newest 5 restore points
  smclipy crop       Crop pillarboxed cover images and re-embed them
  smclipy update     Rescan the library and sync the tracking database
  smclipy update --json  Machine-readable scan report on stdout
  smclipy stats      Totals, playtime, top albums and artists
  smclipy stats --top 5 --json  Machine-readable stats report
  smclipy playlist create Chill  Create an empty playlist
  smclipy playlist add Chill     Interactively add library songs to it
  smclipy playlist export Chill -f m3u  Export it as an .m3u file
  smclipy playlist export Chill -f m3u-link  Export it as URLs, one per line
  smclipy playlist import mix.m3u       Import a playlist from a file
  smclipy playlist show Chill --json    Print its songs as JSON
  smclipy -d         Print the directories smclipy uses
  smclipy -v         Print the version and exit
```

#### `smclipy playlist`

```text
usage: smclipy playlist [-h] action ...

Create, rename, delete, list, and show playlists of your tracked library
songs. Playlists live in the tracking database and reference songs by UUID, so
renames and relocations never break them. Songs can be added, removed, moved,
or sorted by metadata, and playlists can be exported to .m3u, .m3u-link (one
source URL per line with artist/title labels), or .json and imported back.

positional arguments:
  action
    create    Create a new playlist.
    rename    Rename a playlist.
    delete    Delete a playlist and its entries.
    list      List all playlists and their song counts.
    show      Show the songs in a playlist.
    add       Interactively add library songs to a playlist.
    remove    Interactively remove songs from a playlist.
    move      Interactively move a song to a new position.
    sort      Sort a playlist's songs by metadata.
    export    Export a playlist to .m3u, .m3u-link, or .json.
    import    Import a playlist from .m3u or .json.

options:
  -h, --help  show this help message and exit
```

#### `smclipy download`

```text
usage: smclipy download [-h] [-b FILE | -f FILE] [--no-prompt] [-a]

Batch download songs from YouTube or SoundCloud, then interactively tag each
one with a title, artist, and album before saving it. Use --file to fetch the
missing songs from a v2 playlist JSON and apply the playlist metadata
automatically.

options:
  -h, --help            show this help message and exit
  -b FILE, --batch FILE
                        Read URLs from FILE (one per line) instead of
                        prompting, so download can run without an interactive
                        terminal.
  -f FILE, --file FILE  Download the missing songs from a smclipy v2 playlist
                        JSON. Songs already in the library are skipped; each
                        entry's title, artists, album, date, genre, album
                        artist, and track number are applied without prompting
                        when present.
  --no-prompt           Accept the default title, album, and artists for each
                        download without asking (useful with --batch). A file
                        that already exists with the same artist-title name is
                        kept and skipped without prompting.
  -a, --auto            Synonym for --no-prompt: skip all prompts, including
                        the cover crop question. Useful with --file.
```

#### `smclipy update`

```text
usage: smclipy update [-h] [-j]

Reconcile the tracking database with the current state of the music folder:
register new songs, record renames, and mark files that vanished as missing.
Does not require an interactive terminal, so it can be run from scripts or
cron.

options:
  -h, --help  show this help message and exit
  -j, --json  Print a machine-readable JSON report to stdout.
```

#### `smclipy stats`

```text
usage: smclipy stats [-h] [-j] [--top N] [--no-scan]

Summarize the library: total songs, albums, and artists, total reproduction
time, and the albums and artists with the most songs. Rescans the library
first so the numbers are current; use --no-scan to report the database as-is.
Does not require an interactive terminal, so it can be run from scripts or
cron.

options:
  -h, --help  show this help message and exit
  -j, --json  Print a machine-readable JSON report to stdout.
  --top N     How many albums and artists to list (default: 10).
  --no-scan   Skip the library rescan and report the tracking database as-is.
```

#### `smclipy tag`

```text
usage: smclipy tag [-h] [-a | -s] [--all] [--force] [-j] [--skip-not-found]
                   [--max-age AGE] [--search TEXT | --specific FIELD]

List every song in the library, fetch matching metadata from MusicBrainz, and
interactively review and apply title, artist, album, release date, genre,
track number, album artist, and cover art changes.

options:
  -h, --help        show this help message and exit
  -a, --auto        Fully automatic: pick the top match for each selected song
                    and apply the configured tag_fields without any further
                    prompts (only the initial range selection).
  -s, --semi        Semi-automatic: pick the top match for each selected song
                    but confirm each change set (checkbox dialog) before
                    applying.
  --all             Tag every untagged song in the library instead of asking
                    for a range. Combine with --auto to run without an
                    interactive terminal. Use --force to include songs that
                    were already tagged.
  --force           Re-tag songs that were already tagged by a previous tag
                    run instead of skipping them.
  -j, --json        Print a machine-readable JSON report to stdout while human
                    messages go to stderr. Requires --auto --all (headless
                    retag). Exits with status 1 when any song crashed.
  --skip-not-found  Skip songs whose last MusicBrainz lookup found no match
                    instead of retrying them. Run without it to retry those
                    songs.
  --max-age AGE     Only process songs added to the library within the last
                    AGE (e.g. '30m', '24h', '7d'). Useful with --auto --all to
                    retag only recent downloads.
  --search TEXT     Only process songs whose title, album, or any artist
                    contains TEXT (case-insensitive). Combine with --auto
                    --all for a headless retag of the matches.
  --specific FIELD  Autocomplete the library's existing FIELD values (title,
                    album, or artists) and only process songs that match the
                    chosen value. Needs an interactive terminal.

Metadata is provided by MusicBrainz (core data is CC0, supplementary data and
docs are CC BY-NC-SA 3.0; https://musicbrainz.org ), and cover art is served
by the Cover Art Archive (coverartarchive.org).
```

#### `smclipy findsource`

```text
usage: smclipy findsource [-h] [-f FILE] [-a] [--all] [-j] [--force]
                          [--skip-skipped] [--all-providers]
                          [--search TEXT | --specific FIELD]

Search the providers listed in the config's 'source_preferences' (default:
YouTube, then SoundCloud) for each selected song (or playlist entry with
--file) and save the matching URL in the tracking database. Candidates are
ranked by matching duration, then artist similarity, then provider preference.
A candidate is only accepted when it names the song's artist (in the uploader
or an 'Artist - Title feat. Artist2' title) or repeats the song title;
unmatched results are skipped. The first provider with results wins unless
--all-providers is set; interactive mode shows the ranked list and --auto
saves the best plausible one. Each processed song is announced as `[i/n] ===
Title - Artist (mm:ss) ===`; skipped songs are followed by a `Skipped:
<reason>` line (already sourced, no candidates, provider failure, no
artist/title match, or no source selected). With --file, URLs are written back
to the playlist and to the database for already-downloaded entries.

options:
  -h, --help            show this help message and exit
  -f FILE, --file FILE  Search the entries of a smclipy v2 playlist JSON
                        instead of the library. Existing source_url values are
                        kept unless --force; found URLs are written back to
                        the playlist and, for entries already
                        downloaded/tracked, to the database too.
  -a, --auto            Fully automatic: save the best-ranked plausible search
                        result for each selected song without showing the
                        candidate picker.
  --all                 Search every song in the library (or every entry with
                        --file) instead of asking for a range. Combine with
                        --auto to run without an interactive terminal.
  -j, --json            Print a machine-readable JSON report to stdout while
                        human messages go to stderr. Requires --auto --all
                        (headless search). Exits with status 1 when any song
                        crashed.
  --force               Search songs that already have a recorded source URL
                        and replace it when a new source is found.
  --skip-skipped        Skip songs that were skipped, had no match, or were
                        rejected on a previous findsource run instead of
                        searching them again. Run without this flag to retry
                        them.
  --all-providers       With --auto, search every provider in
                        'source_preferences' instead of stopping at the first
                        one that returns results. Candidates are ranked by
                        matching duration, then artist similarity, then
                        provider order.
  --search TEXT         Only process songs whose title, album, or any artist
                        contains TEXT (case-insensitive). Combine with --auto
                        --all for a headless search of the matches.
  --specific FIELD      Autocomplete the library's existing FIELD values
                        (title, album, or artists) and only process songs that
                        match the chosen value. Needs an interactive terminal.
```

#### `smclipy modify`

```text
usage: smclipy modify [-h] [-f FILE] [-s TEXT | --specific FIELD] [--reset-mb]

Pick songs from your library (numbers or ranges) and edit their tags by hand:
title, artists, album, release date, genre, album artist, track number, cover
art, and source URL. One set of values is applied to every selected song, so a
range bulk-edits an album and a single number edits just that song. A blank
answer keeps the field, typing /clear empties it. Source URL is stored only in
the tracking database (not in the audio file) and is not part of restore
snapshots. Use --file to edit one audio file directly or the entries of a v2
playlist JSON (playlist source URLs stay in the file).

options:
  -h, --help            show this help message and exit
  -f FILE, --file FILE  Edit one audio file directly, or edit the entries of a
                        smclipy v2 playlist JSON. Playlist entries are changed
                        only in the JSON; source_url stays a JSON field there.
  -s TEXT, --search TEXT
                        Only consider songs whose title, album, or any artist
                        contains TEXT (case-insensitive) before asking which
                        ones to edit.
  --specific FIELD      Autocomplete the library's existing FIELD values
                        (title, album, or artists) and only consider songs
                        that match the chosen value.
  --reset-mb            Forget the MusicBrainz match of every modified song so
                        the `tag` command offers it again.
```

#### `smclipy dedupe`

```text
usage: smclipy dedupe [-h] [--artists] [--albums] [--dry-run]

Find artist and album names that differ only by case or spacing (for example
'Artist', 'artist', and 'a r t i s t'), pick the spelling to keep for each
group, then rewrite the tags, the artist-title filenames, matching cover
images, and the tracking database. Nothing is written until every group has
been reviewed.

options:
  -h, --help  show this help message and exit
  --artists   Only look for artist names (default: artists and albums).
  --albums    Only look for album names (default: artists and albums).
  --dry-run   Show the planned tag, filename, cover, and database changes
              without writing anything.
```

#### `smclipy restore`

```text
usage: smclipy restore [-h] [--all] [--limit N]

Browse a single list of restore points, newest first, and roll songs back to
an earlier backup. Each row names the song, the operation that followed the
backup, when it was taken, and the values it would restore. A backup of each
song's tags (and cover art) is recorded automatically before every `tag`,
`modify`, `crop`, and `dedupe` change, so a mistaken MusicBrainz match or
manual edit can be undone here. The restore itself is also backed up, so
undoing an undo is possible.

options:
  -h, --help  show this help message and exit
  --all       Show every restore point instead of only the newest 25.
  --limit N   Show at most N restore points (default: 25).
```

#### `smclipy crop`

```text
usage: smclipy crop [-h]

Scan saved cover images for pillarboxing, crop them to a 1:1 ratio
interactively, and re-embed each result into its matching audio file.

options:
  -h, --help  show this help message and exit
```

#### `smclipy playlist create`

```text
usage: smclipy playlist create [-h] [--description DESCRIPTION] name

Create a new, empty playlist.

positional arguments:
  name                  Name of the playlist.

options:
  -h, --help            show this help message and exit
  --description DESCRIPTION
                        Optional description of the playlist.
```

#### `smclipy playlist rename`

```text
usage: smclipy playlist rename [-h] name new_name

Rename an existing playlist.

positional arguments:
  name        Current name of the playlist.
  new_name    New name for the playlist.

options:
  -h, --help  show this help message and exit
```

#### `smclipy playlist delete`

```text
usage: smclipy playlist delete [-h] name

Delete a playlist and remove all of its entries.

positional arguments:
  name        Name of the playlist to delete.

options:
  -h, --help  show this help message and exit
```

#### `smclipy playlist list`

```text
usage: smclipy playlist list [-h] [-j]

List every playlist with its song count.

options:
  -h, --help  show this help message and exit
  -j, --json  Print the list as JSON.
```

#### `smclipy playlist show`

```text
usage: smclipy playlist show [-h] [-j] name

List the songs in a playlist in order.

positional arguments:
  name        Name of the playlist to show.

options:
  -h, --help  show this help message and exit
  -j, --json  Print the songs as JSON.
```

#### `smclipy playlist add`

```text
usage: smclipy playlist add [-h] name

Pick songs from your library to append to a playlist.

positional arguments:
  name        Name of the playlist to add to.

options:
  -h, --help  show this help message and exit
```

#### `smclipy playlist remove`

```text
usage: smclipy playlist remove [-h] name

Pick songs from a playlist to remove.

positional arguments:
  name        Name of the playlist to remove from.

options:
  -h, --help  show this help message and exit
```

#### `smclipy playlist move`

```text
usage: smclipy playlist move [-h] name

Pick a song in a playlist and give it a new position.

positional arguments:
  name        Name of the playlist to reorder.

options:
  -h, --help  show this help message and exit
```

#### `smclipy playlist sort`

```text
usage: smclipy playlist sort [-h] [--key {title,artists,album,path,added}]
                             [--reverse]
                             name

Reorder a playlist's songs by title, artists, album, path, or the time they
were added.

positional arguments:
  name                  Name of the playlist to sort.

options:
  -h, --help            show this help message and exit
  --key {title,artists,album,path,added}
                        What to sort by (default: title).
  --reverse             Sort in descending order.
```

#### `smclipy playlist export`

```text
usage: smclipy playlist export [-h] [-f {m3u,m3u-link,json}] [-o OUTPUT]
                               [--absolute] [--no-scan]
                               name

Export a playlist to an .m3u, .m3u-link, or .json file. Rescans the library
first so the paths are current; use --no-scan to export the tracking database
as-is. Paths are written relative to the exported file unless --absolute is
given. The m3u-link format writes one source URL per line instead of local paths,
each preceded by an #EXTINF line with the song's artists and title.

positional arguments:
  name                  Name of the playlist to export.

options:
  -h, --help            show this help message and exit
  -f {m3u,m3u-link,json}, --format {m3u,m3u-link,json}
                        Export format (default: inferred from --output, else
                        m3u).
  -o OUTPUT, --output OUTPUT
                        Where to write the file (default: <name>.<ext> in the
                        current dir).
  --absolute            Write absolute paths.
  --no-scan             Skip the library rescan and export the tracking
                        database as-is.
```

#### `smclipy playlist import`

```text
usage: smclipy playlist import [-h] [--name NAME] [--replace] file

Import an .m3u/.m3u8 or smclipy .json playlist. Entries are matched to your
tracked library songs by path (relative to the file, your music folder, or
absolute) or UUID; unmatched entries are skipped and reported. For .json
imports, the missing songs are counted and smclipy suggests `smclipy download
--file FILE`, which downloads them from their recorded source_url and applies
the playlist metadata automatically.

positional arguments:
  file         Path to the .m3u/.m3u8/.json file.

options:
  -h, --help   show this help message and exit
  --name NAME  Playlist name (default: the file's name or the JSON name).
  --replace    Overwrite the playlist if it already exists.
```

The JSON export format is versioned by its `smclipy_playlist` field. Version 2
includes each song's full metadata, with covers deliberately excluded:

```json
{
  "smclipy_playlist": 2,
  "name": "Chill",
  "songs": [
    {
      "uuid": "…",
      "title": "Song",
      "artists": "Artist",
      "artists_list": ["Artist"],
      "album": "Album",
      "album_artist": "Artist",
      "genres": ["pop", "rock"],
      "date": "1980-01-01",
      "track_number": "3",
      "source_url": "https://www.youtube.com/watch?v=…",
      "path": "Artist-Song.mp3"
    }
  ]
}
```

Import only uses `uuid`/`path` to resolve entries, so version 1 files remain
fully importable. `download --file` requires version 2 and prefers
`artists_list` when present so artist names containing commas survive the
round-trip; it only downloads missing songs and does not modify the playlist, so
re-run the import afterwards to attach them.
