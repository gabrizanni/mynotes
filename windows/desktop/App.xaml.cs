using Microsoft.UI.Xaml;

namespace MyNotes.Desktop;

public partial class App : Application
{
    private Window? window;
    private Mutex? deploymentMutex;

    public App()
    {
        InitializeComponent();
    }

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        // Inno Setup checks this process-lifetime handle before update or uninstall.
        deploymentMutex = new Mutex(initiallyOwned: false, name: "MyNotes.Windows.Desktop.Running");
        window = new MainWindow();
        window.Activate();
    }
}
