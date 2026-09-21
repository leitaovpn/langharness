"""The palette completer: command-name and argument completion."""

from __future__ import annotations

from prompt_toolkit.completion import CompleteEvent, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import fragment_list_to_text

from langharness_cli.common.completion import PaletteCompleter
from langharness_cli.contracts import InteractiveCommandSpec


def spec(name: str, help_text: str = "does a thing") -> InteractiveCommandSpec:
    return InteractiveCommandSpec(
        name=name, help=help_text, handler=lambda context, line: False
    )


def collect(completer: PaletteCompleter, text: str) -> list[Completion]:
    document = Document(text, cursor_position=len(text))
    return list(completer.get_completions(document, CompleteEvent()))


def texts(completions: list[Completion]) -> list[str]:
    return [completion.text for completion in completions]


def test_command_position_completes_names_with_descriptions() -> None:
    completer = PaletteCompleter({"help": spec("help", "show help")})

    completions = collect(completer, "/he")

    assert texts(completions) == ["/help"]
    assert fragment_list_to_text(completions[0].display_meta) == "show help"


def test_a_bare_slash_lists_every_command_in_name_order() -> None:
    completer = PaletteCompleter(
        {"whoami": spec("whoami"), "session": spec("session"), "help": spec("help")}
    )

    assert texts(collect(completer, "/")) == ["/help", "/session", "/whoami"]


def test_command_completion_replaces_the_whole_typed_token() -> None:
    completer = PaletteCompleter({"help": spec("help")})

    assert collect(completer, "/he")[0].start_position == -3


def test_plain_message_offers_no_completions() -> None:
    completer = PaletteCompleter({"help": spec("help")})

    assert collect(completer, "hello world") == []


def test_argument_position_completes_from_the_command_source() -> None:
    completer = PaletteCompleter(
        {"model": spec("model")}, {"model": lambda prefix: ["demo", "prod"]}
    )

    assert texts(collect(completer, "/model ")) == ["demo", "prod"]


def test_argument_completion_replaces_only_the_typed_argument() -> None:
    completer = PaletteCompleter(
        {"model": spec("model")}, {"model": lambda prefix: ["demo"]}
    )

    assert collect(completer, "/model de")[0].start_position == -2


def test_argument_candidates_are_filtered_by_the_typed_prefix() -> None:
    completer = PaletteCompleter(
        {"model": spec("model")}, {"model": lambda prefix: ["demo", "prod"]}
    )

    assert texts(collect(completer, "/model de")) == ["demo"]


def test_argument_source_receives_the_typed_prefix() -> None:
    seen: list[str] = []

    def source(prefix: str) -> list[str]:
        seen.append(prefix)
        return ["demo"]

    completer = PaletteCompleter({"model": spec("model")}, {"model": source})

    collect(completer, "/model de")

    assert seen == ["de"]


def test_third_segment_offers_no_completions() -> None:
    completer = PaletteCompleter(
        {"model": spec("model")}, {"model": lambda prefix: ["demo"]}
    )

    assert collect(completer, "/model demo ") == []


def test_command_without_an_argument_source_offers_nothing_after_it() -> None:
    completer = PaletteCompleter({"help": spec("help")})

    assert collect(completer, "/help ") == []
