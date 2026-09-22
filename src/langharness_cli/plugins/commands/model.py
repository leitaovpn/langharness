"""Interactive model-provider command plugin."""

from __future__ import annotations

from collections.abc import Sequence

from pelix.ipopo.decorators import ComponentFactory, Property, Provides

from langharness_cli.common.i18n import tr
from langharness_cli.contracts import (
    CLICommandProvider,
    CommandSpec,
    InteractiveCommandContext,
    InteractiveCommandSpec,
)


@ComponentFactory("cli-model-command-factory")
@Provides(CLICommandProvider)
@Property("_plugin_name", "plugin.name", "model-command")
@Property("_plugin_version", "plugin.version", "1.0.0")
@Property("_locale", "plugin.ui.locale", "en")
class ModelCommandPlugin:
    def __init__(self) -> None:
        self._plugin_name = "model-command"
        self._plugin_version = "1.0.0"
        self._locale = "en"

    def get_commands(self) -> list[CommandSpec]:
        return []

    def get_interactive_commands(self) -> list[InteractiveCommandSpec]:
        return [
            InteractiveCommandSpec(
                name="model",
                help=tr(self._locale, "help_model"),
                handler=self._switch,
                complete=self._complete,
            )
        ]

    @staticmethod
    def _complete(
        context: InteractiveCommandContext, words: Sequence[str], prefix: str
    ) -> list[str]:
        """Offer the configured providers; `/model` accepts nothing else."""
        del words, prefix
        return context.list_providers()

    def _switch(self, context: InteractiveCommandContext, line: str) -> bool:
        name = line.strip()
        if name:
            context.switch_provider(name)
            return False
        providers = context.list_providers()
        if not providers:
            print(tr(self._locale, "model_none"))
        else:
            print(tr(self._locale, "model_available", names=", ".join(providers)))
        return False

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}
