"""Installer cleanup changes launch registrations, never backup bundles."""
import importlib.util
import plistlib
from pathlib import Path
from types import SimpleNamespace

spec=importlib.util.spec_from_file_location('launch_cleanup',Path(__file__).parents[1]/'scripts/cleanup_launch_services.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def bundle(path, identifier):
    (path/'Contents').mkdir(parents=True)
    (path/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':identifier}))
    return path


def test_cleanup_preserves_selected_apps_and_backups(tmp_path):
    root=tmp_path/'app';active=bundle(root/'versions/new/MyNotes Capture.app',module.CAPTURE)
    (root/'current').symlink_to(active.parent,target_is_directory=True)
    old=bundle(root/'versions/old/MyNotes Capture.app',module.CAPTURE)
    main=bundle(tmp_path/'Applications/MyNotes.app',module.MAIN)
    nested=bundle(tmp_path/'build.app.disabled/Contents/Resources/MyNotes Capture.app',module.CAPTURE)
    missing=tmp_path/'deleted/MyNotes Capture.app'
    unrelated=bundle(tmp_path/'Other.app','example.unrelated')
    entries={str(p):i for p,i in [(active,module.CAPTURE),(root/'current/MyNotes Capture.app',module.CAPTURE),(old,module.CAPTURE),(nested,module.CAPTURE),(missing,module.CAPTURE),(main,module.MAIN),(unrelated,'example.unrelated')]}
    registry=tmp_path/'lsregister';registry.touch();calls=[]
    def run(args,**kwargs):
        calls.append(args)
        if args[1]=='-dump':return SimpleNamespace(stdout='\n'.join(f'path: {p} (0xabc)\nidentifier: {i}' for p,i in entries.items()))
        if args[1]=='-u':entries.pop(args[2],None)
        return SimpleNamespace(returncode=0)
    result=module.cleanup(root,main,registry=registry,run=run)
    assert result['removed']==4
    assert set(entries)=={str(active),str(main),str(unrelated)}
    assert old.is_dir() and nested.is_dir()
    assert [x[2] for x in calls if x[1]=='-f']==[str(active),str(main)]
    assert module.cleanup(root,main,registry=registry,run=run)['removed']==0


def test_missing_selected_capture_never_unregisters_other_copies(tmp_path):
    registry=tmp_path/'lsregister';registry.touch();calls=[]
    def run(args,**kwargs):
        calls.append(args);return SimpleNamespace(stdout='path: /kept/MyNotes Capture.app (0x123)\nidentifier: '+module.CAPTURE)
    assert module.cleanup(tmp_path,None,registry=registry,run=run)['selected']==0
    assert all(x[1]=='-dump' for x in calls)


def test_parser_does_not_attach_identifier_to_previous_record():
    assert list(module.registered_apps('path: /discard.app (0x1)\npath: /right.app (0x2)\nidentifier: example.right'))==[(Path('/right.app'),'example.right')]


def test_failed_unregistration_is_reported(tmp_path):
    import pytest
    active=bundle(tmp_path/'current/MyNotes Capture.app',module.CAPTURE)
    registry=tmp_path/'lsregister';registry.touch()
    def run(args,**kwargs):
        return SimpleNamespace(stdout='path: /stale/MyNotes Capture.app (0x1)\nidentifier: '+module.CAPTURE,returncode=1)
    with pytest.raises(RuntimeError,match='obsolete'):
        module.cleanup(tmp_path,None,registry=registry,run=run)
    assert active.is_dir()


def test_custom_installation_cli_does_not_touch_user_registry(tmp_path,monkeypatch,capsys):
    import sys
    monkeypatch.setattr(sys,'argv',['cleanup','--app-home',str(tmp_path)])
    monkeypatch.setattr(module,'refresh',lambda: (_ for _ in ()).throw(AssertionError('Unexpected registry write')))
    module.main()
    assert 'custom app home' in capsys.readouterr().out
