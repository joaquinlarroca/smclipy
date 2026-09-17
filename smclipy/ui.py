from typing import TYPE_CHECKING, Any

from prompt_toolkit import prompt
from prompt_toolkit.application import Application, get_app
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.filters import has_focus
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.key_binding.bindings.focus import focus_next, focus_previous
from prompt_toolkit.key_binding.defaults import load_key_bindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit
from prompt_toolkit.shortcuts import choice
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Button, CheckboxList, Dialog, Label

from smclipy.config import settings
from smclipy.downloader import get_author

if TYPE_CHECKING:
    from smclipy.musicbrainz import MusicBrainzMatch


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
        message="Pick a matching recording:",
        options=options,
        default="Skip this song",
    )
    if selection == "Skip this song":
        return None
    try:
        index = int(selection)
    except ValueError:
        return None
    return index - 1


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
    "album_artist",
    "track_number",
]
_TAG_FIELD_LABELS: dict[str, str] = {
    "title": "Title",
    "artists": "Artist(s)",
    "album": "Album",
    "date": "Release date",
    "album_artist": "Album artist",
    "track_number": "Track number",
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


def _tag_checkbox_dialog(
    values: list[tuple[str, str]], default_values: list[str], style: Style
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
        title="Confirm changes",
        body=HSplit(
            [
                Label(
                    text="Space toggles a field, Enter applies (uncheck all to skip "
                    "this song):",
                    dont_extend_height=True,
                ),
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
            f"{_TAG_FIELD_LABELS.get(field, field)}: {before or '(empty)'} -> {after}",
        )
        for field, before, after in changes
    ]
    if cover_pending:
        values.append(("cover", "Cover image"))
    if not values:
        return []
    selected: list[str] = _tag_checkbox_dialog(
        values=values,
        default_values=[field for field, _ in values],
        style=_DARK_DIALOG_STYLE,
    ).run()
    if selected is None:
        return None
    return list(dict.fromkeys(selected))
