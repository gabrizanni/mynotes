import SwiftUI

struct WorkspaceCalendarView: View {
    @ObservedObject var model: CaptureModel
    let lessons: [WorkspaceLesson]
    let now: Date
    let select: (String) -> Void
    @CaptureViewState<Date> private var anchor = Date()
    @CaptureViewState<Bool> private var monthly = false
    @CaptureViewState<String> private var course = "all"
    private let hourHeight: CGFloat = 56
    private var clock: WorkspaceCalendar { WorkspaceCalendar(timezone: model.snapshot?.events.first?.timezone ?? TimeZone.current.identifier) }
    private var days: [Date] { monthly ? clock.month(anchor) : clock.week(anchor) }
    private var entries: [WorkspaceLesson] { lessons.filter { $0.event != nil && (course == "all" || $0.courseCode == course) } }
    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                Button { move(-1) } label: { Image(systemName: "chevron.left") }.accessibilityLabel("Periodo precedente")
                Button("Oggi") { anchor = now }
                Button { move(1) } label: { Image(systemName: "chevron.right") }.accessibilityLabel("Periodo successivo")
                Text(clock.label(anchor, format: "MMMM yyyy").capitalized).font(.headline)
                Spacer(minLength: 4)
                Picker("Corso", selection: $course) {
                    Text("Tutti i corsi").tag("all")
                    ForEach(model.snapshot?.courses ?? []) { Text($0.name).tag($0.code) }
                }.labelsHidden().frame(maxWidth: 180)
                Picker("Vista calendario", selection: $monthly) { Text("Settimana").tag(false); Text("Mese").tag(true) }.pickerStyle(.segmented).labelsHidden().frame(width: 170)
            }.padding(20)
            HStack {
                Text(clock.calendar.timeZone.identifier).font(.caption).foregroundStyle(.secondary)
                Spacer()
                Label("Audio", systemImage: "waveform").font(.caption).foregroundStyle(.secondary)
                Label("Trascrizione", systemImage: "doc.text").font(.caption).foregroundStyle(.secondary)
            }.padding(.horizontal, 20).padding(.bottom, 16)
            Divider()
            if monthly { month } else { week }
        }.onAppear { anchor = now }
    }
    private var week: some View {
        VStack(spacing: 0) {
            HStack(spacing: 0) {
                Color.clear.frame(width: 46, height: 64)
                ForEach(days, id: \.self) { day in
                    VStack(spacing: 6) {
                        Text(clock.label(day, format: "EEE").uppercased()).font(.caption).foregroundStyle(.secondary)
                        Text(clock.label(day, format: "d")).font(.title3.weight(.medium))
                            .foregroundStyle(clock.sameDay(day, now) ? Color.accentColor : Color.primary)
                    }.frame(maxWidth: .infinity).padding(.vertical, 16)
                }
            }
            Divider()
            ScrollViewReader { proxy in
                ScrollView(.vertical) {
                    HStack(alignment: .top, spacing: 0) {
                        VStack(spacing: 0) {
                            ForEach(0..<24, id: \.self) { hour in
                                Text(String(format: "%02d", hour)).font(.caption).foregroundStyle(.secondary)
                                    .frame(width: 46, height: hourHeight, alignment: .top).id(hour)
                            }
                        }
                        ForEach(days, id: \.self) { day in
                            dayColumn(day).frame(maxWidth: .infinity)
                        }
                    }.padding(.top, 8)
                }.onAppear { proxy.scrollTo(8, anchor: .top) }
            }
        }
    }
    private func dayColumn(_ day: Date) -> some View {
        let events = entries.filter { clock.sameDay($0.date, day) }.sorted { $0.date < $1.date }
        return ZStack(alignment: .topLeading) {
            VStack(spacing: 0) {
                ForEach(0..<24, id: \.self) { _ in
                    Rectangle().fill(Color.primary.opacity(0.06)).frame(height: 1)
                    Color.clear.frame(height: hourHeight - 1)
                }
            }
            HStack(alignment: .top, spacing: 2) {
                let lanes = calendarLanes(events)
                ForEach(lanes.indices, id: \.self) { lane in
                    ZStack(alignment: .top) {
                        Color.clear
                        ForEach(lanes[lane]) { lesson in
                            if let event = lesson.event {
                                let startMinute = clock.minuteOfDay(lesson.date)
                                let endMinute = clock.sameDay(event.end, day) ? clock.minuteOfDay(event.end) : 1440
                                let offset = CGFloat(startMinute) / 60 * hourHeight
                                let duration = Double(max(15, endMinute - startMinute)) * 60
                                WorkspaceCalendarEvent(lesson: lesson, status: lesson.recordingLabel(model.snapshot),
                                    time: clock.clock(lesson.date), height: max(32, CGFloat(duration / 3600) * hourHeight - 3), select: select)
                                    .offset(y: offset)

                            }
                        }
                    }
                }
            }.padding(.horizontal, 3)
            if clock.sameDay(day, now) {
                Rectangle().fill(Color.red).frame(height: 1).offset(y: CGFloat(clock.minuteOfDay(now)) / 60 * hourHeight)
                    .accessibilityLabel("Ora attuale: " + clock.clock(now))
            }
        }.frame(height: hourHeight * 24).overlay(alignment: .leading) { Rectangle().fill(Color.primary.opacity(0.06)).frame(width: 1) }
    }
    private var month: some View {
        ScrollView {
            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 0), count: 7), spacing: 0) {
                ForEach(["LUN", "MAR", "MER", "GIO", "VEN", "SAB", "DOM"], id: \.self) { weekday in
                    Text(weekday).font(.caption).foregroundStyle(.secondary).padding(.vertical, 16)
                }
                ForEach(days, id: \.self) { day in
                    let items = entries.filter { clock.sameDay($0.date, day) }.sorted { $0.date < $1.date }
                    VStack(alignment: .leading, spacing: 6) {
                        Button { anchor = day; monthly = false } label: {
                            Text(clock.label(day, format: "d")).font(.callout.weight(.medium))
                                .foregroundStyle(clock.sameDay(day, now) ? Color.accentColor : Color.primary)
                        }.buttonStyle(.plain).padding(.bottom, 4)
                        ForEach(Array(items.prefix(3))) { item in
                            Button { select(item.id) } label: {
                                Text(clock.clock(item.date) + " " + item.title).font(.caption).lineLimit(1).frame(maxWidth: .infinity, alignment: .leading)
                            }.buttonStyle(.plain).padding(4).background(Color.accentColor.opacity(0.08), in: RoundedRectangle(cornerRadius: 4))
                        }
                        if items.count > 3 { Button("Altre \(items.count - 3)") { anchor = day; monthly = false }.font(.caption).buttonStyle(.plain) }
                        Spacer(minLength: 0)
                    }.padding(10).frame(maxWidth: .infinity, minHeight: 114, alignment: .topLeading)
                        .opacity(clock.calendar.component(.month, from: day) == clock.calendar.component(.month, from: anchor) ? 1 : 0.45)
                        .overlay { Rectangle().strokeBorder(Color.primary.opacity(0.07), lineWidth: 0.5) }
                }
            }
        }
    }
    private func move(_ value: Int) { anchor = clock.calendar.date(byAdding: monthly ? .month : .weekOfYear, value: value, to: anchor) ?? anchor }
    private func calendarLanes(_ values: [WorkspaceLesson]) -> [[WorkspaceLesson]] {
        var lanes: [[WorkspaceLesson]] = []
        for item in values {
            if let index = lanes.firstIndex(where: { ($0.last?.event?.end ?? .distantPast) <= item.date }) { lanes[index].append(item) }
            else { lanes.append([item]) }
        }
        return lanes.isEmpty ? [[]] : lanes
    }
}

private struct WorkspaceCalendarEvent: View {
    let lesson: WorkspaceLesson
    let status: String
    let time: String
    let height: CGFloat
    let select: (String) -> Void
    private var backgroundColor: Color { lesson.needsAttention ? Color.orange.opacity(0.1) : Color.accentColor.opacity(0.09) }
    private var summary: String { lesson.title + ", " + time + ", " + status + ", " + lesson.transcriptionLabel }
    var body: some View {
        Button(action: { select(lesson.id) }) { card }
            .buttonStyle(.plain).accessibilityLabel(summary)
    }
    private var card: some View {
        contents.padding(7).frame(maxWidth: .infinity, alignment: .leading)
            .frame(height: height)
            .background(backgroundColor, in: RoundedRectangle(cornerRadius: 7))
            .contentShape(RoundedRectangle(cornerRadius: 7))
    }
    private var contents: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(verbatim: lesson.title).font(Font.caption).fontWeight(.medium).lineLimit(2)
            Text(verbatim: time).font(Font.caption).foregroundStyle(Color.secondary)
            statusLine
            Spacer(minLength: 0)
        }
    }
    @ViewBuilder private var statusLine: some View {
        if height >= 53 {
            Text(verbatim: status).font(Font.caption).foregroundStyle(Color.secondary).lineLimit(1)
            if lesson.transcriptPath != nil {
                Text("TXT disponibile").font(Font.caption).foregroundStyle(Color.secondary)
            }
        }
    }
}
