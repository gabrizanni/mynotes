using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using System.Diagnostics;
using System.Globalization;
using System.Text.Json;
using Windows.Graphics;
using Windows.Storage.Pickers;
using WinRT.Interop;

namespace MyNotes.Desktop;

public sealed partial class MainWindow : Window
{
    private readonly CancellationTokenSource lifetime = new();
    private readonly string queueSession = Guid.NewGuid().ToString("N");
    private readonly Microsoft.UI.Dispatching.DispatcherQueueTimer queueTimer;
    private Backend? backend;
    private bool running;
    private bool heartbeatRunning;
    private string section = "library";
    private string libraryQuery = "";
    private string libraryCourse = "";
    private string jobCourse = "";
    private bool includeHidden;
    private DateOnly calendarDate = DateOnly.FromDateTime(DateTime.Today);
    private CancellationToken Token => lifetime.Token;
    private Backend Engine => backend ?? throw new BackendException("Il motore di MyNotes non è configurato.");

    public MainWindow()
    {
        Title = "MyNotes";
        AppWindow.Resize(new SizeInt32(1100, 780));
        var iconPath = Path.Combine(AppContext.BaseDirectory, "MyNotes.ico");
        if (File.Exists(iconPath))
            AppWindow.SetIcon(iconPath);

        queueTimer = DispatcherQueue.CreateTimer();
        queueTimer.Interval = TimeSpan.FromSeconds(5);
        queueTimer.Tick += Heartbeat;
        Root.Loaded += Start;
        Closed += Stop;
        BuildLayout();
    }

    private async void Start(object sender, RoutedEventArgs args)
    {
        Root.Loaded -= Start;
        App.TraceStartup("Loaded the main window content.");
        await WithBusyAsync(async () =>
        {
            try { backend = Backend.Discover(AppContext.BaseDirectory); }
            catch (InvalidOperationException error) { throw new BackendException(error.Message); }
            if (!File.Exists(Path.Combine(Engine.Workspace, "config", "settings.json"))
                || !File.Exists(Path.Combine(Engine.Workspace, "state", "mynotes.sqlite3")))
                (await Engine.RunAsync(["init"], Token)).RequireSuccess();
            Navigation.SelectedItem = LibraryNavigation;
            queueTimer.Start();
            Heartbeat(queueTimer, null);
            await DisplayPageAsync();
            App.TraceStartup("Rendered the library.");
        });
    }

    private async void Heartbeat(Microsoft.UI.Dispatching.DispatcherQueueTimer sender, object? args)
    {
        if (heartbeatRunning || Token.IsCancellationRequested)
            return;
        heartbeatRunning = true;
        try
        {
            await Engine.RunAsync(["transcription-queue", "heartbeat", "--session", queueSession], Token);
        }
        catch (OperationCanceledException) { }
        catch (BackendException) { }
        catch (System.ComponentModel.Win32Exception) { }
        finally { heartbeatRunning = false; }
    }

    private async void Stop(object sender, WindowEventArgs args)
    {
        queueTimer.Stop();
        lifetime.Cancel();
        if (backend is null)
            return;
        try
        {
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(3));
            await backend.RunAsync(["transcription-queue", "close", "--session", queueSession], timeout.Token);
        }
        catch (OperationCanceledException) { }
        catch (BackendException) { }
        catch (System.ComponentModel.Win32Exception) { }
    }

    private async void NavigationChanged(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        if (args.SelectedItem is NavigationViewItem { Tag: string choice } && choice != section)
        {
            section = choice;
            if (!running)
                await WithBusyAsync(DisplayPageAsync);
        }
    }

    private async void NavigationInvoked(NavigationView sender, NavigationViewItemInvokedEventArgs args)
    {
        if (args.InvokedItemContainer == ChatNavigation)
            await WithBusyAsync(LaunchChatAsync);
    }

    private async void RefreshClicked(object sender, RoutedEventArgs args) => await WithBusyAsync(DisplayPageAsync);

    private async Task WithBusyAsync(Func<Task> action)
    {
        if (running || Token.IsCancellationRequested)
            return;
        running = true;
        Busy.Visibility = Visibility.Visible;
        RefreshButton.IsEnabled = false;
        Notice.IsOpen = false;
        var selectedSection = section;
        try
        {
            await action();
        }
        catch (BackendException error)
        {
            Announce(error.Message, InfoBarSeverity.Error);
        }
        catch (OperationCanceledException) { }
        catch (IOException)
        {
            Announce("Impossibile leggere o scrivere il file. Controlla il percorso e i permessi.", InfoBarSeverity.Error);
        }
        catch (UnauthorizedAccessException)
        {
            Announce("L’accesso al file o alla cartella è stato negato.", InfoBarSeverity.Error);
        }
        catch (System.ComponentModel.Win32Exception)
        {
            Announce("Windows non è riuscito ad avviare il programma richiesto.", InfoBarSeverity.Error);
        }
        finally
        {
            running = false;
            if (!Token.IsCancellationRequested)
            {
                Busy.Visibility = Visibility.Collapsed;
                RefreshButton.IsEnabled = true;
                if (selectedSection != section)
                    _ = WithBusyAsync(DisplayPageAsync);
            }
        }
    }

    private async Task DisplayPageAsync()
    {
        Page.Children.Clear();
        PageScroll.ChangeView(null, 0, null, disableAnimation: true);
        PageTitle.Text = section switch
        {
            "calendar" => "Calendario",
            "activity" => "Attività",
            "courses" => "Corsi",
            "settings" => "Impostazioni",
            _ => "Libreria",
        };
        await (section switch
        {
            "calendar" => CalendarAsync(),
            "activity" => ActivityAsync(),
            "courses" => CoursesAsync(),
            "settings" => SettingsAsync(),
            _ => LibraryAsync(),
        });
    }

    private static TextBlock Label(string text, bool heading = false)
    {
        var label = new TextBlock { Text = text, TextWrapping = TextWrapping.Wrap };
        if (heading)
        {
            label.Style = (Style)Application.Current.Resources["SubtitleTextBlockStyle"];
            AutomationProperties.SetHeadingLevel(label, Microsoft.UI.Xaml.Automation.Peers.AutomationHeadingLevel.Level2);
        }
        return label;
    }

    private static TextBox Input(string header, string id, string text = "")
    {
        var input = new TextBox { Header = header, Text = text };
        AutomationProperties.SetAutomationId(input, id);
        return input;
    }

    private static ComboBox Options(string header, string id, IEnumerable<string> values, string? selected = null)
    {
        var box = new ComboBox { Header = header, HorizontalAlignment = HorizontalAlignment.Stretch };
        foreach (var value in values)
            box.Items.Add(value);
        box.SelectedItem = selected ?? box.Items.OfType<string>().FirstOrDefault();
        AutomationProperties.SetAutomationId(box, id);
        return box;
    }

    private static string Selected(ComboBox input) => input.SelectedItem as string ?? "";

    private Button ButtonFor(string text, string id, Func<Task> action, bool primary = false)
    {
        var button = new Button { Content = text };
        if (primary)
            button.Style = (Style)Application.Current.Resources["AccentButtonStyle"];
        AutomationProperties.SetAutomationId(button, id);
        button.Click += async (_, _) => await WithBusyAsync(action);
        return button;
    }

    private static StackPanel Column(params UIElement[] elements)
    {
        var stack = new StackPanel { Spacing = 10 };
        foreach (var element in elements)
            stack.Children.Add(element);
        return stack;
    }

    private static ItemsControl Actions(params UIElement[] elements)
    {
        // A wrapping horizontal layout keeps actions visible at narrow window widths.
        var actions = new ItemsControl
        {
            ItemsPanel = (ItemsPanelTemplate)Microsoft.UI.Xaml.Markup.XamlReader.Load(
                "<ItemsPanelTemplate xmlns='http://schemas.microsoft.com/winfx/2006/xaml/presentation'>" +
                "<ItemsWrapGrid Orientation='Horizontal'/></ItemsPanelTemplate>"),
        };
        foreach (var element in elements)
        {
            if (element is FrameworkElement control)
                control.Margin = new Thickness(0, 0, 8, 6);
            actions.Items.Add(element);
        }
        return actions;
    }

    private static Border Card(string title, params UIElement[] children)
    {
        var card = new Border
        {
            Background = (Brush)Application.Current.Resources["CardBackgroundFillColorDefaultBrush"],
            BorderBrush = (Brush)Application.Current.Resources["CardStrokeColorDefaultBrush"],
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(8),
            Padding = new Thickness(16),
            Child = Column([Label(title, heading: true), .. children]),
        };
        return card;
    }

    private static string Join(params string[] parts) => string.Join(" · ", parts.Where(value => !string.IsNullOrWhiteSpace(value)));
    private static string DisplayDate(string timestamp) => DateTimeOffset.TryParse(timestamp, out var date)
        ? date.ToLocalTime().ToString("g", CultureInfo.CurrentCulture)
        : timestamp;

    private void Announce(string message, InfoBarSeverity severity = InfoBarSeverity.Success)
    {
        if (severity == InfoBarSeverity.Error)
            App.TraceStartup(message);
        Notice.Message = message;
        Notice.Severity = severity;
        Notice.IsOpen = true;
    }

    private async Task<JsonElement> ReadAsync(params string[] command)
    {
        var result = await Engine.JsonAsync(command, Token);
        return Data(result);
    }

    private static JsonElement Data(BackendResult result)
    {
        result.RequireSuccess();
        return result.RequireJson();
    }

    private async Task ExecuteAsync(string done, params string[] command) =>
        await CompleteAsync(await Engine.JsonAsync(command, Token), done);

    private async Task RequestAsync(string done, string[] command, object data) =>
        await CompleteAsync(await Engine.RequestAsync(command, data, Token), done);

    private async Task CompleteAsync(BackendResult result, string done)
    {
        Data(result);
        await DisplayPageAsync();
        if (result.Pending)
            Announce("Richiesta in attesa di conferma. Aggiorna la schermata per verificarne lo stato.", InfoBarSeverity.Informational);
        else
            Announce(done);
    }

    private async Task<bool> DialogAsync(string title, object content, string action = "Salva")
    {
        var dialog = new ContentDialog
        {
            XamlRoot = Root.XamlRoot,
            Title = title,
            Content = new ScrollViewer { Content = content, MaxHeight = 520 },
            PrimaryButtonText = action,
            CloseButtonText = action.Length == 0 ? "Chiudi" : "Annulla",
            DefaultButton = action.Length == 0 ? ContentDialogButton.Close : ContentDialogButton.Primary,
        };
        return await dialog.ShowAsync() == ContentDialogResult.Primary;
    }

    private Task<bool> ConfirmAsync(string title, string message, string action) =>
        DialogAsync(title, Label(message), action);

    private async Task<string?> PickFolderAsync()
    {
        var picker = new FolderPicker { SuggestedStartLocation = PickerLocationId.DocumentsLibrary };
        picker.FileTypeFilter.Add("*");
        InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(this));
        return (await picker.PickSingleFolderAsync())?.Path;
    }

    private async Task<string[]> PickFilesAsync(string[] extensions, bool multiple)
    {
        var picker = new FileOpenPicker { SuggestedStartLocation = PickerLocationId.DocumentsLibrary };
        foreach (var extension in extensions)
            picker.FileTypeFilter.Add(extension);
        InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(this));
        if (multiple)
            return (await picker.PickMultipleFilesAsync()).Select(file => file.Path).ToArray();
        var path = (await picker.PickSingleFileAsync())?.Path;
        return path is null ? [] : [path];
    }

    private Task OpenLocalAsync(string path)
    {
        var allowed = AudioExtensions.Concat([".txt", ".md", ".docx", ".pdf", ".pptx", ".xlsx"]);
        if (!Path.IsPathFullyQualified(path) || (!Directory.Exists(path)
            && (!File.Exists(path) || !allowed.Contains(Path.GetExtension(path), StringComparer.OrdinalIgnoreCase))))
            throw new BackendException("Il file o la cartella non è disponibile.");
        using var process = Process.Start(new ProcessStartInfo { FileName = path, UseShellExecute = true });
        return Task.CompletedTask;
    }

    private async Task LaunchChatAsync()
    {
        var config = await ReadAsync("config", "show");
        if (config.Field("providers").Text("default").Length == 0)
        {
            Navigation.SelectedItem = SettingsNavigation;
            return;
        }
        Engine.OpenConsole("chat");
        Announce("Terminale MyNotes avviato.", InfoBarSeverity.Informational);
    }
}
