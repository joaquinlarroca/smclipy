# smclipy

**simple music cli py** is a Python script designed to streamline downloading, tagging, and organizing music from **YouTube and SoundCloud**. It allows you to batch-download audio, manually set metadata (including multiple artists, album, and cover art).

## Features

- **Batch Downloading:** Queue up as many YouTube or SoundCloud URLs as you want before processing. Recognizes `youtube.com` watch/shorts/live/embed links, `youtu.be` links, and `soundcloud.com` tracks. Unrecognizable pastes are warned about and duplicate URLs are collapsed.
- **Interactive Tagging:** For each track you set the Title, Album, and Artists (with autocompletion). The title defaults from the video's title, and the artists from the channel/uploader — corrected against your known authors (ignoring case and spaces).
- **MusicBrainz Re-tagging (`tag`):** List every song already in your library and look it up on [MusicBrainz](https://musicbrainz.org/) (free, no API key) to fix title, artists, album, release date, track number, album artist, and cover art — reviewing each candidate and the exact diff before applying.
- **Playlists (`playlist`):** Build playlists of your library songs (create/rename/delete/list/show), add/remove/move songs interactively, sort by metadata, and export them to `.m3u` or `.json` — or import `.m3u`/`.json` playlists by resolving the entries against your library (UUID first, then each path relative to the file, your music folder, or a unique matching filename). Playlists are stored in `smclipy.db` and reference songs by their tracking UUID, so renaming or relocating a song never breaks its playlists.
- **Video Info Preview:** Before tagging, the video's title, artist/s, and the first `description_max_lines` lines of its description are shown for context.
- **Cover Art Cropping:** Crop cover art to a perfect 1:1 square ratio during tagging, with a terminal preview of the art.
- **Cover Rescan (`crop`):** Re-extracts covers from your existing audio files, detects pillarboxed art, lets you crop it to 1:1, and re-embeds it into the matching files. Images you decline to crop are remembered so you're not asked twice.
- **Interrupt & Retry Friendly:** Queues are saved as you paste them — pressing Ctrl+C saves what you've entered, queues survive interruptions, and `download` detects an unfinished queue and offers to resume. Videos that fail (e.g., HTTP errors) are kept pending for a later retry up to `max_download_attempts` times, after which they're marked permanently failed and reported; already-processed ones are never re-downloaded.
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

On the first run of `download`, `crop`, `tag`, `update`, or any `playlist` action, a default config file is created at `~/.config/smclipy/config.json` and the program exits so you can edit it to your liking. The `-d`/`--directories` and `-v`/`--version` options work immediately using defaults, so you can preview where everything will live before a config exists. To store the config somewhere else, set the `SMCLIPY_CONFIG` environment variable to your preferred path (respects `$XDG_CONFIG_HOME`). If a key is missing from your existing config, it is re-added with its default value the next time you run smclipy.

```json
{
  "name": "smclipy",
  "path_to_music_folder": "./Music",
  "description_max_lines": 5,
  "write_album_if_same_as_title": false,
  "audio_format": "mp3",
  "max_download_attempts": 3,
  "tag_fields": ["title", "artists", "album", "date", "album_artist", "track_number", "cover"]
}
```

### Config Breakdown

- `name`: The master folder name (`smclipy`) that will be created inside your music folder to hold all the organized artists.
- `path_to_music_folder`: The base directory where your music library lives (default is `./Music`, relative to wherever you run the command from).
- `description_max_lines`: How many lines of the video description to show while tagging (default is 5).
- `write_album_if_same_as_title`: When `true`, the `tag` command writes the album even if it equals the song title. When `false` (default), an album that matches the title is left empty.
- `audio_format`: The format new downloads are saved in. Valid values are `mp3` (default), `m4a`, `flac`, `opus`, and `ogg` (Ogg Vorbis). Existing files in other supported formats are still recognized and re-tagged, so changing this never orphans your library.
- `max_download_attempts`: How many times `download` tries a URL before giving up on it permanently. After this many failed attempts the URL is marked failed, reported at the start of the next run, and not re-queued again (default is 3).
- `tag_fields`: Which metadata fields the `tag` command may apply to your library songs. Valid values are `title`, `artists`, `album`, `date`, `album_artist`, `track_number`, and `cover`.

## Usage

Run it from your music library's parent directory:

```bash
smclipy <command>
```

You can also invoke it as a module: `python -m smclipy <command>`. Note that `download`, `crop`, `tag`, and the interactive playlist actions (`add`, `remove`, `move`) require an interactive terminal, with two headless exceptions: `download --batch FILE --no-prompt` and `tag --auto --all`. `update`, the JSON modes, and the remaining playlist actions never require a terminal, so they can be scripted or scheduled.

### Commands

- **`smclipy download`** — Batch download and tag songs from YouTube or SoundCloud. Pass `--batch FILE` to read URLs from a file and `--no-prompt` to accept the default title, album, and artists, which together make it safe for cron/scripts.
- **`smclipy crop`** — Re-extract covers from your audio files, find pillarboxed art, crop to 1:1 interactively, and re-embed into the matching files.
- **`smclipy tag`** — Retag songs already in your library using MusicBrainz metadata. Songs you already tagged (recorded in the tracking database) are automatically skipped with a warning, so re-running over `all`, a range, or a single number only processes what's new. Songs imported from the older text-file tracking format are *not* treated as already tagged, so they are offered on the first run.
  - `smclipy tag --auto` (`-a`) — **Full-auto:** after you pick the song range, the top MusicBrainz match is applied to each selected song automatically with no further prompts, writing every enabled `tag_fields` value (including the title). Shows a per-song progress line instead of the match picker and confirm dialog.
  - `smclipy tag --semi` (`-s`) — **Semi-auto:** the top match is picked automatically per song, but you still review and confirm each change set (checkbox dialog) before it is applied.
  - `smclipy tag --all` — Skip the range prompt and select every untagged song. Combine with `--auto` (`smclipy tag --auto --all`) to retag the whole library without an interactive terminal.
- **`smclipy playlist`** — Create, rename, delete, list, show, fill, reorder, sort, and import/export playlists of your library songs. Every action is documented below in the CLI reference. `list` and `show` accept `--json` (`-j`) for machine-readable output.
- **`smclipy update`** — Rescan the music folder and sync the tracking database: new files get registered, renames are recorded, and vanished files are marked missing. Prints a summary of what changed, or a JSON report with `--json`; safe to run anytime, including from scripts or cron.
- **`smclipy -d` / `--directories`** — Print the directories and files smclipy uses (config, music, library, temp, covers, database) and exit.
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
   - Enter the **Artist/s** (prefilled from the channel name; if it matches an author already known — ignoring case and spaces — your existing spelling is kept). To tag multiple artists, separate them using a backslash `\` or a comma (e.g., `Artist 1\Artist 2\Artist 3` or `Artist 1, Artist 2`).
4. **Save / Skip:** Once tagged, the file is moved to your music folder as `artist-title.<audio_format>` (e.g., `Rick Astley-Never Gonna Give You Up.mp3`). If a file with that name already exists, you're asked whether to overwrite it or keep your existing copy; keeping it is remembered so you aren't asked about that track again. Successfully processed videos are recorded so they're skipped on resume; failed videos stay pending and are reported at the end so you can retry them later — up to `max_download_attempts`, after which a dead URL is reported as permanently failed instead of being re-queued.

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
    playlist         Create and manage library playlists, and import/export
                     them.
    update           Rescan the library and sync the tracking database.

options:
  -h, --help         show this help message and exit
  -v, --version      Print the version and exit.
  -d, --directories  Print the directories smclipy uses and exit.

examples:
  smclipy download   Batch download songs and tag them interactively
  smclipy download --batch urls.txt --no-prompt  Headless batch download
  smclipy tag        Retag library songs using MusicBrainz metadata
  smclipy tag --auto Full-auto retag: top match applied to each song
  smclipy tag --semi Auto-pick the top match, confirm each change
  smclipy tag --auto --all  Headless retag of every untagged song
  smclipy crop       Crop pillarboxed cover images and re-embed them
  smclipy update     Rescan the library and sync the tracking database
  smclipy update --json  Machine-readable scan report on stdout
  smclipy playlist create Chill  Create an empty playlist
  smclipy playlist add Chill     Interactively add library songs to it
  smclipy playlist export Chill -f m3u  Export it as an .m3u file
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
or sorted by metadata, and playlists can be exported to .m3u or .json and
imported back.

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
    export    Export a playlist to .m3u or .json.
    import    Import a playlist from .m3u or .json.

options:
  -h, --help  show this help message and exit
```

#### `smclipy download`

```text
usage: smclipy download [-h] [-b FILE] [--no-prompt]

Batch download songs from YouTube or SoundCloud, then interactively tag each
one with a title, artist, and album before saving it.

options:
  -h, --help            show this help message and exit
  -b FILE, --batch FILE
                        Read URLs from FILE (one per line) instead of
                        prompting, so download can run without an interactive
                        terminal.
  --no-prompt           Accept the default title, album, and artists for each
                        download without asking (useful with --batch).
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

#### `smclipy tag`

```text
usage: smclipy tag [-h] [-a | -s] [--all]

List every song in the library, fetch matching metadata from MusicBrainz, and
interactively review and apply title, artist, album, release date, track
number, album artist, and cover art changes.

options:
  -h, --help  show this help message and exit
  -a, --auto  Fully automatic: pick the top match for each selected song and
              apply the configured tag_fields without any further prompts
              (only the initial range selection).
  -s, --semi  Semi-automatic: pick the top match for each selected song but
              confirm each change set (checkbox dialog) before applying.
  --all       Tag every untagged song in the library instead of asking for a
              range. Combine with --auto to run without an interactive
              terminal.

Metadata is provided by MusicBrainz (core data is CC0, supplementary data and
docs are CC BY-NC-SA 3.0; https://musicbrainz.org ), and cover art is served
by the Cover Art Archive (coverartarchive.org).
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
usage: smclipy playlist export [-h] [-f {m3u,json}] [-o OUTPUT] [--absolute]
                               name

Export a playlist to an .m3u or .json file. Paths are written relative to the
exported file unless --absolute is given.

positional arguments:
  name                  Name of the playlist to export.

options:
  -h, --help            show this help message and exit
  -f {m3u,json}, --format {m3u,json}
                        Export format (default: inferred from --output, else
                        m3u).
  -o OUTPUT, --output OUTPUT
                        Where to write the file (default: <name>.<ext> in the
                        current dir).
  --absolute            Write absolute paths.
```

#### `smclipy playlist import`

```text
usage: smclipy playlist import [-h] [--name NAME] [--replace] file

Import an .m3u/.m3u8 or smclipy .json playlist. Entries are matched to your
tracked library songs by path (relative to the file, your music folder, or
absolute) or UUID; unmatched entries are skipped and reported.

positional arguments:
  file         Path to the .m3u/.m3u8/.json file.

options:
  -h, --help   show this help message and exit
  --name NAME  Playlist name (default: the file's name or the JSON name).
  --replace    Overwrite the playlist if it already exists.
```
