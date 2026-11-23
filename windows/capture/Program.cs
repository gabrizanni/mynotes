using Microsoft.Win32;

namespace MyNotes.Capture;

internal static class Program
{
    private static async Task<int> Main(string[] arguments)
    {
        if (arguments.Length != 4 || arguments[0] != "--workspace" || arguments[2] != "--capture-home"
            || !Path.IsPathFullyQualified(arguments[1]) || !Path.IsPathFullyQualified(arguments[3]))
        {
            Console.Error.WriteLine("Expected: MyNotes.Capture.exe --workspace PATH --capture-home PATH");
            return 2;
        }

        try
        {
            using var exit = new CancellationTokenSource();
            using var worker = new Worker(arguments[1], arguments[3]);
            Console.CancelKeyPress += (_, change) =>
            {
                change.Cancel = true;
                exit.Cancel();
            };
            SystemEvents.PowerModeChanged += worker.PowerChanged;
            try
            {
                await worker.RunAsync(exit.Token);
                return 0;
            }
            finally
            {
                SystemEvents.PowerModeChanged -= worker.PowerChanged;
            }
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or System.Text.Json.JsonException or ArgumentException)
        {
            Console.Error.WriteLine(error.Message);
            return 1;
        }
    }
}
