from typing import Any

from prompt_toolkit import prompt
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.shortcuts import choice

from smclipy.config import settings
from smclipy.downloader import get_author


def show_video_info(info_dictionary: dict[str, Any]) -> None:
    print("---------- TITLE ----------")
    print(info_dictionary.get("title", "No title"))
    print("---------- ARTIST/S ----------")
    authors = get_author(info_dictionary)
    if authors:
        print(", ".join(authors))
    else:
        print("No artists")
    print("---------- DESCRIPTION ----------")
    description = str(info_dictionary.get("description", "No description"))
    description_lines = description.splitlines()[: settings().description_max_lines]
    print("\n".join(description_lines))
    print("")


def prompt_crop(default: bool = False) -> bool:
    return (
        choice(
            message="Crop cover to 1:1?",
            options=[("Yes", "Yes"), ("No", "No")],
            default="Yes" if default else "No",
        )
        == "Yes"
    )


def prompt_resume() -> bool:
    return (
        choice(
            message="Found an interrupted download. Resume from where it stopped?",
            options=[("Yes", "Yes"), ("No", "No")],
            default="Yes",
        )
        == "Yes"
    )


def prompt_overwrite() -> bool:
    return (
        choice(
            message="File already exists. Overwrite it?",
            options=[
                ("No (keep existing)", "No (keep existing)"),
                ("Yes (overwrite)", "Yes (overwrite)"),
            ],
            default="No (keep existing)",
        )
        == "Yes (overwrite)"
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
