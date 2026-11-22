using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using System.Globalization;
using System.Text.Json;

namespace MyNotes.Desktop;

public sealed partial class MainWindow
{
    private const string AgentDefault = "Predefinito della CLI";
    private const string EffortDefault = "Predefinita";

    private async Task SettingsAsync()
    {
        var config = await ReadAsync("config", "show");
        var preference = config.Field("providers");
        var savedProvider = preference.Text("default", "codex");
        var profile = config.Field("personal");
        var savedLanguage = profile.Text("interface_language", "it");
        var provider = Options("Agente", "SettingsProvider", ["codex", "agy", "claude"], savedProvider);
        var language = Options("Lingua della conversazione", "SettingsLanguage", ["it", "en"], savedLanguage);
        Page.Children.Add(Card("Agente e modello", Label(Join(savedProvider,
                preference.Field("configured").Field(savedProvider).Text("model"),
                preference.Field("configured").Field(savedProvider).Text("effort"))),
            provider, language, Actions(
                ButtonFor("Scegli modello", "SettingsModel", () => SelectModelAsync(Selected(provider), Selected(language), preference, false), primary: true),
                ButtonFor("Aggiorna modelli", "SettingsModelRefresh", () => SelectModelAsync(Selected(provider), Selected(language), preference, true)),
                ButtonFor("Verifica installazione", "SettingsDiagnostics", () => DiagnosticsAsync(Selected(provider))),
                ButtonFor("Verifica rendering Word", "SettingsWordVerify", async () =>
                {
                    var proof = Data(await Engine.JsonAsync(["verify-word-renderer"], Token, passWorkspace: false));
                    if (!proof.Flag("word") || proof.Text("status") != "VERIFIED")
                        throw new BackendException("MyNotes non ha confermato il rendering del documento Word di prova.");
                    Announce($"Rendering verificato su un documento di prova di {proof.Text("word_pages")} pagina.");
                }))));

        var background = Input("Esperienza e obiettivi", "SettingsBackground", profile.Text("background"));
        background.AcceptsReturn = true;
        Page.Children.Add(Card("Profilo di studio", background, Actions(
            ButtonFor("Salva profilo", "SettingsProfileSave", async () =>
            {
                if (string.IsNullOrWhiteSpace(background.Text))
                    throw new BackendException("Inserisci il profilo o scegli Salta.");
                Data(await Engine.RunAsync(["profile", "--background", background.Text.Trim()], Token));
                Announce("Profilo salvato.");
            }),
            ButtonFor("Salta", "SettingsProfileSkip", async () =>
            {
                Data(await Engine.RunAsync(["profile", "--skip"], Token));
                Announce("Passaggio completato.");
            }))));

        var collection = await ReadAsync("transcription-queue", "library");
        var folder = collection.Text("folder");
        Page.Children.Add(Card("File locali", Label(folder), Actions(
            ButtonFor("Scegli cartella raccolta", "SettingsLibraryFolder", async () =>
            {
                var chosen = await PickFolderAsync();
                if (chosen is not null)
                    await ExecuteAsync("Cartella della raccolta aggiornata.", "transcription-queue", "folder", "--path", chosen);
            }),
            ButtonFor("Apri workspace", "SettingsWorkspaceOpen", () => OpenLocalAsync(Engine.Workspace)))));

        var capture = await ReadAsync("capture", "dashboard");
        var interval = Input("Sincronizza calendari ogni (ore da 0,25 a 168)", "SettingsCaptureInterval", capture.Text("refreshHours", "1"));
        var before = Input("Secondi di registrazione prima dell’inizio (facoltativo)", "SettingsCaptureBefore");
        var after = Input("Secondi di registrazione dopo la fine (facoltativo)", "SettingsCaptureAfter");
        Page.Children.Add(Card("Registrazione e calendario", interval, before, after, Actions(
            ButtonFor("Salva impostazioni", "SettingsCaptureSave", async () =>
            {
                if ((!double.TryParse(interval.Text, NumberStyles.Float, CultureInfo.CurrentCulture, out var value)
                    && !double.TryParse(interval.Text, NumberStyles.Float, CultureInfo.InvariantCulture, out value))
                    || !double.IsFinite(value) || value is < 0.25 or > 168)
                    throw new BackendException("Inserisci un intervallo da 0,25 a 168 ore.");
                var payload = new Dictionary<string, object?> { ["refreshHours"] = value };
                foreach (var (field, input) in new[] { ("recordBeforeSeconds", before), ("recordAfterSeconds", after) })
                {
                    if (string.IsNullOrWhiteSpace(input.Text))
                        continue;
                    if (!int.TryParse(input.Text, out var seconds) || seconds is < 0 or > 1800)
                        throw new BackendException("Indica un margine da 0 a 1800 secondi.");
                    payload[field] = seconds;
                }
                await RequestAsync("Impostazioni Capture salvate.", ["capture", "configure"], payload);
            }),
            ButtonFor("Scegli cartella registrazioni", "SettingsCaptureFolder", async () =>
            {
                var chosen = await PickFolderAsync();
                if (chosen is not null)
                    await RequestAsync("Cartella di registrazione aggiornata.", ["capture", "configure"], new { outputDirectory = chosen });
            }))));

        var instructions = config.Field("style").Text("freeform_preferences");
        var learnerStyle = Input("Preferenze di scrittura", "SettingsStyle", instructions);
        learnerStyle.AcceptsReturn = true;
        Page.Children.Add(Card("Stile degli appunti", learnerStyle, Actions(
            ButtonFor("Salva preferenze", "SettingsStyleSave", async () =>
            {
                (await Engine.RequestAsync(["style", "edit"], new { freeform_preferences = learnerStyle.Text.Trim() }, Token, json: false)).RequireSuccess();
                Announce("Preferenze di scrittura salvate.");
            }))));
    }

    private async Task SelectModelAsync(string provider, string language, JsonElement settings, bool refresh)
    {
        if (provider.Length == 0 || language.Length == 0)
            throw new BackendException("Scegli un agente e una lingua.");
        var current = settings.Field("configured").Field(provider);
        var args = new List<string> { "models", "--provider", provider, "--saved", current.Text("model"), "--saved-effort", current.Text("effort") };
        if (refresh)
            args.Add("--refresh");
        var models = Data(await Engine.JsonAsync(args, Token, passWorkspace: false));
        var available = models.Items("choices").Where(item => item.Flag("selectable")).ToArray();
        var shortcuts = models.Items("presets").Where(item => item.Flag("selectable")).ToArray();
        var choices = available.Select(item => item.Text("model")).Where(item => item.Length > 0).ToArray();
        var model = Options("Modello", "ModelChoice", new[] { AgentDefault }.Concat(choices), current.Text("model").Length > 0 && choices.Contains(current.Text("model"))
            ? current.Text("model") : AgentDefault);
        var presetLabels = shortcuts.ToDictionary(item => $"{item.Text("label")} ({item.Text("default_effort")})", item => item);
        var preset = Options("Preset", "ModelPreset", new[] { "Personalizzato" }.Concat(presetLabels.Keys));
        var effort = Options("Intensità", "ModelEffort", [EffortDefault]);

        void UpdateEffort(string preferred)
        {
            var reference = available.FirstOrDefault(item => item.Text("model") == Selected(model));
            effort.Items.Clear();
            effort.Items.Add(EffortDefault);
            foreach (var value in reference.Strings("supported_efforts"))
                effort.Items.Add(value);
            effort.SelectedItem = effort.Items.Contains(preferred) ? preferred : EffortDefault;
        }
        model.SelectionChanged += (_, _) => UpdateEffort(available.FirstOrDefault(item => item.Text("model") == Selected(model)).Text("default_effort"));
        preset.SelectionChanged += (_, _) =>
        {
            if (presetLabels.TryGetValue(Selected(preset), out var chosen))
            {
                model.SelectedItem = chosen.Text("model");
                UpdateEffort(chosen.Text("default_effort"));
            }
        };
        UpdateEffort(current.Text("effort"));
        if (!await DialogAsync("Scegli agente e modello", Column(Label(models.Text("message")), preset, model, effort)))
            return;
        var selectedModel = Selected(model) == AgentDefault ? "" : Selected(model);
        var selectedEffort = Selected(effort) == EffortDefault ? "" : Selected(effort);
        var result = await Engine.RunAsync(["preferences", "--provider", provider, "--model", selectedModel, "--effort", selectedEffort, "--language", language], Token);
        Data(result);
        await DisplayPageAsync();
        Announce("Preferenze dell’agente salvate.");
    }

    private async Task DiagnosticsAsync(string provider)
    {
        var command = new List<string> { "doctor" };
        if (provider.Length > 0)
            command.AddRange(["--provider", provider]);
        var diagnostics = (await Engine.JsonAsync(command, Token)).RequireJson();
        var checks = diagnostics.Field("checks").Members().Select(item =>
            (UIElement)Column(Label(item.Key, heading: true), Label(Join(item.Value.Text("status"), item.Value.Text("action"))))).ToArray();
        await DialogAsync("Diagnostica MyNotes", Column(checks), "");
    }
}
