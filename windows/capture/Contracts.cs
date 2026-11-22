using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace MyNotes.Capture;

internal sealed record CaptureConfig(
    int SchemaVersion, int Revision, bool Enabled, bool Paused, string OutputDirectory,
    string StageDirectory, string PythonExecutable, string[] CourseCodes, CalendarSource[]? Sources);

internal sealed record CalendarSource(string SourceID);

internal sealed record Schedule(int SchemaVersion, int Revision, Lesson[] Events);

internal sealed record Lesson(
    string Id, string SourceID, string? CourseCode, int? LessonNumber, DateTimeOffset StartsAt,
    DateTimeOffset EndsAt, DateTimeOffset RecordAt, DateTimeOffset StopAt, string Timezone,
    string Title, string Eligibility, string Reason, string? RecordingID = null,
    string? TranscriptionLanguage = null)
{
    private static readonly Regex ShortTitle = new(@"^([A-Z][A-Z0-9]{1,15})[ _-]+(?:L[ _-]*)?([0-9]{1,3})$");
    private static readonly Regex Course = new(@"^[A-Z0-9]+$");
    private static readonly Regex Recording = new(@"^[0-9a-f]{32}$");

    internal string OutputStem
    {
        get
        {
            var match = ShortTitle.Match(Title.Trim());
            if (match.Success && int.TryParse(match.Groups[2].Value, out var number)
                && number is >= 1 and <= 999 && (LessonNumber is null || LessonNumber == number))
                return $"{match.Groups[1].Value}_L{number:00}";
            return CourseCode is not null && LessonNumber is not null
                ? $"{CourseCode}_L{LessonNumber:00}"
                : LegacyStem;
        }
    }

    internal string LegacyStem
    {
        get
        {
            if (RecordingID is null)
                return $"{CourseCode}_L{LessonNumber:00}";
            var slug = new StringBuilder();
            foreach (var rune in Title.EnumerateRunes())
            {
                if (slug.Length == 64)
                    break;
                slug.Append(rune.IsAscii && char.IsAsciiLetterOrDigit((char)rune.Value) ? (char)rune.Value : '-');
            }
            return $"{StartsAt.ToUnixTimeSeconds()}-{slug}-{RecordingID}";
        }
    }

    internal bool ValidWindow(string[] courses)
    {
        if (string.IsNullOrWhiteSpace(Id) || string.IsNullOrWhiteSpace(Title) || Title.Length > 8192)
            return false;
        if (CourseCode is not null && (!Course.IsMatch(CourseCode) || !courses.Contains(CourseCode)))
            return false;
        if (LessonNumber is not null && LessonNumber is not (>= 1 and <= 999))
            return false;
        if (RecordingID is null ? CourseCode is null || LessonNumber is null
            : !Recording.IsMatch(RecordingID))
            return false;
        if (!TimeZoneInfo.TryFindSystemTimeZoneById(Timezone, out _))
            return false;
        return RecordAt <= StartsAt && StartsAt < EndsAt && EndsAt <= StopAt
            && EndsAt - StartsAt <= TimeSpan.FromHours(12) && StopAt - RecordAt <= TimeSpan.FromHours(14);
    }

    internal bool IsCurrent(DateTimeOffset now) => Eligibility == "SCHEDULED" && RecordAt <= now && now < EndsAt;

    internal bool CanResume(Lesson lesson) => Id == lesson.Id && RecordingID == lesson.RecordingID
        && CourseCode == lesson.CourseCode && LessonNumber == lesson.LessonNumber
        && RecordAt == lesson.RecordAt && StartsAt == lesson.StartsAt && EndsAt == lesson.EndsAt
        && StopAt == lesson.StopAt && OutputStem.Equals(lesson.OutputStem, StringComparison.OrdinalIgnoreCase);
}

internal sealed record CaptureSession(
    int SchemaVersion, string Id, Lesson Event, string Directory, DateTimeOffset ActualStart,
    DateTimeOffset? ActualStop, string Status, bool Partial, string Reason, string[] Segments,
    string? OutputPath, string DestinationDirectory, double? DurationSeconds, string? RequestID,
    bool IsTest, string? ExportSHA256 = null, string? ExportTemporaryPath = null);

internal sealed record RequestPayload(Lesson? Event, string? SessionID);
internal sealed record ControlRequest(
    int SchemaVersion, string Id, string Action, DateTimeOffset CreatedAt, DateTimeOffset ExpiresAt, RequestPayload Payload);
internal sealed record ControlResponse(string Status, string RequestID, string Message, string? SessionID, string? OutputPath);

internal static class CaptureJson
{
    private static readonly JsonSerializerOptions Options = new(JsonSerializerDefaults.Web) { WriteIndented = true };

    internal static T Read<T>(string path)
    {
        // Allow a writer to atomically replace the file while it is being read.
        using var file = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
        return JsonSerializer.Deserialize<T>(file, Options) ?? throw new JsonException("Missing Capture state");
    }

    internal static void Write<T>(string path, T value)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write))
            {
                JsonSerializer.Serialize(stream, value, Options);
                stream.Flush(flushToDisk: true);
            }
            for (var attempt = 0; ; attempt++)
            {
                try
                {
                    File.Move(temporary, path, overwrite: true);
                    break;
                }
                catch (IOException error) when ((error.HResult & 0xffff) == 32 && attempt < 5)
                {
                    // Python readers open these short-lived snapshots without FILE_SHARE_DELETE.
                    Thread.Sleep(20 * (attempt + 1));
                }
            }
        }
        finally
        {
            File.Delete(temporary);
        }
    }
}

internal static class CaptureLease
{
    internal static FileStream Acquire(string path)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var stream = new FileStream(path, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.ReadWrite);
        try
        {
            // Uses the same first-byte range as MyNotes' Python LockFileEx wrapper.
            stream.Lock(0, 1);
            return stream;
        }
        catch
        {
            stream.Dispose();
            throw;
        }
    }
}
