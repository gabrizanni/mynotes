using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using System.Text.Json;

namespace MyNotes.Desktop;

public sealed partial class MainWindow
{
    private async Task CoursesAsync()
    {
        var config = await ReadAsync("config", "show");
        Page.Children.Add(Actions(ButtonFor("Aggiungi corso", "CourseAdd", async () =>
        {
            var name = Input("Nome", "CourseName");
            if (await DialogAsync("Aggiungi corso", name, "Aggiungi") && !string.IsNullOrWhiteSpace(name.Text))
            {
                var result = await ReadAsync("course", "add", "--name", name.Text.Trim(), "--defer-context");
                await DisplayPageAsync();
                Announce(result.Text("status") == "EXISTS" ? "Il corso esiste già." : "Corso aggiunto.");
            }
        }, primary: true)));

        var courses = config.Items("courses").ToArray();
        if (courses.Length == 0)
            Page.Children.Add(Label("Aggiungi un corso per organizzare lezioni, materiali e appunti."));
        foreach (var item in courses)
        {
            var code = item.Text("code");
            var files = item.Field("files");
            var folders = item.Field("folders");
            var buttons = new List<UIElement>
            {
                ButtonFor("Profilo", "CourseProfile", () => CourseProfileAsync(code)),
                ButtonFor("Modifica", "CourseEdit", () => EditCourseAsync(item)),
                ButtonFor("Collega cartella", "CourseLink", () => LinkCourseAsync(item)),
                ButtonFor("Allegati lezione", "CourseAttachments", () => AttachLessonAsync(item)),
                ButtonFor("Apri agente", "CourseChat", () =>
                {
                    Engine.OpenConsole("chat", "--", $"Apri il contesto del corso {code}. Attendi una mia richiesta prima di elaborare i contenuti.");
                    return Task.CompletedTask;
                }),
                ButtonFor("Genera appunti", "CourseNotes", () => NotesAsync(code), primary: true),
            };
            var libraryFolder = folders.Text("materials");
            if (libraryFolder.Length > 0)
                buttons.Add(ButtonFor("Materiali", "CourseMaterials", () => OpenLocalAsync(libraryFolder)));
            var personal = files.Text("personal_notes");
            if (personal.Length > 0)
                buttons.Add(ButtonFor("Quaderno personale", "CourseNotebook", () => OpenLocalAsync(personal)));
            var word = files.Text("handout");
            if (!string.IsNullOrEmpty(word) && File.Exists(word))
            {
                buttons.Add(ButtonFor("Apri Word cumulativo", "CourseWord", () => OpenLocalAsync(word)));
                buttons.Add(ButtonFor("Annulla aggiornamento Word", "CourseWordUndo", async () =>
                {
                    if (!await ConfirmAsync("Annulla aggiornamento Word", "Ripristinare la versione precedente del documento cumulativo? MyNotes conserva una copia di sicurezza.", "Ripristina"))
                        return;
                    (await Engine.RunAsync(["undo", code], Token)).RequireSuccess();
                    await DisplayPageAsync();
                    Announce("Ripristino Word completato.");
                }));
            }
            Page.Children.Add(Card(item.Text("name", code),
                Label(Join(code, item.Field("languages").Text("notes"), item.Field("publication").Text("mode") == "review" ? "Pubblicazione con revisione" : "Pubblicazione automatica")),
                Actions(buttons.ToArray())));
        }
    }

    private async Task CourseProfileAsync(string code)
    {
        var profile = await ReadAsync("transcription-queue", "course-profile", "--course", code);
        var course = profile.Field("course");
        var labels = new List<UIElement>();
        foreach (var (field, title) in new[]
        {
            ("context", "Contesto"), ("assessment", "Valutazione"), ("preparation", "Preparazione"),
            ("instructors", "Docenti"), ("modules", "Moduli"), ("difficult_topics", "Argomenti difficili"),
        })
        {
            var value = course.Text(field, string.Join(", ", course.Strings(field)));
            if (value.Length > 0)
                labels.Add(Column(Label(title, heading: true), Label(value)));
        }
        var instructions = course.Field("preferences").Strings("persistent_instructions").ToArray();
        if (instructions.Length > 0)
            labels.Add(Column(Label("Preferenze sugli appunti", heading: true), Label(string.Join(Environment.NewLine, instructions))));
        if (labels.Count == 0)
            labels.Add(Label("Il profilo è vuoto. Puoi completarlo con Modifica corso."));
        await DialogAsync(course.Text("name", code), Column(labels.ToArray()), "");
    }

    private async Task EditCourseAsync(JsonElement course)
    {
        var name = Input("Nome", "CourseEditName", course.Text("name"));
        var kind = Options("Tipologia", "CourseKind", ["mixed", "quantitative", "theoretical", "cases", "programming"], course.Text("kind"));
        var teaching = Input("Lingua delle lezioni", "CourseLessonLanguage", course.Field("languages").Text("lesson"));
        var notes = Input("Lingua degli appunti", "CourseNotesLanguage", course.Field("languages").Text("notes"));
        var context = Input("Contesto", "CourseContext", course.Text("context"));
        context.AcceptsReturn = true;
        var assessment = Input("Valutazione", "CourseAssessment", course.Text("assessment"));
        var preparation = Input("Preparazione", "CoursePreparation", course.Text("preparation"));
        var publication = Options("Pubblicazione Word", "CoursePublication", ["automatic", "review"], course.Field("publication").Text("mode"));
        var speech = course.Field("transcription");
        var speechLanguage = Input("Lingua della trascrizione (it, en, auto)", "CourseSpeechLanguage", speech.Text("language", "auto"));
        var savedDiarization = speech.Text("diarization_engine");
        var speakers = Options("Distingui le voci", "CourseDiarization", ["sherpa-onnx", "disattivato"],
            savedDiarization == "sherpa-onnx" ? savedDiarization : "disattivato");
        var teacherVoice = Input("Campione audio del docente (facoltativo)", "CourseTeacherVoice", speech.Text("professor_reference"));
        if (!await DialogAsync("Modifica corso", Column(name, kind, teaching, notes, context, assessment, preparation,
            publication, speechLanguage, speakers, teacherVoice)))
            return;
        if (new[] { name.Text, teaching.Text, notes.Text }.Any(string.IsNullOrWhiteSpace))
            throw new BackendException("Indica il nome del corso e le lingue.");
        var patch = new
        {
            name = name.Text.Trim(), kind = Selected(kind), context = context.Text.Trim(),
            assessment = assessment.Text.Trim(), preparation = preparation.Text.Trim(),
            languages = new { lesson = teaching.Text.Trim(), notes = notes.Text.Trim() },
            publication = new { mode = Selected(publication) },
            transcription = new
            {
                language = speechLanguage.Text.Trim(),
                diarization_engine = Selected(speakers) == "sherpa-onnx" ? "sherpa-onnx" : null,
                professor_reference = teacherVoice.Text.Trim(),
            },
        };
        await UpdateCourseAsync(course.Text("code"), patch);
    }

    private async Task UpdateCourseAsync(string code, object patch)
    {
        (await Engine.RequestAsync(["course", "edit", code], patch, Token, json: false)).RequireSuccess();
        await DisplayPageAsync();
        Announce("Corso aggiornato.");
    }

    private async Task LinkCourseAsync(JsonElement course)
    {
        var path = await PickFolderAsync();
        if (path is null)
            return;
        var args = new List<string> { "course", "link", course.Text("code"), path };
        var preview = await Engine.JsonAsync([.. args, "--dry-run"], Token);
        if (preview.Data.Text("status") == "NEEDS_SELECTION")
        {
            var ambiguity = preview.Data.Field("ambiguities");
            var options = new Dictionary<string, ComboBox>();
            foreach (var (field, caption) in new[] { ("personal_notes", "Cartella quaderno"), ("notes_file", "File quaderno"), ("recordings", "Cartella registrazioni") })
            {
                var choices = ambiguity.Strings(field).ToArray();
                if (choices.Length > 0)
                    options[field] = Options(caption, $"CourseLink{field}", choices);
            }
            if (options.Count == 0)
                throw new BackendException("La cartella contiene elementi non leggibili o troppi candidati. Scegli una cartella del corso più specifica.");
            if (!await DialogAsync("Scegli i collegamenti del corso", Column(options.Values.ToArray()), "Anteprima"))
                return;
            foreach (var (field, option) in options)
                args.AddRange([field switch { "personal_notes" => "--notes", "notes_file" => "--notes-file", _ => "--recordings" }, Selected(option)]);
            preview = await Engine.JsonAsync([.. args, "--dry-run"], Token);
        }
        var data = Data(preview);
        var paths = data.Field("note_files");
        if (!await ConfirmAsync("Collega cartella", $"Quaderno personale: {paths.Text("personal_notes")}{Environment.NewLine}Word cumulativo: {paths.Text("handout")}", "Collega"))
            return;
        await ExecuteAsync("Cartella collegata al corso.", args.ToArray());
    }

    private async Task AttachLessonAsync(JsonElement course)
    {
        var number = Input("Numero della lezione", "AttachmentLesson");
        var role = Options("Tipo di allegato", "AttachmentRole", ["Materiali", "Appunti personali"]);
        if (!await DialogAsync("Allegati lezione", Column(number, role), "Scegli file"))
            return;
        if (!int.TryParse(number.Text, out var lesson) || lesson is < 1 or > 999)
            throw new BackendException("Indica un numero di lezione da 1 a 999.");
        var materials = Selected(role) == "Materiali";
        var extensions = materials ? new[] { ".pdf", ".docx", ".pptx", ".xlsx", ".txt", ".md", ".ipynb" } : [".docx", ".md", ".txt"];
        var files = await PickFilesAsync(extensions, multiple: materials);
        if (files.Length == 0)
            return;
        var key = lesson.ToString(System.Globalization.CultureInfo.InvariantCulture);
        var existing = course.Field("lesson_sources").Field(key);
        var choices = new Dictionary<string, object?>
        {
            ["materials"] = materials ? existing.Strings("materials").Concat(files).Distinct(StringComparer.OrdinalIgnoreCase).ToArray() : existing.Strings("materials").ToArray(),
            ["personal_notes"] = materials ? existing.Text("personal_notes") : files[0],
        };
        if (!materials && existing.Text("personal_notes").Length > 0
            && !await ConfirmAsync("Sostituisci allegato", "Usare il nuovo file come appunto personale per questa lezione?", "Sostituisci"))
            return;
        await UpdateCourseAsync(course.Text("code"), new { lesson_sources = new Dictionary<string, object?> { [key] = choices } });
    }

    private async Task NotesAsync(string code)
    {
        var selection = Input("Lezioni (per esempio 1–3, 5)", "NotesSelection");
        var instructions = Input("Indicazioni temporanee", "NotesInstructions");
        instructions.AcceptsReturn = true;
        var recover = Input("ID lavoro da riprendere (facoltativo)", "NotesResume");
        if (!await DialogAsync("Genera appunti Word", Column(selection, instructions, recover), "Controlla fonti"))
            return;
        var ids = selection.Text.Trim().Replace('–', '-');
        var batch = recover.Text.Trim();
        if ((ids.Length == 0) == (batch.Length == 0))
            throw new BackendException("Indica una selezione di lezioni oppure l’identificativo di un lavoro da riprendere.");

        var parameters = new List<string> { "notes", code };
        parameters.AddRange(batch.Length > 0 ? ["--resume", batch] : ["--lessons", ids]);
        if (!string.IsNullOrWhiteSpace(instructions.Text))
            parameters.AddRange(["--instructions", instructions.Text.Trim()]);
        var preview = await Engine.JsonAsync([.. parameters, "--plan-only"], Token);
        var previewData = Data(preview);
        var report = JsonSerializer.Serialize(previewData, new JsonSerializerOptions { WriteIndented = true });
        var details = Input("Anteprima del motore", "NotesPlan", report);
        details.IsReadOnly = true;
        details.AcceptsReturn = true;
        details.TextWrapping = TextWrapping.Wrap;
        details.MaxHeight = 250;
        if (!await DialogAsync("Controlla fonti e genera", Column(Label("La generazione usa l’agente configurato e può consumare la quota del tuo account."), details), "Genera"))
            return;
        var result = await Engine.JsonAsync(parameters, Token);
        var data = result.RequireJson();
        var publication = data.Field("publication");
        var status = data.Text("status");
        var output = publication.Text("output", publication.Text("candidate"));
        var batchId = data.Text("batch_id");
        var summary = Label(Join(status, batchId.Length > 0 ? $"Lavoro {batchId}" : "",
            data.Field("problem").Text("message")));
        if (output.Length > 0 && File.Exists(output))
        {
            var action = status == "AWAITING_APPROVAL" ? "Apri anteprima Word" : "Apri Word";
            if (await DialogAsync("Risultato Word", Column(summary, Label(output)), action))
                await OpenLocalAsync(output);
        }
        else
            await DialogAsync("Risultato Word", summary, "");
        if (result.ExitCode != 0)
            Announce($"Elaborazione fermata: {status}.", InfoBarSeverity.Warning);
        else if (status == "AWAITING_APPROVAL")
            Announce("L’anteprima del documento è pronta per la revisione.", InfoBarSeverity.Informational);
        else
            Announce(status == "COMPLETED_WITH_WARNINGS" ? "Elaborazione completata con avvisi." : "Elaborazione completata.");
    }
}
