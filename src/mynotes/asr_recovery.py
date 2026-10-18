"""Bounded, evidence-preserving local repairs. No fabricated timing or text."""
from __future__ import annotations

from copy import deepcopy
from difflib import SequenceMatcher
import json
import math
from pathlib import Path
import re

from .utils import atomic_json_write, file_sha256

POLICY_VERSION = 2
PARSER_VERSION = 3


def _finite(value):
    return isinstance(value, (float, int)) and math.isfinite(value)


def _repetitive(text):
    tokens = re.findall(r"\w+", text.casefold())
    for width, repeats in ((1, 8), (2, 5), (3, 5)):
        for i in range(len(tokens)-width*repeats+1):
            if all(tokens[i:i+width] == tokens[i+width*j:i+width*(j+1)] for j in range(1, repeats)):
                return True
    return False


def assessment(segments, duration, settings=None):
    """Locate suspect emission ranges, without sorting or modifying the report."""
    from .asr import validate_transcript
    checked = [{**deepcopy(s), 'start_seconds': s.get('start'), 'end_seconds': s.get('end')} for s in segments]
    issues = validate_transcript({'segments': checked}, duration)
    settings = settings or {}
    for i, s in enumerate(segments, 1):
        if _repetitive(str(s.get('text', ''))):
            issues.append({'segment': i, 'code': 'REPETITIVE_TEXT', 'reason': 'Repeated words or phrases require an independent check'})
        logprob, compression = s.get('avg_logprob'), s.get('compression_ratio')
        if (_finite(logprob) and logprob < settings.get("logprob_threshold", -1.0)) or (_finite(compression) and compression > settings.get("compression_ratio_threshold", 2.4)):
            issues.append({'segment': i, 'code': 'ENGINE_QUALITY_WARNING', 'reason': 'Engine decoding quality threshold exceeded'})
    # Decoder repetition can also straddle otherwise valid segment boundaries.
    run_start = 0
    tokens = [re.findall(r'\w+', str(s.get('text', '')).casefold()) for s in segments]
    for i in range(1, len(segments)+1):
        if i < len(segments) and tokens[i] == tokens[run_start]:
            continue
        minimum = 3 if len(tokens[run_start]) >= 2 else 5
        if tokens[run_start] and i-run_start >= minimum:
            for index in range(run_start, i):
                issues.append({'segment': index+1, 'code': 'REPETITIVE_SEGMENTS',
                               'reason': 'Identical text repeated across consecutive segments requires verification'})
        run_start = i
    if not segments:
        return issues, []
    indices = sorted({max(0, int(issue['segment'])-1) for issue in issues if issue.get('segment')})
    groups = []
    for i in indices:
        # Context is audio padding, not permission to replace adjacent good text.
        backwards = any(x.get('segment') == i+1 and x.get('code') == 'SEGMENTS_OUT_OF_ORDER' for x in issues)
        first, last = max(0, i-1) if backwards else i, i
        if groups and first <= groups[-1]['last']+1:
            groups[-1]['last'] = max(last, groups[-1]['last'])
        else:
            groups.append({'first': first, 'last': last})
    def bounds(group):
        rows = segments[group['first']:group['last']+1]
        starts = [s['start'] for s in rows if _finite(s.get('start'))]
        ends = [s['end'] for s in rows if _finite(s.get('end'))]
        if not starts or not ends:
            return None
        lo, hi = max(0.0, min(starts+ends)), min(duration, max(starts+ends))
        if any(not _finite(s.get('start')) or not _finite(s.get('end')) or s['end'] <= s['start'] for s in rows):
            # Neighbors bound the audio to inspect; the replacement timestamps
            # still come exclusively from the independent engine.
            previous = segments[group['first']-1] if group['first'] else None
            following = segments[group['last']+1] if group['last']+1 < len(segments) else None
            before = previous.get('end') if previous else 0.0
            after = following.get('start') if following else duration
            if _finite(before) and _finite(after) and 0 <= before < after <= duration:
                lo, hi = min(lo, before), max(hi, after)
            else:
                # Collapsed neighboring boundaries require inspecting the
                # anchored neighboring passages together, never stretching a
                # defective segment to manufacture a positive final interval.
                first, last = max(0, group['first']-1), min(len(segments)-1, group['last']+1)
                anchors = segments[first:last+1]
                times = [s[k] for s in anchors for k in ('start', 'end') if _finite(s.get(k))]
                if times:
                    group = {**group, 'first': first, 'last': last}
                    lo, hi = max(0.0, min(times)), min(duration, max(times))
        return {**group, 'start': lo, 'end': hi, 'clip_start': max(0.0, lo-5), 'clip_end': min(duration, hi+5)} if hi > lo else None
    bounded = []
    for group in groups:
        item = bounds(group)
        if item is None:
            return issues + [{'code': 'UNLOCATABLE_REPAIR', 'reason': 'No trustworthy neighboring timing anchors'}], []
        if bounded and item['clip_start'] <= bounded[-1]['clip_end']:
            previous = bounded.pop()
            item = bounds({'first': previous['first'], 'last': item['last']})
        bounded.append(item)
    return issues, bounded


def _word_segments(words):
    from .asr import parse_asr_payload
    return parse_asr_payload({'wordTimings': words})


def repair(primary, *, audio, duration, engine, settings, directory, update):
    from .asr import ASRReviewError, EngineResult, validate_engine_result, extract_audio_clip, parse_asr_payload
    issues, groups = assessment(primary.segments, duration, settings)
    report = directory/'repair-plan.json'
    plan = {'policy_version': POLICY_VERSION, 'parser_version': PARSER_VERSION, 'issues': issues, 'windows': groups, 'attempted_clips': []}
    atomic_json_write(report, plan)
    def reject(code, reason):
        plan['issues'].append({'code': code, 'reason': reason})
        atomic_json_write(report, plan)
        raise ASRReviewError(reason, plan['issues'], report)
    if not issues:
        return primary, plan
    if not groups or any(x.get('code') == 'UNLOCATABLE_REPAIR' for x in issues):
        reject('UNLOCATABLE_REPAIR', 'Local recovery has no trustworthy timing anchors; a new complete attempt is required')
    fraction = sum(g['clip_end']-g['clip_start'] for g in groups)/duration
    plan['audio_fraction'] = fraction
    if fraction > .30:
        reject('REPAIR_TOO_EXTENSIVE', f'Local verification would cover {fraction:.0%} of the audio (limit 30%); no complete secondary pass was started')
    if engine is None:
        reject('REPAIR_ENGINE_UNAVAILABLE', 'Configure the local FluidAudio engine to repair these passages; original audio and reports are preserved')
    replacements = []
    for number, group in enumerate(groups, 1):
        pieces, low, part_number = [], group['start'], 0
        while low < group['end']:
            high = min(group['end'], low+80)
            start, end = max(0.0, low-5), min(duration, high+5)
            part_number += 1
            target = directory/'repairs'/f'window-{number:03d}'/f'part-{part_number:03d}'
            target.mkdir(parents=True, exist_ok=True)
            clip = target/'input.wav'
            offset = extract_audio_clip(audio, clip, start=start, duration=end-start)
            receipt = {'window': number, 'core': [low, high], 'clip': [offset, end], 'audio_sha256': file_sha256(clip)}
            update('repairing', repair_window=number, repair_windows=len(groups))
            cache = target/'result.json'
            executable = Path(getattr(engine, 'executable', ''))
            request = {'engine': engine.name, 'executable_sha256': file_sha256(executable) if executable.is_file() else None,
                       'clip_sha256': receipt['audio_sha256'], 'language': settings['language'],
                       'model': settings.get('recovery_model', settings.get('secondary_model', 'v2')),
                       'vocabulary': settings['vocabulary'], 'policy_version': POLICY_VERSION, 'parser_version': PARSER_VERSION}
            import hashlib
            shared = directory.parent.parent/'repair-cache'/hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()/'result.json'
            result = None
            if not settings.get('_force_fresh') and not cache.exists() and shared.is_file():
                cache = shared
            if cache.exists():
                try:
                    saved = json.loads(cache.read_text())
                    from .asr import segments_digest
                    if saved.get('request') == request and saved.get('segments_sha256') == segments_digest(saved.get('segments')):
                        result = EngineResult(engine.name, request['model'], saved['version'], saved['segments'], 'word')
                except (OSError, ValueError, KeyError, TypeError):
                    pass
            if result is None:
                result = engine.transcribe(clip, target/'engine', model=request['model'], language=request['language'], vocabulary=request['vocabulary'], prompt='')
            validate_engine_result(result, target/'checked-result.json', end-offset)
            from .asr import segments_digest
            saved = {'request': request, 'version': result.version, 'segments': result.segments, 'segments_sha256': segments_digest(result.segments)}
            atomic_json_write(target/'result.json', saved)
            if not settings.get('_force_fresh'):
                atomic_json_write(shared, saved)
            selected_words = []
            for s in result.segments:
                for word in s.get('words', []):
                    a, b = word['start']+offset, word['end']+offset
                    if low <= (a+b)/2 < high or (high == duration and (a+b)/2 == high):
                        selected_words.append({**word, 'start': a, 'end': b})
            selected = _word_segments(selected_words) if selected_words else [
                {**s, 'start': s['start']+offset, 'end': s['end']+offset}
                for s in result.segments if low <= s['start']+offset < s['end']+offset <= high]
            if not selected:
                reject('EMPTY_REPAIR', 'The independent engine returned no usable text inside a passage that needs verification')
            pieces.extend(selected)
            receipt.update(engine=engine.name, model=result.model, version=result.version, selected_segments=len(selected))
            plan['attempted_clips'].append(receipt)
            low = high
        original = ' '.join(s['text'] for s in primary.segments[group['first']:group['last']+1])
        replacement = ' '.join(s['text'] for s in pieces)
        clean = lambda text: ' '.join(re.findall(r"\w+", text.casefold()))
        numbers = lambda text: set(re.findall(r'\b\d+(?:[.,]\d+)?\b', text))
        agreement = SequenceMatcher(None, clean(original), clean(replacement), autojunk=False).ratio()
        evidence = {**group, 'original_text': original, 'replacement_text': replacement, 'agreement': agreement}
        plan.setdefault('comparisons', []).append(evidence)
        if agreement < settings.get('agreement_threshold', .72) or numbers(original) != numbers(replacement):
            reject('MATERIAL_DISAGREEMENT', 'The engines disagree on a repaired passage; no final transcript or TXT was published')
        original_tokens, replacement_tokens = clean(original).split(), clean(replacement).split()
        if original_tokens[:2] != replacement_tokens[:2] or original_tokens[-2:] != replacement_tokens[-2:]:
            reject('REPAIR_BOUNDARY_MISMATCH', 'The repair does not preserve its boundary anchors; no words were silently removed or duplicated')
        for s in pieces:
            s['source_engine'] = engine.name
            s['recovery_window'] = number
        replacements.append((group, pieces))
    combined, cursor = [], 0
    for group, pieces in replacements:
        combined.extend(deepcopy(primary.segments[cursor:group['first']]))
        combined.extend(pieces)
        cursor = group['last']+1
    combined.extend(deepcopy(primary.segments[cursor:]))
    repaired = EngineResult(primary.engine, primary.model, primary.version, combined, primary.timestamp_mode)
    validate_engine_result(repaired, directory/'repaired-result.json', duration)
    remaining, _ = assessment(repaired.segments, duration, settings)
    if remaining:
        reject('UNRESOLVED_REPAIR', 'Quality issues remain after the single permitted repair pass')
    atomic_json_write(report, plan)
    return repaired, plan


def recovery_candidate(directory):
    """Read-only discovery; admission performs all hashes and source checks."""
    try:
        state = json.loads((directory/'status.json').read_text())
        identifier = state['attempt_id']
        if not re.fullmatch(r'[a-f0-9]{32}', identifier):
            return None
        attempt = directory/'attempts'/identifier
        if state.get('status') == 'RUNNING':
            from .transcription_queue import _alive
            if _alive(state.get('pid')):
                return None
        elif state.get('status') not in {'NEEDS_REVIEW', 'FAILED', 'INTERRUPTED'}:
            return None
        plan_path = attempt/'repair-plan.json'
        if plan_path.is_file():
            plan = json.loads(plan_path.read_text())
            exhausted = {'MATERIAL_DISAGREEMENT', 'REPAIR_BOUNDARY_MISMATCH', 'UNRESOLVED_REPAIR', 'REPAIR_TOO_EXTENSIVE', 'UNLOCATABLE_REPAIR', 'EMPTY_REPAIR'}
            if plan.get('policy_version') == POLICY_VERSION and any(i.get('code') in exhausted for i in plan.get('issues', [])):
                return None
        report = attempt/'primary-result.json'
        if report.is_file() and (attempt/'input.wav').is_file():
            return {'attempt_id': identifier, 'directory': str(attempt)}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def attest_recovery(directory, audio_sha256, model=None):
    candidate = recovery_candidate(directory)
    if candidate is None:
        raise ValueError('Nessuna riparazione automatica disponibile per questo tentativo. Audio e diagnosi sono conservati: usa Trascrivi da zero.')
    attempt = Path(candidate['directory'])
    report = attempt/'primary-result.json'
    raw = json.loads(report.read_text())
    if raw.get('engine') != 'argmax' or not isinstance(raw.get('segments'), list) or not raw['segments']:
        raise ValueError('Report primario non recuperabile')
    if model is not None and raw.get('model') != model:
        raise ValueError('Il modello del report non corrisponde al tentativo richiesto')
    engine_reports = {str(p.relative_to(attempt)): file_sha256(p) for p in (attempt/'primary').glob('*.json')}
    binding_path = attempt/'primary-binding.json'
    binding = json.loads(binding_path.read_text()) if binding_path.exists() else None
    if binding and binding.get('engine_reports', engine_reports) != engine_reports:
        raise ValueError('Report originali modificati: usa Trascrivi da zero')
    if binding and (binding.get('audio_sha256') != audio_sha256 or binding.get('primary_sha256') != file_sha256(report)
                    or binding.get('normalized_audio_sha256') != file_sha256(attempt/'input.wav')):
        raise ValueError('Integrità del tentativo non verificata: usa Trascrivi da zero')
    # Pre-policy attempts have no binding: their normalized PCM must match a
    # fresh normalization of the hash-verified original before any repair runs.
    return {**candidate, 'audio_sha256': audio_sha256, 'report_sha256': file_sha256(report),
            'normalized_sha256': file_sha256(attempt/'input.wav'),
            'binding_sha256': file_sha256(binding_path) if binding else None, 'engine_reports': engine_reports}


def load_recovery(receipt, audio_sha256, normalized_audio):
    from .asr import ASRError, EngineResult, parse_asr_payload
    attempt = Path(receipt['directory'])
    if attempt.name != receipt['attempt_id'] or audio_sha256 != receipt['audio_sha256']:
        raise ASRError('RECOVERY_IDENTITY_MISMATCH: sorgente o tentativo cambiato')
    report = attempt/'primary-result.json'
    if (file_sha256(report) != receipt['report_sha256'] or
        file_sha256(attempt/'input.wav') != receipt['normalized_sha256'] or
        file_sha256(normalized_audio) != receipt['normalized_sha256']):
        raise ASRError('RECOVERY_INTEGRITY_MISMATCH: audio o report modificati; nessun recupero avviato')
    if receipt.get('binding_sha256') and file_sha256(attempt/'primary-binding.json') != receipt['binding_sha256']:
        raise ASRError('RECOVERY_BINDING_CHANGED: attestazione modificata')
    actual_reports = {str(p.relative_to(attempt)): file_sha256(p) for p in (attempt/'primary').glob('*.json')}
    if actual_reports != receipt.get('engine_reports', {}):
        raise ASRError('RECOVERY_REPORT_CHANGED: report originali modificati')
    raw = json.loads(report.read_text())
    # Reparse original engine JSON where available to retain quality metrics
    # missing from old projections. Do not replace the saved emission order.
    reports = list((attempt/'primary').glob('*.json'))
    segments = raw['segments']
    for path in reports:
        payload = json.loads(path.read_text())
        parsed = parse_asr_payload(payload)
        projection = lambda rows: [(s.get('start'), s.get('end'), s.get('text')) for s in rows]
        if projection(parsed) == projection(segments):
            segments = parsed
            break
    return EngineResult(raw['engine'], raw['model'], raw.get('version', 'saved-report'), segments, raw.get('timestamp_mode', 'segment'))


def review_message(error):
    """Explain the actionable blocker while preserving structured diagnostics."""
    code = error.issues[-1].get('code') if error.issues else None
    messages = {
        'MATERIAL_DISAGREEMENT': 'I due motori restituiscono testi diversi nel passaggio verificato.',
        'REPAIR_BOUNDARY_MISMATCH': 'Il confronto non permette di allineare con sicurezza l’inizio o la fine del passaggio.',
        'REPAIR_TOO_EXTENSIVE': 'I passaggi da verificare superano il 30% dell’audio; non è stata avviata una seconda trascrizione completa.',
        'UNLOCATABLE_REPAIR': 'Mancano riferimenti temporali sufficienti per una riparazione locale.',
        'REPAIR_ENGINE_UNAVAILABLE': 'Configura il motore locale FluidAudio per verificare i passaggi dubbi.',
        'EMPTY_REPAIR': 'Il secondo motore non ha restituito testo utilizzabile nel passaggio dubbio.',
        'UNRESOLVED_REPAIR': 'Restano problemi dopo l’unico passaggio automatico di riparazione.',
    }
    return messages[code] + ' Audio e diagnosi sono conservati; nessuna trascrizione finale è stata pubblicata.' if code in messages else str(error)



def review_summary(issues):
    """A failed check has already run; describe its actual result to the user."""
    codes = {issue.get('code') for issue in issues if isinstance(issue, dict)}
    if codes & {'MATERIAL_DISAGREEMENT', 'REPAIR_BOUNDARY_MISMATCH', 'UNRESOLVED_REPAIR'}:
        return 'Confronto del testo non conclusivo. Alcuni passaggi restano dubbi; audio e diagnosi sono conservati.'
    if codes & {'INVALID_SEGMENT_INTERVAL', 'SEGMENTS_OUT_OF_ORDER', 'NON_FINITE_TIMESTAMP', 'WORD_OUTSIDE_SEGMENT', 'TIMESTAMP_BEYOND_AUDIO', 'EMPTY_INTERVAL_AFTER_ROUNDING'}:
        return 'Trascrizione bloccata: timestamp incoerenti. L’audio è conservato; il controllo non indica che tutta la trascrizione sia sbagliata.'
    if 'REPAIR_TOO_EXTENSIVE' in codes:
        return 'Troppi passaggi dubbi per una riparazione locale. Audio e diagnosi sono conservati.'
    if 'REPAIR_ENGINE_UNAVAILABLE' in codes:
        return 'Riparazione in attesa del motore locale FluidAudio. Audio e risultati già elaborati sono conservati.'
    return None
