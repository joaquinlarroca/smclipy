# Project roadmap

The development of `smclipy` is divided into three key phases, focusing on code health, expanding audio capabilities, and delivering a better terminal experience.

## Code Architecture & Foundations

- **Asynchronous Batching:** Implement concurrent downloading and processing using `asyncio` or multi-threading to drastically speed up large queues.

## Audio & Metadata

- **Multi-Format Support:** Support additional formats beyond the current **MP3**, **M4A**, **FLAC**, **Opus**, and **Ogg Vorbis** (e.g. WAV, ALAC).
- **Enhanced Multi-Value Tagging:** Multi-artist tags are now written natively per format by the format adapters; extend the same treatment to multi-genre tags and support more tags (e.g., ensuring proper native delimiters so they read flawlessly in advanced offline media players).

## Directory Organization & UI/UX

- **Dynamic Library Structure:** Upgrade the configuration file to support custom path schemas (e.g., allowing users to choose between a flat structure or an organized `Music/Artist/Album/Track.ext` hierarchy).
- **TUI (Text User Interface) Upgrade:** Transition from standard CLI prompts to a rich, interactive terminal dashboard using libraries like `Textual` or `Rich`, featuring live download progress bars and enhanced autocompletion menus.
