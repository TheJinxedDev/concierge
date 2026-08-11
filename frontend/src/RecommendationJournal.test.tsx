import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { RecommendationJournal } from "./RecommendationJournal";

const media = [
  {
    id: "anime-flcl-2000",
    title: "FLCL",
    category: "anime_series",
    status: "finished",
    observations: [
      {
        id: "obs-flcl-visuals",
        scope: "work",
        polarity: "positive",
        dimension: "visual_style",
        text: "The animation crackles.",
        provenance: "user_explicit",
        privacy: "assistant_readable",
        review_state: "accepted",
        observed_on: "2026-07-15",
      },
    ],
  },
  {
    id: "movie-mind-game-2004",
    title: "Mind Game",
    category: "anime_movie",
    status: "planned",
    archived_on: "2026-07-20",
  },
];

const recommendation = {
  id: "recommendation-mind-game-2026-07-18",
  media_item_id: "movie-mind-game-2004",
  recommended_on: "2026-07-18",
  source: "assistant",
  source_context: "conversation:42",
  rationale: "Its elastic visual language may fit.",
  evidence: [
    { media_item_id: "anime-flcl-2000", observation_id: "obs-flcl-visuals" },
  ],
  confidence: 0.72,
};

afterEach(() => vi.unstubAllGlobals());

it("loads a factual recommendation journal and resolves archived targets and exact evidence", async () => {
  vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
    if (url === "/api/recommendations") {
      return Promise.resolve(new Response(JSON.stringify([recommendation]), { status: 200 }));
    }
    if (url === "/api/media?include_archived=true") {
      return Promise.resolve(new Response(JSON.stringify(media), { status: 200 }));
    }
    throw new Error(`unexpected request ${url}`);
  }));

  render(<RecommendationJournal />);
  await userEvent.click(screen.getByRole("button", { name: "Open recommendation journal" }));

  const journal = await screen.findByRole("region", { name: "Recommendation journal" });
  expect(within(journal).getByRole("heading", { name: "Mind Game" })).toBeInTheDocument();
  expect(within(journal).getByText("Archived target")).toBeInTheDocument();
  expect(within(journal).getByText("Its elastic visual language may fit.")).toBeInTheDocument();
  expect(within(journal).getByText("The animation crackles.")).toBeInTheDocument();
  expect(within(journal).getByText(/FLCL · Visual style/)).toBeInTheDocument();
});

it("starts an evolved recommendation draft on its first currently legal outcome kind", async () => {
  const evolved = {
    ...recommendation,
    outcomes: [
      {
        id: "outcome-initial",
        kind: "initial_response",
        recorded_on: "2026-07-18",
        text: "That sounds interesting.",
      },
    ],
  };
  const tried = {
    id: "outcome-recommendation-mind-game-2026-07-18-tried-1",
    kind: "tried",
    recorded_on: "2026-07-19",
  };
  const fetchMock = vi.fn().mockImplementation((url: string) => {
    if (url === "/api/recommendations") {
      return Promise.resolve(new Response(JSON.stringify([evolved]), { status: 200 }));
    }
    if (url === "/api/media?include_archived=true") {
      return Promise.resolve(new Response(JSON.stringify(media), { status: 200 }));
    }
    if (url.endsWith("/outcomes")) {
      return Promise.resolve(new Response(JSON.stringify({
        created: true,
        recommendation: { ...evolved, outcomes: [...evolved.outcomes, tried] },
      }), { status: 201 }));
    }
    throw new Error(`unexpected request ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);

  render(<RecommendationJournal />);
  await userEvent.click(screen.getByRole("button", { name: "Open recommendation journal" }));
  await screen.findByRole("heading", { name: "Mind Game" });
  await userEvent.click(screen.getByRole("button", { name: "Record outcome for Mind Game" }));

  expect(screen.getByLabelText("Outcome kind")).toHaveValue("tried");
  await userEvent.clear(screen.getByLabelText("Recorded date"));
  await userEvent.type(screen.getByLabelText("Recorded date"), "2026-07-19");
  await userEvent.click(screen.getByRole("button", { name: "Record outcome" }));
  await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/recommendations/recommendation-mind-game-2026-07-18/outcomes",
    expect.objectContaining({ body: JSON.stringify(tried) }),
  ));
});

it("ignores an older journal load that settles after a close and reopen", async () => {
  const user = userEvent.setup();
  let resolveFirstHistory!: (records: unknown[]) => void;
  const firstHistory = new Promise<unknown[]>((resolve) => { resolveFirstHistory = resolve; });
  let historyCalls = 0;
  const staleRecommendation = {
    ...recommendation,
    id: "recommendation-flcl-stale",
    media_item_id: "anime-flcl-2000",
  };
  vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
    if (url === "/api/recommendations") {
      historyCalls += 1;
      return historyCalls === 1
        ? firstHistory.then((records) => new Response(JSON.stringify(records), { status: 200 }))
        : Promise.resolve(new Response(JSON.stringify([recommendation]), { status: 200 }));
    }
    if (url === "/api/media?include_archived=true") {
      return Promise.resolve(new Response(JSON.stringify(media), { status: 200 }));
    }
    throw new Error(`unexpected request ${url}`);
  }));

  render(<RecommendationJournal />);
  await user.click(screen.getByRole("button", { name: "Open recommendation journal" }));
  await user.click(screen.getByRole("button", { name: "Loading recommendation journal…" }));
  await user.click(screen.getByRole("button", { name: "Open recommendation journal" }));
  await screen.findByRole("heading", { name: "Mind Game" });

  await act(async () => { resolveFirstHistory([staleRecommendation]); });

  expect(screen.getByRole("heading", { name: "Mind Game" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "FLCL" })).not.toBeInTheDocument();
});

it("describes required outcome validation beside the invalid field", async () => {
  const user = userEvent.setup();
  vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
    if (url === "/api/recommendations") {
      return Promise.resolve(new Response(JSON.stringify([recommendation]), { status: 200 }));
    }
    if (url === "/api/media?include_archived=true") {
      return Promise.resolve(new Response(JSON.stringify(media), { status: 200 }));
    }
    throw new Error(`unexpected request ${url}`);
  }));

  render(<RecommendationJournal />);
  await user.click(screen.getByRole("button", { name: "Open recommendation journal" }));
  await screen.findByRole("heading", { name: "Mind Game" });
  await user.click(screen.getByRole("button", { name: "Record outcome for Mind Game" }));
  await user.click(screen.getByRole("button", { name: "Record outcome" }));

  expect(screen.getByLabelText("Outcome note")).toHaveAccessibleDescription("Enter an outcome note.");
  expect(screen.getByText("Enter an outcome note.")).toHaveClass("field-error");
});

it("appends a tried event, locks the draft while pending, and renders the verified receipt", async () => {
  let resolveAppend!: (response: Response) => void;
  const appendResponse = new Promise<Response>((resolve) => { resolveAppend = resolve; });
  const fetchMock = vi.fn().mockImplementation((url: string) => {
    if (url === "/api/recommendations") {
      return Promise.resolve(new Response(JSON.stringify([recommendation]), { status: 200 }));
    }
    if (url === "/api/media?include_archived=true") {
      return Promise.resolve(new Response(JSON.stringify(media), { status: 200 }));
    }
    if (url.endsWith("/outcomes")) return appendResponse;
    throw new Error(`unexpected request ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);

  render(<RecommendationJournal />);
  await userEvent.click(screen.getByRole("button", { name: "Open recommendation journal" }));
  await screen.findByRole("heading", { name: "Mind Game" });
  await userEvent.click(screen.getByRole("button", { name: "Record outcome for Mind Game" }));
  await userEvent.selectOptions(screen.getByLabelText("Outcome kind"), "tried");
  await userEvent.clear(screen.getByLabelText("Recorded date"));
  await userEvent.type(screen.getByLabelText("Recorded date"), "2026-07-19");
  await userEvent.click(screen.getByRole("button", { name: "Record outcome" }));

  expect(screen.getByRole("button", { name: "Recording outcome…" })).toBeDisabled();
  const outcome = {
    id: "outcome-recommendation-mind-game-2026-07-18-tried-1",
    kind: "tried",
    recorded_on: "2026-07-19",
  };
  resolveAppend(new Response(JSON.stringify({
    created: true,
    recommendation: { ...recommendation, outcomes: [outcome] },
  }), { status: 201 }));

  await waitFor(() => expect(screen.getByText("Tried · 2026-07-19")).toBeInTheDocument());
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/recommendations/recommendation-mind-game-2026-07-18/outcomes",
    expect.objectContaining({ body: JSON.stringify(outcome) }),
  );
});
