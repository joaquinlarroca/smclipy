# smclipy

**simple music cli py** is a Python script designed to streamline downloading, tagging, and organizing music from **YouTube and SoundCloud**. It allows you to batch-download audio, manually set metadata (including multiple artists, album, and cover art).

## Features

- **Batch Downloading:** Queue up as many YouTube or SoundCloud URLs as you want before processing. Recognizes `youtube.com` watch/shorts/live/embed links, `youtu.be` links, and `soundcloud.com` tracks. Unrecognizable pastes are warned about and duplicate URLs are collapsed.
- **Interactive Tagging:** For each track you set the Title, Album, and Artists (with autocompletion). The title defaults from the video's title, and the artists from the channel/uploader — corrected against your known authors (ignoring case and spaces).
- **MusicBrainz Re-tagging (`tag`):** List every song already in your library and look it up on [MusicBrainz](https://musicbrainz.org/) (free, no API key) to fix title, artists, album, release date, track number, album artist, and cover art — reviewing each candidate and the exact diff before applying.
- **Video Info Preview:** Before tagging, the video's title, artist/s, and the first `description_max_lines` lines of its description are shown for context.
- **Cover Art Cropping:** Crop cover art to a perfect 1:1 square ratio during tagging, with a terminal preview of the art.
- **Cover Rescan (`crop`):** Re-extracts covers from your existing audio files, detects pillarboxed art, lets you crop it to 1:1, and re-embeds it into the matching files. Images you decline to crop are remembered so you're not asked twice.
- **Interrupt & Retry Friendly:** Queues are saved as you paste them — pressing Ctrl+C saves what you've entered, queues survive interruptions, and `download` detects an unfinished queue and offers to resume. Videos that fail (e.g., HTTP errors) are kept pending for a later retry, and already-processed ones are never re-downloaded.
- **Overwrite Protection:** Asks before overwriting an existing audio file with the same `artist-title` name, with a per-track `[i/N]` progress line while processing. A choice to keep your existing copy is remembered, so that track isn't re-asked on a later resume.
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

On the first run of `download`, `crop`, `tag`, or `update`, a default config file is created at `~/.config/smclipy/config.json` and the program exits so you can edit it to your liking. The `-d`/`--directories` and `-v`/`--version` options work immediately using defaults, so you can preview where everything will live before a config exists. To store the config somewhere else, set the `SMCLIPY_CONFIG` environment variable to your preferred path (respects `$XDG_CONFIG_HOME`). If a key is missing from your existing config, it is re-added with its default value the next time you run smclipy.

```json
{
  "name": "smclipy",
  "path_to_music_folder": "./Music",
  "description_max_lines": 5,
  "write_album_if_same_as_title": false,
  "audio_format": "mp3",
  "tag_fields": ["title", "artists", "album", "date", "album_artist", "track_number", "cover"]
}
```

### Config Breakdown

- `name`: The master folder name (`smclipy`) that will be created inside your music folder to hold all the organized artists.
- `path_to_music_folder`: The base directory where your music library lives (default is `./Music`, relative to wherever you run the command from).
- `description_max_lines`: How many lines of the video description to show while tagging (default is 5).
- `write_album_if_same_as_title`: When `true`, the `tag` command writes the album even if it equals the song title. When `false` (default), an album that matches the title is left empty.
- `audio_format`: The format new downloads are saved in. Valid values are `mp3` (default), `m4a`, `flac`, `opus`, and `ogg` (Ogg Vorbis). Existing files in other supported formats are still recognized and re-tagged, so changing this never orphans your library.
- `tag_fields`: Which metadata fields the `tag` command may apply to your library songs. Valid values are `title`, `artists`, `album`, `date`, `album_artist`, `track_number`, and `cover`.

## Usage

Run it from your music library's parent directory:

```bash
smclipy <command>
```

You can also invoke it as a module: `python -m smclipy <command>`. Note that `download`, `crop`, and `tag` require an interactive terminal; `update` does not, so it can be scripted or scheduled.

### Commands

- **`smclipy download`** — Batch download and tag songs from YouTube or SoundCloud.
- **`smclipy crop`** — Re-extract covers from your audio files, find pillarboxed art, crop to 1:1 interactively, and re-embed into the matching files.
- **`smclipy tag`** — Retag songs already in your library using MusicBrainz metadata (attribution: MusicBrainz data is licensed CC BY-NC-SA 3.0). Songs you already tagged (recorded in the tracking database) are automatically skipped with a warning, so re-running over `all`, a range, or a single number only processes what's new. Songs imported from the older text-file tracking format are *not* treated as already tagged, so they are offered on the first run.
  - `smclipy tag --auto` (`-a`) — **Full-auto:** after you pick the song range, the top MusicBrainz match is applied to each selected song automatically with no further prompts, writing every enabled `tag_fields` value (including the title). Shows a per-song progress line instead of the match picker and confirm dialog.
  - `smclipy tag --semi` (`-s`) — **Semi-auto:** the top match is picked automatically per song, but you still review and confirm each change set (checkbox dialog) before it is applied.
- **`smclipy update`** — Rescan the music folder and sync the tracking database: new files get registered, renames are recorded, and vanished files are marked missing. Prints a summary of what changed; safe to run anytime, including from scripts or cron.
- **`smclipy -d` / `--directories`** — Print the directories and files smclipy uses (config, music, library, temp, covers, database) and exit.
- **`smclipy -v` / `--version`** — Print the version and exit.

### Download flow

1. **Queue URLs:** Paste your URLs one by one (enter an empty line to finish).
2. **Resume or restart:** If a previous run left an unfinished queue, you're asked whether to resume from where it stopped or start a fresh queue.
3. **Tagging Flow (Per Track):**
   - The track is downloaded to `Music/smclipy/.temp`.
   - If a cover image is found, it's previewed; if it isn't already 1:1 you're prompted to crop it.
   - A preview shows the video's title, artist/s, and its description (truncated to `description_max_lines` lines).
   - Enter the **Title** of the track (prefilled with the video's title).
   - Enter the **Album** (prefilled when the video provides one).
   - Enter the **Artist/s** (prefilled from the channel name; if it matches an author already known — ignoring case and spaces — your existing spelling is kept). To tag multiple artists, separate them using a backslash `\` or a comma (e.g., `Artist 1\Artist 2\Artist 3` or `Artist 1, Artist 2`).
4. **Save / Skip:** Once tagged, the file is moved to your music folder as `artist-title.<audio_format>` (e.g., `Rick Astley-Never Gonna Give You Up.mp3`). If a file with that name already exists, you're asked whether to overwrite it or keep your existing copy; keeping it is remembered so you aren't asked about that track again. Successfully processed videos are recorded so they're skipped on resume; failed videos stay pending and are reported at the end so you can retry them later.

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
    └── 📄 smclipy.db                  <-- Tracking database (songs, history, queue, authors)
```

> **Tracker:** every song is identified by a UUID stored in its tags, so renaming a file keeps it tracked (the rename is recorded). The database keeps the download URL, MusicBrainz match status (tagged / skipped / not found), cover status (not pillarbox / false positive / cropped), and an append-only event history per song, all inspectable directly in `smclipy.db`.
>
> **Re-process a song:** to re-tag a song that was already tagged, or be offered a cover you previously declined, reset its MusicBrainz / cover status in `smclipy.db` (or delete the row to forget it entirely). A song that is missing from the music folder is marked missing but keeps its history; put the file back and it re-appears under the same tracking ID.
>
> **Upgrading:** the old text-file tracking (`authors.txt`, `tagged_files.txt`, the queue files, and `cropping_tool_false_positives.txt`) is imported once into `smclipy.db` on the first run of a version that has the database, after which those files are ignored. The database schema is versioned, so future releases upgrade it in place without losing history. Legacy rows are imported as untagged, so the first `tag` run offers every existing library song for MusicBrainz metadata.

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

See [roadmap.md](roadmap.md) for planned work: async/concurrent downloads, an interactive library edit/re-tag mode, a configurable library folder structure, and a full TUI upgrade.