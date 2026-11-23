using Microsoft.UI.Xaml;

namespace MyNotes.Desktop;

public partial class App : Application
{
    private Window? window;
    private Mutex? deploymentMutex;

    public App()
    {
        TraceStartup("Constructing the application.");
        UnhandledException += (_, args) => TraceStartup(args.Exception.ToString());
        try { InitializeComponent(); }
        catch (Exception error)
        {
            TraceStartup(error.ToString());
            throw;
        }
    }

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        TraceStartup("Constructing the main window.");
        try
        {
            // Inno Setup checks this process-lifetime handle before update or uninstall.
            deploymentMutex = new Mutex(initiallyOwned: false, name: "MyNotes.Windows.Desktop.Running");
            window = new MainWindow();
            window.Activate();
            TraceStartup("Activated the main window.");
        }
        catch (Exception error)
        {
            TraceStartup(error.ToString());
            throw;
        }
    }

    private static void TraceStartup(string message)
    {
        var location = Environment.GetEnvironmentVariable("MYNOTES_DESKTOP_TEST_TRACE");
        if (string.IsNullOrWhiteSpace(location) || !Path.IsPathFullyQualified(location))
            return;
        try { File.AppendAllText(location, message + Environment.NewLine); }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }
}
