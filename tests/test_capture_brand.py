"""Capture's icon is a complete, public bundle resource."""
from pathlib import Path
import importlib.util
import plistlib
import struct

from PIL import Image

ROOT=Path(__file__).parents[1]


def test_capture_logo_and_icns_match_bundle_reference_and_public_manifest():
    resources=ROOT/'capture/Resources'
    info=plistlib.loads((resources/'Info.plist').read_bytes())
    assert info['CFBundleIconFile']=='MyNotesCapture.icns'
    with Image.open(resources/'MyNotesCaptureLogo.png') as image:
        assert image.size==(1024,1024) and image.mode=='RGBA'
        assert image.getpixel((0,0))[3]==0
        assert image.getpixel((512,512))[3]>=250
    with Image.open(resources/info['CFBundleIconFile']) as icon:
        sizes={size[0]*size[2] for size in icon.info['sizes']}
        assert {32,64,128,256,512,1024} <= sizes
    # iconutil's 16px ic04 representation is not listed by Pillow's ICNS reader.
    payload=(resources/info['CFBundleIconFile']).read_bytes()
    position=8; chunks=set()
    while position<len(payload):
        kind,length=struct.unpack('>4sI',payload[position:position+8])
        assert length>=8 and position+length<=len(payload)
        chunks.add(kind); position+=length
    assert position==len(payload) and b'ic04' in chunks
    spec=importlib.util.spec_from_file_location('capture_public_check',ROOT/'scripts/check_public_tree.py')
    checker=importlib.util.module_from_spec(spec); spec.loader.exec_module(checker)
    paths=checker.public_paths(ROOT)
    assert resources/'MyNotesCaptureLogo.png' in paths
    assert resources/info['CFBundleIconFile'] in paths
    assert not checker.validate(ROOT)
