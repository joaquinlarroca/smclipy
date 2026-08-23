from typing import Any

from prompt_toolkit import prompt
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.shortcuts import choice

from smclipy.config import settings


def show_yt_video_info(info_dictionary: dict[str, Any]) -> None:
    print("---------- TITLE ----------")
    print(info_dictionary.get("title", "No title"))
    print("---------- CHANNEL ----------")
    print(info_dictionary.get("channel", "No channel"))
    print(info_dictionary.get("creators", "No creators"))
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


def prompt_title(default: str) -> str:
    return prompt("Enter Title: ", default=default)


def prompt_authors(authors_list: list[str], default: str = "") -> str:
    artists_completer = WordCompleter(authors_list, ignore_case=True)
    return prompt(
        "Enter Author/s: ",
        completer=artists_completer,
        complete_while_typing=True,
        default=default,
        mouse_support=True,
    )
