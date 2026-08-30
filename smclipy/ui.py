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


def prompt_crop(default: str = "False") -> bool:
    result = choice(
        message="Crop cover to 1:1?",
        options=[("False", "False"), ("True", "True")],
        default=default,
    )
    return result == "True"


def prompt_resume() -> bool:
    result = choice(
        message="Found an interrupted download. Resume from where it stopped?",
        options=[("Yes", "Yes"), ("No", "No")],
        default="Yes",
    )
    return result == "Yes"


def prompt_overwrite() -> bool:
    result = choice(
        message="File already exists. Overwrite?",
        options=[("Cancel", "Cancel"), ("Overwrite", "Overwrite")],
        default="Cancel",
    )
    return result == "Overwrite"


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
