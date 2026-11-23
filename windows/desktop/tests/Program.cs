using System.Text.Json;
using MyNotes.Desktop;

// Launch a child process to exercise real argument and request-file handling.
// No application dependencies, provider credentials or interactive inputs are used.
if (args is ["-I", "-X", "utf8", "-m", "mynotes", .. var childArgs])
{
    if (childArgs.Contains("wait"))
        await Task.Delay(TimeSpan.FromSeconds(30));
    var requestIndex = Array.IndexOf(childArgs, "--file");
    var childRequest = requestIndex >= 0
        ? JsonDocument.Parse(File.ReadAllBytes(childArgs[requestIndex + 1])).RootElement.Clone()
        : (JsonElement?)null;
    var status = childArgs.Contains("reject") ? "REJECTED" : childArgs.Contains("defer") ? "DEFERRED" : "OK";
    Console.WriteLine(JsonSerializer.Serialize(new
    {
        status,
        arguments = childArgs,
        workspace = Environment.GetEnvironmentVariable(Backend.WorkspaceOverride),
        request = childRequest,
    }));
    return childArgs.Contains("reject") ? 2 : 0;
}

using var workspace = new TemporaryWorkspace();
var engine = new Backend(Environment.ProcessPath!, workspace.Path);
var literal = "Lezione è \"A & B\" con apostrofo ' e spazi";
var command = engine.CreateProcess(["test", "--title", literal]);
Check(!command.UseShellExecute && command.RedirectStandardOutput, "Data commands bypass the shell and redirect stdout.");
Check(command.ArgumentList.ToArray() is ["-I", "-X", "utf8", "-m", "mynotes", "test", "--title", var value, "--workspace", var path]
    && value == literal && path == workspace.Path, "Arguments preserve UTF-8 and their original boundaries.");
var chat = engine.CreateProcess(["chat", "--provider", "claude"], console: true);
Check(!chat.UseShellExecute && !chat.RedirectStandardOutput && !chat.CreateNoWindow, "Chat keeps an interactive child console.");
var json = (await engine.JsonAsync(["test", "--title", literal], CancellationToken.None)).RequireSuccess();
Check(json.Text("workspace") == workspace.Path && json.Strings("arguments").Contains(literal), "Environment and real child argv round-trip.");
Check(json.Strings("arguments").Contains("--json"), "JSON commands request structured output.");
var noWorkspaceArgument = (await engine.JsonAsync(["models"], CancellationToken.None, passWorkspace: false)).RequireSuccess();
Check(!noWorkspaceArgument.Strings("arguments").Contains("--workspace") && noWorkspaceArgument.Text("workspace") == workspace.Path,
    "Catalog and renderer use the private workspace environment without passing an unsupported argument.");
var request = new { text = literal, names = new[] { "Uno", "Due" } };
var fileResult = (await engine.RequestAsync(["file-test"], request, CancellationToken.None)).RequireSuccess();
Check(fileResult.Field("request").Text("text") == literal, "The child reads the private UTF-8 request JSON.");
var requestedPath = fileResult.Strings("arguments").SkipWhile(arg => arg != "--file").Skip(1).First();
Check(Path.GetRelativePath(workspace.Path, requestedPath).StartsWith(Path.Combine("state", "desktop-requests"), StringComparison.Ordinal),
    "Request files are located inside workspace state.");
Check(!File.Exists(requestedPath), "Handled requests are removed.");
Check((await engine.RunAsync(["defer"], CancellationToken.None)).Pending, "Deferred work is reported as pending.");
var rejected = await engine.RunAsync(["reject"], CancellationToken.None);
try
{
    rejected.RequireSuccess();
    throw new InvalidOperationException("Rejected work cannot report success.");
}
catch (BackendException) { }
Check(new BackendResult(0, "not JSON").Data.ValueKind == JsonValueKind.Undefined, "Malformed responses cannot be read as JSON.");
using var cancellation = new CancellationTokenSource(TimeSpan.FromMilliseconds(500));
try
{
    await engine.RunAsync(["wait"], cancellation.Token);
    throw new InvalidOperationException("Cancellation must stop in-flight work.");
}
catch (OperationCanceledException) { }
Console.WriteLine("Desktop bridge tests passed.");
return 0;

static void Check(bool result, string message)
{
    if (!result)
        throw new InvalidOperationException(message);
}

internal sealed class TemporaryWorkspace : IDisposable
{
    public string Path { get; } = Directory.CreateTempSubdirectory("MyNotes bridge à ").FullName;
    public void Dispose() => Directory.Delete(Path, recursive: true);
}
