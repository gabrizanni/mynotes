"""Round-trip native Warp configuration and execute paths with shell metacharacters."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

import pytest


@pytest.mark.skipif(sys.platform!='darwin' or not shutil.which('swiftc'),reason='native macOS helper')
def test_warp_configuration_preserves_paths_without_shell_injection(tmp_path):
    source=Path(__file__).parents[1]/'setup/Sources/MyNotesSetup/ChatTerminal.swift'
    main=tmp_path/'main.swift'
    main.write_text('import Foundation\nlet config = try ChatTerminal.configuration(script: URL(fileURLWithPath: CommandLine.arguments[1]), directory: URL(fileURLWithPath: CommandLine.arguments[2]))\nFileHandle.standardOutput.write(config.data)\n')
    binary=tmp_path/'config-test'
    subprocess.run(['swiftc','-module-cache-path',str(tmp_path/'modules'),str(source),str(main),'-o',str(binary)],check=True,capture_output=True,text=True)
    folder=tmp_path/'Folder "quotes" and $dollars';folder.mkdir()
    script=folder/"launch ' $(touch SHOULD_NOT_EXIST).command"
    script.write_text('printf verified > result.txt\n')
    result=subprocess.run([str(binary),str(script),str(folder)],check=True,capture_output=True,text=True)
    config=tomllib.loads(result.stdout);pane=config['panes'][0]
    assert pane['directory']==str(folder) and pane['type']=='terminal'
    subprocess.run(['/bin/zsh','-c',pane['commands'][0]],cwd=folder,check=True,env=dict(os.environ))
    assert (folder/'result.txt').read_text()=='verified'
    assert not (folder/'SHOULD_NOT_EXIST').exists()
