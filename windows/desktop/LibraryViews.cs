using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using System.Globalization;
using System.Text.Json;

namespace MyNotes.Desktop;

public sealed partial class MainWindow
{
    private int libraryPage;
    private const int PageSize = 24;

    private async Task LibraryAsync()
    {
        var config = await ReadAsync("config", "show");
        var codes = CourseCodes(config);
        var search = Input("Cerca lezioni, audio o trascrizioni", "LibrarySearch", libraryQuery);
        var course = Options("Corso", "LibraryCourse", new[] { "Tutti i corsi" }.Concat(codes),
            libraryCourse.Length == 0 ? "Tutti i corsi" : libraryCourse);
        var hidden = new CheckBox { Content = "Mostra lezioni archiviate", IsChecked = includeHidden };
        var filter = ButtonFor("Cerca", "LibraryFilter", async () =>
        {
            libraryQuery = search.Text.Trim();
            libraryCourse = Selected(course) == "Tutti i corsi" ? "" : Selected(course);
            includeHidden = hidden.IsChecked == true;
            libraryPage = 0;
            await DisplayPageAsync();
        }, primary: true);
        search.KeyDown += (_, args) =>
        {
            if (args.Key == Windows.System.VirtualKey.Enter)
            {
                args.Handled = true;
                _ = WithBusyAsync(async () =>
                {
                    libraryQuery = search.Text.Trim();
                    libraryPage = 0;
                    await DisplayPageAsync();
                });
            }
        };
        Page.Children.Add(Card("Raccolta", search, course, hidden,
            Actions(filter,
                ButtonFor("Aggiungi lezione", "LibraryAddLesson", () => EditLessonAsync(default, codes)),
                ButtonFor("Importa audio", "LibraryImport", () => ImportAudioAsync(codes)),
                ButtonFor("Verifica file", "VerifyLibrary", () => ExecuteAsync("Raccolta verificata.", "transcription-queue", "verify-library")))));

        var args = new List<string> { "transcription-queue", "library" };
        if (libraryQuery.Length > 0)
            args.AddRange(["--query", libraryQuery]);
        if (libraryCourse.Length > 0)
            args.AddRange(["--course", libraryCourse]);
        var library = await ReadAsync(args.ToArray());
        var recordings = library.Items("recordings").ToDictionary(item => item.Text("id"));
        var transcripts = library.Items("transcripts").ToDictionary(item => item.Text("id"));
        var allLessons = library.Items("lessons").ToArray();
        var lessons = allLessons.Where(item => item.Flag("hidden") == includeHidden).ToArray();
        var linkedAudio = allLessons.SelectMany(item => item.Strings("recording_ids")).ToHashSet();
        var linkedText = allLessons.SelectMany(item => item.Strings("transcript_ids")).ToHashSet();
        var visibleCount = lessons.Length + recordings.Keys.Count(id => !linkedAudio.Contains(id)) + transcripts.Keys.Count(id => !linkedText.Contains(id));
        var lastPage = Math.Max(0, (visibleCount - 1) / PageSize);
        libraryPage = Math.Clamp(libraryPage, 0, lastPage);

        var views = lessons.Select<JsonElement, Func<UIElement>>(lesson => () => LessonCard(lesson, codes, recordings, transcripts, allLessons))
            .Concat(recordings.Where(item => !linkedAudio.Contains(item.Key)).Select<KeyValuePair<string, JsonElement>, Func<UIElement>>(item => () => AudioCard(item.Value, allLessons)))
            .Concat(transcripts.Where(item => !linkedText.Contains(item.Key)).Select<KeyValuePair<string, JsonElement>, Func<UIElement>>(item => () => TranscriptCard(item.Value, default, allLessons)));

        foreach (var view in views.Skip(libraryPage * PageSize).Take(PageSize))
            Page.Children.Add(view());
        if (visibleCount == 0)
            Page.Children.Add(Label("Nessuna lezione, registrazione o trascrizione per questa ricerca."));
        if (lastPage > 0)
        {
            var previous = ButtonFor("Precedente", "LibraryPrevious", async () => { libraryPage--; await DisplayPageAsync(); });
            var next = ButtonFor("Successiva", "LibraryNext", async () => { libraryPage++; await DisplayPageAsync(); });
            previous.IsEnabled = libraryPage > 0;
            next.IsEnabled = libraryPage < lastPage;
            Page.Children.Add(Column(Label($"Pagina {libraryPage + 1} di {lastPage + 1}"), Actions(previous, next)));
        }
    }

    private Border LessonCard(JsonElement lesson, string[] courses, Dictionary<string, JsonElement> recordings,
        Dictionary<string, JsonElement> transcripts, JsonElement[] lessons)
    {
        var id = lesson.Text("id");
        var hidden = lesson.Flag("hidden");
        var controls = new List<UIElement>
        {
            ButtonFor(hidden ? "Ripristina" : "Archivia", "LessonArchive", async () =>
            {
                var action = hidden ? "restore" : "hide";
                if (!hidden && !await ConfirmAsync("Archivia lezione", "Nascondere questa lezione dalla libreria?", "Archivia"))
                    return;
                await LessonActionAsync(id, action, hidden ? "Lezione ripristinata." : "Lezione archiviata.");
            }),
        };
        if (!hidden)
        {
            controls.Insert(0, ButtonFor("Modifica", "LessonEdit", () => EditLessonAsync(lesson, courses)));
            controls.Add(ButtonFor(lesson.Flag("cancelled") ? "Ripristina evento" : "Segna annullata", "LessonCancelled",
                () => LessonActionAsync(id, lesson.Flag("cancelled") ? "uncancelled" : "cancelled", "Stato della lezione aggiornato.")));
            controls.Add(ButtonFor(lesson.Flag("not_recorded") ? "Ripristina registrazione" : "Segna non registrata",
                "LessonNotRecorded", () => LessonActionAsync(id, lesson.Flag("not_recorded") ? "recorded" : "not-recorded", "Stato della lezione aggiornato.")));
            if (lesson.Items("pending_renames").Any())
                controls.Add(ButtonFor("Riprendi rinomina", "LessonResumeRenames", () => LessonActionAsync(id, "resume-renames", "Rinomina verificata.")));
        }
        var children = new List<UIElement>
        {
            Label(Join(lesson.Text("course"), lesson.Text("number"), DisplayDate(lesson.Text("date")),
                lesson.Flag("cancelled") ? "Annullata" : "", lesson.Flag("not_recorded") ? "Non registrata" : "",
                lesson.Flag("revision_required") ? "Collegamenti da verificare" : "")),
            Actions(controls.ToArray()),
        };
        foreach (var key in lesson.Strings("recording_ids").Where(recordings.ContainsKey))
            children.Add(AudioCard(recordings[key], lessons, lesson));
        foreach (var key in lesson.Strings("transcript_ids").Where(transcripts.ContainsKey))
            children.Add(TranscriptCard(transcripts[key], lesson, lessons));
        return Card(lesson.Text("title", "Lezione"), children.ToArray());
    }

    private Border AudioCard(JsonElement audio, JsonElement[] lessons, JsonElement lesson = default)
    {
        var id = audio.Text("id");
        var path = audio.Text("path");
        var actions = new List<UIElement>();
        var supported = audio.Strings("actions").ToHashSet();
        if (audio.Flag("audio_available"))
            actions.Add(ButtonFor("Ascolta", "AudioOpen", () => OpenLocalAsync(path)));
        if (supported.Contains("enqueue") && audio.Flag("can_transcribe"))
            actions.Add(ButtonFor("Trascrivi", "AudioTranscribe", () => ExecuteAsync("Richiesta aggiunta alla coda.",
                "transcription-queue", "enqueue", "--recording", id), primary: true));
        if (supported.Contains("download"))
            actions.Add(ButtonFor("Scarica", "AudioDownload", () => ExecuteAsync("Richiesta di download gestita.", "transcription-queue", "download", "--recording", id)));
        if (!audio.Flag("audio_available"))
            actions.Add(ButtonFor("Ricollega file", "AudioRelink", async () =>
            {
                var selected = await PickFilesAsync(AudioExtensions, multiple: false);
                if (selected.Length > 0)
                    await ExecuteAsync("Audio ricollegato.", "transcription-queue", "relink", "--recording", id, "--path", selected[0]);
            }));
        actions.Add(ButtonFor("Rinomina", "AudioRename", async () =>
        {
            var title = Input("Titolo", "AudioTitle", audio.Text("title"));
            if (await DialogAsync("Rinomina registrazione", title) && !string.IsNullOrWhiteSpace(title.Text))
                await ExecuteAsync("Registrazione rinominata.", "transcription-queue", "rename", "--recording", id, "--title", title.Text.Trim());
        }));
        if (lessons.Any(item => !item.Flag("hidden") && item.Text("id") != lesson.Text("id")))
            actions.Add(ButtonFor("Sposta in lezione", "AudioMove", () => MoveSourceAsync(audio, "move-audio", lessons, lesson)));
        if (audio.Flag("source_excluded"))
            actions.Add(ButtonFor("Ripristina sorgente", "AudioRestore", () => ExecuteAsync("Sorgente ripristinata.", "transcription-queue", "restore-audio", "--recording", id)));
        else
        {
            actions.Add(ButtonFor("Usa come sorgente", "AudioPrefer", () => ExecuteAsync("Preferenza aggiornata.", "transcription-queue", "prefer-audio", "--recording", id)));
            actions.Add(ButtonFor("Escludi sorgente", "AudioExclude", async () =>
            {
                if (await ConfirmAsync("Escludi audio", "L’esclusione conserva il file e i suoi collegamenti.", "Escludi"))
                    await ExecuteAsync("Sorgente esclusa.", "transcription-queue", "exclude-audio", "--recording", id);
            }));
        }
        var txt = audio.Text("transcript_text");
        if (txt.Length > 0)
            actions.Add(ButtonFor("Apri TXT", "AudioTranscriptOpen", () => OpenLocalAsync(txt)));
        if (lesson.Flag("revision_required"))
            actions.Add(ButtonFor("Conferma sorgente", "AudioReview", () => RequestAsync("Sorgente verificata.", ["transcription-queue", "lesson-edit"],
                new { id = lesson.Text("id"), action = "review-sources", recording = id })));
        return Card(audio.Text("title", "Audio"), Label(Join(audio.Text("course_code"), audio.Text("transcription_status", audio.Text("status")),
            DisplayDate(audio.Text("created_at")), audio.Flag("source_excluded") ? "Esclusa" : "")), Actions(actions.ToArray()));
    }

    private Border TranscriptCard(JsonElement transcript, JsonElement lesson, JsonElement[] lessons)
    {
        var id = transcript.Text("id");
        var buttons = new List<UIElement>
        {
            ButtonFor("Apri TXT", "TranscriptOpen", () => OpenLocalAsync(transcript.Text("path"))),
            ButtonFor("Rinomina", "TranscriptRename", async () =>
            {
                var title = Input("Nome", "TranscriptName", transcript.Text("title"));
                if (await DialogAsync("Rinomina trascrizione", title) && !string.IsNullOrWhiteSpace(title.Text))
                    await ExecuteAsync("Trascrizione rinominata.", "transcription-queue", "rename-transcript", "--transcript", id, "--name", title.Text.Trim());
            }),
        };
        if (lesson.ValueKind == JsonValueKind.Object)
        {
            var action = lesson.Flag("revision_required") ? "review-sources" : "prefer-transcript";
            buttons.Add(ButtonFor("Usa questa trascrizione", "TranscriptSource", () => RequestAsync("Sorgente della lezione aggiornata.",
                ["transcription-queue", "lesson-edit"], new { id = lesson.Text("id"), action, transcript = id })));
        }
        if (transcript.Text("recording_id").Length == 0 && lessons.Any(item => !item.Flag("hidden") && item.Text("id") != lesson.Text("id")))
            buttons.Add(ButtonFor("Sposta in lezione", "TranscriptMove", () => MoveSourceAsync(transcript, "move-transcript", lessons, lesson)));
        return Card(transcript.Text("title", "Trascrizione"), Label(Join(transcript.Text("course_code"), transcript.Text("status"))), Actions(buttons.ToArray()));
    }

    private async Task MoveSourceAsync(JsonElement item, string action, JsonElement[] lessons, JsonElement current)
    {
        var choices = lessons.Where(item => !item.Flag("hidden") && item.Text("id") != current.Text("id"))
            .ToDictionary(lesson => Join(lesson.Text("course"), lesson.Text("number"), lesson.Text("title"), lesson.Text("id")), lesson => lesson.Text("id"));
        var target = Options("Destinazione", "SourceTarget", choices.Keys);
        if (!await DialogAsync("Sposta sorgente", target, "Sposta") || !choices.TryGetValue(Selected(target), out var targetId))
            return;
        if (action == "move-audio")
        {
            var command = new[] { "transcription-queue", "lesson-edit" };
            var draft = Data(await Engine.RequestAsync(command, new { action, target = targetId, recording = item.Text("id"), preview = true }, Token));
            if (await ConfirmAsync("Conferma destinazione", $"Spostare la registrazione in {Selected(target)}?", "Sposta"))
                await RequestAsync("Audio spostato.", command, new { action, target = targetId, recording = item.Text("id"), token = draft.Text("token") });
        }
        else
            await RequestAsync("Trascrizione spostata.", ["transcription-queue", "lesson-edit"],
                new { action, target = targetId, id = current.Text("id"), transcript = item.Text("id") });
    }

    private Task LessonActionAsync(string id, string action, string done) =>
        RequestAsync(done, ["transcription-queue", "lesson-edit"], new { id, action });

    private async Task EditLessonAsync(JsonElement lesson, string[] codes)
    {
        if (codes.Length == 0)
            throw new BackendException("Aggiungi prima un corso nella schermata Corsi.");
        var creating = lesson.ValueKind != JsonValueKind.Object;
        var course = Options("Corso", "LessonCourse", codes, creating ? null : lesson.Text("course"));
        var number = Input("Numero da 1 a 999", "LessonNumber", lesson.Text("number"));
        var title = Input("Titolo (facoltativo)", "LessonTitle", lesson.Text("title"));
        var date = Input("Data e ora ISO 8601 con fuso (facoltativa)", "LessonDate", lesson.Text("date"));
        if (!await DialogAsync(creating ? "Aggiungi lezione" : "Modifica lezione", Column(course, number, title, date), "Anteprima"))
            return;
        if (!int.TryParse(number.Text, out var lessonNumber) || lessonNumber is < 1 or > 999 || Selected(course).Length == 0)
            throw new BackendException("Scegli un corso e un numero da 1 a 999.");
        var patch = new Dictionary<string, object?> { ["course"] = Selected(course), ["number"] = lessonNumber };
        if (!string.IsNullOrWhiteSpace(title.Text) && (creating || title.Text != lesson.Text("title")))
            patch["title"] = title.Text.Trim();
        if (!string.IsNullOrWhiteSpace(date.Text) && (creating || date.Text != lesson.Text("date")))
            patch["date"] = StrictDate(date.Text).ToString("o", CultureInfo.InvariantCulture);
        var command = new[] { "transcription-queue", "lesson-edit" };
        var payload = new Dictionary<string, object?>
        {
            ["action"] = creating ? "create" : "update",
            ["id"] = creating ? null : lesson.Text("id"),
            ["patch"] = patch, ["preview"] = true, ["conflict"] = "stop",
        };
        var result = await Engine.RequestAsync(command, payload, Token);
        if (result.Data.Text("status") == "CONFLICT")
        {
            var options = creating ? new[] { "Unisci" } : new[] { "Scambia", "Unisci" };
            var resolution = Options("Risoluzione", "LessonConflict", options);
            var conflictDetails = result.Data.Items("conflicts").Select(item => Join(item.Text("course"), item.Text("number"), item.Text("title")));
            if (!await DialogAsync("Numero già in uso", Column(Label(string.Join(Environment.NewLine, conflictDetails)), resolution), "Prepara anteprima"))
                return;
            payload["conflict"] = Selected(resolution) == "Scambia" ? "swap" : "merge";
            result = await Engine.RequestAsync(command, payload, Token);
        }

        var preview = Data(result);
        var after = preview.Field("after");
        var change = $"{Join(lesson.Text("course"), lesson.Text("number"), lesson.Text("title"))} → {Join(after.Text("course"), after.Text("number"), after.Text("title"), DisplayDate(after.Text("date")))}";
        var renames = preview.Items("renames").Select(item => (Path: item.Text("path"), Checkbox: new CheckBox
        {
            Content = $"{Path.GetFileName(item.Text("path"))} → {Path.GetFileName(item.Text("target"))}",
        })).ToArray();
        if (!await DialogAsync("Conferma lezione", Column([Label(change), .. renames.Select(item => item.Checkbox)]), "Applica"))
            return;

        payload["preview"] = false;
        payload["token"] = preview.Text("token");
        payload["new_id"] = preview.Text("new_id");
        payload["rename_paths"] = renames.Where(item => item.Checkbox.IsChecked == true).Select(item => item.Path).ToArray();
        await RequestAsync("Lezione aggiornata.", command, payload);
    }

    private static readonly string[] AudioExtensions = [".m4a", ".mp4", ".wav", ".mp3", ".flac", ".ogg", ".opus", ".aac", ".caf", ".aiff", ".webm"];

    private async Task ImportAudioAsync(string[] codes)
    {
        var paths = await PickFilesAsync(AudioExtensions, multiple: true);
        if (paths.Length == 0)
            return;
        var course = Options("Corso", "ImportAudioCourse", new[] { "Non assegnato" }.Concat(codes));
        var number = Input("Numero della lezione (facoltativo)", "ImportAudioLesson");
        if (!await DialogAsync("Importa audio", Column(Label(string.Join(Environment.NewLine, paths.Select(Path.GetFileName))), course, number), "Importa"))
            return;
        var request = new Dictionary<string, object?> { ["paths"] = paths };
        if (Selected(course) != "Non assegnato")
            request["course"] = Selected(course);
        if (!string.IsNullOrWhiteSpace(number.Text))
        {
            if (!request.ContainsKey("course") || !int.TryParse(number.Text, out var value) || value is < 1 or > 999)
                throw new BackendException("Per assegnare un numero da 1 a 999, scegli un corso.");
            request["lesson"] = value;
        }
        Data(await Engine.RequestAsync(["transcription-queue", "inspect-audio"], new { paths }, Token));
        await RequestAsync("Importazione audio completata.", ["transcription-queue", "import-audio"], request);
    }

    private static string[] CourseCodes(JsonElement config) =>
        config.Items("courses").Select(item => item.Text("code")).Where(code => code.Length > 0).ToArray();

    private static DateTimeOffset StrictDate(string date)
    {
        var value = date.Trim();
        if (value.Length > 10 && (value.EndsWith('Z') || value[10..].Contains('+') || value[10..].Contains('-'))
            && DateTimeOffset.TryParse(value, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out var parsed))
            return parsed;
        throw new BackendException("Inserisci una data con ora e fuso, per esempio 2026-12-08T15:00:00+01:00.");
    }
}
