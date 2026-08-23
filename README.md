# smclipy

**simple music cli py** is a Python script designed to streamline downloading, tagging, and organizing music from YouTube. It allows you to batch-download audio, manually set metadata (including multiple artists and cover art).

## Features

- **Batch Downloading:** Queue up as many YouTube URLs as you want before processing.
- **Interactive Tagging:** Prompts you for the track Title and Artists (has autocompletion). Both come prefilled: the title from the video's title, and the artists from the channel name, corrected against your known authors.
- **Cover Art Cropping:** Easily crop your cover art to a perfect 1:1 square ratio during the tagging process.
- **Clean Cleanup:** Uses a `.temp` directory during the download and tagging process to keep your main library clean.

## Requirements

- **Python 3.12+** (handled automatically if you use `uv`).
- **FFmpeg** — required by yt-dlp to extract the audio to MP3 and embed the cover art.
- **[Deno](https://docs.deno.com/runtime/getting_started/installation/)** — required by yt-dlp (2025.11+) to solve YouTube's JavaScript challenges during extraction. Without it, downloads may fail or have limited format availability.

## Installation

Install it as a standalone CLI tool:

```bash
uv tool install smclipy
```

or, if you prefer pipx:

```bash
pipx install smclipy
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

### Commands

- **`smclipy download`** — Batch download and tag songs from YouTube.
- **`smclipy crop`** — Scan saved covers for pillarboxed art, crop to 1:1, and re-embed into the matching MP3s.

### Download flow

1. **Queue URLs:** Paste your YouTube URLs one by one (enter an empty line to finish).
2. **Tagging Flow (Per Track):**
   - The script will download the current track to `.temp`.
   - If a cover image is found, you will be prompted to crop it to a 1:1 ratio.
   - Enter the **Title** of the track (prefilled with the video's title).
   - Enter the **Artist/s** (prefilled from the channel name; if it matches an author already in `authors.txt` — ignoring case and spaces — your existing spelling is kept). To tag multiple artists, separate them using a backslash `\` (e.g., `Artist 1\Artist 2\Artist 3`).

3. **Completion:** Once tagged, the final music file is moved to your designated music folder, using the following filename artist-title.mp3 (e.g., `Rick Astley-Never Gonna Give You Up.mp3`).

## Folder Structure Example

After running the script and tagging a few songs, your output directory will look something like this:

```text
📁 ./.temp                 <-- (Used temporarily during processing)
📁 ./Music
├──🎵 artist1-title1.mp3
├──🎵 artist1-title2.mp3
├──🎵 artist2-title1.mp3
├──🎵 artist3-title1.mp3
└── ...
```

## Development

Clone the repo and run it from the project root with uv (set `SMCLIPY_CONFIG` to use your existing `config.json`):

```bash
uv run smclipy <command>
```
