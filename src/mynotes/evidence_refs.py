"""Lossless, context-bound reference canonicalization, never prose rewriting."""
from copy import deepcopy
import re


class EvidenceReferenceError(ValueError):
    def __init__(self, defects):
        self.defects = defects
        super().__init__('; '.join(f"{d['path']}: {d['reference']} ({d['code']})" for d in defects))


def normalize_references(value, valid_ids):
    valid = set(valid_ids)
    index = {}
    for identifier in valid:
        match = re.fullmatch(r'([A-Z]+)([0-9]+)', identifier)
        if match:
            index.setdefault((match[1], int(match[2])), []).append(identifier)
    report, defects = [], []
    def resolve(text, path):
        if not isinstance(text, str) or text in valid:
            return text
        match = re.fullmatch(r'([A-Z]+)([0-9]+)', text)
        candidates = index.get((match[1], int(match[2])), []) if match else []
        if len(candidates) != 1:
            defects.append({'path': path, 'reference': text, 'code': 'AMBIGUOUS_REFERENCE' if candidates else 'UNKNOWN_REFERENCE',
                            'action': 'Review this reference against the original request inventory'})
            return text
        canonical = candidates[0]
        report.append({'path': path, 'original': text, 'canonical': canonical, 'reason': 'Unique numeric identity in the same source namespace'})
        return canonical
    def visit(item, field=None, path='$'):
        if isinstance(item, dict):
            return {k: visit(v, k, path + '.' + k) for k, v in item.items()}
        if isinstance(item, list):
            return [visit(v, 'evidence_id' if field == 'evidence_ids' else None, f'{path}[{i}]') for i,v in enumerate(item)]
        return resolve(item,path) if field in {'evidence_id','visual_id'} else deepcopy(item)
    result = visit(value)
    if defects:
        raise EvidenceReferenceError(defects)
    return result, report
