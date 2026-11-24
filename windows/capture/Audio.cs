using System.Diagnostics;
using System.Runtime.InteropServices;
using NAudio.CoreAudioApi;
using NAudio.Wave;

namespace MyNotes.Capture;

internal sealed class MicrophoneRecording : IDisposable
{
    private readonly object gate = new();
    private readonly string folder;
    private readonly WasapiCapture capture;
    private readonly TaskCompletionSource<Exception?> completed = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private readonly Stopwatch flushed = Stopwatch.StartNew();
    private WaveFileWriter? writer;

    internal Task<Exception?> Completed => completed.Task;

    internal MicrophoneRecording(string folder)
    {
        this.folder = folder;
        Directory.CreateDirectory(folder);
        capture = new WasapiCapture();
        capture.DataAvailable += OnDataAvailable;
        capture.RecordingStopped += OnStopped;
    }

    internal static string Permission()
    {
        try
        {
            using var devices = new MMDeviceEnumerator();
            using var device = devices.GetDefaultAudioEndpoint(DataFlow.Capture, Role.Console);
            // A microphone being available does not prove user consent.
            return "NOT_DETERMINED";
        }
        catch (COMException error) when (error.HResult == unchecked((int)0x80070005))
        {
            return "DENIED";
        }
        catch (COMException)
        {
            return "UNAVAILABLE";
        }
    }

    internal void Start() => capture.StartRecording();

    private void OnDataAvailable(object? sender, WaveInEventArgs eventArgs)
    {
        if (eventArgs.BytesRecorded == 0)
            return;
        try
        {
            lock (gate)
            {
                if (writer is null)
                {
                    // Stage each attempt in a distinct file. Time and UUID keep
                    // recovery stable without overwriting an earlier segment.
                    var filename = $"{DateTimeOffset.UtcNow.UtcTicks:0000000000000000000}-{Guid.NewGuid():N}.wav";
                    writer = new WaveFileWriter(Path.Combine(folder, filename), capture.WaveFormat);
                }
                writer.Write(eventArgs.Buffer, 0, eventArgs.BytesRecorded);
                if (flushed.Elapsed >= TimeSpan.FromSeconds(1))
                {
                    writer.Flush();
                    flushed.Restart();
                }
                if (writer.Length >= capture.WaveFormat.AverageBytesPerSecond * 30L)
                    CloseSegment();
            }
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException)
        {
            completed.TrySetResult(error);
            capture.StopRecording();
        }
    }

    private void OnStopped(object? sender, StoppedEventArgs eventArgs)
    {
        try
        {
            lock (gate)
                CloseSegment();
            completed.TrySetResult(eventArgs.Exception);
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException)
        {
            completed.TrySetResult(error);
        }
    }

    private void CloseSegment()
    {
        var segment = writer;
        writer = null;
        segment?.Dispose();
    }

    internal async Task<Exception?> StopAsync()
    {
        capture.StopRecording();
        var result = await completed.Task.WaitAsync(TimeSpan.FromSeconds(10));
        // A write failure can signal completion before NAudio raises
        // RecordingStopped. Finish the valid prefix under the same lock.
        lock (gate)
            CloseSegment();
        return result;
    }

    public void Dispose()
    {
        capture.DataAvailable -= OnDataAvailable;
        capture.RecordingStopped -= OnStopped;
        capture.Dispose();
        lock (gate)
            CloseSegment();
    }
}
