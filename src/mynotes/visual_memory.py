"""Derived inspection cache, bound to image bytes and requested review settings."""
import json
from pathlib import Path

from .progressive_review import digest
from .utils import atomic_json_write, file_sha256


def scope(visual, requested):
    source=Path(visual.get('original_image_path') or visual['image_path'])
    return {'version':2,'image_sha256':file_sha256(source),'requested':requested}


def load(directory, visuals, requested):
    results=[]
    for visual in visuals:
        if not visual.get('image_path'):continue
        expected=scope(visual,requested);path=directory/(digest(expected)+'.json')
        if not path.is_file() or path.is_symlink():continue
        try:
            saved=json.loads(path.read_text());payload=saved['payload']
            if digest(payload)!=saved['sha256'] or payload['scope']!=expected:continue
            if payload['source_access']['sha256']!=expected['image_sha256']:continue
            results.append({'evidence_id':visual['evidence_id'],'image_sha256':expected['image_sha256'],
                'content_kind':payload['content_kind'],'reason':payload['reason'],
                'origin':'verified_inspection_cache','certificate':saved})
        except (OSError,ValueError,KeyError,TypeError):continue
    return results


def certificate_access(cached, requested):
    result=[]
    for item in cached:
        saved=item['certificate'];p=saved['payload']
        if (digest(p)!=saved['sha256'] or p['scope']['requested']!=requested
                or p['scope']['image_sha256']!=item['image_sha256']
                or p['content_kind']!=item['content_kind'] or p['reason']!=item['reason']):
            raise ValueError('Inspection cache certificate changed')
        result.append({**p['source_access'],'origin':'verified_inspection_cache','content_kind':p['content_kind']})
    return result


def save(directory, request, response):
    """Called only after batch source/content validation, never on ERROR."""
    record=json.loads(Path(response.attempt_path).read_text())
    if record.get('error') or record.get('envelope',{}).get('transport')!='agy_progressive_v2':return
    receipt=json.loads(Path(record['envelope']['receipt']).read_text())
    access={row['sha256']:row for row in receipt['access']}
    decisions={}
    for ref in receipt['units']:
        unit=json.loads((Path(record['envelope']['receipt']).parent/ref['file']).read_text())['effective']
        decisions.update({v['evidence_id']:v for v in unit['visual_delta']})
    if receipt.get('visual_review'):
        reviewed=json.loads(Path(receipt['visual_review']['path']).read_text())
        for choice in reviewed['changes']:
            decisions[choice['evidence_id']]={'content_kind':choice['content_kind'],
                'reason':'Explicit visual review: '+choice['reason']}
    directory.mkdir(parents=True,exist_ok=True)
    for visual in request.source_pack.get('visuals',[]):
        decision=decisions.get(visual['evidence_id'])
        if not decision or not visual.get('image_path'):continue
        key=scope(visual,record['requested']);observed=access.get(key['image_sha256'])
        if observed is None:continue
        payload={'scope':key,'content_kind':decision['content_kind'],'reason':decision['reason'],
                 'source_access':observed,'validated_attempt':str(response.attempt_path)}
        atomic_json_write(directory/(digest(key)+'.json'),{'payload':payload,'sha256':digest(payload)})
