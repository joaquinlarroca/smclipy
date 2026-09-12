# smclipy

**simple music cli py** is a Python script designed to streamline downloading, tagging, and organizing music from **YouTube and SoundCloud**. It allows you to batch-download audio, manually set metadata (including multiple artists, album, and cover art).

## Features

- **Batch Downloading:** Queue up as many YouTube or SoundCloud URLs as you want before processing. Recognizes `youtube.com` watch/shorts/live/embed links, `youtu.be` links, and `soundcloud.com` tracks. Unrecognizable pastes are warned about and duplicate URLs are collapsed.
- **Interactive Tagging:** For each track you set the Title, Album, and Artists (with autocompletion). The title defaults from the video's title, and the artists from the channel/uploader — corrected against your known authors (ignoring case and spaces).
- **Video Info Preview:** Before tagging, the video's title, artist/s, and the first `description_max_lines` lines of its description are shown for context.
- **Cover Art Cropping:** Crop cover art to a perfect 1:1 square ratio during tagging, with a terminal preview of the art.
- **Cover Rescan (`crop`):** Re-extracts covers from your existing MP3s, detects pillarboxed art, lets you crop it to 1:1, and re-embeds it into the matching files. Images you decline to crop are remembered so you're not asked twice.
- **Interrupt & Retry Friendly:** Queues are saved as you paste them — pressing Ctrl+C saves what you've entered, queues survive interruptions, and `download` detects an unfinished queue and offers to resume. Videos that fail (e.g., HTTP errors) are kept pending for a later retry, and already-processed ones are never re-downloaded.
- **Overwrite Protection:** Asks before overwriting an existing MP3 with the same `artist-title` name, with a per-track `[i/N]` progress line while processing. A choice to keep your existing copy is remembered, so that track isn't re-asked on a later resume.
- **Clean Cleanup:** Uses a `.temp` directory (inside your music folder's `smclipy` subfolder) during the download and tagging process to keep your main library clean.

## Requirements

- **Python 3.12+** (handled automatically if you use `uv`).
- **FFmpeg** — required by yt-dlp to extract the audio to MP3 and embed the cover art.
- **[Deno](https://docs.deno.com/runtime/getting_started/installation/)** — required by yt-dlp (2025.11+) to solve YouTube's JavaScript challenges during extraction. Without it, downloads may fail or have limited format availability.

## Installation

```bash
pip install smclipy
```

## Configuration

smclipy relies on a configuration file to know where to organize your files.

On first run, a default config file is created at `~/.config/smclipy/config.json` and the program exits so you can edit it to your liking. To store it somewhere else, set the `SMCLIPY_CONFIG` environment variable to your preferred path (respects `$XDG_CONFIG_HOME`).

```json
{
  "name": "smclipy",
  "path_to_music_folder": "./Music",
  "description_max_lines": 5
}
```

### Config Breakdown

- `name`: The master folder name (`smclipy`) that will be created inside your music folder to hold all the organized artists.
- `path_to_music_folder`: The base directory where your music library lives (default is `./Music`, relative to wherever you run the command from).
- `description_max_lines`: How many lines of the video description to show while tagging (default is 5).

## Usage

Run it from your music library's parent directory:

```bash
smclipy <command>
```

You can also invoke it as a module: `python -m smclipy <command>`. Note that `download` and `crop` require an interactive terminal.

### Commands

- **`smclipy download`** — Batch download and tag songs from YouTube or SoundCloud.
- **`smclipy crop`** — Re-extract covers from your MP3s, find pillarboxed art, crop to 1:1 interactively, and re-embed into the matching MP3s.
- **`smclipy -d` / `--directories`** — Print the directories and files smclipy uses (config, music, library, temp, covers, authors, songs info) and exit.
- **`smclipy -v` / `--version`** — Print the version and exit.

### Download flow

1. **Queue URLs:** Paste your URLs one by one (enter an empty line to finish).
2. **Resume or restart:** If a previous run left an unfinished queue, you're asked whether to resume from where it stopped or start a fresh queue.
3. **Tagging Flow (Per Track):**
   - The track is downloaded to `Music/smclipy/.temp`.
   - A preview shows the video's title, artist/s, and its description (truncated to `description_max_lines` lines).
   - If a cover image is found, it's previewed; if it isn't already 1:1 you're prompted to crop it.
   - Enter the **Title** of the track (prefilled with the video's title).
   - Enter the **Album** (prefilled when the video provides one).
   - Enter the **Artist/s** (prefilled from the channel name; if it matches an author already in `authors.txt` — ignoring case and spaces — your existing spelling is kept). To tag multiple artists, separate them using a backslash `\` (e.g., `Artist 1\Artist 2\Artist 3`).
4. **Save / Skip:** Once tagged, the file is moved to your music folder as `artist-title.mp3` (e.g., `Rick Astley-Never Gonna Give You Up.mp3`). If a file with that name already exists, you're asked whether to overwrite it or keep your existing copy; keeping it is remembered so you aren't asked about that track again. Successfully processed videos are recorded so they're skipped on resume; failed videos stay pending and are reported at the end so you can retry them later.

## Folder Structure Example

After running the script and tagging a few songs, your output directory will look something like this:

```text
📁 ./Music
├── 🎵 artist1-title1.mp3
├── 🎵 artist1-title2.mp3
├── 🎵 artist2-title1.mp3
├── 🎵 artist3-title1.mp3
└── 📁 smclipy
    ├── 📁 .temp                       <-- Pending/processed queues + temp files during processing
    ├── 📁 covers                      <-- Cover art extracted from your MP3s
    ├── 📄 authors.txt                 <-- Known authors, used for autocompletion
    ├── 📄 songs_info.txt              <-- Log of tagged songs
    └── 📄 cropping_tool_false_positives.txt  <-- Covers you declined to crop
```

## Development

Clone the repo and run it from the project root with uv (set `SMCLIPY_CONFIG` to use your existing `config.json`):

```bash
uv run smclipy <command>
```

Check your changes with the same tooling used in CI:

```bash
uv run pytest    # tests
uv run ruff check  # lint
uv run mypy     # type checks
```

## Roadmap

See [roadmap.md](roadmap.md) for planned work: async/concurrent downloads, support for more formats (FLAC, M4A, Opus), an interactive library edit/re-tag mode, a configurable library folder structure, and a full TUI upgrade.