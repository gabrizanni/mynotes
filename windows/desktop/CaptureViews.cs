using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using System.Globalization;
using System.Text.Json;

namespace MyNotes.Desktop;

public sealed partial class MainWindow
{
    private async Task CalendarAsync()
    {
        var capture = await ReadAsync("capture", "dashboard");
        var capturePanel = new ContentControl { Content = CapturePanel(capture), HorizontalContentAlignment = HorizontalAlignment.Stretch };
        Page.Children.Add(capturePanel);

        var month = calendarDate.ToString("MMMM yyyy", CultureInfo.CurrentCulture);
        var picker = new DatePicker { Header = "Vai a una data", Date = new DateTimeOffset(calendarDate.ToDateTime(new TimeOnly(12, 0))) };
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetAutomationId(picker, "CalendarDate");
        Page.Children.Add(Card(month, picker, Actions(
            ButtonFor("Mese precedente", "CalendarPrevious", () => ChangeMonthAsync(calendarDate.AddMonths(-1))),
            ButtonFor("Oggi", "CalendarToday", () => ChangeMonthAsync(DateOnly.FromDateTime(DateTime.Today))),
            ButtonFor("Mese successivo", "CalendarNext", () => ChangeMonthAsync(calendarDate.AddMonths(1))),
            ButtonFor("Vai alla data", "CalendarGoTo", () => ChangeMonthAsync(DateOnly.FromDateTime(picker.Date.LocalDateTime))))));

        var entries = new ContentControl { Content = CalendarEntries(capture), HorizontalContentAlignment = HorizontalAlignment.Stretch };
        Page.Children.Add(entries);
        refreshVisiblePage = RefreshCalendarAsync;

        async Task RefreshCalendarAsync()
        {
            var updated = await ReadAsync("capture", "dashboard");
            if (running || refreshVisiblePage != (Func<Task>)RefreshCalendarAsync)
                return;
            var oldRuntime = capture.Field("runtime");
            var runtime = updated.Field("runtime");
            if (FieldsChanged(capture, updated, "configured", "enabled", "running", "paused", "courses", "standaloneSupported", "sources")
                || FieldsChanged(oldRuntime, runtime, "recording", "exporting", "sessions"))
                capturePanel.Content = CapturePanel(updated);
            if (FieldsChanged(capture, updated, "events", "sources", "courses", "standaloneSupported"))
                entries.Content = CalendarEntries(updated);
            capture = updated;
        }
    }

    private StackPanel CapturePanel(JsonElement capture)
    {
        var runtime = capture.Field("runtime");
        var codes = CourseCodes(capture);
        var panel = Column();
        var status = capture.Field("running").ValueKind switch
        {
            JsonValueKind.True => runtime.Flag("recording") ? "Registrazione in corso" : runtime.Flag("exporting") ? "Esportazione in corso" : "Capture in esecuzione",
            JsonValueKind.False => "Capture fermo",
            _ => "Stato di Capture non verificabile",
        };
        var controls = new List<UIElement>();
        if (!capture.Flag("configured"))
            controls.Add(ButtonFor("Configura Capture", "CaptureConfigure", () => ExecuteAsync("Capture configurato.", "capture", "configure")));
        else
        {
            var enabled = capture.Flag("enabled");
            controls.Add(ButtonFor(enabled ? "Disattiva" : "Attiva Capture", "CaptureEnable", () =>
                ExecuteAsync(enabled ? "Capture disattivato." : "Capture attivato.", "capture", enabled ? "disable" : "enable")));
            var paused = capture.Flag("paused");
            if (enabled)
                controls.Add(ButtonFor(paused ? "Riprendi pianificazione" : "Sospendi pianificazione", "CapturePause",
                    () => ExecuteAsync(paused ? "Pianificazione ripresa." : "Pianificazione sospesa.", "capture", paused ? "resume" : "pause")));
            if (enabled && capture.Field("running").ValueKind == JsonValueKind.False)
                controls.Add(ButtonFor("Riavvia Capture", "CaptureRestart", () =>
                    ExecuteAsync("Riavvio di Capture richiesto.", "capture", "enable")));
            if (capture.Flag("running"))
            {
                if (runtime.Flag("recording"))
                    controls.Add(ButtonFor("Ferma e salva", "CaptureStop", () => ExecuteAsync("Richiesta di arresto confermata.", "capture", "stop"), primary: true));
                else if (!runtime.Flag("exporting"))
                {
                    controls.Add(ButtonFor("Registra ora", "CaptureStart", () => RecordNowAsync(codes, capture.Flag("standaloneSupported")), primary: true));
                    controls.Add(ButtonFor("Prova microfono", "CaptureTest", () => ExecuteAsync("Prova microfono elaborata da Capture.", "capture", "test")));
                }
            }
        }
        controls.Add(ButtonFor("Collega calendario", "CalendarConnect", () => ConnectCalendarAsync()));
        controls.Add(ButtonFor("Importa orario JSON", "CalendarImport", ImportTimetableAsync));
        if (capture.Items("sources").Any())
            controls.Add(ButtonFor("Sincronizza", "CalendarSync", () => ExecuteAsync("Richiesta di sincronizzazione elaborata.", "capture", "sync")));
        if (codes.Length > 0 || capture.Flag("standaloneSupported"))
            controls.Add(ButtonFor("Programma lezione", "CalendarAdd", () => EditEventAsync(default, codes, capture.Flag("standaloneSupported"))));
        panel.Children.Add(Card("Capture", Label(status), Actions(controls.ToArray())));

        var sessions = runtime.Items("sessions").ToArray();
        foreach (var session in sessions.Where(item => item.Text("status") is "INTERRUPTED" or "FAILED" or "RECOVERABLE" || item.Flag("isTest")).Take(6))
        {
            var output = session.Text("outputPath");
            var sessionStatus = session.Text("status");
            var sessionActions = new List<UIElement>();
            if (sessionStatus is "INTERRUPTED" or "FAILED" or "RECOVERABLE")
                sessionActions.Add(ButtonFor("Recupera", "CaptureRecover", () => ExecuteAsync("Richiesta di recupero elaborata.", "capture", "recover", "--session", session.Text("id"))));
            if (output.Length > 0 && File.Exists(output))
            {
                sessionActions.Add(ButtonFor("Ascolta", "CaptureSessionPlay", () => OpenLocalAsync(output)));
                if (session.Flag("isTest") && sessionStatus == "COMPLETED")
                    sessionActions.Add(ButtonFor("Conferma ascolto", "CaptureConfirmTest", async () =>
                    {
                        if (await ConfirmAsync("Verifica del microfono", "Hai ascoltato il file e la registrazione è udibile?", "Conferma"))
                            await ExecuteAsync("Prova microfono confermata.", "capture", "confirm-test", "--audio", output, "--audible-confirmed");
                    }));
            }
            panel.Children.Add(Card(session.Flag("isTest") ? "Prova microfono" : session.Field("event").Text("title", "Registrazione"),
                Label(Join(sessionStatus, DisplayDate(session.Text("actualStart")))), Actions(sessionActions.ToArray())));
        }
        return panel;
    }

    private StackPanel CalendarEntries(JsonElement capture)
    {
        var panel = Column();
        var codes = CourseCodes(capture);
        var sourceRows = capture.Items("sources").ToArray();
        var visibleEvents = capture.Items("events").Select(item =>
        {
            var hasDate = DateTimeOffset.TryParse(item.Text("startsAt"), out var start);
            return (Item: item, Date: hasDate ? DateOnly.FromDateTime(start.ToLocalTime().DateTime) : (DateOnly?)null);
        }).Where(item => item.Date?.Month == calendarDate.Month && item.Date?.Year == calendarDate.Year)
            .GroupBy(item => item.Date!.Value).OrderBy(item => item.Key).ToArray();

        if (visibleEvents.Length == 0)
            panel.Children.Add(Label("Nessuna lezione programmata per questo mese."));
        foreach (var day in visibleEvents)
        {
            panel.Children.Add(Label(day.Key.ToString("D", CultureInfo.CurrentCulture), heading: true));
            foreach (var (eventItem, _) in day.OrderBy(item => item.Item.Text("startsAt")))
                panel.Children.Add(CalendarCard(eventItem, codes, capture.Flag("standaloneSupported")));
        }
        if (sourceRows.Length > 0)
            panel.Children.Add(Label("Calendari collegati", heading: true));
        foreach (var source in sourceRows)
            panel.Children.Add(CalendarSourceCard(source));
        return panel;
    }

    private Task ChangeMonthAsync(DateOnly date)
    {
        calendarDate = date;
        return DisplayPageAsync();
    }

    private async Task ImportTimetableAsync()
    {
        var paths = await PickFilesAsync([".json"], multiple: false);
        if (paths.Length == 0)
            return;
        if (!await ConfirmAsync("Importa orario", "Il file sostituirà gli eventi del precedente orario importato. I calendari collegati restano separati.", "Importa"))
            return;
        try
        {
            using var stream = File.OpenRead(paths[0]);
            using var document = await JsonDocument.ParseAsync(stream, cancellationToken: Token);
            await RequestAsync("Orario importato.", ["capture", "import"], document.RootElement);
        }
        catch (JsonException)
        {
            throw new BackendException("Il file scelto non contiene un orario JSON leggibile.");
        }
    }

    private Border CalendarCard(JsonElement item, string[] courses, bool standalone)
    {
        var id = item.Text("id");
        var actions = new List<UIElement>
        {
            ButtonFor("Modifica", "CalendarEdit", () => EditEventAsync(item, courses, standalone)),
        };
        if (item.Flag("localChanges"))
            actions.Add(ButtonFor("Ripristina calendario", "CalendarReset", async () =>
            {
                if (await ConfirmAsync("Ripristina evento", "Rimuovere le modifiche locali a questo evento?", "Ripristina"))
                    await ExecuteAsync("Modifiche locali rimosse.", "capture", "reset-lesson", "--event", id);
            }));
        return Card(item.Text("title", "Lezione"), Label(Join(item.Text("courseCode"), item.Text("lessonNumber"),
            DisplayDate(item.Text("startsAt")), DisplayDate(item.Text("endsAt")), item.Text("timezone"))),
            Label(Join(item.Text("eligibility"), item.Text("reason"))), Actions(actions.ToArray()));
    }

    private Border CalendarSourceCard(JsonElement source)
    {
        var id = source.Text("sourceID");
        return Card(source.Text("label", "Calendario"),
            Label(Join(source.Text("host"), source.Text("timezone"), DisplayDate(source.Text("lastSuccess")), source.Text("message"))),
            Actions(
                ButtonFor("Sincronizza", "CalendarSourceSync", () => ExecuteAsync("Richiesta di sincronizzazione elaborata.", "capture", "sync", "--source", id)),
                ButtonFor("Correggi collegamento", "CalendarSourceRepair", async () =>
                {
                    var result = await ReadAsync("capture", "source", "--source", id);
                    await ConnectCalendarAsync(result.Field("source"));
                }),
                ButtonFor("Scollega", "CalendarSourceDisconnect", async () =>
                {
                    if (await ConfirmAsync("Scollega calendario", "Il calendario sarà scollegato. Le registrazioni già salvate saranno conservate.", "Scollega"))
                        await ExecuteAsync("Calendario scollegato.", "capture", "unsubscribe", "--source", id);
                })));
    }

    private async Task RecordNowAsync(string[] courses, bool standalone)
    {
        if (courses.Length == 0 && !standalone)
            throw new BackendException("Aggiungi un corso prima di registrare con questa versione di Capture.");
        var course = Options("Corso", "RecordCourse", (standalone ? new[] { "Non assegnato" } : []).Concat(courses));
        var lesson = Input("Numero della lezione", "RecordLesson");
        var title = Input("Titolo (facoltativo)", "RecordTitle");
        var duration = Input("Durata massima in minuti", "RecordDuration", "60");
        var language = Options("Lingua della trascrizione", "RecordLanguage", ["Automatica", "it", "en"]);
        if (!await DialogAsync("Registra ora", Column(course, lesson, title, duration, language), "Registra"))
            return;
        if (!int.TryParse(duration.Text, out var minutes) || minutes is < 1 or > 720)
            throw new BackendException("Indica una durata da 1 a 720 minuti.");
        var arguments = new List<string> { "capture", "start", "--duration-minutes", minutes.ToString(CultureInfo.InvariantCulture) };
        if (Selected(course) == "Non assegnato")
            arguments.Add("--unassigned");
        else
        {
            if (!int.TryParse(lesson.Text, out var number) || number is < 1 or > 999)
                throw new BackendException("Indica un numero di lezione da 1 a 999.");
            arguments.AddRange(["--course", Selected(course), "--lesson", number.ToString(CultureInfo.InvariantCulture)]);
        }
        if (!string.IsNullOrWhiteSpace(title.Text))
            arguments.AddRange(["--title", title.Text.Trim()]);
        if (Selected(language) != "Automatica")
            arguments.AddRange(["--language", Selected(language)]);
        await ExecuteAsync("Capture ha confermato la richiesta di registrazione.", arguments.ToArray());
    }

    private async Task EditEventAsync(JsonElement item, string[] courses, bool standalone)
    {
        var existing = item.ValueKind == JsonValueKind.Object;
        var local = new DateTimeOffset(calendarDate.ToDateTime(new TimeOnly(9, 0)));
        var title = Input("Titolo", "EventTitle", item.Text("title"));
        var start = Input("Inizio ISO 8601 con fuso", "EventStart", item.Text("startsAt", local.ToString("o")));
        var end = Input("Fine ISO 8601 con fuso", "EventEnd", item.Text("endsAt", local.AddHours(1).ToString("o")));
        var zone = Input("Fuso del calendario (IANA)", "EventTimezone", item.Text("timezone", LocalTimeZone()));
        var course = Options("Corso", "EventCourse", (standalone ? new[] { "Non assegnato" } : []).Concat(courses));
        var number = Input("Numero della lezione", "EventNumber");
        var enabled = new CheckBox { Content = "Registra questa lezione", IsChecked = item.Flag("recordingEnabled") };
        var fields = existing ? Column(title, start, end, zone, enabled) : Column(title, start, end, zone, course, number);
        if (!await DialogAsync(existing ? "Modifica evento" : "Programma lezione", fields))
            return;
        var from = StrictDate(start.Text);
        var to = StrictDate(end.Text);
        if (string.IsNullOrWhiteSpace(title.Text) || to <= from || string.IsNullOrWhiteSpace(zone.Text))
            throw new BackendException("Indica un titolo, un fuso e un orario di fine successivo a quello di inizio.");
        var payload = new Dictionary<string, object?>
        {
            ["title"] = title.Text.Trim(), ["timezone"] = zone.Text.Trim(),
            ["startsAt"] = from.ToString("o"), ["endsAt"] = to.ToString("o"),
        };
        if (existing)
        {
            payload["recordingEnabled"] = enabled.IsChecked == true;
            await RequestAsync("Evento aggiornato.", ["capture", "edit-lesson", "--event", item.Text("id")], payload);
            return;
        }
        if (Selected(course) == "Non assegnato")
            payload["unassigned"] = true;
        else
        {
            if (!int.TryParse(number.Text, out var value) || value is < 1 or > 999)
                throw new BackendException("Indica un numero di lezione da 1 a 999.");
            payload["courseCode"] = Selected(course);
            payload["lessonNumber"] = value;
        }
        await RequestAsync("Lezione programmata.", ["capture", "add-lesson"], payload);
    }

    private async Task ConnectCalendarAsync(JsonElement source = default)
    {
        var existing = source.ValueKind == JsonValueKind.Object;
        var label = Input("Nome del calendario", "CalendarSourceName", source.Text("label", "Calendario lezioni"));
        var url = new PasswordBox { Header = "Link Google Calendar, HTTPS o webcal", Password = source.Text("url") };
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetAutomationId(url, "CalendarSourceUrl");
        var timezone = source.Text("timezone", LocalTimeZone());
        var zone = Input("Fuso del calendario (IANA)", "CalendarSourceTimezone", timezone);
        var start = Input("Da (AAAA-MM-GG)", "CalendarSourceFrom", CalendarPeriodDate(source.Text("periodStart"), timezone, DateTime.Today));
        var end = Input("Fino a (AAAA-MM-GG)", "CalendarSourceTo", CalendarPeriodDate(source.Text("periodEnd"), timezone, DateTime.Today.AddDays(120), exclusive: true));
        const string allTimed = "Tutti gli eventi con orario";
        const string mappedOnly = "Solo gli eventi associati";
        var mode = Options("Importa", "CalendarSourceMode", [allTimed, mappedOnly], source.Text("importMode") == "mapped" ? mappedOnly : allTimed);
        var rulesText = string.Join(Environment.NewLine, source.Items("mappings").Select(rule => string.Join(" = ",
            new[] { rule.Text("match"), rule.Text("courseCode"), rule.Text("firstLesson") }.Where(value => value.Length > 0))));
        var mappings = Input("Associazioni «testo = corso = prima lezione»", "CalendarSourceMappings", rulesText);
        mappings.AcceptsReturn = true;
        if (!await DialogAsync(existing ? "Correggi collegamento" : "Collega calendario iCal", Column(label, url, zone, start, end, mode, mappings), existing ? "Verifica e salva" : "Collega"))
            return;
        if (!Uri.TryCreate(url.Password.Trim(), UriKind.Absolute, out var address) || address.Scheme is not ("https" or "webcal")
            || !DateOnly.TryParseExact(start.Text.Trim(), "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var first)
            || !DateOnly.TryParseExact(end.Text.Trim(), "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var last)
            || first > last || last == DateOnly.MaxValue)
            throw new BackendException("Indica un link HTTPS o webcal e un intervallo di date valido.");
        var mapped = Selected(mode) == mappedOnly;
        var rules = new List<object>();
        foreach (var line in mappings.Text.Split(['\r', '\n'], StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
        {
            var pair = line.Split('=', 3, StringSplitOptions.TrimEntries);
            if (pair.Length < 2 || pair.Any(string.IsNullOrWhiteSpace))
                throw new BackendException("Scrivi ogni associazione nel formato «testo = corso = prima lezione».");
            if (pair.Length == 2 && !mapped)
                rules.Add(new { match = pair[0], courseCode = pair[1] });
            else if (pair.Length == 3 && int.TryParse(pair[2], out var firstLesson) && firstLesson is >= 1 and <= 999)
                rules.Add(new { match = pair[0], courseCode = pair[1], firstLesson });
            else
                throw new BackendException("Indica una prima lezione da 1 a 999 per l’associazione.");
        }
        if (mapped && rules.Count == 0)
            throw new BackendException("Aggiungi almeno un’associazione per importare solo gli eventi associati.");
        await RequestAsync(existing ? "Collegamento calendario aggiornato." : "Calendario collegato.", ["capture", "connect"], new
        {
            sourceID = source.Text("sourceID", Guid.NewGuid().ToString("N")), label = label.Text.Trim(), url = url.Password.Trim(), timezone = zone.Text.Trim(),
            periodStart = first.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
            periodEnd = last.AddDays(1).ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
            importMode = mapped ? "mapped" : "all_timed", mappings = rules,
        });
    }

    private static string CalendarPeriodDate(string value, string timezone, DateTime fallback, bool exclusive = false)
    {
        if (!DateTimeOffset.TryParse(value, CultureInfo.InvariantCulture, DateTimeStyles.None, out var instant)
            || !TimeZoneInfo.TryFindSystemTimeZoneById(timezone, out var zone))
            return fallback.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        var boundary = TimeZoneInfo.ConvertTime(exclusive ? instant.AddTicks(-1) : instant, zone);
        return boundary.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
    }

    private static string LocalTimeZone() => TimeZoneInfo.TryConvertWindowsIdToIanaId(TimeZoneInfo.Local.Id, out var timezone)
        ? timezone
        : "UTC";
}
