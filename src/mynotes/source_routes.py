"""Small search hints derived from saved paths, without enumerating files."""
from pathlib import Path
import json


def route_index(workspace, settings, course=None):
    def read(path):
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return {}
    library = read(workspace.state_dir / 'recording-library/settings.json')
    capture = read(workspace.config_dir / 'capture.json')
    base = Path(library.get('folder') or capture.get('outputDirectory') or workspace.root / 'recordings/capture')
    shared = list(dict.fromkeys([str(base), *settings.get('paths', {}).get('recording_dirs', [])]))
    courses = []
    for item in settings.get('courses', []):
        if course and item['code'] != course.upper():
            continue
        folders = item.get('folders', {})
        courses.append({
            'code': item['code'], 'name': item['name'], 'aliases': item.get('aliases', []),
            'materials': folders.get('materials', ''),
            'recordings': folders.get('recordings', ''),
            'transcripts': list(dict.fromkeys(filter(None, [folders.get('transcripts', ''), str(base / 'Trascrizioni' / item['code'])]))),
            'personal_notes': item.get('files', {}).get('personal_notes') or folders.get('personal_notes', ''),
            'handout_output_only': item.get('files', {}).get('handout') or folders.get('handout', ''),
        })
    return {'shared_recording_folders': shared, 'unassigned_transcripts': str(base / 'Trascrizioni' / 'Dubbi'),
            'courses': courses, 'paths_are_search_hints_not_verified_inventory': True}


def remember_transcript_folder(workspace, item):
    """Called after validated import/export/assignment, never read-only polling."""
    code = item.get('course_code')
    path = item.get('export_text') or item.get('path')
    if not code or not path or Path(path).suffix.lower() not in {'.txt', '.json'}:
        return
    def edit(settings):
        target = next((c for c in settings['courses'] if c['code'] == code), None)
        if target:
            target['folders']['transcripts'] = str(Path(path).parent)
        return settings
    workspace.configuration_store().update(edit)
