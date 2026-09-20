# Project roadmap

The development of `smclipy` is organized into the following areas, focusing on code health, expanding audio capabilities, and delivering a better terminal experience.

## Code Architecture & Foundations

- **Asynchronous Batching:** Implement concurrent downloading and processing using `asyncio` or multi-threading to drastically speed up large queues.

## Audio & Metadata

- **Artist Pictures:** Fetch and embed artist/band pictures in addition to album covers.
- **Artist & Album Name Dedup:** Detect artist and album names that differ only by case, spacing, or formatting (e.g. `Artist`, `artist`, `a r t i s t`) and present them in a checkbox list so the user selects one canonical spelling to rename the others to; a `-a/--artist` flag targets a single artist.
- **Album Collision Handling:** When the same album name appears under different artists, ask whether it's the same album; if not, rename each set to `Album - Artist1` / `Album - Artist2`. During tagging, when a matched online album has the same or a similar name, prompt to merge, disambiguate, or do nothing — showing enough evidence to judge whether it's a mismatch.

## Downloads & Sources

- **Playlist & Album Support:** Accept an entire playlist, prompting for a range or just the current track; albums get an "all songs" option (where finite — handle infinite playlists that never end).
- **SpotDL Integration:** Add support for downloading via Spotify (spotDL).

## Directory Organization & UI/UX

- **Dynamic Library Structure:** Upgrade the configuration file to support custom path schemas, e.g. a flat structure or an organized hierarchy using templates like `/(main_artist)/(album)/(track_number).(format)` or `/(main_artist) - (title).(format)`.
- **TUI (Text User Interface) Upgrade:** Transition from standard CLI prompts to a rich, interactive terminal dashboard using libraries like `Textual` or `Rich`, featuring live download progress bars and enhanced autocompletion menus.
- **`-f/--file` Flag:** Let `crop`, `modify`, and similar commands accept an explicit file path instead of only library songs.
- **Ordered Ranges:** Add a flag so `tag`, `modify`, and similar commands accept a range against a differently ordered song list (e.g. sorted by artist, title, or album).
- **Remembered Checkbox Opt-Outs:** If a field is unchecked in the tag dialog, keep that field opted out by default on future prompts.