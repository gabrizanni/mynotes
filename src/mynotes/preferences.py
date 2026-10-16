"""Small atomic edits shared by the native app and the conversation."""
from __future__ import annotations

from .configuration import ConfigurationError, SUPPORTED_PROVIDERS, merge_group_answers
from .workspace import Workspace
from .models import preset_choice, valid_model, validate_effort


def save_preferences(workspace: Workspace, provider: str, model: str, language: str | None = None,
                     *, effort: str | None = None, preset: str | None = None) -> dict:
    if provider not in SUPPORTED_PROVIDERS:
        raise ConfigurationError("Choose codex, agy or claude")
    if preset is not None:
        selected = preset_choice(provider, preset)
        model = selected["model"]
        if effort is None:
            effort = selected["default_effort"]
    if not isinstance(model, str):
        raise ConfigurationError("Model must be text")
    model = model.strip()
    if not valid_model(model):
        raise ConfigurationError("Enter a model identifier without spaces or control characters")
    if effort is not None:
        if not isinstance(effort, str):
            raise ConfigurationError("Effort must be text")
        effort = effort.strip()
        validate_effort(provider, model, effort)
    if language is not None and language not in {"it", "en"}:
        raise ConfigurationError("Choose Italian (it) or English (en)")
    workspace.initialize()
    def edit(settings):
        settings["providers"]["default"] = provider
        options = settings["providers"]["configured"].setdefault(provider, {})
        # Explicit empty model distinguishes an intentional provider default
        # from an unset model, which now selects the MyNotes preset.
        options["model"] = model
        if effort is not None:
            options["effort"] = effort
        if language:
            settings["personal"]["interface_language"] = language
        return settings
    settings = workspace.configuration_store().update(edit)
    return {"provider": provider, "model": settings["providers"]["configured"][provider].get("model", ""),
            "effort": settings["providers"]["configured"][provider].get("effort", ""),
            "language": settings["personal"]["interface_language"]}


def save_background(workspace: Workspace, background: str, *, skip: bool = False) -> dict:
    if not isinstance(background, str) or (not background.strip() and not skip):
        raise ConfigurationError("Tell us about your background, or choose to skip it")
    workspace.initialize()
    def edit(settings):
        # A deliberate skip acknowledges this step without erasing a saved profile.
        answers = {"background_confirmed": True}
        if not skip:
            answers["background"] = background.strip()
        return merge_group_answers(settings, "personal", answers)
    return workspace.configuration_store().update(edit)["personal"]
