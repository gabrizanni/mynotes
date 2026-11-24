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


def model_preferences(workspace: Workspace) -> dict:
    """Read model choices without initializing a workspace or contacting providers."""
    import hashlib
    import json
    from .configuration import validate_configuration
    # Read one atomically replaced file once: the revision must describe the
    # same bytes as the returned settings, even if another editor saves now.
    content = workspace.configuration_path.read_bytes()
    settings = validate_configuration(json.loads(content))
    revision = hashlib.sha256(content).hexdigest()
    return _model_preferences_snapshot(settings, revision)


def _model_preferences_snapshot(settings: dict, revision: str) -> dict:
    from .models import resolve_role_selection, resolve_provider_options
    provider = settings['providers']['default']
    model, effort = resolve_provider_options(settings, provider) if provider else ('', '')
    return {'default': dict(provider=provider, model=model, effort=effort),
            'overrides': {role: settings['providers'].get('roles', {}).get(role) for role in ('chat', 'notes')},
            'effective': {role: resolve_role_selection(settings, role) for role in ('chat', 'notes')},
            'language': settings['personal']['interface_language'],
            'revision': revision}


def save_model_preferences(workspace: Workspace, payload: dict, expected_revision: str) -> dict:
    """Atomically save the shared choice and both independent overrides."""
    from .utils import file_sha256
    if not isinstance(payload, dict) or set(payload) != {'default', 'overrides', 'language'}:
        raise ConfigurationError('Supply default, overrides and language')
    if not isinstance(payload['overrides'], dict) or set(payload['overrides']) != {'chat', 'notes'}:
        raise ConfigurationError('Supply both model roles; null means inherit')
    if not isinstance(payload['language'], str) or payload['language'] not in {'it', 'en'}:
        raise ConfigurationError('Choose Italian or English')
    for value in [payload['default'], *payload['overrides'].values()]:
        if value is None:
            continue
        if (not isinstance(value, dict) or set(value) != {'provider', 'model', 'effort'}
                or not isinstance(value['provider'], str) or value['provider'] not in SUPPORTED_PROVIDERS or not valid_model(value['model'])
                or not isinstance(value['effort'], str)):
            raise ConfigurationError('Each selection needs an agent, model and effort')
        validate_effort(value['provider'], value['model'], value['effort'])
    if payload['default'] is None or not isinstance(expected_revision, str) or not expected_revision:
        raise ConfigurationError('A shared default and expected revision are required')
    before = {}
    def edit(settings):
        from copy import deepcopy
        before.update(deepcopy(settings))
        if file_sha256(workspace.configuration_path) != expected_revision:
            raise ConfigurationError('CONFIGURATION_CONFLICT: settings changed; reread before saving')
        shared = payload['default']
        settings['providers']['default'] = shared['provider']
        settings['providers']['configured'].setdefault(shared['provider'], {}).update(model=shared['model'], effort=shared['effort'])
        settings['providers']['roles'] = payload['overrides']
        for custom in payload['overrides'].values():
            if custom is not None:
                settings['providers']['configured'].setdefault(custom['provider'], {})
        settings['personal']['interface_language'] = payload['language']
        return settings
    written = workspace.configuration_store().update(edit)
    import hashlib
    import json
    # Return the revision of our own transaction, never a later concurrent edit.
    revision = expected_revision if written == before else hashlib.sha256(
        (json.dumps(written, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode()).hexdigest()
    return _model_preferences_snapshot(written, revision)
