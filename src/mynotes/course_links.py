"""Connect a supplied course folder with bounded, metadata-only discovery."""
from __future__ import annotations

import os
import unicodedata
from pathlib import Path
from typing import Any

from .configuration import ConfigurationError
from .path_links import PathLinkError, resolve_course_directory
from .workspace import Workspace

MATERIAL_TYPES = {'.pdf', '.ppt', '.pptx', '.xlsx'}
NOTE_TYPES = {'.docx', '.txt', '.md', '.ipynb'}
AUDIO_TYPES = {'.mp3', '.wav', '.m4a', '.aac', '.aiff', '.flac', '.ogg'}
ROLE_NAMES = {
    'personal_notes': {'appunti', 'notes', 'note', 'personal notes', 'class notes', 'appunti personali'},
    'recordings': {'registrazioni', 'registrazione', 'recordings', 'recording', 'audio', 'reg'},
}


def _role(name: str) -> str | None:
    value = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().casefold().replace('_', ' ')
    for suffix in ('.alias', ' alias'):
        if value.endswith(suffix):
            value = value[:-len(suffix)]
    return next((role for role, names in ROLE_NAMES.items() if value.strip() in names), None)


def inspect_course_folder(path: Path, *, max_entries: int = 1000, max_files_per_role: int = 50) -> dict[str, Any]:
    """Inspect the supplied folder and immediate children, never file content."""
    if max_entries < 1 or max_files_per_role < 1:
        raise ValueError('inventory limits must be positive')
    root = resolve_course_directory(path)
    candidates: dict[str, set[str]] = {'personal_notes': set(), 'recordings': set()}
    files: dict[str, list[str]] = {'materials': [], 'personal_notes': [], 'recordings': []}
    warnings: list[str] = []
    scanned = 0
    truncated = False
    children: list[tuple[Path, str | None]] = []

    def scan(directory: Path, role: str | None, *, first_level: bool = False) -> None:
        nonlocal scanned, truncated
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if scanned >= max_entries:
                        truncated = True
                        return
                    scanned += 1
                    if entry.name.startswith('.') or entry.name.startswith('~$'):
                        continue
                    item = Path(entry.path)
                    named_role = _role(entry.name)
                    if first_level and (entry.is_dir(follow_symlinks=False) or named_role is not None or entry.is_symlink()):
                        # Only explicitly named role links are followed outside
                        # the root; ordinary child directories stay in scope.
                        if entry.is_symlink() and named_role is None:
                            continue
                        try:
                            folder = resolve_course_directory(item)
                        except (PathLinkError, OSError) as error:
                            warnings.append(f'{item.name}: {error}')
                            continue
                        children.append((folder, named_role))
                        if named_role:
                            candidates[named_role].add(str(folder))
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    suffix = item.suffix.casefold()
                    detected = ('materials' if suffix in MATERIAL_TYPES else 'personal_notes' if suffix in NOTE_TYPES else 'recordings' if suffix in AUDIO_TYPES else None)
                    if detected:
                        files[detected].append(str(item))
                        if detected in candidates:
                            candidates[detected].add(str(directory))
        except OSError as error:
            warnings.append(f'{directory.name}: inventory unavailable ({type(error).__name__}); choose an accessible folder.')

    scan(root, None, first_level=True)
    info = root.stat()
    visited = {(info.st_dev, info.st_ino)}
    for child, named_role in children:
        try:
            info = child.stat()
        except OSError as error:
            warnings.append(f'{child.name}: inventory unavailable ({type(error).__name__}); choose an accessible folder.')
            continue
        identity = (info.st_dev, info.st_ino)
        if identity not in visited and not truncated:
            visited.add(identity)
            scan(child, named_role)
    counts = {role: len(items) for role, items in files.items()}
    return {'resolved_folder': str(root), 'files': {role: sorted(items)[:max_files_per_role] for role, items in files.items()},
            'file_counts': counts, 'file_list_truncated': [role for role, count in counts.items() if count > max_files_per_role],
            'role_candidates': {role: sorted(items) for role, items in candidates.items()},
            'entries_scanned': scanned, 'inventory_truncated': truncated, 'warnings': warnings}


def link_course_folder(workspace: Workspace, code: str, path: Path, *, dry_run: bool = False,
                       notes: Path | None = None, recordings: Path | None = None) -> dict[str, Any]:
    """Save clear connections atomically; ambiguities require a selection."""
    code = code.upper()
    if not workspace.is_initialized():
        raise ConfigurationError('Create the first course before linking its folder.')
    store = workspace.configuration_store()
    if not any(course['code'] == code for course in store.load()['courses']):
        raise ConfigurationError(f'Course not found: {code}')
    try:
        inventory = inspect_course_folder(path)
        selected = {'materials': inventory['resolved_folder']}
        ambiguity = {}
        for role, explicit in [('personal_notes', notes), ('recordings', recordings)]:
            candidates = inventory['role_candidates'][role]
            if explicit is not None:
                selected[role] = str(resolve_course_directory(explicit))
            elif len(candidates) == 1:
                selected[role] = candidates[0]
            elif len(candidates) > 1:
                ambiguity[role] = candidates
    except (PathLinkError, OSError) as error:
        raise ConfigurationError(str(error)) from error
    if ambiguity or inventory['inventory_truncated']:
        return {'status': 'NEEDS_SELECTION', 'course': code, 'saved': False, 'proposed_connections': selected,
                'ambiguities': ambiguity, 'inventory': inventory,
                'action': 'Choose the notes/recordings folder explicitly, or select a more specific course folder.'}
    if dry_run:
        return {'status': 'READY', 'course': code, 'saved': False, 'connections': selected, 'inventory': inventory}

    def edit(settings):
        course = next((item for item in settings['courses'] if item['code'] == code), None)
        if course is None:
            raise ConfigurationError(f'Course no longer configured: {code}')
        course['folders']['materials'] = selected['materials']
        if 'personal_notes' in selected:
            course['folders']['personal_notes'] = selected['personal_notes']
        if 'recordings' in selected and selected['recordings'] not in settings['paths']['recording_dirs']:
            settings['paths']['recording_dirs'].append(selected['recordings'])
        return settings
    saved = store.update(edit)
    course = next(item for item in saved['courses'] if item['code'] == code)
    return {'status': 'LINKED_WITH_WARNINGS' if inventory['warnings'] else 'LINKED', 'course': code,
            'saved': True, 'connections': selected, 'course_folders': course['folders'], 'inventory': inventory,
            'action': 'Folder connected. Review lesson-to-slide matching before preparing sources.'}
