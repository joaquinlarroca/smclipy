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

## Configuration

The script relies on a configuration file to know where to organize your files.

Create a config file (e.g., `config.json`, see `config.example.json`) with the following structure:

```json
{
  "name": "smclipy",
  "path_to_music_folder": "./Music",
  "description_max_lines": 5
}
```

### Config Breakdown

- `name`: The master folder name (`smclipy`) that will be created inside your music folder to hold all the organized artists.
- `path_to_music_folder`: The base directory where your music library lives (default is `./Music`).
- `description_max_lines`: How many lines of the video description to show while tagging (default is 5).

## Usage

The project is a Python package. Run it with `uv run smclipy <command>` (or `python -m smclipy <command>` inside the virtualenv).

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
