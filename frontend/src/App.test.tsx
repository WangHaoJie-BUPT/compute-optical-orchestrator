import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import App from "./App";

afterEach(cleanup);

describe("Compute-Optical Console", () => {
  it("opens a persisted task decision from the task table", async () => {
    render(<App />);

    expect(screen.getByText("Operations overview")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Tasks" }));
    expect(screen.getByRole("heading", { name: "Tasks" })).toBeInTheDocument();

    fireEvent.click(screen.getByText("climate-inference-042"));
    expect(screen.getByText("Joint planning decision")).toBeInTheDocument();
    expect(screen.getByText("Committed resources")).toBeInTheDocument();
  });

  it("adds a controlled replay task to the admission queue", async () => {
    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: "Submit" }));
    const taskId = screen.getByLabelText("Task ID");
    fireEvent.change(taskId, { target: { value: "replay-console-001" } });
    fireEvent.click(screen.getByRole("button", { name: "Submit to admission queue" }));

    expect(await screen.findByText("replay-console-001")).toBeInTheDocument();
    expect(screen.getByText("Tenant rotation")).toBeInTheDocument();
  });
});
