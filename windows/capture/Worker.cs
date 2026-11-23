using System.Diagnostics;
using System.Globalization;
using System.Security.Cryptography;
using System.Text.Json;
using Microsoft.Win32;

namespace MyNotes.Capture;

internal sealed class Worker : IDisposable
{
    private readonly string workspace;
    private readonly string captureHome;
    private readonly string stateDirectory;
    private readonly string configPath;
    private readonly string schedulePath;
    private readonly string requestDirectory;
    private readonly string responseDirectory;
    private readonly string sessionDirectory;
    private readonly FileStream instance;
    private readonly Dictionary<string, CaptureSession> sessions = new(StringComparer.Ordinal);
    private readonly Dictionary<string, (CaptureSession Session, Task<double> Task, Action<bool>? Done)> exports = new(StringComparer.Ordinal);
    private readonly HashSet<string> attempted = new(StringComparer.Ordinal);
    private readonly HashSet<string> automaticRecoveryAttempted = new(StringComparer.Ordinal);
    private readonly Dictionary<string, string> blocked = new(StringComparer.Ordinal);
    private CaptureConfig config;
    private Schedule schedule;
    private CaptureSession? current;
    private MicrophoneRecording? recorder;
    private FileStream? updateLease;
    private Process? calendarSync;
    private DateTimeOffset nextSync;
    private DateTimeOffset nextStatus;
    private string title = "Capture";
    private string detail = "";
    private string permission = MicrophoneRecording.Permission();
    private volatile bool suspended;
    private int interrupted;

    internal Worker(string workspace, string captureHome)
    {
        this.workspace = workspace;
        this.captureHome = captureHome;
        stateDirectory = Path.Combine(workspace, "state", "capture");
        configPath = Path.Combine(workspace, "config", "capture.json");
        schedulePath = Path.Combine(stateDirectory, "schedule.json");
        requestDirectory = Path.Combine(stateDirectory, "requests");
        responseDirectory = Path.Combine(stateDirectory, "responses");
        sessionDirectory = Path.Combine(stateDirectory, "sessions");
        config = CaptureJson.Read<CaptureConfig>(configPath);
        schedule = CaptureJson.Read<Schedule>(schedulePath);
        ValidateState(config, schedule);
        RebuildBlockedEvents(config, schedule);
        instance = CaptureLease.Acquire(Path.Combine(captureHome, "app.lock"));
        try
        {
            foreach (var directory in new[] { requestDirectory, responseDirectory, sessionDirectory })
                Directory.CreateDirectory(directory);
            LoadSessions();
        }
        catch
        {
            instance.Dispose();
            throw;
        }
    }

    internal void PowerChanged(object? sender, PowerModeChangedEventArgs args)
    {
        if (args.Mode == PowerModes.Suspend)
        {
            suspended = true;
            Interlocked.Exchange(ref interrupted, 1);
        }
        else if (args.Mode == PowerModes.Resume)
            suspended = false;
    }

    internal async Task RunAsync(CancellationToken cancellation)
    {
        try
        {
            while (!cancellation.IsCancellationRequested)
            {
                var now = DateTimeOffset.UtcNow;
                if (Interlocked.Exchange(ref interrupted, 0) != 0)
                    await StopAsync(now, "Windows sleep interrupted recording", interrupt: true);
                if (!suspended)
                {
                    var valid = Reload();
                    await FinishExports();
                    if (current is not null)
                    {
                        if (recorder?.Completed.IsCompleted == true)
                            await StopAsync(now, "Microphone recording interrupted", interrupt: true);
                        else if (now >= current.Event.StopAt)
                            await StopAsync(now, "Scheduled end");
                    }
                    if (valid && !config.Enabled && current is null && exports.Count == 0)
                        break;
                    if (valid && config.Enabled)
                    {
                        await HandleRequests(now);
                        ReconcileElapsed(now);
                        if (current is null && !config.Paused)
                            StartScheduled(now);
                        RecoverAutomatically(now);
                        SyncCalendar(now);
                    }
                    if (now >= nextStatus)
                        PersistRuntime(now);
                }
                await Task.Delay(500, cancellation);
            }
        }
        catch (OperationCanceledException) when (cancellation.IsCancellationRequested)
        {
        }
        finally
        {
            await StopAsync(DateTimeOffset.UtcNow, "Capture shut down", interrupt: true);
            foreach (var export in exports.Values)
            {
                try { await export.Task; }
                catch (Exception error) when (ResourceError(error)) { }
            }
            await FinishExports();
            title = "Capture stopped";
            PersistRuntime(DateTimeOffset.UtcNow, stopped: true);
        }
    }

    private static void ValidateState(CaptureConfig next, Schedule nextSchedule)
    {
        if (next.SchemaVersion != 1 || nextSchedule.SchemaVersion != 1
            || next.CourseCodes is null || nextSchedule.Events is null
            || !Path.IsPathFullyQualified(next.StageDirectory) || !Path.IsPathFullyQualified(next.OutputDirectory))
            throw new InvalidDataException("Unsupported Capture configuration or schedule");
    }

    private bool Reload()
    {
        try
        {
            var next = CaptureJson.Read<CaptureConfig>(configPath);
            var nextSchedule = CaptureJson.Read<Schedule>(schedulePath);
            ValidateState(next, nextSchedule);
            if (next.Revision != config.Revision || nextSchedule.Revision != schedule.Revision)
                RebuildBlockedEvents(next, nextSchedule);
            config = next;
            schedule = nextSchedule;
            return true;
        }
        catch (Exception error) when (ResourceError(error))
        {
            Announce("Capture configuration unavailable", error.Message);
            return false;
        }
    }

    private void RebuildBlockedEvents(CaptureConfig next, Schedule timetable)
    {
        blocked.Clear();
        var eligible = timetable.Events.Where(item => item.Eligibility == "SCHEDULED").ToArray();
        foreach (var eventGroup in eligible.GroupBy(item => item.Id).Where(group => group.Count() > 1))
            blocked[eventGroup.Key] = "Duplicate calendar event";
        foreach (var item in eligible.Where(item => !item.ValidWindow(next.CourseCodes)))
            blocked[item.Id] = "Invalid recording details or time window";

        var valid = eligible.Where(item => !blocked.ContainsKey(item.Id)).OrderBy(item => item.StartsAt).ToArray();
        foreach (var group in valid.GroupBy(item => item.OutputStem, StringComparer.OrdinalIgnoreCase).Where(group => group.Count() > 1))
            foreach (var item in group)
                blocked[item.Id] = "Duplicate recording filename";
        for (var first = 0; first < valid.Length; first++)
            for (var second = first + 1; second < valid.Length && valid[second].StartsAt < valid[first].EndsAt; second++)
            {
                blocked[valid[first].Id] = "Overlapping lectures";
                blocked[valid[second].Id] = "Overlapping lectures";
            }
    }

    private void StartScheduled(DateTimeOffset now)
    {
        var item = schedule.Events.Where(item => item.IsCurrent(now) && !blocked.ContainsKey(item.Id))
            .OrderBy(item => item.RecordAt)
            .FirstOrDefault(item => !attempted.Contains(item.Id) && !HasOutput(item) && !Exporting(item.Id));
        if (item is null)
            return;
        try
        {
            Begin(item, now);
        }
        catch (Exception error) when (ResourceError(error) || error is InvalidOperationException)
        {
            // A live installer holds the lease only temporarily. Retry after
            // it releases the lock, if this worker has not been stopped.
            if (error is not CaptureBusyException)
                attempted.Add(item.Id);
            Announce("Recording could not start", error.Message);
        }
    }

    private void Begin(Lesson item, DateTimeOffset now, string? requestID = null, bool isTest = false)
    {
        if (current is not null)
            throw new InvalidOperationException("A recording is already active");
        if ((!isTest && (!item.ValidWindow(config.CourseCodes) || blocked.ContainsKey(item.Id)))
            || !item.IsCurrent(now) || (!isTest && (HasOutput(item) || Exporting(item.Id))))
            throw new InvalidOperationException("Recording is unavailable or existing audio was retained");

        EnsureUpdateLease();
        try
        {
            var interruptedSessions = sessions.Values.Where(s => s.Event.Id == item.Id && s.Status == "INTERRUPTED" && !s.IsTest).ToArray();
            if (interruptedSessions.Length == 1 && interruptedSessions[0].Event.CanResume(item) && !isTest)
            {
                current = interruptedSessions[0] with
                {
                    ActualStop = null, Status = "RECORDING", Partial = true,
                    Reason = "Resumed after interruption; audio contains a gap", RequestID = requestID
                };
            }
            else
            {
                var id = Guid.NewGuid().ToString();
                var late = !isTest && now > item.StartsAt.AddSeconds(5);
                var reason = interruptedSessions.Length != 0
                    ? "New attempt after a calendar change or ambiguity; earlier audio retained"
                    : late ? "Started late; beginning of the lesson was not recorded" : "";
                var directory = Path.Combine(config.StageDirectory, isTest ? Path.Combine("tests", id) : id);
                current = new CaptureSession(1, id, item, directory, now, null, "RECORDING",
                    late || interruptedSessions.Length != 0, reason, [], null, config.OutputDirectory, null, requestID, isTest);
            }
            Save(current);
            recorder = new MicrophoneRecording(current.Directory);
            recorder.Start();
            Announce(isTest ? "Microphone test" : "Recording", item.Title);
            PersistRuntime(now);
        }
        catch (Exception error) when (ResourceError(error))
        {
            if (error is UnauthorizedAccessException
                || error is System.Runtime.InteropServices.COMException com && com.HResult == unchecked((int)0x80070005))
                permission = "DENIED";
            recorder?.Dispose();
            recorder = null;
            var failed = current;
            current = null;
            try
            {
                if (failed is not null)
                    Save(failed with { Status = "FAILED", ActualStop = now, Partial = true, Reason = error.Message });
            }
            finally
            {
                ReleaseWhenIdle();
            }
            throw;
        }
    }

    private async Task StopAsync(DateTimeOffset now, string reason, bool interrupt = false, string? requestID = null)
    {
        if (current is not { } session || recorder is not { } microphone)
        {
            if (requestID is not null)
                Respond(requestID, "REJECTED", "No active recording");
            return;
        }
        try
        {
            var error = await microphone.StopAsync();
            if (error is not null)
            {
                interrupt = true;
                reason = error.Message;
            }
        }
        catch (Exception error) when (ResourceError(error))
        {
            interrupt = true;
            reason = error.Message;
        }
        finally
        {
            microphone.Dispose();
            recorder = null;
            current = null;
        }

        var segments = StageSegments(session);
        if (segments.Length != 0)
            permission = "AUTHORIZED";
        var early = !session.IsTest && now < session.Event.EndsAt.AddSeconds(-5);
        if (!string.IsNullOrWhiteSpace(session.Reason))
            reason = session.Reason + "; " + reason;
        session = session with
        {
            ActualStop = now, Partial = session.Partial || interrupt || early,
            Reason = reason, Segments = segments, RequestID = requestID ?? session.RequestID
        };
        if (interrupt || segments.Length == 0)
        {
            session = session with { Status = interrupt ? "INTERRUPTED" : "FAILED", Partial = true };
            Save(session);
            ReleaseWhenIdle();
            if (requestID is not null)
                Respond(requestID, session.Status, segments.Length == 0 ? "No microphone audio was captured" : reason, session);
            return;
        }
        try
        {
            QueueExport(session, requestID);
        }
        catch (Exception error) when (ResourceError(error))
        {
            session = session with { Status = "FAILED", Partial = true, Reason = error.Message };
            Save(session);
            ReleaseWhenIdle();
            if (requestID is not null)
                Respond(requestID, session.Status, error.Message, session);
            Announce("Audio export unavailable", error.Message);
        }
    }

    private void QueueExport(CaptureSession value, string? requestID = null, Action<bool>? done = null)
    {
        EnsureUpdateLease();
        try
        {
            var destination = value.IsTest ? Path.Combine(value.Directory, "microphone-test.m4a")
                : Path.Combine(value.DestinationDirectory, value.Event.OutputStem + ".m4a");
            if (File.Exists(destination) || (!value.IsTest && HasOutput(value.Event, value.DestinationDirectory)))
                throw new IOException("Existing output retained");

            var temporary = Path.Combine(Path.GetDirectoryName(destination)!, $".capture-{value.Id}-{Guid.NewGuid():N}.tmp");
            var session = value with
            {
                Status = "EXPORTING", Segments = StageSegments(value), RequestID = requestID ?? value.RequestID,
                OutputPath = destination, ExportSHA256 = null, ExportTemporaryPath = temporary
            };
            Save(session);
            // Both arguments are derived from this worker's private state; the
            // CLI shares the workspace path via argv, not shell expansion.
            var export = RunPythonExport(session.Id, config.PythonExecutable);
            exports.Add(session.Id, (session, export, done));
            if (current is null)
                Announce("Saving audio", value.Event.Title);
            PersistRuntime(DateTimeOffset.UtcNow);
        }
        catch
        {
            ReleaseWhenIdle();
            throw;
        }
    }

    private async Task<double> RunPythonExport(string id, string python)
    {
        using var process = StartPython(python, ["export", "--workspace", workspace, "--session", id], "mynotes.capture_windows", redirect: true);
        var stdout = process.StandardOutput.ReadToEndAsync();
        var stderr = process.StandardError.ReadToEndAsync();
        using var deadline = new CancellationTokenSource(TimeSpan.FromMinutes(30));
        try
        {
            await process.WaitForExitAsync(deadline.Token);
        }
        catch (OperationCanceledException)
        {
            process.Kill(entireProcessTree: true);
            await process.WaitForExitAsync();
            throw new IOException("Capture export exceeded the time limit");
        }
        var result = await stdout;
        _ = await stderr;
        if (process.ExitCode != 0 || !double.TryParse(result.Trim(), CultureInfo.InvariantCulture, out var seconds) || !double.IsFinite(seconds) || seconds <= 0)
            throw new IOException("Audio conversion failed; staged recording preserved for recovery");
        return seconds;
    }

    private async Task FinishExports()
    {
        foreach (var entry in exports.Where(entry => entry.Value.Task.IsCompleted).ToArray())
        {
            var (session, task, done) = entry.Value;
            var success = false;
            try
            {
                var temporary = session.ExportTemporaryPath!;
                var duration = await task;
                session = session with { DurationSeconds = duration, ExportSHA256 = Hash(temporary) };
                Save(session);
                File.Move(temporary, session.OutputPath!, overwrite: false);
                session = FinalizeExport(session);
                success = true;
            }
            catch (Exception error) when (ResourceError(error))
            {
                // A successful move may have been followed by a failed metadata
                // write. Keep the digest so startup can reconcile that move.
                session = session with { Status = "FAILED", Partial = true, Reason = error.Message };
                Save(session);
            }
            finally
            {
                exports.Remove(entry.Key);
                ReleaseWhenIdle();
            }
            if (session.RequestID is { } id && done is null)
                Respond(id, session.Status, success ? "Audio saved" : session.Reason, session);
            done?.Invoke(success);
            if (current is null)
                Announce(success ? "Audio saved" : "Audio export failed", success ? session.Event.Title : session.Reason);
        }
    }

    private CaptureSession FinalizeExport(CaptureSession session)
    {
        var final = session with
        {
            Status = session.Partial ? "PARTIAL" : "COMPLETED",
            ExportTemporaryPath = null
        };
        if (final.IsTest)
            CaptureJson.Write(final.OutputPath + ".capture.json", final);
        Save(final);
        return final;
    }

    private async Task HandleRequests(DateTimeOffset now)
    {
        foreach (var path in Directory.EnumerateFiles(requestDirectory, "*.json").Order())
        {
            var id = Path.GetFileNameWithoutExtension(path);
            if (!Guid.TryParseExact(id, "N", out _) && !Guid.TryParseExact(id, "D", out _))
                continue;
            if (File.Exists(Path.Combine(responseDirectory, id + ".json")))
            {
                File.Delete(path);
                continue;
            }
            if (exports.Values.Any(entry => entry.Session.RequestID == id))
                continue;
            try
            {
                var request = CaptureJson.Read<ControlRequest>(path);
                if (request.Id != id || request.SchemaVersion != 1 || request.ExpiresAt < now
                    || request.CreatedAt > now.AddSeconds(5) || request.ExpiresAt < request.CreatedAt
                    || request.ExpiresAt - request.CreatedAt > TimeSpan.FromMinutes(1))
                    throw new InvalidDataException("Expired or invalid request");
                if (sessions.Values.FirstOrDefault(s => s.RequestID == id) is { } previous)
                {
                    Respond(id, previous.Status, "Previously accepted action", previous);
                    continue;
                }
                switch (request.Action)
                {
                    case "start" when request.Payload.Event is { } item && item.StopAt <= now.AddHours(12):
                        Begin(item, now, id);
                        Respond(id, "RECORDING", "Recording started", current);
                        break;
                    case "test":
                        var end = now.AddMinutes(1);
                        var test = new Lesson(Guid.NewGuid().ToString(), "test", "TEST", 1, now, end, now, end,
                            "UTC", "Microphone test", "SCHEDULED", "");
                        Begin(test, now, id, isTest: true);
                        Respond(id, "RECORDING", "60-second microphone test started", current);
                        break;
                    case "stop":
                        await StopAsync(now, "Stopped manually", requestID: id);
                        break;
                    case "recover":
                        RecoverRequested(request.Payload.SessionID, id);
                        break;
                    default:
                        Respond(id, "REJECTED", "Unknown action or invalid recording details");
                        break;
                }
            }
            catch (Exception error) when (ResourceError(error) || error is InvalidOperationException)
            {
                Respond(id, "REJECTED", error.Message);
            }
        }
    }

    private void RecoverRequested(string? requested, string id)
    {
        if (current is not null)
            throw new InvalidOperationException("Stop the recording before recovering staged audio");
        var eligible = RecoveryCandidates().Where(s => requested is null || s.Id == requested).ToArray();
        if (eligible.Length == 0)
            throw new InvalidOperationException("No eligible staged session");
        if (requested is null && eligible.GroupBy(s => s.Event.OutputStem, StringComparer.OrdinalIgnoreCase).Any(group => group.Count() != 1))
            throw new InvalidOperationException("Multiple attempts exist; specify --session");

        var remaining = eligible.Length;
        var successes = 0;
        foreach (var session in eligible)
        {
            void Done(bool ok)
            {
                if (ok)
                    successes++;
                if (--remaining == 0)
                    Respond(id, successes == eligible.Length ? "PARTIAL" : "FAILED", $"Recovered {successes}/{eligible.Length} attempts",
                        eligible.Length == 1 ? sessions[session.Id] : null);
            }
            try
            {
                // A single recovery has a durable request association of its own.
                QueueExport(session with { Partial = true, Reason = "Recovered staged audio; verify coverage", RequestID = null },
                    eligible.Length == 1 ? id : null, Done);
            }
            catch (Exception error) when (ResourceError(error))
            {
                Announce("Recovery failed", error.Message);
                Done(false);
            }
        }
        // A grouped request is in flight until the export callbacks publish its response.
        if (eligible.Length > 1 && remaining > 0)
        {
            CaptureJson.Write(Path.Combine(responseDirectory, id + ".json"), new ControlResponse("EXPORTING", id, "Recovering staged audio", null, null));
            File.Delete(Path.Combine(requestDirectory, id + ".json"));
        }
    }

    private IEnumerable<CaptureSession> RecoveryCandidates() => sessions.Values.Where(session =>
        (session.Status is "INTERRUPTED" or "FAILED") && !session.IsTest && !Exporting(session.Event.Id)
        && !HasOutput(session.Event, session.DestinationDirectory) && StageSegments(session).Length != 0);

    private void RecoverAutomatically(DateTimeOffset now)
    {
        foreach (var group in RecoveryCandidates().Where(s => s.Event.StopAt <= now && current?.Event.Id != s.Event.Id)
                     .GroupBy(s => s.Event.OutputStem, StringComparer.OrdinalIgnoreCase).ToArray())
        {
            var candidates = group.ToArray();
            if (candidates.Length != 1 || automaticRecoveryAttempted.Contains(candidates[0].Id))
                continue;
            var session = candidates[0];
            try
            {
                QueueExport(session with { Partial = true, Reason = "Recovered after interruption; verify coverage", RequestID = null });
                automaticRecoveryAttempted.Add(session.Id);
            }
            catch (Exception error) when (ResourceError(error))
            {
                if (error is not CaptureBusyException)
                    automaticRecoveryAttempted.Add(session.Id);
                Announce("Recovery unavailable", error.Message);
            }
        }
    }

    private void ReconcileElapsed(DateTimeOffset now)
    {
        foreach (var item in schedule.Events.Where(item => item.Eligibility == "SCHEDULED" && item.EndsAt <= now
                     && !blocked.ContainsKey(item.Id)))
        {
            if (sessions.Values.Any(session => session.Event.Id == item.Id))
                continue;
            var exists = HasOutput(item);
            Save(new CaptureSession(1, Guid.NewGuid().ToString(), item, "", now, now, exists ? "EXISTING" : "MISSED",
                !exists, exists ? "Existing output retained" : "Recording window elapsed", [], null, config.OutputDirectory, null, null, false));
        }
    }

    private bool HasOutput(Lesson item, string? folder = null)
    {
        folder ??= config.OutputDirectory;
        if (new[] { item.OutputStem, item.LegacyStem }.Any(name =>
                new[] { ".m4a", ".wav" }.Any(extension => File.Exists(Path.Combine(folder, name + extension)))))
            return true;
        return sessions.Values.Any(s => s.Event.Id == item.Id && File.Exists(s.OutputPath));
    }

    private bool Exporting(string eventID) => exports.Values.Any(entry => entry.Session.Event.Id == eventID);

    private void LoadSessions()
    {
        foreach (var path in Directory.EnumerateFiles(sessionDirectory, "*.json"))
        {
            try
            {
                var session = CaptureJson.Read<CaptureSession>(path);
                if (session.SchemaVersion != 1 || !Guid.TryParse(session.Id, out _) || session.Id != Path.GetFileNameWithoutExtension(path))
                    continue;
                sessions[session.Id] = session;
                if (session.Status is "RECORDING" or "EXPORTING" || (session.Status == "FAILED" && session.ExportSHA256 is not null))
                {
                    if (session.ExportSHA256 is not null && session.OutputPath is { } output && File.Exists(output)
                        && string.Equals(Hash(output), session.ExportSHA256, StringComparison.OrdinalIgnoreCase))
                    {
                        FinalizeExport(session);
                        continue;
                    }
                    Save(session with
                    {
                        Status = "INTERRUPTED", Partial = true, ActualStop = session.ActualStop ?? DateTimeOffset.UtcNow,
                        Reason = "Capture restarted during recording or export; staged audio retained",
                        Segments = StageSegments(session)
                    });
                }
            }
            catch (Exception error) when (ResourceError(error))
            {
                Announce("A Capture session could not be restored", error.Message);
            }
        }
    }

    private static string[] StageSegments(CaptureSession session) => Directory.Exists(session.Directory)
        ? Directory.EnumerateFiles(session.Directory, "*.wav").Where(path => new FileInfo(path).Length > 44).Order(StringComparer.Ordinal).ToArray()
        : [];

    private void Save(CaptureSession session)
    {
        CaptureJson.Write(Path.Combine(sessionDirectory, session.Id + ".json"), session);
        sessions[session.Id] = session;
    }

    private void Respond(string id, string status, string message, CaptureSession? session = null)
    {
        CaptureJson.Write(Path.Combine(responseDirectory, id + ".json"),
            new ControlResponse(status, id, message, session?.Id, session?.OutputPath));
        File.Delete(Path.Combine(requestDirectory, id + ".json"));
    }

    private void SyncCalendar(DateTimeOffset now)
    {
        if (calendarSync is { HasExited: true })
        {
            if (calendarSync.ExitCode != 0)
                Announce("Calendar update unsuccessful", "Check the calendar source in MyNotes");
            calendarSync.Dispose();
            calendarSync = null;
        }
        if (calendarSync is not null || now < nextSync || config.Sources is not { Length: > 0 })
            return;
        nextSync = now.AddMinutes(5);
        try
        {
            calendarSync = StartPython(config.PythonExecutable, ["capture", "sync", "--workspace", workspace, "--if-due", "--json"]);
        }
        catch (Exception error) when (ResourceError(error))
        {
            Announce("Calendar sync unavailable", error.Message);
        }
    }

    private Process StartPython(string python, string[] arguments, string module = "mynotes", bool redirect = false)
    {
        if (!Path.IsPathFullyQualified(python) || !Path.GetExtension(python).Equals(".exe", StringComparison.OrdinalIgnoreCase))
            throw new IOException("Capture requires a native Python executable");
        var start = new ProcessStartInfo(python)
        {
            UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardOutput = redirect, RedirectStandardError = redirect
        };
        start.Environment["MYNOTES_CAPTURE_HOME"] = captureHome;
        start.ArgumentList.Add("-I");
        start.ArgumentList.Add("-X");
        start.ArgumentList.Add("utf8");
        start.ArgumentList.Add("-m");
        start.ArgumentList.Add(module);
        foreach (var argument in arguments)
            start.ArgumentList.Add(argument);
        return Process.Start(start) ?? throw new IOException("Cannot start Capture runtime");
    }

    private void PersistRuntime(DateTimeOffset now, bool stopped = false)
    {
        nextStatus = now.AddSeconds(4);
        CaptureJson.Write(Path.Combine(stateDirectory, "runtime.json"), new
        {
            SchemaVersion = 1, Pid = stopped ? 0 : Environment.ProcessId, UpdatedAt = now,
            Recording = current is not null, Exporting = exports.Count != 0,
            MicrophonePermission = permission, Title = title, Detail = detail,
            CurrentSession = current, Sessions = sessions.Values.OrderByDescending(s => s.ActualStart).ToArray(),
            Capabilities = new[] { "standalone-recordings-v1", "calendar-recordings-v1" },
            ScheduleIssues = schedule.Events.Where(item => blocked.ContainsKey(item.Id))
                .Select(item => $"{item.Title}: {blocked[item.Id]}").ToArray(), BlockedEvents = blocked
        });
    }

    private void Announce(string message, string details)
    {
        title = message;
        detail = details;
        nextStatus = DateTimeOffset.MinValue;
    }

    private void EnsureUpdateLease()
    {
        if (updateLease is not null)
            return;
        try
        {
            updateLease = CaptureLease.Acquire(Path.Combine(captureHome, "update.lock"));
        }
        catch (IOException error) when ((error.HResult & 0xffff) == 33)
        {
            throw new CaptureBusyException(error);
        }
    }

    private void ReleaseWhenIdle()
    {
        if (current is not null || exports.Count != 0)
            return;
        updateLease?.Dispose();
        updateLease = null;
    }

    private static string Hash(string path)
    {
        using var stream = File.OpenRead(path);
        return Convert.ToHexString(SHA256.HashData(stream));
    }

    private static bool ResourceError(Exception error) => error is IOException or UnauthorizedAccessException or JsonException
        or ArgumentException or System.ComponentModel.Win32Exception or System.Runtime.InteropServices.COMException or TimeoutException;

    public void Dispose()
    {
        recorder?.Dispose();
        calendarSync?.Dispose();
        updateLease?.Dispose();
        instance.Dispose();
    }

    private sealed class CaptureBusyException(Exception inner) : IOException("Capture is blocked during an application update", inner)
    {
    }
}
