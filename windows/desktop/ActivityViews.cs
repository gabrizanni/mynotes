using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using System.Globalization;
using System.Text.Json;

namespace MyNotes.Desktop;

public sealed partial class MainWindow
{
    private async Task ActivityAsync()
    {
        var queue = await ReadAsync("transcription-queue", "status");
        var config = await ReadAsync("config", "show");
        var codes = CourseCodes(config);
        var filteredCourse = Options("Filtra per corso", "ActivityCourse", new[] { "Tutti i corsi" }.Concat(codes),
            jobCourse.Length == 0 ? "Tutti i corsi" : jobCourse);
        var idle = queue.Flag("paused");
        var parallel = Options("Trascrizioni simultanee", "QueueParallel", ["1", "2", "3", "4"], queue.Text("parallel", "1"));
        var waiting = queue.Text("wait_reason");
        Page.Children.Add(Card("Coda di trascrizione", Label(idle ? "In pausa" : "Attiva"),
            Label(waiting), filteredCourse, parallel, Actions(
                ButtonFor("Applica filtro", "ActivityFilter", async () =>
                {
                    jobCourse = Selected(filteredCourse) == "Tutti i corsi" ? "" : Selected(filteredCourse);
                    await DisplayPageAsync();
                }),
                ButtonFor("Salva limite", "QueueParallelSave", () => ExecuteAsync("Limite della coda salvato.", "transcription-queue", "configure", "--parallel", Selected(parallel))),
                ButtonFor(idle ? "Riprendi coda" : "Sospendi coda", "QueuePause", () =>
                    ExecuteAsync(idle ? "Coda ripresa." : "Coda sospesa.", "transcription-queue", idle ? "resume" : "pause")),
                ButtonFor("Accoda audio pronti", "QueueEnqueueBatch", EnqueueReadyAsync, primary: true),
                ButtonFor("Rimuovi in attesa", "QueueClear", async () =>
                {
                    if (await ConfirmAsync("Svuota coda in attesa", "Rimuovere le richieste in attesa, conservando gli audio e le attività già avviate?", "Rimuovi"))
                        await ExecuteAsync("Coda in attesa svuotata.", "transcription-queue", "clear");
                }))));

        var jobs = queue.Items("jobs").Concat(queue.Items("history_events"))
            .Where(item => jobCourse.Length == 0 || item.Text("course_code") == jobCourse).ToArray();
        if (jobs.Length == 0)
        {
            Page.Children.Add(Label("Nessuna attività di trascrizione per il filtro selezionato."));
            return;
        }
        foreach (var job in jobs)
            Page.Children.Add(JobCard(job));
    }

    private async Task EnqueueReadyAsync()
    {
        var args = new List<string> { "transcription-queue", "library" };
        if (jobCourse.Length > 0)
            args.AddRange(["--course", jobCourse]);
        var library = await ReadAsync(args.ToArray());
        var ready = library.Items("recordings").Where(item => item.Text("transcription_status") == "NOT_STARTED"
            && item.Strings("actions").Contains("enqueue")).ToArray();
        if (ready.Length == 0)
        {
            Announce("Non ci sono nuovi audio pronti per il filtro attivo.", InfoBarSeverity.Informational);
            return;
        }
        var checkBoxes = ready.Select(item => (Id: item.Text("id"), Check: new CheckBox
        {
            Content = item.Text("title", "Audio"),
            IsChecked = true,
        })).ToArray();
        if (!await DialogAsync("Accoda audio pronti", Column(checkBoxes.Select(item => (UIElement)item.Check).ToArray()), "Accoda"))
            return;
        var identifiers = checkBoxes.Where(item => item.Check.IsChecked == true).Select(item => item.Id).ToArray();
        if (identifiers.Length == 0)
            return;
        // Stay within the Windows process command-line limit for large libraries.
        var responses = new List<JsonElement>();
        foreach (var batch in identifiers.Chunk(75))
        {
            var command = new List<string> { "transcription-queue", "enqueue" };
            foreach (var id in batch)
                command.AddRange(["--ready-recording", id]);
            responses.Add(await ReadAsync(command.ToArray()));
        }
        var added = responses.Sum(item => int.TryParse(item.Text("added"), out var count) ? count : 0);
        await DisplayPageAsync();
        Announce($"Richieste aggiunte alla coda: {added}.", added > 0 ? InfoBarSeverity.Success : InfoBarSeverity.Informational);
    }

    private Border JobCard(JsonElement job)
    {
        var id = job.Text("id");
        var textStatus = job.Text("status");
        var commands = new Dictionary<string, string>
        {
            ["remove"] = "Rimuovi",
            ["cancel"] = "Annulla",
            ["retry"] = "Riprova",
            ["restart"] = "Ricrea tentativo",
            ["recover"] = "Recupera",
            ["finish"] = "Termina salvataggio",
        };
        var actions = new List<UIElement>();
        foreach (var action in job.Strings("actions").Where(commands.ContainsKey))
        {
            var title = commands[action];
            actions.Add(ButtonFor(title, $"QueueJob{action}", async () =>
            {
                if (action is "remove" or "cancel" or "restart")
                {
                    if (!await ConfirmAsync(title, $"Applicare l’azione alla registrazione «{job.Text("title")}»?", title))
                        return;
                }
                if (action == "finish")
                    await ExecuteAsync("Richiesta di salvataggio elaborata.", "transcription-queue", "finish", "--recording", job.Text("recording_id"));
                else
                    await ExecuteAsync("Azione sulla coda elaborata.", "transcription-queue", action, "--job", id);
            }));
        }
        var output = job.Text("export_text", job.Field("result").Text("transcript_text"));
        if (output.Length > 0)
            actions.Add(ButtonFor("Apri TXT", "QueueOutput", () => OpenLocalAsync(output)));
        var diagnosis = job.Text("diagnostic_path");
        if (diagnosis.Length > 0 && Path.GetExtension(diagnosis) == ".txt")
            actions.Add(ButtonFor("Apri diagnostica", "QueueDiagnosis", () => OpenLocalAsync(diagnosis)));
        var duration = double.TryParse(job.Text("elapsed_seconds"), CultureInfo.InvariantCulture, out var seconds) && seconds is >= 0 and < 31536000
            ? TimeSpan.FromSeconds(seconds).ToString(@"hh\:mm\:ss", CultureInfo.InvariantCulture) : "";
        return Card(job.Text("title", "Trascrizione"), Label(Join(job.Text("course_code"), textStatus, job.Text("activity"), duration)),
            Label(Join(job.Text("error"), job.Text("recovery_notice"))), Actions(actions.ToArray()));
    }
}
