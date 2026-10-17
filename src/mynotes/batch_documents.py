"""Candidate-only batch documents, composed from validated structured notes."""
from contextlib import ExitStack
import json
from pathlib import Path

from .utils import atomic_json_write,file_sha256
from .publication import (PublicationError,_load_candidate,_new_document,_replace_or_append,
                          _docx_structure,_render_and_validate,word_renderer_hash,build_word_candidate)
from .configuration import effective_style
from .workflow import _course,_generation_lock


def build_batch_documents(workspace,batch_id,*,mode='both'):
    from .batch import load_batch_manifest
    if mode not in {'standalone','cumulative','both'}:raise PublicationError('Unknown batch document mode')
    manifest=load_batch_manifest(workspace,batch_id);lessons=manifest['lectures']
    root=workspace.runs_dir/'_batches'/batch_id
    with ExitStack() as locks:
        for lesson in lessons:locks.enter_context(_generation_lock(workspace,lesson))
        # Validate all inputs before creating any partial delivery.
        rows=[]
        from .batch_recovery import batch_status
        ready=batch_status(workspace,batch_id)
        if not all(r['content_validated'] for r in ready['lessons']):
            raise PublicationError('All batch lessons need validated candidates before a complete delivery can be built')
        for lesson in lessons:
            candidate,pack=_load_candidate(workspace,lesson)
            if candidate.get('batch_id')!=batch_id:raise PublicationError('Candidate belongs to a different batch')
            rows.append((lesson,candidate,pack))
        sources={l:file_sha256(workspace.runs_dir/l/'lecture_notes.candidate.json') for l,_,_ in rows}
        configuration=workspace.configuration_store().load();course=_course(configuration,manifest['course_code'])
        profile=effective_style(configuration,manifest['course_code'])
        key=__import__('hashlib').sha256(json.dumps({'sources':sources,'renderer':word_renderer_hash(),'mode':mode,
            'course':course,'style':profile,'builder':file_sha256(Path(__file__))},sort_keys=True).encode()).hexdigest()
        output=root/'documents'/key;index=output/'index.json'
        if index.exists():
            cached=json.loads(index.read_text())
            if all(Path(r['path']).is_file() and file_sha256(Path(r['path']))==r['sha256'] for r in cached['files']):
                return {**cached,'reused':True}
            raise PublicationError('A completed delivery was changed; preserve it and inspect before rebuilding')
        output.mkdir(parents=True,exist_ok=True);files=[]
        if mode in {'standalone','both'}:
            for lesson,_,_ in rows:
                built=build_word_candidate(workspace,lesson,mode='standalone')
                files.append({'kind':'standalone','lecture_id':lesson,'path':built['candidate'],'sha256':built['candidate_hash'],
                              'validation':str(Path(built['candidate']).with_suffix('.validation.json'))})
        if mode in {'cumulative','both'}:
            document=_new_document(course,profile)
            for lesson,candidate,pack in rows:
                _replace_or_append(document,candidate['notes'],pack,language=course.get('languages',{}).get('notes','en'),
                                   recap_language=profile.get('recap_language','it'))
            path=output/f"{manifest['course_code']}-L{lessons[0].rsplit('L',1)[1]}-L{lessons[-1].rsplit('L',1)[1]}.candidate.docx"
            document.save(path);structure={}
            for lesson,candidate,pack in rows:
                notes=candidate['notes'];blocks=notes.get('blocks',[])
                structure[lesson]=_docx_structure(path,lesson,sum(v.get('decision')=='include' for v in notes.get('visual_decisions',[])),
                    bool(notes.get('formula') or notes.get('formulas') or any(b.get('kind')=='formula' for b in blocks)),
                    [b['rows'] for b in blocks if b.get('kind')=='table'] if blocks else ([notes['table']] if notes.get('table') else []))
            render=_render_and_validate(path,output/'render')
            validation={'mode':'cumulative','candidate':str(path),'candidate_hash':file_sha256(path),'source_candidates':sources,
                        'renderer_hash':word_renderer_hash(),'structure':structure,'render':render,'published':False}
            atomic_json_write(path.with_suffix('.validation.json'),validation)
            files.append({'kind':'cumulative','path':str(path),'sha256':file_sha256(path),'validation':str(path.with_suffix('.validation.json'))})
        if any(file_sha256(workspace.runs_dir/l/'lecture_notes.candidate.json')!=h for l,h in sources.items()):
            raise PublicationError('Input candidate changed during build')
        receipt={'batch_id':batch_id,'status':'WORD_CANDIDATES_READY','mode':mode,'source_candidates':sources,
                 'renderer_hash':word_renderer_hash(),'files':files,'provider_invoked':False,'published':False,'reused':False}
        atomic_json_write(index,receipt)
        return {**receipt,'index':str(index)}
