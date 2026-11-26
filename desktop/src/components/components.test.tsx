import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MoreHorizontal } from "lucide-react";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { I18nProvider } from "../i18n";
import { IconButton } from "./Button";
import { ProgressBar } from "./ProgressBar";
import { Segmented } from "./Segmented";
import { StageStepper } from "./StageStepper";
import { StatusChip, knownStatuses } from "./StatusChip";
import { Stepper } from "./Stepper";
import { TagInput } from "./TagInput";
import { TextField } from "./TextField";
import { Toggle } from "./Toggle";

describe("StatusChip", () => {
  it("maps every known status to a label, never the raw code", () => {
    for (const kind of ["notes", "transcription", "recording", "assistant"] as const) {
      for (const status of knownStatuses(kind)) {
        const { container, unmount } = render(<StatusChip kind={kind} status={status} />);
        expect(container.textContent).not.toBe("");
        expect(container.textContent).not.toContain(status);
        unmount();
      }
    }
  });

  it("falls back to a neutral label for unknown statuses", () => {
    render(<StatusChip kind="notes" status="SOMETHING_NEW" />);
    expect(screen.getByText("Status unknown")).toBeTruthy();
  });

  it("follows the language", () => {
    render(
      <I18nProvider lang="it">
        <StatusChip kind="notes" status="AWAITING_APPROVAL" />
      </I18nProvider>,
    );
    expect(screen.getByText("Da rivedere")).toBeTruthy();
  });
});

describe("inputs", () => {
  it("wires label, hint and error to a text field", () => {
    render(<TextField label="Course name" hint="As in your timetable." error="Add a name to continue." />);
    const input = screen.getByLabelText("Course name");
    expect(input.getAttribute("aria-invalid")).toBe("true");
    expect(input.getAttribute("aria-describedby")?.split(" ")).toHaveLength(2);
    expect(screen.getByRole("alert").textContent).toContain("Add a name");
  });

  it("moves through a segmented control with arrow keys", async () => {
    function Harness() {
      const [value, setValue] = useState<"a" | "b" | "c">("a");
      return (
        <Segmented
          label="Detail"
          value={value}
          onChange={setValue}
          options={[
            { value: "a", label: "Concise" },
            { value: "b", label: "Clear" },
            { value: "c", label: "Detailed" },
          ]}
        />
      );
    }
    render(<Harness />);
    const user = userEvent.setup();
    await user.tab();
    expect(document.activeElement?.textContent).toBe("Concise");
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("radio", { name: "Clear" }).getAttribute("aria-checked")).toBe("true");
    await user.keyboard("{End}");
    expect(screen.getByRole("radio", { name: "Detailed" }).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByRole("radiogroup", { name: "Detail" })).toBeTruthy();
  });

  it("is a spinbutton with one tab stop and bounded values", async () => {
    function Harness() {
      const [value, setValue] = useState(4);
      return <Stepper label="Images per lesson" value={value} min={0} max={5} onChange={setValue} />;
    }
    render(<Harness />);
    const user = userEvent.setup();
    await user.tab();
    const spin = screen.getByRole("spinbutton", { name: "Images per lesson" });
    expect(document.activeElement).toBe(spin);
    await user.keyboard("{ArrowUp}{ArrowUp}");
    expect(spin.getAttribute("aria-valuenow")).toBe("5");
    await user.keyboard("{Home}");
    expect(spin.getAttribute("aria-valuenow")).toBe("0");
  });

  it("adds tags with Enter and removes the last with Backspace", async () => {
    function Harness() {
      const [tags, setTags] = useState<string[]>([]);
      return <TagInput label="Hard topics" value={tags} onChange={setTags} />;
    }
    render(<Harness />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Hard topics"), "Bayes{Enter}Regression{Enter}");
    expect(screen.getByRole("button", { name: "Remove Regression" })).toBeTruthy();
    await user.keyboard("{Backspace}");
    expect(screen.queryByRole("button", { name: "Remove Regression" })).toBeNull();
    expect(screen.getByRole("button", { name: "Remove Bayes" })).toBeTruthy();
  });

  it("toggles a switch from its label", async () => {
    function Harness() {
      const [on, setOn] = useState(false);
      return <Toggle label="Review before updating Word" checked={on} onChange={setOn} />;
    }
    render(<Harness />);
    await userEvent.setup().click(screen.getByText("Review before updating Word"));
    expect(screen.getByRole("switch", { name: "Review before updating Word" }).getAttribute("aria-checked")).toBe("true");
  });
});

describe("feedback", () => {
  it("gives icon-only buttons an accessible name", () => {
    render(<IconButton label="More actions" icon={MoreHorizontal} />);
    expect(screen.getByRole("button", { name: "More actions" })).toBeTruthy();
  });

  it("reports determinate progress only", () => {
    render(<ProgressBar label="LibreOffice" value={212} max={300} valueText="212 / 300 MB" />);
    const bar = screen.getByRole("progressbar", { name: "LibreOffice" });
    expect(bar.getAttribute("aria-valuenow")).toBe("212");
    expect(bar.getAttribute("aria-valuetext")).toBe("212 / 300 MB");
  });

  it("marks the running stage and shows elapsed time, not percentages", () => {
    render(
      <StageStepper
        label="Notes"
        now={new Date("2026-11-26T10:00:00Z")}
        current="GENERATING"
        stages={[
          { id: "CHECKING", label: "Checking sources" },
          { id: "GENERATING", label: "Writing notes" },
          { id: "BUILDING_WORD", label: "Formatting Word" },
        ]}
        timings={{
          CHECKING: { startedAt: "2026-11-26T09:58:00Z", finishedAt: "2026-11-26T09:58:30Z" },
          GENERATING: { startedAt: "2026-11-26T09:58:30Z", finishedAt: null },
        }}
      />,
    );
    const current = screen.getByText("Writing notes").closest("li");
    expect(current?.getAttribute("aria-current")).toBe("step");
    expect(current?.textContent).toContain("01:30");
    expect(document.body.textContent).not.toContain("%");
  });
});
