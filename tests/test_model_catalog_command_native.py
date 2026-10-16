"""Actual Swift async transport: stderr separation, timeout and cancellation."""
import os
import shutil
import subprocess
import sys
from pathlib import Path
import pytest

pytestmark=pytest.mark.skipif(sys.platform!='darwin' or not shutil.which('swiftc'),reason='macOS Swift required')

def test_native_catalog_command_separates_stderr_and_cancels_owned_process(tmp_path):
    cli=tmp_path/'fake-cli';cli.write_text('#!'+sys.executable+'\n'+'''import sys,time,os,json
if sys.argv[1]=='ok':
 print('a diagnostic warning',file=sys.stderr);print(json.dumps({'new_model':'never-hardcoded','effort':'future_effort','runtime':os.environ['MYNOTES_HOME']}))
else: time.sleep(30)
''');cli.chmod(0o700)
    driver=tmp_path/'Driver.swift';driver.write_text('''import Foundation
@main struct Driver {
 static func main() async throws {
  let executable = URL(fileURLWithPath: CommandLine.arguments[1])
  let data = try await ModelCatalogCommand.read(executable, arguments: ["ok"])
  let value = try JSONSerialization.jsonObject(with: data) as! [String:String]
  precondition(value["effort"] == "future_effort")
  precondition(value["runtime"] == "fixture-runtime", "The subprocess must inherit the app runtime environment")
  do { _ = try await ModelCatalogCommand.read(executable, arguments: ["blocked"], timeout: 0.2); preconditionFailure("timeout missing") }
  catch is CancellationError { }
  let task = Task { try await ModelCatalogCommand.read(executable, arguments: ["blocked"], timeout: 10) }
  try await Task.sleep(for: .milliseconds(100));task.cancel()
  do { _ = try await task.value; preconditionFailure("cancellation missing") }
  catch is CancellationError { }
  print("PASS")
 }
}
''')
    source=Path(__file__).parents[1]/'setup/Sources/MyNotesSetup/ModelCatalogCommand.swift';binary=tmp_path/'native-test'
    build=subprocess.run(['swiftc','-parse-as-library','-swift-version','5','-target','arm64-apple-macosx14.0','-module-cache-path',str(tmp_path/'cache'),str(source),str(driver),'-o',str(binary)],capture_output=True,text=True)
    assert build.returncode==0,build.stderr
    result=subprocess.run([str(binary),str(cli)],env={**os.environ,'MYNOTES_HOME':'fixture-runtime'},capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    assert 'PASS' in result.stdout
