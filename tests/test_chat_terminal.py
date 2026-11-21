"""External chat targets Terminal explicitly and reports invalid launch files."""
from pathlib import Path
import shutil
import subprocess
import sys
import pytest


@pytest.mark.skipif(sys.platform != 'darwin' or not shutil.which('swiftc'), reason='native macOS helper')
def test_terminal_target_and_invalid_script_fail_before_opening_app(tmp_path):
    source=Path(__file__).parents[1]/'setup/Sources/MyNotesSetup/ChatTerminal.swift'
    main=tmp_path/'main.swift'
    main.write_text('''import AppKit
import Foundation
precondition(ChatTerminal.bundleIdentifier == "com.apple.Terminal")
Task { @MainActor in
    do {
        try await ChatTerminal.open(script: URL(fileURLWithPath: CommandLine.arguments[1]), directory: URL(fileURLWithPath: CommandLine.arguments[2]))
        exit(1)
    } catch { print("PASS rejected invalid launch file"); exit(0) }
}
dispatchMain()
''')
    binary=tmp_path/'terminal-test'
    subprocess.run(['swiftc','-module-cache-path',str(tmp_path/'modules'),str(source),str(main),'-o',str(binary)],check=True,capture_output=True,text=True)
    folder=tmp_path/'Folder "quotes" and $dollars';folder.mkdir()
    missing=folder/"launch ' $(touch SHOULD_NOT_EXIST).command"
    result=subprocess.run([str(binary),str(missing),str(folder)],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr
    assert 'PASS' in result.stdout
    assert not (folder/'SHOULD_NOT_EXIST').exists()
