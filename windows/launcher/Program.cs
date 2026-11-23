using System.ComponentModel;
using System.Diagnostics;

var python = Path.Combine(AppContext.BaseDirectory, "python.exe");
if (!File.Exists(python))
{
    Console.Error.WriteLine("MyNotes Python runtime is missing. Repair the installation.");
    return 1;
}

var start = new ProcessStartInfo(python) { UseShellExecute = false };
foreach (var argument in new[] { "-I", "-X", "utf8", "-m", "mynotes" })
{
    start.ArgumentList.Add(argument);
}
foreach (var argument in args)
{
    start.ArgumentList.Add(argument);
}

try
{
    using var process = Process.Start(start);
    if (process is null)
    {
        Console.Error.WriteLine("MyNotes could not start its Python runtime.");
        return 1;
    }
    // The child shares the attached console and handles Ctrl+C itself.
    Console.CancelKeyPress += (_, interrupt) => interrupt.Cancel = true;
    process.WaitForExit();
    return process.ExitCode;
}
catch (Win32Exception error)
{
    Console.Error.WriteLine($"MyNotes could not start: {error.Message}");
    return 1;
}
