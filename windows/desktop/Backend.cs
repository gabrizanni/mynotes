using System.Diagnostics;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace MyNotes.Desktop;

internal sealed class Backend(string python, string workspace)
{
    public const string WorkspaceOverride = "MYNOTES_WORKSPACE";
    public string Workspace { get; } = Path.GetFullPath(workspace);
    public string Python { get; } = Path.GetFullPath(python);

    public static Backend Discover(string applicationDirectory)
    {
        var overridePath = Environment.GetEnvironmentVariable("MYNOTES_PYTHON");
        if (!string.IsNullOrWhiteSpace(overridePath) && !Path.IsPathFullyQualified(overridePath))
            throw new InvalidOperationException("MYNOTES_PYTHON deve indicare un percorso assoluto.");

        var candidates = new[]
        {
            Path.GetFullPath(Path.Combine(applicationDirectory, "..", "runtime", "python.exe")),
            Path.GetFullPath(Path.Combine(applicationDirectory, "runtime", "python.exe")),
        };
        var workspace = Environment.GetEnvironmentVariable(WorkspaceOverride);
        if (string.IsNullOrWhiteSpace(workspace))
            workspace = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "MyNotes", "workspace");
        if (!Path.IsPathFullyQualified(workspace))
            throw new InvalidOperationException("MYNOTES_WORKSPACE deve indicare un percorso assoluto.");

        return new Backend(overridePath ?? candidates.FirstOrDefault(File.Exists) ?? candidates[0], workspace);
    }

    internal ProcessStartInfo CreateProcess(IEnumerable<string> args, bool console = false, bool passWorkspace = true)
    {
        var startInfo = new ProcessStartInfo
        {
            FileName = Python,
            WorkingDirectory = Directory.Exists(Workspace) ? Workspace : AppContext.BaseDirectory,
            UseShellExecute = false,
            RedirectStandardOutput = !console,
            RedirectStandardError = !console,
            CreateNoWindow = !console,
        };
        if (!console)
        {
            startInfo.StandardOutputEncoding = Encoding.UTF8;
            startInfo.StandardErrorEncoding = Encoding.UTF8;
        }
        startInfo.Environment[WorkspaceOverride] = Workspace;
        var arguments = new List<string>(args);
        if (passWorkspace)
        {
            var separator = arguments.IndexOf("--");
            arguments.InsertRange(separator < 0 ? arguments.Count : separator, ["--workspace", Workspace]);
        }
        foreach (var arg in new[] { "-I", "-X", "utf8", "-m", "mynotes" }.Concat(arguments))
            startInfo.ArgumentList.Add(arg);
        return startInfo;
    }

    public void OpenConsole(params string[] options)
    {
        EnsurePythonExists();
        using var process = Process.Start(CreateProcess(options, console: true));
        if (process is null)
            throw new BackendException("Impossibile aprire il terminale di MyNotes.");
    }

    public async Task<BackendResult> RunAsync(IEnumerable<string> args, CancellationToken cancellationToken, bool passWorkspace = true)
    {
        EnsurePythonExists();
        using var process = Process.Start(CreateProcess(args, passWorkspace: passWorkspace))
            ?? throw new BackendException("Impossibile avviare il motore di MyNotes.");
        var standardOutput = process.StandardOutput.ReadToEndAsync(cancellationToken);
        var standardError = process.StandardError.ReadToEndAsync(cancellationToken);
        try
        {
            await Task.WhenAll(process.WaitForExitAsync(cancellationToken), standardOutput, standardError);
        }
        catch (OperationCanceledException)
        {
            try { process.Kill(entireProcessTree: true); }
            catch (InvalidOperationException) { }
            catch (System.ComponentModel.Win32Exception) { }
            throw;
        }

        return new BackendResult(process.ExitCode, standardOutput.Result);
    }

    public Task<BackendResult> JsonAsync(IEnumerable<string> args, CancellationToken cancellationToken, bool passWorkspace = true) =>
        RunAsync([.. args, "--json"], cancellationToken, passWorkspace);

    public async Task<BackendResult> RequestAsync(IEnumerable<string> args, object payload, CancellationToken cancellationToken, bool json = true)
    {
        // Captured paths and scheduling data stay under the private workspace.
        var directory = Path.Combine(Workspace, "state", "desktop-requests");
        Directory.CreateDirectory(directory);
        var path = Path.Combine(directory, Convert.ToHexString(RandomNumberGenerator.GetBytes(16)) + ".json");
        try
        {
            await using (var request = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                await JsonSerializer.SerializeAsync(request, payload, cancellationToken: cancellationToken);
            }
            var arguments = new List<string>(args) { "--file", path };
            return json ? await JsonAsync(arguments, cancellationToken) : await RunAsync(arguments, cancellationToken);
        }
        finally
        {
            try { File.Delete(path); }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }
    }

    private void EnsurePythonExists()
    {
        if (!File.Exists(Python))
            throw new BackendException("Il motore Python di MyNotes non è disponibile. Controlla l’installazione.");
    }
}

internal sealed class BackendResult(int exitCode, string output)
{
    public int ExitCode { get; } = exitCode;
    public JsonElement Data { get; } = ReadJson(output);

    public bool Pending => Data.Text("status").ToUpperInvariant() is "PENDING" or "DEFERRED";

    public JsonElement RequireSuccess()
    {
        var status = Data.Text("status").ToUpperInvariant();
        if (ExitCode != 0 || status is "ERROR" or "BLOCKED" or "FAILED" or "CONFLICT" or "UNAVAILABLE" or "REJECTED" or "SYNC_FAILED" or "INTERRUPTED")
            throw new BackendException(status.Length > 0
                ? $"MyNotes ha restituito lo stato {status} (codice {ExitCode})."
                : $"MyNotes ha restituito un errore (codice {ExitCode}).");
        return Data;
    }

    public JsonElement RequireJson()
    {
        if (Data.ValueKind != JsonValueKind.Object)
            throw new BackendException($"Il motore MyNotes ha restituito una risposta illeggibile (codice {ExitCode}).");
        return Data;
    }

    private static JsonElement ReadJson(string text)
    {
        try
        {
            using var document = JsonDocument.Parse(text);
            return document.RootElement.Clone();
        }
        catch (JsonException)
        {
            return default;
        }
    }
}

internal sealed class BackendException(string message) : Exception(message) { }

internal static class JsonValue
{
    public static JsonElement Field(this JsonElement value, string field) =>
        value.ValueKind == JsonValueKind.Object && value.TryGetProperty(field, out var found) ? found : default;

    public static string Text(this JsonElement value, string field, string fallback = "")
    {
        var item = value.Field(field);
        return item.ValueKind switch
        {
            JsonValueKind.String => item.GetString() ?? fallback,
            JsonValueKind.Number => item.GetRawText(),
            _ => fallback,
        };
    }

    public static bool Flag(this JsonElement value, string field) => value.Field(field).ValueKind == JsonValueKind.True;

    public static IEnumerable<JsonElement> Items(this JsonElement value, string field)
    {
        var member = value.Field(field);
        if (member.ValueKind != JsonValueKind.Array)
            yield break;
        foreach (var item in member.EnumerateArray())
            yield return item;
    }

    public static IEnumerable<KeyValuePair<string, JsonElement>> Members(this JsonElement value)
    {
        if (value.ValueKind != JsonValueKind.Object)
            yield break;
        foreach (var item in value.EnumerateObject())
            yield return new(item.Name, item.Value);
    }

    public static IEnumerable<string> Strings(this JsonElement value, string field) =>
        value.Items(field).Where(item => item.ValueKind == JsonValueKind.String)
            .Select(item => item.GetString()!);
}
