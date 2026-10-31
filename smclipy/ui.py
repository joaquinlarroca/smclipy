import sys
from html import escape
from typing import TYPE_CHECKING, Any

from prompt_toolkit import prompt
from prompt_toolkit.application import Application, get_app
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.filters import has_focus
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.key_binding.bindings.focus import focus_next, focus_previous
from prompt_toolkit.key_binding.defaults import load_key_bindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit
from prompt_toolkit.shortcuts import choice, radiolist_dialog
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Button, CheckboxList, Dialog, Label

from smclipy.config import settings
from smclipy.downloader import get_author, matches_duration

if TYPE_CHECKING:
    from smclipy.downloader import SourceCandidate
    from smclipy.musicbrainz import MusicBrainzMatch


def msg(*args: Any) -> None:
    """Print a status/progress message to stderr, keeping stdout for data."""
    print(*args, file=sys.stderr)


def show_video_info(info_dictionary: dict[str, Any]) -> None:
    print("---------- TITLE ----------")
    print(info_dictionary.get("title", "No title"))
    print("---------- ARTIST/S ----------")
    authors: list[str] = get_author(info_dictionary)
    if authors:
        print(", ".join(authors))
    else:
        print("No artists")
    print("---------- DESCRIPTION ----------")
    description = str(info_dictionary.get("description", "No description"))
    description_lines: list[str] = description.splitlines()[
        : settings().description_max_lines
    ]
    print("\n".join(description_lines))
    print("")


def _choice_yes_no(
    message: str, *, default: bool, yes: str = "Yes", no: str = "No"
) -> bool:
    return (
        choice(
            message=message,
            options=[(yes, yes), (no, no)],
            default=yes if default else no,
        )
        == yes
    )


def prompt_crop(default: bool = False) -> bool:
    return _choice_yes_no("Crop cover to 1:1?", default=default)


def prompt_confirm(message: str, default: bool = False) -> bool:
    """Ask a plain Yes/No question, defaulting to ``default``."""
    return _choice_yes_no(message, default=default)


def prompt_resume() -> bool:
    return _choice_yes_no(
        "Found an interrupted download. Resume from where it stopped?", default=True
    )


def prompt_overwrite() -> bool:
    return _choice_yes_no(
        "File already exists. Overwrite it?",
        default=False,
        yes="Yes (overwrite)",
        no="No (keep existing)",
    )


def prompt_title(default: str) -> str:
    return prompt("Enter Title: ", default=default)


def prompt_album(default: str) -> str:
    return prompt("Enter Album: ", default=default)


def prompt_authors(authors_list: list[str], default: str = "") -> str:
    artists_completer = WordCompleter(authors_list, ignore_case=True)
    return prompt(
        "Enter Author/s: ",
        completer=artists_completer,
        complete_while_typing=True,
        default=default,
        mouse_support=True,
    )


def prompt_search_value(label: str, values: list[str], default: str = "") -> str:
    """Pick one of the library's existing values with autocomplete.

    Uses sentence completion so multi-word titles and albums complete as a
    whole instead of only their last word; the interaction mirrors the
    download artist prompt.
    """
    completer = WordCompleter(values, ignore_case=True, sentence=True)
    return prompt(
        f"{label} (type to search): ",
        completer=completer,
        complete_while_typing=True,
        default=default,
        mouse_support=True,
    )


def prompt_canonical_spelling(
    label: str, variants: list[tuple[str, int]], default: str
) -> str | None:
    """Pick one spelling to keep for a group of equivalent names.

    ``variants`` holds ``(spelling, song_count)`` pairs and ``default`` is the
    preselected spelling (the most used one). Cancelling skips the group.
    """
    values: list[tuple[str, str]] = [
        (
            spelling,
            f"{spelling}  ({count} song{'s' if count != 1 else ''})",
        )
        for spelling, count in variants
    ]
    return radiolist_dialog(
        title=f"Choose one spelling to keep for the {label}",
        text="Every other spelling is merged into the selected one.",
        values=values,
        default=default,
        ok_text="Merge",
        cancel_text="Skip",
        style=_DARK_DIALOG_STYLE,
    ).run()


def _expand_song_token(token: str) -> list[int]:
    """Expand ``"7"`` or ``"2-5"`` into a list of 1-based song numbers."""
    bounds: list[str] = [part.strip() for part in token.split("-")]
    if len(bounds) == 1:
        return [int(bounds[0])]
    if len(bounds) != 2 or not bounds[0] or not bounds[1]:
        raise ValueError
    start, end = int(bounds[0]), int(bounds[1])
    if start > end:
        raise ValueError
    return list(range(start, end + 1))


def prompt_song_selection(count: int) -> list[int]:
    """Ask which songs to process, returning 0-indexed positions.

    Supports comma-separated numbers and ranges (``1,3,5``, ``1-100``,
    ``1-3,8,10-12``). Returns an empty list when the user cancels (blank,
    ``q``, ``quit``, or ``0``).
    """
    while True:
        try:
            answer: str = prompt(
                "Song numbers/ranges (e.g. 1,3,5 or 2-10), 'all' for every song, "
                "or 'q' to cancel: "
            )
        except KeyboardInterrupt:
            print("\nInterrupted, exiting...")
            raise SystemExit(130) from None
        answer = answer.strip()
        if not answer or answer.casefold() in ("q", "quit", "exit", "cancel", "0"):
            print("No songs selected.")
            return []
        if answer.casefold() == "all":
            return list(range(count))
        parts: list[str] = [part.strip() for part in answer.split(",") if part.strip()]
        try:
            numbers: list[int] = [
                number for part in parts for number in _expand_song_token(part)
            ]
        except ValueError:
            print("Invalid selection. Use numbers or ranges (e.g. 1,3,5 or 2-10).")
            continue
        if not numbers or any(number < 1 or number > count for number in numbers):
            print(f"Selection out of range. Choose between 1 and {count}.")
            continue
        return [number - 1 for number in dict.fromkeys(numbers)]


def prompt_position_selection(count: int, message: str) -> int | None:
    """Let the user pick one position, returning a 0-based index or None.

    Supports cancel via blank, ``q``, ``quit``, ``0``, or Ctrl+C.
    """
    while True:
        try:
            answer: str = prompt(message)
        except KeyboardInterrupt:
            print("\nInterrupted, exiting...")
            raise SystemExit(130) from None
        answer = answer.strip()
        if not answer or answer.casefold() in ("q", "quit", "exit", "cancel", "0"):
            print("Cancelled.")
            return None
        try:
            number = int(answer)
        except ValueError:
            print(f"Invalid selection. Enter a number between 1 and {count}.")
            continue
        if number < 1 or number > count:
            print(f"Selection out of range. Choose between 1 and {count}.")
            continue
        return number - 1


def prompt_match_selection(matches: list["MusicBrainzMatch"]) -> int | None:
    """Let the user pick a candidate match, or None to skip the song."""
    options: list[tuple[str, str]] = [("Skip this song", "Skip this song")]
    for index, match in enumerate(matches, start=1):
        label: str = ", ".join(match.artists) + f" - {match.title}"
        if match.album:
            label += f" | {match.album}"
        if match.date:
            label += f" ({match.date})"
        options.append((str(index), label))
    selection: str = choice(
        message="Pick a matching recording (MusicBrainz):",
        options=options,
        default="1" if matches else "Skip this song",
    )
    if selection == "Skip this song":
        return None
    try:
        index = int(selection)
    except ValueError:
        return None
    return index - 1


def format_duration(seconds: float) -> str:
    total = int(seconds)
    if total >= 3600:
        return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"
    return f"{total // 60}:{total % 60:02d}"


def prompt_source_selection(
    candidates: list["SourceCandidate"],
    *,
    song_duration: float | None = None,
    matches: list[bool] | None = None,
) -> int | None:
    """Let the user pick one source candidate, or None to skip the song.

    Candidates whose length is within a couple of seconds of the library song
    are highlighted so the most plausible match stands out. When ``matches``
    is given, candidates marked ``False`` are shown in red as unmatched, and
    if none match, Skip is highlighted and selected by default.
    """
    if matches is not None and len(matches) != len(candidates):
        raise ValueError("matches must have one entry per candidate")
    no_match: bool = matches is not None and not any(matches)
    options: list[tuple[str, Any]] = []
    skip_label: str = "Skip this song"
    if no_match:
        skip_label += "  [no artist/title match]"
        options.append(
            (
                "Skip this song",
                HTML(f"<ansired><b>{escape(skip_label)}</b></ansired>"),
            )
        )
    else:
        options.append(("Skip this song", skip_label))
    for index, candidate in enumerate(candidates, start=1):
        label: str = candidate.title
        if candidate.uploader:
            label += f" - {candidate.uploader}"
        label += f" | {candidate.provider}"
        if candidate.duration:
            label += f" ({format_duration(candidate.duration)})"
        if matches is not None and not matches[index - 1]:
            label += "  [no artist/title match]"
            options.append((str(index), HTML(f"<ansired>{escape(label)}</ansired>")))
        elif matches_duration(candidate, song_duration):
            label = f"★ {label}  [same length]"
            options.append(
                (
                    str(index),
                    HTML(f"<ansigreen><b>{escape(label)}</b></ansigreen>"),
                )
            )
        else:
            options.append((str(index), label))
    duration_hint: str = (
        f" ({format_duration(song_duration)})"
        if song_duration is not None and song_duration > 0
        else ""
    )
    if matches is None:
        default: str = "1" if candidates else "Skip this song"
    else:
        default = next(
            (str(index) for index, matched in enumerate(matches, start=1) if matched),
            "Skip this song",
        )
    selection: str = choice(
        message=f"Pick a source for this song{duration_hint}:",
        options=options,
        default=default,
    )
    if selection == "Skip this song":
        return None
    try:
        index = int(selection)
    except ValueError:
        return None
    if 1 <= index <= len(candidates):
        return index - 1
    return None


_DARK_DIALOG_STYLE: Style = Style.from_dict(
    {
        "dialog": "bg:#1e1e1e #d4d4d4",
        "dialog.body": "bg:#1e1e1e #d4d4d4",
        "frame.border": "#5a5a5a",
        "frame.label": "bg:#1e1e1e #ffffff bold",
        "label": "bg:#1e1e1e #d4d4d4",
        "shadow": "bg:#000000",
        "checkbox-list": "bg:#1e1e1e #d4d4d4",
        "checkbox": "bg:#1e1e1e #d4d4d4",
        "checkbox-selected": "bg:#264f78 #ffffff bold",
        "checkbox-checked": "bg:#1e1e1e #4ec9b0 bold",
        "button": "bg:#3c3c3c #d4d4d4",
        "button.arrow": "bg:#3c3c3c #0088cc",
        "button.text": "bg:#3c3c3c #d4d4d4",
        "button.focused": "bg:#0e639c #ffffff",
        "button.focused.arrow": "bg:#0e639c #4ec9b0",
        "button.focused.text": "bg:#0e639c #ffffff",
    }
)

_TAG_FIELD_ORDER: list[str] = [
    "title",
    "artists",
    "album",
    "date",
    "genre",
    "album_artist",
    "track_number",
]
TAG_FIELD_LABELS: dict[str, str] = {
    "title": "Title",
    "artists": "Artist(s)",
    "album": "Album",
    "date": "Release date",
    "genre": "Genre",
    "album_artist": "Album artist",
    "track_number": "Track number",
    "source_url": "Source URL",
}


def collect_tag_changes(
    current: dict[str, str], intended: dict[str, str]
) -> list[tuple[str, str, str]]:
    """Return ``(field, before, after)`` for every intended value that differs."""
    return [
        (field, current.get(field, ""), intended.get(field, ""))
        for field in _TAG_FIELD_ORDER
        if intended.get(field) and intended.get(field) != current.get(field, "")
    ]


def _checkbox_dialog(
    title: str,
    message: str,
    values: list[tuple[str, str]],
    default_values: list[str],
    style: Style = _DARK_DIALOG_STYLE,
) -> Application[list[str]]:
    """Checkbox dialog where Enter applies and Space toggles the focused row."""
    cb_list: CheckboxList[str] = CheckboxList(
        values=values, default_values=default_values
    )

    def apply_now() -> None:
        get_app().exit(result=cb_list.current_values)

    bindings = KeyBindings()
    bindings.add("enter", eager=True, filter=has_focus(cb_list))(
        lambda event: apply_now()
    )
    bindings.add("tab")(focus_next)
    bindings.add("s-tab")(focus_previous)

    dialog = Dialog(
        title=title,
        body=HSplit(
            [
                Label(text=message, dont_extend_height=True),
                cb_list,
            ],
            padding=1,
        ),
        buttons=[
            Button(text="Apply", handler=apply_now),
        ],
        with_background=True,
    )

    return Application(
        layout=Layout(dialog),
        key_bindings=merge_key_bindings([load_key_bindings(), bindings]),
        mouse_support=True,
        style=style,
        full_screen=True,
    )


def prompt_tag_changes(
    changes: list[tuple[str, str, str]], cover_pending: bool = False
) -> list[str] | None:
    """Let the user tick which fields to apply.

    Every offered change is checked by default. Pressing Enter applies the
    checked fields immediately, Space toggles the focused row, and unchecking
    every field and pressing Apply skips the song (an empty selection).
    """
    values: list[tuple[str, str]] = [
        (
            field,
            f"{TAG_FIELD_LABELS.get(field, field)}: {before or '(empty)'} -> {after}",
        )
        for field, before, after in changes
    ]
    if cover_pending:
        values.append(("cover", "Cover image"))
    if not values:
        return []
    selected: list[str] = _checkbox_dialog(
        title="Confirm changes",
        message="Space toggles a field, Enter applies (uncheck all to skip this song):",
        values=values,
        default_values=[field for field, _ in values],
        style=_DARK_DIALOG_STYLE,
    ).run()
    if selected is None:
        return None
    return list(dict.fromkeys(selected))


def prompt_field_selection(
    values: list[tuple[str, str]],
) -> list[str] | None:
    """Let the user tick which tag fields to modify.

    Every field is unchecked by default. Pressing Enter applies the checked
    fields immediately, Space toggles the focused row, and unchecking every
    field and pressing Apply is treated like a cancel (an empty selection).
    """
    if not values:
        return []
    selected: list[str] = _checkbox_dialog(
        title="Select fields to modify",
        message="Space toggles a field, Enter applies the selected fields to the "
        "chosen song(s) (uncheck all to cancel):",
        values=values,
        default_values=[],
        style=_DARK_DIALOG_STYLE,
    ).run()
    if selected is None:
        return None
    return list(dict.fromkeys(selected))


def prompt_field_value(label: str, default: str = "") -> str:
    """Ask for one tag field's new value, prefilling the current one.

    Enter keeps the default, editing replaces it, and the caller decides how
    blank or special input is interpreted.
    """
    suffix: str = f" [{default}]" if default else ""
    return prompt(f"{label}{suffix}: ", default=default)
