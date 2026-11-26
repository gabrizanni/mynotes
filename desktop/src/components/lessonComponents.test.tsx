import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { ChoiceList } from "./ChoiceList";
import { Disclosure } from "./Disclosure";
import { Menu } from "./Menu";
import { Slider } from "./Slider";
import { TabNav } from "./TabNav";

describe("Menu", () => {
  it("opens from its icon button, moves with arrows and returns focus on Escape", async () => {
    const user = userEvent.setup();
    const edit = vi.fn();
    render(<Menu label="Lesson actions" items={[{ id: "a", label: "Edit lesson", onSelect: edit }, { id: "b", label: "Remove lesson", destructive: true, onSelect: vi.fn() }]} />);
    const trigger = screen.getByRole("button", { name: "Lesson actions" });
    await user.click(trigger);
    expect(document.activeElement).toBe(screen.getByRole("menuitem", { name: "Edit lesson" }));
    await user.keyboard("{ArrowDown}");
    expect(document.activeElement?.textContent).toBe("Remove lesson");
    await user.keyboard("{ArrowDown}");
    expect(document.activeElement?.textContent).toBe("Edit lesson");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(trigger);
    await user.click(trigger);
    await user.click(screen.getByRole("menuitem", { name: "Edit lesson" }));
    expect(edit).toHaveBeenCalledOnce();
  });
});

describe("ChoiceList", () => {
  it("is a radiogroup with one tab stop; arrows select", async () => {
    const user = userEvent.setup();
    function Harness() {
      const [value, setValue] = useState<"a" | "b">("a");
      return <ChoiceList label="Quality" value={value} onChange={setValue} choices={[{ value: "a", label: "Standard", description: "Usually under 10 min." }, { value: "b", label: "Thorough" }]} />;
    }
    render(<Harness />);
    expect(screen.getByRole("radiogroup", { name: "Quality" })).toBeTruthy();
    const standard = screen.getByRole("radio", { name: /Standard/ });
    expect(standard.getAttribute("tabindex")).toBe("0");
    standard.focus();
    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("radio", { name: /Thorough/ }).getAttribute("aria-checked")).toBe("true");
  });
});

describe("Disclosure", () => {
  it("names the toggle by its label only and reveals its region", async () => {
    const user = userEvent.setup();
    render(
      <Disclosure label="More options" summary="Instructions, images">
        <p>Inside</p>
      </Disclosure>,
    );
    const toggle = screen.getByRole("button", { name: "More options" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.getByText("Inside").closest("[hidden]")).not.toBeNull();
    await user.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("Inside").closest("[hidden]")).toBeNull();
  });
});

describe("TabNav and Slider", () => {
  it("marks the current tab as the current page", () => {
    render(<TabNav label="Statistics sections" current="notes" items={[{ id: "lessons", label: "Lessons", href: "#/a" }, { id: "notes", label: "Notes", href: "#/b" }]} />);
    expect(screen.getByRole("link", { name: "Notes" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("link", { name: "Lessons" }).getAttribute("aria-current")).toBeNull();
  });

  it("is a native slider with a spoken value", () => {
    const onChange = vi.fn();
    render(<Slider label="Position" value={30} max={120} valueText="00:30 of 02:00" onChange={onChange} />);
    const slider = screen.getByRole("slider", { name: "Position" });
    expect(slider.getAttribute("aria-valuetext")).toBe("00:30 of 02:00");
  });
});
