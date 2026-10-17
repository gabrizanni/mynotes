"""Archive categories decode the additive backend contract, including old data."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'darwin' or not shutil.which('swiftc'), reason='macOS Swift compiler required')


def test_recording_categories_are_independent_from_legacy_review(tmp_path):
    fixture = tmp_path/'recordings.json'
    base = {'id':'one','path':'/synthetic.wav','title':'A recorded lesson','created_at':'2026-01-01T00:00:00Z'}
    fixture.write_text(json.dumps({'recordings':[
        {**base,'status':'NEEDS_REVIEW','transcription_status':'COMPLETED','can_transcribe':False,'audio_available':True,
         'warnings':[{'code':'PARTIAL_AUDIO','message':'Fermata manualmente'}],'blocking_reasons':[],'transcript_text':'/transcript.txt'},
        {**base,'id':'two','status':'NEEDS_REVIEW','transcription_status':'NOT_STARTED','can_transcribe':True,
         'warnings':[{'code':'UNASSIGNED','message':'Corso da associare'}],'blocking_reasons':[]},
        {**base,'id':'old','status':'NOT_STARTED'},
        {**base,'id':'bad','status':'WAITING','transcription_status':'WAITING','can_transcribe':False,'audio_available':False,
         'blocking_reasons':[{'code':'AUDIO_NOT_READY','message':'Audio ancora in salvataggio'}]}
    ]}))
    driver=tmp_path/'main.swift'
    driver.write_text('''import Foundation
let values = try JSONDecoder().decode(CaptureLibrary.self, from: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))).recordings
precondition(values.map { $0.isTranscribed } == [true,false,false,false])
precondition(values.map { $0.transcriptionAllowed } == [false,true,true,false])
precondition(values[0].warnings?.first?.message == "Fermata manualmente")
precondition(values[3].blockingReasons?.first?.message == "Audio ancora in salvataggio")
print("PASS")
''')
    binary=tmp_path/'recording-decoder'
    source=Path(__file__).parents[1]/'setup/Sources/MyNotesSetup/CaptureDashboard.swift'
    build=subprocess.run(['swiftc','-module-cache-path',str(tmp_path/'module-cache'),str(source),str(driver),'-o',str(binary)],capture_output=True,text=True)
    assert build.returncode==0,build.stderr
    run=subprocess.run([str(binary),str(fixture)],capture_output=True,text=True)
    assert run.returncode==0,run.stderr
    assert 'PASS' in run.stdout
