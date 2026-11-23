using Microsoft.UI.Text;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;

namespace MyNotes.Desktop;

public sealed partial class MainWindow
{
    private readonly NavigationView Navigation = new()
    {
        PaneDisplayMode = NavigationViewPaneDisplayMode.Left,
        IsBackButtonVisible = NavigationViewBackButtonVisible.Collapsed,
        IsSettingsVisible = false,
        IsTabStop = false,
    };
    private readonly NavigationViewItem LibraryNavigation = NavigationItem("Libreria", "library", "NavLibrary", Symbol.Library);
    private readonly NavigationViewItem SettingsNavigation = NavigationItem("Impostazioni", "settings", "NavSettings", Symbol.Setting);
    private readonly NavigationViewItem ChatNavigation = NavigationItem("Apri agente", "chat", "NavChat", Symbol.Message);
    private readonly Grid Root = new() { Padding = new Thickness(28, 20, 28, 0), RowSpacing = 12 };
    private readonly TextBlock PageTitle = new() { Text = "Libreria", FontSize = 28, FontWeight = FontWeights.SemiBold };
    private readonly Button RefreshButton = new() { Content = "Aggiorna", VerticalAlignment = VerticalAlignment.Center };
    private readonly ProgressBar Busy = new() { IsIndeterminate = true, Visibility = Visibility.Collapsed };
    private readonly InfoBar Notice = new() { IsOpen = false };
    private readonly ScrollViewer PageScroll = new() { VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
    private readonly StackPanel Page = new() { Padding = new Thickness(0, 0, 12, 24), Spacing = 12 };

    private static NavigationViewItem NavigationItem(string label, string tag, string id, Symbol symbol)
    {
        var item = new NavigationViewItem { Content = label, Tag = tag, Icon = new SymbolIcon(symbol) };
        AutomationProperties.SetAutomationId(item, id);
        return item;
    }

    private void BuildLayout()
    {
        Navigation.PaneHeader = new TextBlock
        {
            Text = "MyNotes", Margin = new Thickness(16, 8, 16, 8), FontSize = 20, FontWeight = FontWeights.SemiBold,
        };
        Navigation.MenuItems.Add(LibraryNavigation);
        Navigation.MenuItems.Add(NavigationItem("Calendario", "calendar", "NavCalendar", Symbol.Calendar));
        Navigation.MenuItems.Add(NavigationItem("Attività", "activity", "NavActivity", Symbol.Clock));
        Navigation.MenuItems.Add(NavigationItem("Corsi", "courses", "NavCourses", Symbol.Folder));
        ChatNavigation.SelectsOnInvoked = false;
        Navigation.FooterMenuItems.Add(ChatNavigation);
        Navigation.FooterMenuItems.Add(SettingsNavigation);
        Navigation.SelectionChanged += NavigationChanged;
        Navigation.ItemInvoked += NavigationInvoked;

        var heading = new Grid { ColumnSpacing = 12 };
        heading.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        heading.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        AutomationProperties.SetHeadingLevel(PageTitle, AutomationHeadingLevel.Level1);
        AutomationProperties.SetAutomationId(RefreshButton, "Refresh");
        RefreshButton.Click += RefreshClicked;
        Grid.SetColumn(RefreshButton, 1);
        heading.Children.Add(PageTitle);
        heading.Children.Add(RefreshButton);

        var feedback = new StackPanel { Spacing = 6 };
        AutomationProperties.SetAutomationId(Notice, "Notice");
        feedback.Children.Add(Busy);
        feedback.Children.Add(Notice);
        PageScroll.Content = Page;
        Root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        Root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        Root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        Grid.SetRow(feedback, 1);
        Grid.SetRow(PageScroll, 2);
        Root.Children.Add(heading);
        Root.Children.Add(feedback);
        Root.Children.Add(PageScroll);
        Navigation.Content = Root;
        Content = Navigation;
    }
}
