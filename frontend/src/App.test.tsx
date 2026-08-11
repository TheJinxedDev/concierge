import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { expect, test, vi } from "vitest";

import { App, EmptyLibraryState } from "./App";
import { parsePortableImportDocument } from "./api";

const mediaItem = {
  id: "movie-mirrormask",
  title: "MirrorMask",
  category: "movie",
  status: "finished",
  aliases: [{ value: "Mirrormask" }],
  terms: [{ kind: "theme", value: "identity" }],
};

test("does not expose the quarantined automation policy surface", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });

  expect(screen.queryByRole("heading", { name: "Delayed auto-promotion" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Configure delayed auto-promotion" })).not.toBeInTheDocument();
});

test("accepts current v1.8 portable exports with typed capture proposals", () => {
  const document = parsePortableImportDocument({
    schema_version: "1.8",
    exported_on: "2026-08-04",
    creators: [],
    media_items: [],
    proposals: [],
    recommendations: [],
    capture_proposals: [],
  });

  expect(document.schema_version).toBe("1.8");
  expect(document.capture_proposals).toEqual([]);
});

function currentLocalDate() {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}

function completeProgress(overrides: Record<string, unknown>) {
  return { amount_completed: null, unit: null, started_on: null, ended_on: null, return_intent: null, reason: null, ...overrides };
}

function importReviewFor(document: Record<string, unknown>) {
  const mediaItems = document.media_items as Array<Record<string, unknown>>;
  const creators = (document.creators ?? []) as Array<Record<string, unknown>>;
  const proposals = (document.proposals ?? []) as Array<Record<string, unknown>>;
  const recommendations = (document.recommendations ?? []) as Array<Record<string, unknown>>;
  const entry = (value: Record<string, unknown>, label: string, after: Record<string, unknown>) => ({
    id: String(value.id), label, action: "create", before: null, after,
  });
  const mediaAfter = (value: Record<string, unknown>) => ({
    ...value,
    aliases: value.aliases ?? [], terms: value.terms ?? [], relationships: value.relationships ?? [],
    credits: value.credits ?? [], rating_history: value.rating_history ?? [],
    progress_records: value.progress_records ?? [], observations: value.observations ?? [],
  });
  const creatorAfter = (value: Record<string, unknown>) => ({ ...value, aliases: value.aliases ?? [] });
  const proposalAfter = (value: Record<string, unknown>) => ({
    ...value, review_state: value.review_state ?? "needs_review",
  });
  const recommendationAfter = (value: Record<string, unknown>) => ({
    ...value, evidence: value.evidence ?? [], outcomes: value.outcomes ?? [],
  });
  const schemaVersion = String(document.schema_version);
  return {
    review_schema_version: "1.0",
    schema_version: schemaVersion,
    review_token: "c".repeat(64),
    can_import: true,
    blocking_reasons: [],
    media_items: {
      mode: "merge",
      entries: mediaItems.map((value) => entry(value, String(value.title), mediaAfter(value))),
      preserved_ids: [],
      current_ids: [],
    },
    creators: {
      mode: "merge",
      entries: creators.map((value) => entry(value, String(value.name), creatorAfter(value))),
      preserved_ids: [],
      current_ids: [],
    },
    proposals: {
      mode: ["1.4", "1.5", "1.6"].includes(schemaVersion) ? "replace" : "preserve",
      entries: proposals.map((value) => entry(value, String(value.id), proposalAfter(value))),
      preserved_ids: [],
      current_ids: [],
    },
    recommendations: {
      mode: schemaVersion === "1.6" ? "merge" : "preserve",
      entries: recommendations.map((value) => entry(value, String(value.id), recommendationAfter(value))),
      preserved_ids: [],
      current_ids: [],
    },
  };
}

function portableExportResponse(mediaItems: Array<typeof mediaItem> = []) {
  return new Response(JSON.stringify({
    schema_version: "1.6", exported_on: "2026-07-18",
    creators: [], media_items: mediaItems, proposals: [], recommendations: [],
  }), { status: 200, headers: { "Content-Type": "application/json" } });
}

test("opens a pristine new-entry draft without weakening existing ID immutability", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  expect(screen.getByRole("textbox", { name: "Stable ID" })).toHaveAttribute("readonly");

  await user.click(screen.getByRole("button", { name: "New entry" }));

  expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("");
  expect(screen.getByRole("textbox", { name: "Stable ID" })).toHaveValue("");
  expect(screen.getByRole("textbox", { name: "Stable ID" })).not.toHaveAttribute("readonly");
  expect(screen.getByRole("combobox", { name: "Category" })).toHaveValue("movie");
  expect(screen.getByRole("combobox", { name: "Status" })).toHaveValue("planned");
  expect(screen.getByRole("button", { name: "Create entry" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Cancel edits" })).toBeEnabled();
});

test("blocks category changes that would hide or discard stored capability data", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify([{
    ...mediaItem,
    progress_records: [{ status: "finished", recorded_on: "2026-07-17" }],
    credits: [{ creator_id: "creator-example", role: "director" }],
  }]), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })));

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  const category = screen.getByRole("combobox", { name: "Category" });

  expect(within(category).getByRole("option", { name: "Painting" })).toBeDisabled();
  expect(within(category).getByRole("option", { name: "Comedian" })).toBeDisabled();
  expect(within(category).getByRole("option", { name: "Art museum" })).toBeDisabled();
  expect(category).toHaveValue("movie");
});

test("blocks a category change that would hide stored credits or relationships", async () => {
  const painting = {
    id: "painting-starry-night",
    title: "The Starry Night",
    category: "painting",
    credits: [{ creator_id: "creator-van-gogh", role: "artist" }],
    relationships: [{ relationship_type: "same_creator", target_media_item_id: "painting-sunflowers" }],
  };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify([painting]), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })));

  render(<App />);
  await screen.findByRole("button", { name: /Starry Night/ });
  const category = screen.getByRole("combobox", { name: "Category" });

  expect(within(category).getByRole("option", { name: "Comedian" })).toBeDisabled();
  expect(within(category).getByRole("option", { name: "Art museum" })).toBeDisabled();
  expect(within(category).getByRole("option", { name: "Movie" })).toBeEnabled();
  expect(category).toHaveValue("painting");
});

test("uses only shared taste fields for a zero-capability opinion category", async () => {
  const user = userEvent.setup();
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify([mediaItem]), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })));

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "New entry" }));
  await user.type(screen.getByRole("textbox", { name: "Title" }), "Museum of Modern Art");
  await user.selectOptions(screen.getByRole("combobox", { name: "Category" }), "art_museum");

  expect(screen.queryByRole("combobox", { name: "Status" })).not.toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Progress" })).not.toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Creator credits" })).not.toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Media relationships" })).not.toBeInTheDocument();
  expect(JSON.parse(screen.getByTestId("record-preview").textContent ?? "")).toEqual({
    id: "art_museum-museum-of-modern-art",
    title: "Museum of Modern Art",
    category: "art_museum",
  });
});

test("uses only shared taste fields for a simple opinion category", async () => {
  const user = userEvent.setup();
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify([mediaItem]), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })));

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "New entry" }));
  await user.type(screen.getByRole("textbox", { name: "Title" }), "George Carlin");
  await user.selectOptions(screen.getByRole("combobox", { name: "Category" }), "comedian");

  expect(screen.queryByRole("combobox", { name: "Status" })).not.toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Progress" })).not.toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Creator credits" })).not.toBeInTheDocument();
  expect(JSON.parse(screen.getByTestId("record-preview").textContent ?? "")).toEqual({
    id: "comedian-george-carlin",
    title: "George Carlin",
    category: "comedian",
  });
});

test("suggests a stable ID until the user edits it", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "New entry" }));
  await user.type(screen.getByRole("textbox", { name: "Title" }), "Spirited Away!");

  expect(screen.getByRole("textbox", { name: "Stable ID" })).toHaveValue(
    "movie-spirited-away",
  );
  await user.selectOptions(
    screen.getByRole("combobox", { name: "Category" }),
    "anime_movie",
  );
  expect(screen.getByRole("textbox", { name: "Stable ID" })).toHaveValue(
    "anime_movie-spirited-away",
  );

  await user.clear(screen.getByRole("textbox", { name: "Stable ID" }));
  await user.type(screen.getByRole("textbox", { name: "Stable ID" }), "film-sen");
  await user.clear(screen.getByRole("textbox", { name: "Title" }));
  await user.type(screen.getByRole("textbox", { name: "Title" }), "Sen");

  expect(screen.getByRole("textbox", { name: "Stable ID" })).toHaveValue("film-sen");
  expect(JSON.parse(screen.getByTestId("record-preview").textContent ?? "")).toEqual({
    id: "film-sen",
    title: "Sen",
    category: "anime_movie",
    status: "planned",
  });
});

test("creates the exact new record through the create-only endpoint", async () => {
  const user = userEvent.setup();
  const createdItem = {
    id: "movie-coraline",
    title: "Coraline",
    category: "movie",
    status: "finished",
    aliases: [{ value: "Coraline: The Other Mother" }],
    rating: { score: 8, rated_on: "2026-07-16", provisional: true },
    rating_history: [{ score: 8, rated_on: "2026-07-16", provisional: true }],
    progress_records: [{
      status: "finished",
      recorded_on: "2026-07-16",
      amount_completed: 100,
      unit: "percent",
      reason: "Completed.",
    }],
    observations: [{
      id: "obs-movie-coraline-1",
      scope: "work",
      polarity: "mixed",
      dimension: "atmosphere",
      text: "Beautiful, sinister, and oddly cozy.",
      provenance: "manual",
      privacy: "assistant_readable",
      review_state: "accepted",
      observed_on: "2026-07-16",
    }],
  };
  let resolveCreate!: (response: Response) => void;
  const createResponse = new Promise<Response>((resolve) => {
    resolveCreate = resolve;
  });
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(
      new Response(JSON.stringify([mediaItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    .mockReturnValueOnce(createResponse);
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "New entry" }));
  await user.type(screen.getByRole("textbox", { name: "Title" }), "Coraline");
  await user.type(
    screen.getByRole("textbox", { name: "New alternate title" }),
    "Coraline: The Other Mother",
  );
  await user.keyboard("{Enter}");
  await user.type(screen.getByRole("spinbutton", { name: "Rating score" }), "8");
  await user.clear(screen.getByLabelText("Rating date"));
  await user.type(screen.getByLabelText("Rating date"), "2026-07-16");
  await user.click(screen.getByRole("checkbox", { name: "Provisional rating" }));
  await user.click(screen.getByRole("button", { name: "Record rating" }));
  await user.selectOptions(screen.getByRole("combobox", { name: "Progress status" }), "finished");
  await user.type(screen.getByRole("spinbutton", { name: "Amount completed" }), "100");
  await user.selectOptions(screen.getByRole("combobox", { name: "Progress unit" }), "percent");
  await user.clear(screen.getByLabelText("Progress date"));
  await user.type(screen.getByLabelText("Progress date"), "2026-07-16");
  await user.type(screen.getByRole("textbox", { name: "Progress reason" }), "Completed.");
  await user.click(screen.getByRole("button", { name: "Record progress" }));
  await user.type(screen.getByRole("textbox", { name: "Observation dimension" }), "atmosphere");
  await user.type(
    screen.getByRole("textbox", { name: "Observation text" }),
    "Beautiful, sinister, and oddly cozy.",
  );
  await user.clear(screen.getByLabelText("Observed on"));
  await user.type(screen.getByLabelText("Observed on"), "2026-07-16");
  await user.click(screen.getByRole("button", { name: "Record observation" }));
  await user.selectOptions(screen.getByRole("combobox", { name: "Status" }), "finished");
  await user.click(screen.getByRole("button", { name: "Create entry" }));

  expect(screen.getByRole("button", { name: "Creating…" })).toBeDisabled();
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/media",
    expect.objectContaining({
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(createdItem),
    }),
  );

  resolveCreate(
    new Response(JSON.stringify(createdItem), {
      status: 201,
      headers: { "Content-Type": "application/json" },
    }),
  );

  expect(await screen.findByRole("status")).toHaveTextContent("Created locally");
  expect(screen.getByRole("button", { name: /^CoralineMovie/ })).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Stable ID" })).toHaveAttribute("readonly");
});

test("keeps a new-entry draft when its stable ID already exists", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify([mediaItem]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: "media item id already exists" }), {
          status: 409,
          headers: { "Content-Type": "application/json" },
        }),
      ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "New entry" }));
  await user.type(screen.getByRole("textbox", { name: "Title" }), "MirrorMask");
  await user.clear(screen.getByRole("textbox", { name: "Stable ID" }));
  await user.type(screen.getByRole("textbox", { name: "Stable ID" }), "movie-mirrormask");
  await user.click(screen.getByRole("button", { name: "Create entry" }));

  const summary = await screen.findByRole("alert");
  expect(summary).toHaveTextContent("media item id already exists");
  expect(summary).toHaveFocus();
  expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("MirrorMask");
  expect(screen.getByRole("textbox", { name: "Stable ID" })).toHaveValue("movie-mirrormask");
  expect(screen.getByRole("button", { name: "Create entry" })).toBeEnabled();
});

test("keeps a blank custom stable ID visible and blocks creation", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "New entry" }));
  await user.type(screen.getByRole("textbox", { name: "Title" }), "Coraline");
  await user.clear(screen.getByRole("textbox", { name: "Stable ID" }));

  expect(screen.getByText("Stable ID is required.")).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Stable ID" })).toHaveAttribute(
    "aria-invalid",
    "true",
  );
  expect(screen.getByRole("button", { name: "Create entry" })).toBeDisabled();
});

test("guards new-entry transitions without prompting for a pristine cancellation", async () => {
  const user = userEvent.setup();
  const confirmMock = vi.fn().mockReturnValueOnce(false).mockReturnValueOnce(true);
  vi.stubGlobal("confirm", confirmMock);
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "New entry" }));
  await user.click(screen.getByRole("button", { name: "Cancel edits" }));
  expect(confirmMock).not.toHaveBeenCalled();
  expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("MirrorMask");

  await user.clear(screen.getByRole("textbox", { name: "Title" }));
  await user.type(screen.getByRole("textbox", { name: "Title" }), "Dirty existing draft");
  await user.click(screen.getByRole("button", { name: "New entry" }));
  expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("Dirty existing draft");
  await user.click(screen.getByRole("button", { name: "New entry" }));
  expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("");
  expect(confirmMock).toHaveBeenCalledTimes(2);
});

test("adds an alternate title without dropping the existing alias or advanced fields", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.type(
    screen.getByRole("textbox", { name: "New alternate title" }),
    "Mirror Mask",
  );
  await user.click(screen.getByRole("button", { name: "Add alternate title" }));

  expect(screen.getByRole("button", { name: "Remove Mirrormask" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Remove Mirror Mask" })).toBeInTheDocument();
  expect(JSON.parse(screen.getByTestId("record-preview").textContent ?? "")).toEqual({
    ...mediaItem,
    aliases: [{ value: "Mirrormask" }, { value: "Mirror Mask" }],
  });
});

test("adds a taxonomy term without dropping existing terms or unrelated fields", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.selectOptions(screen.getByRole("combobox", { name: "Taxonomy kind" }), "tone");
  await user.type(screen.getByRole("textbox", { name: "Taxonomy value" }), "dreamlike");
  await user.click(screen.getByRole("button", { name: "Add taxonomy term" }));

  expect(screen.getByRole("button", { name: "Remove theme identity taxonomy term" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Remove tone dreamlike taxonomy term" })).toBeInTheDocument();
  expect(JSON.parse(screen.getByTestId("record-preview").textContent ?? "")).toEqual({
    ...mediaItem,
    terms: [
      { kind: "theme", value: "identity" },
      { kind: "tone", value: "dreamlike" },
    ],
  });
});

test("prevents duplicate taxonomy terms and protects an unfinished term draft", async () => {
  const user = userEvent.setup();
  const confirmMock = vi.fn().mockReturnValueOnce(false).mockReturnValueOnce(true);
  vi.stubGlobal("confirm", confirmMock);
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.selectOptions(screen.getByRole("combobox", { name: "Taxonomy kind" }), "theme");
  await user.type(screen.getByRole("textbox", { name: "Taxonomy value" }), " Identity ");

  expect(screen.getByText("This taxonomy term is already attached.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Add taxonomy term" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Cancel edits" }));
  expect(screen.getByRole("textbox", { name: "Taxonomy value" })).toHaveValue(" Identity ");
  await user.click(screen.getByRole("button", { name: "Cancel edits" }));
  expect(screen.getByRole("textbox", { name: "Taxonomy value" })).toHaveValue("");
  expect(confirmMock).toHaveBeenCalledTimes(2);
});

test("removes one taxonomy term without disturbing its siblings", async () => {
  const user = userEvent.setup();
  const itemWithTerms = {
    ...mediaItem,
    terms: [
      { kind: "theme", value: "identity" },
      { kind: "tone", value: "dreamlike" },
    ],
  };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
    new Response(JSON.stringify([itemWithTerms]), { status: 200, headers: { "Content-Type": "application/json" } }),
  ));

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Remove theme identity taxonomy term" }));

  expect(screen.queryByRole("button", { name: "Remove theme identity taxonomy term" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Remove tone dreamlike taxonomy term" })).toBeInTheDocument();
  expect(JSON.parse(screen.getByTestId("record-preview").textContent ?? "").terms).toEqual([
    { kind: "tone", value: "dreamlike" },
  ]);
});

test("removes one alternate title without disturbing its siblings or advanced fields", async () => {
  const user = userEvent.setup();
  const itemWithAliases = {
    ...mediaItem,
    aliases: [{ value: "Mirrormask" }, { value: "Mirror Mask" }],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([itemWithAliases]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Remove Mirrormask" }));

  expect(screen.queryByRole("button", { name: "Remove Mirrormask" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Remove Mirror Mask" })).toBeInTheDocument();
  expect(JSON.parse(screen.getByTestId("record-preview").textContent ?? "")).toEqual({
    ...itemWithAliases,
    aliases: [{ value: "Mirror Mask" }],
  });
});

test("prevents a case-insensitive duplicate alternate title", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.type(
    screen.getByRole("textbox", { name: "New alternate title" }),
    "  MIRRORMASK  ",
  );

  expect(screen.getByText("That alternate title is already recorded.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Add alternate title" })).toBeDisabled();
  await user.keyboard("{Enter}");
  expect(screen.getAllByRole("button", { name: /Remove Mirrormask/i })).toHaveLength(1);
});

test("protects an unfinished alternate-title draft from cancellation", async () => {
  const user = userEvent.setup();
  const confirmMock = vi.fn().mockReturnValueOnce(false).mockReturnValueOnce(true);
  vi.stubGlobal("confirm", confirmMock);
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  const aliasInput = screen.getByRole("textbox", { name: "New alternate title" });
  await user.type(aliasInput, "unfinished alias");

  await user.click(screen.getByRole("button", { name: "Cancel edits" }));
  expect(aliasInput).toHaveValue("unfinished alias");
  await user.click(screen.getByRole("button", { name: "Cancel edits" }));
  expect(aliasInput).toHaveValue("");
  expect(confirmMock).toHaveBeenCalledTimes(2);
});

test("clears an unfinished alternate-title draft after confirmed record navigation", async () => {
  const user = userEvent.setup();
  const secondItem = { ...mediaItem, id: "movie-coraline", title: "Coraline", aliases: [] };
  vi.stubGlobal("confirm", vi.fn().mockReturnValue(true));
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem, secondItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.type(
    screen.getByRole("textbox", { name: "New alternate title" }),
    "unfinished alias",
  );
  await user.click(screen.getByRole("button", { name: /Coraline/ }));

  expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("Coraline");
  expect(screen.getByRole("textbox", { name: "New alternate title" })).toHaveValue("");
});

test("clears an unfinished alternate-title draft after confirmed new-entry navigation", async () => {
  const user = userEvent.setup();
  vi.stubGlobal("confirm", vi.fn().mockReturnValue(true));
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.type(
    screen.getByRole("textbox", { name: "New alternate title" }),
    "unfinished alias",
  );
  await user.click(screen.getByRole("button", { name: "New entry" }));

  expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("");
  expect(screen.getByRole("textbox", { name: "New alternate title" })).toHaveValue("");
});

test("records a first rating as the authoritative latest history entry", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.type(screen.getByRole("spinbutton", { name: "Rating score" }), "8.5");
  await user.clear(screen.getByLabelText("Rating date"));
  await user.type(screen.getByLabelText("Rating date"), "2026-07-16");
  await user.click(screen.getByRole("button", { name: "Record rating" }));

  const rating = { score: 8.5, rated_on: "2026-07-16" };
  expect(JSON.parse(screen.getByTestId("record-preview").textContent ?? "")).toEqual({
    ...mediaItem,
    rating,
    rating_history: [rating],
  });
  expect(screen.getByText("8.5 / 10")).toBeInTheDocument();
});

test("blocks a rating dated before the latest history entry", async () => {
  const user = userEvent.setup();
  const ratedItem = {
    ...mediaItem,
    rating: { score: 9, rated_on: "2026-07-14" },
    rating_history: [
      { score: 8, rated_on: "2022-05-01", provisional: true },
      { score: 9, rated_on: "2026-07-14" },
    ],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([ratedItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.type(screen.getByRole("spinbutton", { name: "Rating score" }), "7");
  await user.clear(screen.getByLabelText("Rating date"));
  await user.type(screen.getByLabelText("Rating date"), "2025-01-01");

  expect(screen.getByText("Rating date cannot be earlier than the latest history entry.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Record rating" })).toBeDisabled();
});

test("appends a provisional rating and makes it the current projection", async () => {
  const user = userEvent.setup();
  const previous = { score: 9, rated_on: "2026-07-14" };
  const ratedItem = { ...mediaItem, rating: previous, rating_history: [previous] };
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([ratedItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.type(screen.getByRole("spinbutton", { name: "Rating score" }), "7.5");
  await user.clear(screen.getByLabelText("Rating date"));
  await user.type(screen.getByLabelText("Rating date"), "2026-07-16");
  await user.click(screen.getByRole("checkbox", { name: "Provisional rating" }));
  await user.click(screen.getByRole("button", { name: "Record rating" }));

  const latest = { score: 7.5, rated_on: "2026-07-16", provisional: true };
  expect(JSON.parse(screen.getByTestId("record-preview").textContent ?? "")).toEqual({
    ...ratedItem,
    rating: latest,
    rating_history: [previous, latest],
  });
  expect(screen.getByText("7.5 / 10")).toBeInTheDocument();
  expect(screen.getByText("Provisional")).toBeInTheDocument();
});

test("shows field validation for a rating outside the one-to-ten range", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  const score = screen.getByRole("spinbutton", { name: "Rating score" });
  await user.type(score, "10.5");

  expect(score).toHaveAttribute("aria-invalid", "true");
  expect(screen.getByText("Rating must be between 1 and 10.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Record rating" })).toBeDisabled();
});

test("records a valid rating with the Enter key", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  const score = screen.getByRole("spinbutton", { name: "Rating score" });
  await user.type(score, "8");
  await user.clear(screen.getByLabelText("Rating date"));
  await user.type(screen.getByLabelText("Rating date"), "2026-07-16");
  await user.click(score);
  await user.keyboard("{Enter}");

  expect(screen.getByText("8 / 10")).toBeInTheDocument();
  expect(score).toHaveValue(null);
});

test("records a domain-valid fractional rating without native step rejection", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.type(screen.getByRole("spinbutton", { name: "Rating score" }), "8.25");
  await user.clear(screen.getByLabelText("Rating date"));
  await user.type(screen.getByLabelText("Rating date"), "2026-07-16");
  await user.click(screen.getByRole("button", { name: "Record rating" }));

  expect(screen.getByText("8.25 / 10")).toBeInTheDocument();
  expect(screen.getByRole("spinbutton", { name: "Rating score" })).toHaveValue(null);
});

test("protects an unfinished rating draft from cancellation", async () => {
  const user = userEvent.setup();
  const confirmMock = vi.fn().mockReturnValueOnce(false).mockReturnValueOnce(true);
  vi.stubGlobal("confirm", confirmMock);
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  const score = screen.getByRole("spinbutton", { name: "Rating score" });
  await user.type(score, "8");

  await user.click(screen.getByRole("button", { name: "Cancel edits" }));
  expect(score).toHaveValue(8);
  await user.click(screen.getByRole("button", { name: "Cancel edits" }));
  expect(score).toHaveValue(null);
  expect(confirmMock).toHaveBeenCalledTimes(2);
});

test("requires a date before recording a rating", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.type(screen.getByRole("spinbutton", { name: "Rating score" }), "8");
  const date = screen.getByLabelText("Rating date");
  await user.clear(date);

  expect(date).toHaveAttribute("aria-invalid", "true");
  expect(screen.getByText("Rating date is required.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Record rating" })).toBeDisabled();
});

test("clears an unfinished rating draft after confirmed record navigation", async () => {
  const user = userEvent.setup();
  const secondItem = { ...mediaItem, id: "movie-coraline", title: "Coraline" };
  vi.stubGlobal("confirm", vi.fn().mockReturnValue(true));
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem, secondItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.type(screen.getByRole("spinbutton", { name: "Rating score" }), "8");
  await user.click(screen.getByRole("button", { name: /Coraline/ }));

  expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("Coraline");
  expect(screen.getByRole("spinbutton", { name: "Rating score" })).toHaveValue(null);
});

test("keeps a pristine progress status aligned with direct current-status edits", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.selectOptions(screen.getByRole("combobox", { name: "Status" }), "paused");

  expect(screen.getByRole("combobox", { name: "Progress status" })).toHaveValue("paused");
  expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
});

test("appends a progress event and projects its status without dropping prior history", async () => {
  const user = userEvent.setup();
  const previous = {
    status: "currently_consuming",
    amount_completed: 2,
    unit: "hour",
    recorded_on: "2026-07-10",
  };
  const progressedItem = { ...mediaItem, progress_records: [previous] };
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([progressedItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.selectOptions(screen.getByRole("combobox", { name: "Progress status" }), "paused");
  await user.type(screen.getByRole("spinbutton", { name: "Amount completed" }), "3.5");
  await user.selectOptions(screen.getByRole("combobox", { name: "Progress unit" }), "hour");
  await user.clear(screen.getByLabelText("Progress date"));
  await user.type(screen.getByLabelText("Progress date"), "2026-07-16");
  await user.selectOptions(screen.getByRole("combobox", { name: "Return intent" }), "true");
  await user.type(screen.getByRole("textbox", { name: "Progress reason" }), "Waiting for the right mood.");
  await user.click(screen.getByRole("button", { name: "Record progress" }));

  const latest = {
    status: "paused",
    amount_completed: 3.5,
    unit: "hour",
    recorded_on: "2026-07-16",
    return_intent: true,
    reason: "Waiting for the right mood.",
  };
  expect(JSON.parse(screen.getByTestId("record-preview").textContent ?? "")).toEqual({
    ...progressedItem,
    status: "paused",
    progress_records: [previous, latest],
  });
  expect(screen.getByText("Paused · 3.5 hours")).toBeInTheDocument();
  expect(screen.getByText("Plans to return", { selector: ".progress-intent" })).toBeInTheDocument();
  expect(screen.getByText("Waiting for the right mood.", { selector: ".progress-context p" })).toBeInTheDocument();
});

test("records and projects optional progress lifecycle dates", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.type(screen.getByLabelText("Started on"), "2026-07-01");
  await user.type(screen.getByLabelText("Ended on"), "2026-07-16");
  await user.click(screen.getByRole("button", { name: "Record progress" }));

  const preview = JSON.parse(screen.getByTestId("record-preview").textContent ?? "");
  expect(preview.progress_records).toEqual([{
    status: "finished",
    recorded_on: currentLocalDate(),
    started_on: "2026-07-01",
    ended_on: "2026-07-16",
  }]);
  expect(screen.getByText("Started 2026-07-01 · Ended 2026-07-16")).toBeInTheDocument();
});

test("blocks a progress end date before its start date", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.type(screen.getByLabelText("Started on"), "2026-07-10");
  await user.type(screen.getByLabelText("Ended on"), "2026-07-09");

  expect(screen.getByLabelText("Ended on")).toHaveAttribute("aria-invalid", "true");
  expect(screen.getByText("Ended on cannot be before Started on.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Record progress" })).toBeDisabled();
});

test("records zero progress with Enter and omits a blank optional reason", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.selectOptions(
    screen.getByRole("combobox", { name: "Progress status" }),
    "currently_consuming",
  );
  const amount = screen.getByRole("spinbutton", { name: "Amount completed" });
  await user.type(amount, "0");
  await user.selectOptions(screen.getByRole("combobox", { name: "Progress unit" }), "percent");
  await user.type(screen.getByRole("textbox", { name: "Progress reason" }), "   ");
  await user.click(amount);
  await user.keyboard("{Enter}");

  expect(JSON.parse(screen.getByTestId("record-preview").textContent ?? "")).toEqual({
    ...mediaItem,
    status: "currently_consuming",
    progress_records: [{
      status: "currently_consuming",
      amount_completed: 0,
      unit: "percent",
      recorded_on: currentLocalDate(),
    }],
  });
  expect(amount).toHaveValue(null);
});

test("shows backend-valid amount-only and unit-only progress projections", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.type(screen.getByRole("spinbutton", { name: "Amount completed" }), "4.25");
  await user.click(screen.getByRole("button", { name: "Record progress" }));
  expect(screen.getByText("Finished · 4.25 completed")).toBeInTheDocument();

  await user.selectOptions(screen.getByRole("combobox", { name: "Progress unit" }), "chapter");
  await user.click(screen.getByRole("button", { name: "Record progress" }));
  expect(screen.getByText("Finished · Unit: Chapter")).toBeInTheDocument();

  expect(JSON.parse(screen.getByTestId("record-preview").textContent ?? "").progress_records).toEqual([
    { status: "finished", recorded_on: currentLocalDate(), amount_completed: 4.25 },
    { status: "finished", recorded_on: currentLocalDate(), unit: "chapter" },
  ]);
});

test("validates negative progress and a missing progress date", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  const amount = screen.getByRole("spinbutton", { name: "Amount completed" });
  const date = screen.getByLabelText("Progress date");
  await user.type(amount, "-1");
  await user.clear(date);

  expect(amount).toHaveAttribute("aria-invalid", "true");
  expect(screen.getByText("Amount completed cannot be negative.")).toBeInTheDocument();
  expect(date).toHaveAttribute("aria-invalid", "true");
  expect(screen.getByText("Progress date is required.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Record progress" })).toBeDisabled();
});

test("protects and clears an unfinished progress draft after confirmed cancellation", async () => {
  const user = userEvent.setup();
  const confirmMock = vi.fn().mockReturnValueOnce(false).mockReturnValueOnce(true);
  vi.stubGlobal("confirm", confirmMock);
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  const reason = screen.getByRole("textbox", { name: "Progress reason" });
  const startedOn = screen.getByLabelText("Started on");
  await user.type(reason, "Need a breather");
  await user.type(startedOn, "2026-07-01");

  await user.click(screen.getByRole("button", { name: "Cancel edits" }));
  expect(reason).toHaveValue("Need a breather");
  expect(startedOn).toHaveValue("2026-07-01");
  await user.click(screen.getByRole("button", { name: "Cancel edits" }));
  expect(reason).toHaveValue("");
  expect(startedOn).toHaveValue("");
  expect(confirmMock).toHaveBeenCalledTimes(2);
});

test("clears an unfinished progress draft after confirmed record navigation", async () => {
  const user = userEvent.setup();
  const secondItem = { ...mediaItem, id: "movie-coraline", title: "Coraline" };
  vi.stubGlobal("confirm", vi.fn().mockReturnValue(true));
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem, secondItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.type(screen.getByRole("textbox", { name: "Progress reason" }), "Need a breather");
  await user.click(screen.getByRole("button", { name: /Coraline/ }));

  expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("Coraline");
  expect(screen.getByRole("textbox", { name: "Progress reason" })).toHaveValue("");
  expect(screen.getByRole("combobox", { name: "Progress status" })).toHaveValue("finished");
});

test("appends an evidence-backed work observation without dropping prior observations", async () => {
  const user = userEvent.setup();
  const previous = {
    id: "obs-existing",
    scope: "work",
    polarity: "mixed",
    dimension: "tone",
    text: "Delightfully chaotic but exhausting.",
    provenance: "user_explicit",
    privacy: "assistant_readable",
    review_state: "accepted",
    observed_on: "2026-07-01",
  };
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([{ ...mediaItem, observations: [previous] }]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.selectOptions(screen.getByRole("combobox", { name: "Observation polarity" }), "positive");
  await user.type(screen.getByRole("textbox", { name: "Observation dimension" }), "visual style");
  await user.type(screen.getByRole("textbox", { name: "Observation text" }), "The handmade dream logic is gorgeous.");
  await user.selectOptions(screen.getByRole("combobox", { name: "Observation provenance" }), "user_explicit");
  await user.selectOptions(screen.getByRole("combobox", { name: "Observation privacy" }), "exclude_from_recommendations");
  await user.type(screen.getByRole("textbox", { name: "Source context" }), "Personal note after rewatching.");
  await user.clear(screen.getByLabelText("Observed on"));
  await user.type(screen.getByLabelText("Observed on"), "2026-07-16");
  await user.click(screen.getByRole("button", { name: "Record observation" }));

  expect(JSON.parse(screen.getByTestId("record-preview").textContent ?? "").observations).toEqual([
    previous,
    {
      id: "obs-movie-mirrormask-2",
      scope: "work",
      polarity: "positive",
      dimension: "visual style",
      text: "The handmade dream logic is gorgeous.",
      provenance: "user_explicit",
      privacy: "exclude_from_recommendations",
      source_context: "Personal note after rewatching.",
      review_state: "accepted",
      observed_on: "2026-07-16",
    },
  ]);
  expect(screen.getByText("Positive · visual style")).toBeInTheDocument();
  expect(screen.getByText("The handmade dream logic is gorgeous.")).toBeInTheDocument();
});

test("allocates an observation ID without colliding with sparse existing IDs", async () => {
  const user = userEvent.setup();
  const existing = {
    id: "obs-movie-mirrormask-2",
    scope: "work",
    polarity: "neutral",
    dimension: "context",
    text: "Existing note.",
    provenance: "manual",
    observed_on: "2026-07-01",
  };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
    JSON.stringify([{ ...mediaItem, observations: [existing] }]),
    { status: 200, headers: { "Content-Type": "application/json" } },
  )));

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.type(screen.getByRole("textbox", { name: "Observation dimension" }), "music");
  await user.type(screen.getByRole("textbox", { name: "Observation text" }), "The score feels tactile.");
  await user.click(screen.getByRole("button", { name: "Record observation" }));

  const observations = JSON.parse(screen.getByTestId("record-preview").textContent ?? "").observations;
  expect(observations.at(-1).id).toBe("obs-movie-mirrormask-3");
});

test("requires traceable subjects for non-work observations", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.selectOptions(screen.getByRole("combobox", { name: "Observation scope" }), "character");
  await user.type(screen.getByRole("textbox", { name: "Observation dimension" }), "character focus");
  await user.type(screen.getByRole("textbox", { name: "Observation text" }), "Helena's uncertainty carries the story.");

  expect(screen.getByText("Subject ID and label are required outside whole-work scope.")).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Subject ID" })).toHaveAttribute("aria-invalid", "true");
  expect(screen.getByRole("button", { name: "Record observation" })).toBeDisabled();
});

test("shows accessible errors for blank observation dimension and text", async () => {
  const user = userEvent.setup();
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify([mediaItem]), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })));

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.type(screen.getByRole("textbox", { name: "Observation dimension" }), "   ");
  await user.type(screen.getByRole("textbox", { name: "Observation text" }), "   ");
  await user.click(screen.getByRole("button", { name: "Record observation" }));

  expect(screen.getByText("Observation dimension is required.")).toBeInTheDocument();
  expect(screen.getByText("Observation text is required.")).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Observation dimension" })).toHaveAttribute("aria-invalid", "true");
  expect(JSON.parse(screen.getByTestId("record-preview").textContent ?? "").observations).toBeUndefined();
});

test("shows an accessible error when the observation date is missing", async () => {
  const user = userEvent.setup();
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify([mediaItem]), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })));

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.type(screen.getByRole("textbox", { name: "Observation dimension" }), "tone");
  await user.type(screen.getByRole("textbox", { name: "Observation text" }), "Dreamlike.");
  await user.clear(screen.getByLabelText("Observed on"));
  await user.click(screen.getByRole("button", { name: "Record observation" }));

  expect(screen.getByText("Observed on is required.")).toBeInTheDocument();
  expect(screen.getByLabelText("Observed on")).toHaveAttribute("aria-invalid", "true");
});

test("protects and clears an unfinished observation draft after confirmed cancellation", async () => {
  const user = userEvent.setup();
  const confirmMock = vi.fn().mockReturnValueOnce(false).mockReturnValueOnce(true);
  vi.stubGlobal("confirm", confirmMock);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify([mediaItem]), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })));

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  const text = screen.getByRole("textbox", { name: "Observation text" });
  await user.type(text, "The dream logic stuck with me.");
  await user.click(screen.getByRole("button", { name: "Cancel edits" }));
  expect(text).toHaveValue("The dream logic stuck with me.");
  await user.click(screen.getByRole("button", { name: "Cancel edits" }));
  expect(text).toHaveValue("");
  expect(confirmMock).toHaveBeenCalledTimes(2);
});

test("loads creator identities on demand and resolves existing typed credits", async () => {
  const user = userEvent.setup();
  const creditedMedia = {
    ...mediaItem,
    credits: [{ creator_id: "creator-jim-henson", role: "producer" }],
  };
  const creators = [
    { id: "creator-jim-henson", name: "Jim Henson" },
    { id: "creator-dave-mckean", name: "Dave McKean" },
  ];
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([creditedMedia]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify(creators), { status: 200, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Manage creator credits" }));

  expect(await screen.findByText("Jim Henson · Producer")).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "Creator" })).toHaveValue("");
  expect(screen.getByRole("option", { name: "Dave McKean" })).toBeInTheDocument();
  expect(fetchMock).toHaveBeenLastCalledWith("/api/creators", expect.anything());
});

test("loads a credited creator work index and opens an existing work", async () => {
  const user = userEvent.setup();
  const creditedMedia = {
    ...mediaItem,
    credits: [{ creator_id: "creator-dave-mckean", role: "director" }],
  };
  const otherWork = {
    ...mediaItem,
    id: "movie-wolves-in-the-walls",
    title: "The Wolves in the Walls",
    credits: [{ creator_id: "creator-dave-mckean", role: "director" }],
  };
  const creator = { id: "creator-dave-mckean", name: "Dave McKean" };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([creditedMedia, otherWork]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify([creator]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify([creditedMedia, otherWork]), { status: 200, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Manage creator credits" }));
  await user.click(await screen.findByRole("button", { name: "View Dave McKean works" }));

  const index = await screen.findByRole("region", { name: "Dave McKean work index" });
  expect(within(index).getAllByRole("button").map((button) => button.textContent)).toEqual([
    "MirrorMask", "The Wolves in the Walls",
  ]);
  expect(fetchMock).toHaveBeenLastCalledWith("/api/creators/creator-dave-mckean/media");

  await user.click(within(index).getByRole("button", { name: "The Wolves in the Walls" }));
  expect(screen.getByRole("heading", { name: "The Wolves in the Walls" })).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(3);
});

test("composes archived visibility and shows an empty creator work index", async () => {
  const user = userEvent.setup();
  const creditedMedia = {
    ...mediaItem,
    credits: [{ creator_id: "creator-dave-mckean", role: "director" }],
  };
  const creator = { id: "creator-dave-mckean", name: "Dave McKean" };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([creditedMedia]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify([creditedMedia]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify([creator]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify([]), { status: 200, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Show archived records" }));
  await user.click(screen.getByRole("button", { name: "Manage creator credits" }));
  await user.click(await screen.findByRole("button", { name: "View Dave McKean works" }));

  expect(await screen.findByText("No matching visible works.")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenLastCalledWith("/api/creators/creator-dave-mckean/media?include_archived=true");
});

test("rejects unverifiable creator work evidence", async () => {
  const user = userEvent.setup();
  const creditedMedia = {
    ...mediaItem,
    credits: [{ creator_id: "creator-dave-mckean", role: "director" }],
  };
  const creator = { id: "creator-dave-mckean", name: "Dave McKean" };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([creditedMedia]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify([creator]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify([{ ...creditedMedia, id: "missing-work" }]), { status: 200, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Manage creator credits" }));
  await user.click(await screen.findByRole("button", { name: "View Dave McKean works" }));

  expect(await screen.findByRole("alert"))
    .toHaveTextContent("The creator work index refers to unverifiable visible-library evidence.");
  expect(screen.queryByRole("region", { name: "Dave McKean work index" })).not.toBeInTheDocument();
});

test("rejects malformed successful creator work payloads", async () => {
  const user = userEvent.setup();
  const creditedMedia = {
    ...mediaItem,
    credits: [{ creator_id: "creator-dave-mckean", role: "director" }],
  };
  const creator = { id: "creator-dave-mckean", name: "Dave McKean" };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([creditedMedia]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify([creator]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify([{ id: "broken-work", title: "" }]), { status: 200, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Manage creator credits" }));
  await user.click(await screen.findByRole("button", { name: "View Dave McKean works" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("The creator work index could not be verified.");
  expect(screen.queryByRole("region", { name: "Dave McKean work index" })).not.toBeInTheDocument();
});

test("claims the shared mutex before creator work loading renders and releases it on failure", async () => {
  const user = userEvent.setup();
  let resolveWorks!: (response: Response) => void;
  const worksResponse = new Promise<Response>((resolve) => { resolveWorks = resolve; });
  const creditedMedia = {
    ...mediaItem,
    credits: [{ creator_id: "creator-dave-mckean", role: "director" }],
  };
  const creator = { id: "creator-dave-mckean", name: "Dave McKean" };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([creditedMedia]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify([creator]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockReturnValueOnce(worksResponse);
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Manage creator credits" }));
  const viewWorks = await screen.findByRole("button", { name: "View Dave McKean works" });
  const newEntry = screen.getByRole("button", { name: "New entry" });
  const archive = screen.getByRole("button", { name: "Archive record" });

  act(() => {
    viewWorks.click();
    newEntry.click();
    archive.click();
  });

  expect(within(screen.getByRole("region", { name: "Creator credits" })).getByRole("status"))
    .toHaveTextContent("Loading directly credited works…");
  expect(screen.getByRole("heading", { name: "MirrorMask" })).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(3);

  resolveWorks(new Response("unavailable", { status: 503 }));
  expect(await screen.findByRole("alert")).toHaveTextContent("The creator work index is unavailable.");
  expect(screen.getByRole("button", { name: "New entry" })).toBeEnabled();
});

test("creates a reusable creator identity before crediting the record", async () => {
  const user = userEvent.setup();
  const created = { id: "creator-dave-mckean", name: "Dave McKean" };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify([]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify(created), { status: 200, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Manage creator credits" }));
  await user.type(screen.getByRole("textbox", { name: "Creator name" }), "Dave McKean");
  expect(screen.getByRole("textbox", { name: "Creator stable ID" })).toHaveValue("creator-dave-mckean");
  await user.click(screen.getByRole("button", { name: "Create creator" }));

  expect(fetchMock).toHaveBeenLastCalledWith("/api/creators", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(created),
  });
  expect(await screen.findByRole("option", { name: "Dave McKean" })).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "Creator" })).toHaveValue("creator-dave-mckean");
});

test("locks record-changing controls while creator creation is pending", async () => {
  const user = userEvent.setup();
  let resolveCreate: ((response: Response) => void) | undefined;
  const createResponse = new Promise<Response>((resolve) => { resolveCreate = resolve; });
  const created = { id: "creator-dave-mckean", name: "Dave McKean" };
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify([]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockReturnValueOnce(createResponse));

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Manage creator credits" }));
  await user.type(screen.getByRole("textbox", { name: "Creator name" }), "Dave McKean");
  await user.click(screen.getByRole("button", { name: "Create creator" }));

  expect(screen.getByRole("textbox", { name: "Title" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "New entry" })).toBeDisabled();
  expect(screen.getByRole("searchbox", { name: "Search library" })).toBeDisabled();

  resolveCreate?.(new Response(JSON.stringify(created), { status: 201, headers: { "Content-Type": "application/json" } }));
  expect(await screen.findByRole("option", { name: "Dave McKean" })).toBeInTheDocument();
});

test("suggests a stable creator ID for a non-Latin name", async () => {
  const user = userEvent.setup();
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify([]), { status: 200, headers: { "Content-Type": "application/json" } })));

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Manage creator credits" }));
  await user.type(screen.getByRole("textbox", { name: "Creator name" }), "宮崎駿");

  expect(screen.getByRole("textbox", { name: "Creator stable ID" })).toHaveValue("creator-宮崎駿");
});

test("keeps a manually edited creator stable ID while the name continues changing", async () => {
  const user = userEvent.setup();
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify([]), { status: 200, headers: { "Content-Type": "application/json" } })));

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Manage creator credits" }));
  const name = screen.getByRole("textbox", { name: "Creator name" });
  const id = screen.getByRole("textbox", { name: "Creator stable ID" });
  await user.type(name, "Dave McKean");
  await user.clear(id);
  await user.type(id, "creator-custom-dave");
  await user.type(name, " Jr.");
  expect(id).toHaveValue("creator-custom-dave");
});

test("appends a typed creator credit without dropping existing record fields", async () => {
  const user = userEvent.setup();
  const creditedMedia = {
    ...mediaItem,
    credits: [{ creator_id: "creator-jim-henson", role: "producer" }],
    terms: [{ kind: "theme", value: "identity" }],
  };
  const creators = [
    { id: "creator-jim-henson", name: "Jim Henson" },
    { id: "creator-dave-mckean", name: "Dave McKean" },
  ];
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([creditedMedia]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify(creators), { status: 200, headers: { "Content-Type": "application/json" } })));

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Manage creator credits" }));
  await user.selectOptions(screen.getByRole("combobox", { name: "Creator" }), "creator-dave-mckean");
  await user.selectOptions(screen.getByRole("combobox", { name: "Credit role" }), "director");
  await user.click(screen.getByRole("button", { name: "Add creator credit" }));

  const preview = JSON.parse(screen.getByTestId("record-preview").textContent ?? "");
  expect(preview.credits).toEqual([
    { creator_id: "creator-jim-henson", role: "producer" },
    { creator_id: "creator-dave-mckean", role: "director" },
  ]);
  expect(preview.terms).toEqual([{ kind: "theme", value: "identity" }]);
  expect(screen.getByText("Dave McKean · Director")).toBeInTheDocument();
});

test("removes one creator credit without disturbing its siblings", async () => {
  const user = userEvent.setup();
  const creditedMedia = {
    ...mediaItem,
    credits: [
      { creator_id: "creator-jim-henson", role: "producer" },
      { creator_id: "creator-dave-mckean", role: "director" },
    ],
  };
  const creators = [
    { id: "creator-jim-henson", name: "Jim Henson" },
    { id: "creator-dave-mckean", name: "Dave McKean" },
  ];
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([creditedMedia]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify(creators), { status: 200, headers: { "Content-Type": "application/json" } })));

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Manage creator credits" }));
  await user.click(await screen.findByRole("button", { name: "Remove Dave McKean director credit" }));

  expect(JSON.parse(screen.getByTestId("record-preview").textContent ?? "").credits).toEqual([
    { creator_id: "creator-jim-henson", role: "producer" },
  ]);
  expect(screen.getByText("Jim Henson · Producer")).toBeInTheDocument();
  expect(screen.queryByText("Dave McKean · Director")).not.toBeInTheDocument();
});

test("appends a typed relationship to another existing media record", async () => {
  const user = userEvent.setup();
  const related = { id: "movie-labyrinth", title: "Labyrinth", category: "movie", status: "finished" };
  const source = {
    ...mediaItem,
    credits: [{ creator_id: "creator-jim-henson", role: "producer" }],
    relationships: [],
  };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(
    new Response(JSON.stringify([source, related]), { status: 200, headers: { "Content-Type": "application/json" } }),
  ));

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Manage media relationships" }));
  await user.selectOptions(screen.getByRole("combobox", { name: "Related media" }), "movie-labyrinth");
  await user.selectOptions(screen.getByRole("combobox", { name: "Relationship type" }), "same_creator");
  await user.click(screen.getByRole("button", { name: "Add relationship" }));

  const preview = JSON.parse(screen.getByTestId("record-preview").textContent ?? "");
  expect(preview.relationships).toEqual([
    { relationship_type: "same_creator", target_media_item_id: "movie-labyrinth" },
  ]);
  expect(preview.credits).toEqual([{ creator_id: "creator-jim-henson", role: "producer" }]);
  expect(screen.getByText("Labyrinth · Same creator")).toBeInTheDocument();
});

test("removes one relationship without disturbing its siblings", async () => {
  const user = userEvent.setup();
  const labyrinth = { id: "movie-labyrinth", title: "Labyrinth", category: "movie", status: "finished" };
  const coraline = { id: "movie-coraline", title: "Coraline", category: "movie", status: "finished" };
  const source = {
    ...mediaItem,
    relationships: [
      { relationship_type: "same_creator", target_media_item_id: "movie-labyrinth" },
      { relationship_type: "same_universe", target_media_item_id: "movie-coraline" },
    ],
  };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(
    new Response(JSON.stringify([source, labyrinth, coraline]), { status: 200, headers: { "Content-Type": "application/json" } }),
  ));

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Remove Labyrinth same_creator relationship" }));

  expect(JSON.parse(screen.getByTestId("record-preview").textContent ?? "").relationships).toEqual([
    { relationship_type: "same_universe", target_media_item_id: "movie-coraline" },
  ]);
  expect(screen.getByText("Coraline · Same universe")).toBeInTheDocument();
  expect(screen.queryByText("Labyrinth · Same creator")).not.toBeInTheDocument();
});

test("blocks record saving while a relationship selection remains unfinished", async () => {
  const user = userEvent.setup();
  const related = { id: "movie-labyrinth", title: "Labyrinth", category: "movie", status: "finished" };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(
    new Response(JSON.stringify([mediaItem, related]), { status: 200, headers: { "Content-Type": "application/json" } }),
  ));

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.clear(screen.getByRole("textbox", { name: "Title" }));
  await user.type(screen.getByRole("textbox", { name: "Title" }), "MirrorMask revised");
  await user.click(screen.getByRole("button", { name: "Manage media relationships" }));
  await user.selectOptions(screen.getByRole("combobox", { name: "Related media" }), "movie-labyrinth");

  expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
});

test("protects an unfinished relationship draft across record navigation", async () => {
  const user = userEvent.setup();
  const related = { id: "movie-labyrinth", title: "Labyrinth", category: "movie", status: "finished" };
  const confirmMock = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
  confirmMock.mockClear();
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(
    new Response(JSON.stringify([mediaItem, related]), { status: 200, headers: { "Content-Type": "application/json" } }),
  ));

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Manage media relationships" }));
  const target = screen.getByRole("combobox", { name: "Related media" });
  await user.selectOptions(target, "movie-labyrinth");
  await user.click(screen.getByRole("button", { name: /LabyrinthMovie/ }));
  expect(screen.getByRole("heading", { name: "MirrorMask" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /LabyrinthMovie/ }));
  expect(screen.getByRole("heading", { name: "Labyrinth" })).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "Related media" })).toHaveValue("");
  expect(confirmMock).toHaveBeenCalledTimes(2);
});

test("loads pending assistant observation proposals with their evidence trail", async () => {
  const user = userEvent.setup();
  const proposal = {
    id: "proposal-mirrormask-art",
    target_media_item_id: "movie-mirrormask",
    kind: "observation",
    proposed_observation: {
      id: "obs-inferred-art",
      scope: "character",
      subject_id: "character-helena",
      subject_label: "Helena",
      polarity: "positive",
      dimension: "visual style",
      text: "Likely values handmade surreal imagery.",
      provenance: "assistant_inferred",
      privacy: "assistant_readable",
      source_context: "The handmade imagery felt like a dream someone physically built.",
      confidence: 0.78,
      review_state: "needs_review",
      observed_on: "2026-07-16",
    },
    source_context: "telegram:session-123:message-456",
    confidence: 0.78,
    review_state: "needs_review",
    proposed_on: "2026-07-16",
  };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify([proposal]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Review inference proposals" }));

  expect(await screen.findByText("Likely values handmade surreal imagery.")).toBeInTheDocument();
  expect(screen.getByText("78% confidence")).toBeInTheDocument();
  expect(screen.getByText("telegram:session-123:message-456")).toBeInTheDocument();
  expect(screen.getByText("The handmade imagery felt like a dream someone physically built.")).toBeInTheDocument();
  const proposalCard = screen.getByRole("article");
  expect(within(proposalCard).getByText("Assistant inferred")).toBeInTheDocument();
  expect(within(proposalCard).getByText("Assistant readable")).toBeInTheDocument();
  expect(within(proposalCard).getByText("Helena")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Accept inference" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Reject inference" })).toBeInTheDocument();
  expect(fetchMock).toHaveBeenLastCalledWith("/api/proposals", expect.anything());
});

test("reveals reviewed proposal history for the selected record without crowding the active queue", async () => {
  const user = userEvent.setup();
  const observation = {
    id: "obs-history", scope: "work", polarity: "positive", dimension: "visual style",
    text: "Likely values handmade sets.", provenance: "assistant_inferred",
    source_context: "The sets felt touched by human hands.", confidence: 0.8,
    review_state: "needs_review", observed_on: "2026-07-16",
  };
  const rejected = {
    id: "proposal-rejected", target_media_item_id: "movie-mirrormask", kind: "observation",
    proposed_observation: observation, source_context: "telegram:message-1", confidence: 0.8,
    review_state: "rejected", proposed_on: "2026-07-16",
  };
  const promoted = {
    ...rejected, id: "proposal-promoted", review_state: "accepted",
    proposed_on: "2026-07-17",
    promoted_observation_id: "obs-history-2",
    proposed_observation: { ...observation, id: "obs-history-promoted", text: "Values intimate dream logic." },
  };
  const pending = { ...rejected, id: "proposal-pending", review_state: "needs_review" };
  const otherRecord = { ...rejected, id: "proposal-other", target_media_item_id: "movie-other" };
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify([rejected, promoted, pending, otherRecord]), { status: 200, headers: { "Content-Type": "application/json" } })));

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Review inference proposals" }));

  expect(await screen.findByRole("button", { name: "Show proposal history (2)" })).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByRole("heading", { name: "Proposal history" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Show proposal history (2)" }));

  expect(screen.getByRole("button", { name: "Hide proposal history (2)" })).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByRole("heading", { name: "Proposal history" })).toBeInTheDocument();
  const history = screen.getByRole("region", { name: "Proposal history" });
  const historyCards = within(history).getAllByRole("article");
  expect(within(historyCards[0]).getByText("Promoted as obs-history-2")).toBeInTheDocument();
  expect(within(historyCards[1]).getByText("Rejected")).toBeInTheDocument();
  expect(within(history).getByText("Rejected")).toBeInTheDocument();
  expect(within(history).getByText("Promoted as obs-history-2")).toBeInTheDocument();
  expect(within(history).getByText("Likely values handmade sets.")).toBeInTheDocument();
  expect(within(history).getByText("Values intimate dream logic.")).toBeInTheDocument();
});

test("renders rejected metadata proposal history as inert inspectable text", async () => {
  const user = userEvent.setup();
  const proposal = {
    id: "proposal-metadata", target_media_item_id: "movie-mirrormask", kind: "metadata",
    metadata_field: "status", metadata_value: { candidate: "<img src=x onerror=alert(1)>" },
    source_context: "import:row-12", confidence: 0.61,
    review_state: "rejected", proposed_on: "2026-07-16",
  };
  const acceptedMetadata = {
    ...proposal, id: "proposal-metadata-accepted", metadata_field: "category",
    metadata_value: "anime_movie", review_state: "accepted",
  };
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify([proposal, acceptedMetadata]), { status: 200, headers: { "Content-Type": "application/json" } })));

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Review inference proposals" }));
  await user.click(await screen.findByRole("button", { name: "Show proposal history (2)" }));

  const history = screen.getByRole("region", { name: "Proposal history" });
  expect(within(history).getByText("Accepted")).toBeInTheDocument();
  expect(within(history).getByText("Status")).toBeInTheDocument();
  expect(within(history).getByText('{"candidate":"<img src=x onerror=alert(1)>"}')).toBeInTheDocument();
  expect(history.querySelector("img")).toBeNull();
});


test("reviews and explicitly promotes a targetless media candidate without a silent canonical write", async () => {
  const user = userEvent.setup();
  const proposal = {
    id: "proposal-perfect-blue", kind: "media_item",
    proposed_media_item: { id: "movie-perfect-blue-1997", title: "Perfect Blue", category: "movie", status: "finished" },
    source_context: "conversation:perfect-blue", confidence: 0.9,
    review_state: "needs_review", proposed_on: "2026-07-21",
  };
  const accepted = { ...proposal, review_state: "accepted" };
  const promoted = { ...accepted, promoted_media_item_id: "movie-perfect-blue-1997" };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify([proposal]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify(accepted), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ proposal: promoted, media_item: proposal.proposed_media_item }), { status: 200, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Review inference proposals" }));
  expect(await screen.findByText("New library candidate · Perfect Blue")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Accept candidate" }));
  await user.click(await screen.findByRole("button", { name: "Promote to library" }));

  await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/proposals/proposal-perfect-blue/promote-media",
    expect.objectContaining({ method: "POST" }),
  ));
  expect(screen.getByText("Perfect Blue was promoted into the canonical library.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Perfect BlueMovie/ })).toBeInTheDocument();
});

test("accepts an inference review outcome without silently changing media evidence", async () => {
  const user = userEvent.setup();
  const proposal = {
    id: "proposal-mirrormask-art",
    target_media_item_id: "movie-mirrormask",
    kind: "observation",
    proposed_observation: {
      id: "obs-inferred-art", scope: "work", polarity: "positive",
      dimension: "visual style", text: "Likely values handmade surreal imagery.",
      provenance: "assistant_inferred", source_context: "telegram:message-456",
      confidence: 0.78, review_state: "needs_review", observed_on: "2026-07-16",
    },
    source_context: "telegram:message-456", confidence: 0.78,
    review_state: "needs_review", proposed_on: "2026-07-16",
  };
  const accepted = { ...proposal, review_state: "accepted" };
  let resolveReview!: (response: Response) => void;
  const reviewResponse = new Promise<Response>((resolve) => { resolveReview = resolve; });
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify([proposal]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockReturnValueOnce(reviewResponse);
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Review inference proposals" }));
  await user.click(await screen.findByRole("button", { name: "Accept inference" }));

  expect(screen.getByRole("button", { name: "Saving review…" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Reject inference" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Review inference proposals" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Concierge home" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "New entry" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Search" })).toBeDisabled();
  expect(screen.getByRole("button", { name: /MirrorMaskMovie/ })).toBeDisabled();
  resolveReview(new Response(JSON.stringify(accepted), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }));

  await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/proposals/proposal-mirrormask-art/accept",
    expect.objectContaining({ method: "POST" }),
  ));
  expect(screen.getByText("Likely values handmade surreal imagery.")).toBeInTheDocument();
  expect(screen.getByText("Accepted inference awaiting promotion")).toBeInTheDocument();
  expect(screen.getByText("Inference accepted as a review outcome; media evidence was not changed.")).toBeInTheDocument();
  expect(JSON.parse(screen.getByTestId("record-preview").textContent ?? "").observations).toBeUndefined();
});

test("rejects an inference review outcome without changing media evidence", async () => {
  const user = userEvent.setup();
  const proposal = {
    id: "proposal-reject", target_media_item_id: "movie-mirrormask", kind: "observation",
    proposed_observation: {
      id: "obs-reject", scope: "work", polarity: "negative", dimension: "pacing",
      text: "Likely dislikes the middle stretch.", provenance: "assistant_inferred",
      source_context: "telegram:message-789", confidence: 0.64,
      review_state: "needs_review", observed_on: "2026-07-16",
    },
    source_context: "telegram:message-789", confidence: 0.64,
    review_state: "needs_review", proposed_on: "2026-07-16",
  };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify([proposal]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ ...proposal, review_state: "rejected" }), { status: 200, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Review inference proposals" }));
  await user.click(await screen.findByRole("button", { name: "Reject inference" }));

  await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/proposals/proposal-reject/reject",
    expect.objectContaining({ method: "POST" }),
  ));
  expect(screen.queryByText("Likely dislikes the middle stretch.")).not.toBeInTheDocument();
  expect(screen.getByText("Inference rejected; media evidence was not changed.")).toBeInTheDocument();
  expect(JSON.parse(screen.getByTestId("record-preview").textContent ?? "").observations).toBeUndefined();
});

test("loads accepted inferences awaiting explicit evidence promotion", async () => {
  const user = userEvent.setup();
  const proposal = {
    id: "proposal-accepted", target_media_item_id: "movie-mirrormask", kind: "observation",
    proposed_observation: {
      id: "obs-accepted", scope: "work", polarity: "positive", dimension: "dream logic",
      text: "Values handmade dream logic.", provenance: "assistant_inferred",
      source_context: "The crooked city felt intimate.", confidence: 0.82,
      review_state: "needs_review", observed_on: "2026-07-16",
    },
    source_context: "telegram:message-456", confidence: 0.82,
    review_state: "accepted", proposed_on: "2026-07-16",
  };
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify([proposal]), { status: 200, headers: { "Content-Type": "application/json" } })));

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Review inference proposals" }));

  expect(await screen.findByText("Accepted inference awaiting promotion")).toBeInTheDocument();
  expect(screen.getByText("Values handmade dream logic.")).toBeInTheDocument();
  expect(screen.getByText("82% confidence")).toBeInTheDocument();
  expect(screen.getByText("telegram:message-456")).toBeInTheDocument();
  expect(screen.getByText("The crooked city felt intimate.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Promote to evidence" })).toBeInTheDocument();
});

test("promotes an accepted inference through the atomic endpoint and refreshes canonical evidence", async () => {
  const user = userEvent.setup();
  const proposedObservation = {
    id: "obs-accepted", scope: "work", polarity: "positive", dimension: "dream logic",
    text: "Values handmade dream logic.", provenance: "assistant_inferred",
    source_context: "The crooked city felt intimate.", confidence: 0.82,
    review_state: "needs_review", observed_on: "2026-07-16",
  };
  const proposal = {
    id: "proposal-accepted", target_media_item_id: "movie-mirrormask", kind: "observation",
    proposed_observation: proposedObservation, source_context: "telegram:message-456",
    confidence: 0.82, review_state: "accepted", proposed_on: "2026-07-16",
  };
  const promotedObservation = { ...proposedObservation, review_state: "accepted" };
  const promotedProposal = { ...proposal, promoted_observation_id: "obs-accepted" };
  const promotedMedia = { ...mediaItem, observations: [promotedObservation] };
  const pendingProposal = {
    ...proposal,
    id: "proposal-pending",
    review_state: "needs_review",
    proposed_observation: { ...proposedObservation, id: "obs-pending" },
  };
  let resolvePromotion!: (response: Response) => void;
  const promotionResponse = new Promise<Response>((resolve) => {
    resolvePromotion = resolve;
  });
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify([proposal, pendingProposal]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockReturnValueOnce(promotionResponse);
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Review inference proposals" }));
  await user.click(await screen.findByRole("button", { name: "Promote to evidence" }));

  expect(screen.getByRole("button", { name: "Promoting…" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Reject inference" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Review inference proposals" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Concierge home" })).toBeDisabled();
  expect(screen.getByRole("textbox", { name: "Title" })).toBeDisabled();
  expect(screen.getByRole("combobox", { name: "Category" })).toBeDisabled();
  resolvePromotion(new Response(
    JSON.stringify({ proposal: promotedProposal, media_item: promotedMedia }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  ));

  await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/proposals/proposal-accepted/promote",
    expect.objectContaining({ method: "POST" }),
  ));
  expect(screen.queryByText("Accepted inference awaiting promotion")).not.toBeInTheDocument();
  expect(screen.getByText("Inference promoted to canonical evidence.")).toBeInTheDocument();
  expect(JSON.parse(screen.getByTestId("record-preview").textContent ?? "").observations).toEqual([
    promotedObservation,
  ]);
});

test("journal loading claims the same-tick library-operation lock before another write", async () => {
  const recommendationResponse = new Promise<Response>(() => undefined);
  const archiveResponse = new Promise<Response>(() => undefined);
  const fetchMock = vi.fn().mockImplementation((url: string) => {
    if (url === "/api/media") {
      return Promise.resolve(new Response(JSON.stringify([mediaItem]), { status: 200, headers: { "Content-Type": "application/json" } }));
    }
    if (url === "/api/recommendations") return recommendationResponse;
    if (url === "/api/media?include_archived=true") {
      return Promise.resolve(new Response(JSON.stringify([mediaItem]), { status: 200, headers: { "Content-Type": "application/json" } }));
    }
    if (url.endsWith("/archive")) return archiveResponse;
    throw new Error(`unexpected request ${url}`);
  });
  vi.stubGlobal("confirm", vi.fn().mockReturnValue(true));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });

  act(() => {
    fireEvent.click(screen.getByRole("button", { name: "Open recommendation journal" }));
    fireEvent.click(screen.getByRole("button", { name: "Archive record" }));
  });

  expect(fetchMock.mock.calls.some(([url]) => url === "/api/recommendations")).toBe(true);
  expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/archive"))).toBe(false);
});

test("does not start a recommendation outcome after archive claims the same-tick write lock", async () => {
  const user = userEvent.setup();
  const recommendationRecord = {
    id: "recommendation-mirrormask-2026-07-18",
    media_item_id: mediaItem.id,
    recommended_on: "2026-07-18",
    source: "user",
    rationale: "A factual recommendation occurrence.",
  };
  const archiveResponse = new Promise<Response>(() => undefined);
  const outcomeResponse = new Promise<Response>(() => undefined);
  const fetchMock = vi.fn().mockImplementation((url: string) => {
    if (url === "/api/media") {
      return Promise.resolve(new Response(JSON.stringify([mediaItem]), { status: 200, headers: { "Content-Type": "application/json" } }));
    }
    if (url === "/api/recommendations") {
      return Promise.resolve(new Response(JSON.stringify([recommendationRecord]), { status: 200, headers: { "Content-Type": "application/json" } }));
    }
    if (url === "/api/media?include_archived=true") {
      return Promise.resolve(new Response(JSON.stringify([mediaItem]), { status: 200, headers: { "Content-Type": "application/json" } }));
    }
    if (url.endsWith("/archive")) return archiveResponse;
    if (url.endsWith("/outcomes")) return outcomeResponse;
    throw new Error(`unexpected request ${url}`);
  });
  vi.stubGlobal("confirm", vi.fn().mockReturnValue(true));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Open recommendation journal" }));
  await screen.findByRole("region", { name: "Recommendation journal" });
  await user.click(screen.getByRole("button", { name: "Record outcome for MirrorMask" }));
  await user.selectOptions(screen.getByLabelText("Outcome kind"), "tried");
  await user.clear(screen.getByLabelText("Recorded date"));
  await user.type(screen.getByLabelText("Recorded date"), "2026-07-18");
  const outcomeForm = screen.getByRole("button", { name: "Record outcome" }).closest("form");
  expect(outcomeForm).not.toBeNull();

  act(() => {
    fireEvent.click(screen.getByRole("button", { name: "Archive record" }));
    fireEvent.submit(outcomeForm!);
  });

  expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/archive"))).toBe(true);
  expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/outcomes"))).toBe(false);
});

test("locks library navigation while a recommendation outcome request is pending", async () => {
  const user = userEvent.setup();
  const recommendationRecord = {
    id: "recommendation-mirrormask-2026-07-18",
    media_item_id: mediaItem.id,
    recommended_on: "2026-07-18",
    source: "user",
    rationale: "A factual recommendation occurrence.",
  };
  let resolveOutcome!: (response: Response) => void;
  const outcomeResponse = new Promise<Response>((resolve) => { resolveOutcome = resolve; });
  const fetchMock = vi.fn().mockImplementation((url: string) => {
    if (url === "/api/media") {
      return Promise.resolve(new Response(JSON.stringify([mediaItem]), { status: 200, headers: { "Content-Type": "application/json" } }));
    }
    if (url === "/api/recommendations") {
      return Promise.resolve(new Response(JSON.stringify([recommendationRecord]), { status: 200, headers: { "Content-Type": "application/json" } }));
    }
    if (url === "/api/media?include_archived=true") {
      return Promise.resolve(new Response(JSON.stringify([mediaItem]), { status: 200, headers: { "Content-Type": "application/json" } }));
    }
    if (url.endsWith("/outcomes")) return outcomeResponse;
    throw new Error(`unexpected request ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Open recommendation journal" }));
  await screen.findByRole("region", { name: "Recommendation journal" });
  await user.click(screen.getByRole("button", { name: "Record outcome for MirrorMask" }));
  await user.selectOptions(screen.getByLabelText("Outcome kind"), "tried");
  await user.clear(screen.getByLabelText("Recorded date"));
  await user.type(screen.getByLabelText("Recorded date"), "2026-07-18");
  const outcomeForm = screen.getByRole("button", { name: "Record outcome" }).closest("form");
  const homeButton = screen.getByRole("button", { name: "Concierge home" });
  expect(outcomeForm).not.toBeNull();
  act(() => {
    fireEvent.submit(outcomeForm!);
    fireEvent.click(homeButton);
  });

  expect(screen.getByRole("button", { name: "Recording outcome…" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Concierge home" })).toBeDisabled();
  expect(screen.getByDisplayValue("MirrorMask")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "New entry" })).toBeDisabled();
  expect(screen.getByRole("searchbox", { name: "Search library" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Search" })).toBeDisabled();
  expect(screen.getByRole("button", { name: /MirrorMaskMovie/ })).toBeDisabled();

  const outcome = {
    id: "outcome-recommendation-mirrormask-2026-07-18-tried-1",
    kind: "tried",
    recorded_on: "2026-07-18",
  };
  resolveOutcome(new Response(JSON.stringify({
    created: true,
    recommendation: { ...recommendationRecord, outcomes: [outcome] },
  }), { status: 201, headers: { "Content-Type": "application/json" } }));

  await waitFor(() => expect(screen.getByText("Tried · 2026-07-18")).toBeInTheDocument());
  expect(screen.getByRole("button", { name: "Concierge home" })).toBeEnabled();
});

test("loads the active library and opens the first record", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);

  expect(screen.getByText("Loading your library…")).toBeInTheDocument();
  expect(await screen.findByRole("button", { name: /MirrorMask/ })).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("MirrorMask");
  expect(fetch).toHaveBeenCalledWith("/api/media", expect.anything());
});

test("searches active media through the loopback API", async () => {
  const user = userEvent.setup();
  const flcl = {
    ...mediaItem,
    id: "anime-flcl",
    title: "FLCL",
    category: "anime_series",
  };
  const fetchMock = vi.fn().mockImplementation((url: string) =>
    Promise.resolve(
      new Response(JSON.stringify(url.includes("title=FLCL") ? [flcl] : [mediaItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });

  await user.type(screen.getByRole("searchbox", { name: "Search library" }), "FLCL");
  await user.click(screen.getByRole("button", { name: "Search" }));

  expect(await screen.findByRole("button", { name: /FLCL/ })).toBeInTheDocument();
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/media?title=FLCL",
    expect.anything(),
  );
});

test("clears an unfinished alternate-title draft after confirmed search navigation", async () => {
  const user = userEvent.setup();
  const flcl = { ...mediaItem, id: "anime-flcl", title: "FLCL", aliases: [] };
  vi.stubGlobal("confirm", vi.fn().mockReturnValue(true));
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string) =>
      Promise.resolve(
        new Response(JSON.stringify(url.includes("title=FLCL") ? [flcl] : [mediaItem]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.type(
    screen.getByRole("textbox", { name: "New alternate title" }),
    "unfinished alias",
  );
  await user.type(screen.getByRole("searchbox", { name: "Search library" }), "FLCL");
  await user.click(screen.getByRole("button", { name: "Search" }));

  expect(await screen.findByRole("button", { name: /FLCL/ })).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "New alternate title" })).toHaveValue("");
});

test("edits core fields while preserving the complete selected record in preview", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });

  const stableId = screen.getByRole("textbox", { name: "Stable ID" });
  expect(stableId).toHaveValue("movie-mirrormask");
  expect(stableId).toHaveAttribute("readonly");

  await user.clear(screen.getByRole("textbox", { name: "Title" }));
  await user.type(screen.getByRole("textbox", { name: "Title" }), "MirrorMask (2005)");
  await user.selectOptions(screen.getByRole("combobox", { name: "Category" }), "anime_movie");
  await user.selectOptions(screen.getByRole("combobox", { name: "Status" }), "rewatched");

  expect(screen.getByTestId("record-preview")).toHaveTextContent('"title": "MirrorMask (2005)"');
  expect(screen.getByTestId("record-preview")).toHaveTextContent('"category": "anime_movie"');
  expect(screen.getByTestId("record-preview")).toHaveTextContent('"status": "rewatched"');
  expect(screen.getByTestId("record-preview")).toHaveTextContent('"terms"');
  expect(screen.getByTestId("record-preview")).toHaveTextContent('"identity"');
});

test("saves the exact edited record with pending and success states", async () => {
  const user = userEvent.setup();
  let resolveSave!: (response: Response) => void;
  const saveResponse = new Promise<Response>((resolve) => {
    resolveSave = resolve;
  });
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(
      new Response(JSON.stringify([mediaItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    .mockReturnValueOnce(saveResponse);
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.type(
    screen.getByRole("textbox", { name: "New alternate title" }),
    "Mirror Mask",
  );
  await user.keyboard("{Enter}");
  await user.type(screen.getByRole("spinbutton", { name: "Rating score" }), "8.5");
  await user.clear(screen.getByLabelText("Rating date"));
  await user.type(screen.getByLabelText("Rating date"), "2026-07-16");
  await user.click(screen.getByRole("button", { name: "Record rating" }));
  await user.selectOptions(screen.getByRole("combobox", { name: "Progress status" }), "paused");
  await user.type(screen.getByRole("spinbutton", { name: "Amount completed" }), "3.5");
  await user.selectOptions(screen.getByRole("combobox", { name: "Progress unit" }), "hour");
  await user.clear(screen.getByLabelText("Progress date"));
  await user.type(screen.getByLabelText("Progress date"), "2026-07-16");
  await user.type(screen.getByLabelText("Started on"), "2026-07-01");
  await user.type(screen.getByLabelText("Ended on"), "2026-07-15");
  await user.type(screen.getByRole("textbox", { name: "Progress reason" }), "Taking stock.");
  await user.click(screen.getByRole("button", { name: "Record progress" }));
  await user.clear(screen.getByRole("textbox", { name: "Title" }));
  await user.type(screen.getByRole("textbox", { name: "Title" }), "MirrorMask restored");
  await user.click(screen.getByRole("button", { name: "Save changes" }));

  expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
  expect(screen.getByRole("textbox", { name: "Title" })).toBeDisabled();
  expect(screen.getByRole("combobox", { name: "Category" })).toBeDisabled();
  expect(screen.getByRole("combobox", { name: "Status" })).toBeDisabled();
  expect(screen.getByRole("textbox", { name: "New alternate title" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Remove Mirror Mask" })).toBeDisabled();
  expect(screen.getByRole("spinbutton", { name: "Rating score" })).toBeDisabled();
  expect(screen.getByLabelText("Rating date")).toBeDisabled();
  expect(screen.getByRole("checkbox", { name: "Provisional rating" })).toBeDisabled();
  expect(screen.getByRole("combobox", { name: "Progress status" })).toBeDisabled();
  expect(screen.getByRole("spinbutton", { name: "Amount completed" })).toBeDisabled();
  expect(screen.getByLabelText("Started on")).toBeDisabled();
  expect(screen.getByLabelText("Ended on")).toBeDisabled();
  expect(screen.getByRole("textbox", { name: "Progress reason" })).toBeDisabled();
  expect(screen.getByRole("combobox", { name: "Observation scope" })).toBeDisabled();
  expect(screen.getByRole("combobox", { name: "Observation polarity" })).toBeDisabled();
  expect(screen.getByRole("textbox", { name: "Observation dimension" })).toBeDisabled();
  expect(screen.getByRole("textbox", { name: "Observation text" })).toBeDisabled();
  expect(screen.getByRole("combobox", { name: "Observation provenance" })).toBeDisabled();
  expect(screen.getByRole("combobox", { name: "Observation privacy" })).toBeDisabled();
  expect(screen.getByRole("textbox", { name: "Source context" })).toBeDisabled();
  expect(screen.getByLabelText("Observed on")).toBeDisabled();
  expect(screen.getByRole("searchbox", { name: "Search library" })).toBeDisabled();
  expect(screen.getByRole("button", { name: /MirrorMaskMovie · Finished/ })).toBeDisabled();
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/media/movie-mirrormask",
    expect.objectContaining({
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...mediaItem,
        title: "MirrorMask restored",
        status: "paused",
        aliases: [{ value: "Mirrormask" }, { value: "Mirror Mask" }],
        rating: { score: 8.5, rated_on: "2026-07-16" },
        rating_history: [{ score: 8.5, rated_on: "2026-07-16" }],
        progress_records: [{
          status: "paused",
          recorded_on: "2026-07-16",
          amount_completed: 3.5,
          unit: "hour",
          started_on: "2026-07-01",
          ended_on: "2026-07-15",
          reason: "Taking stock.",
        }],
      }),
    }),
  );

  resolveSave(
    new Response(JSON.stringify({
      ...mediaItem,
      title: "MirrorMask restored",
      status: "paused",
      aliases: [{ value: "Mirrormask" }, { value: "Mirror Mask" }],
      rating: { score: 8.5, rated_on: "2026-07-16" },
      rating_history: [{ score: 8.5, rated_on: "2026-07-16" }],
      progress_records: [{
        status: "paused",
        recorded_on: "2026-07-16",
        amount_completed: 3.5,
        unit: "hour",
        started_on: "2026-07-01",
        ended_on: "2026-07-15",
        reason: "Taking stock.",
      }],
    }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );

  expect(await screen.findByRole("status")).toHaveTextContent("Saved locally");
  expect(screen.getByRole("button", { name: "Saved" })).toBeDisabled();
});

test("blocks a blank title and keeps its validation message beside the field", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.clear(screen.getByRole("textbox", { name: "Title" }));

  expect(screen.getByText("Title is required.")).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Title" })).toHaveAttribute(
    "aria-invalid",
    "true",
  );
  expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
});

test("shows an unavailable state when the loopback API cannot load", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));

  render(<App />);

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "The local library is unavailable.",
  );
  expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
});

test("keeps loading until the current Strict Mode request finishes", async () => {
  let resolveCurrent!: (response: Response) => void;
  const currentResponse = new Promise<Response>((resolve) => {
    resolveCurrent = resolve;
  });
  const fetchMock = vi
    .fn()
    .mockImplementationOnce((_url: string, options: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        options.signal?.addEventListener("abort", () =>
          reject(new DOMException("Aborted", "AbortError")),
        );
      }),
    )
    .mockReturnValueOnce(currentResponse);
  vi.stubGlobal("fetch", fetchMock);

  render(
    <StrictMode>
      <App />
    </StrictMode>,
  );

  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  expect(screen.getByText("Loading your library…")).toBeInTheDocument();

  resolveCurrent(
    new Response(JSON.stringify([mediaItem]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
  expect(await screen.findByRole("button", { name: /MirrorMask/ })).toBeInTheDocument();
});

test("keeps the draft and shows API validation details after a rejected save", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify([mediaItem]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: "duplicate creator credit" }), {
          status: 422,
          headers: { "Content-Type": "application/json" },
        }),
      ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.clear(screen.getByRole("textbox", { name: "Title" }));
  await user.type(screen.getByRole("textbox", { name: "Title" }), "Still in my draft");
  await user.click(screen.getByRole("button", { name: "Save changes" }));

  const validationSummary = await screen.findByRole("alert");
  expect(validationSummary).toHaveTextContent("duplicate creator credit");
  expect(validationSummary).toHaveFocus();
  expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("Still in my draft");
  expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
});

test("keeps the draft when the loopback API disappears during save", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify([mediaItem]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockRejectedValueOnce(new TypeError("fetch failed")),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.clear(screen.getByRole("textbox", { name: "Title" }));
  await user.type(screen.getByRole("textbox", { name: "Title" }), "Offline draft");
  await user.click(screen.getByRole("button", { name: "Save changes" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "The local library is unavailable. Your draft is still here.",
  );
  expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("Offline draft");
});

test("asks before discarding edits and restores the selected record", async () => {
  const user = userEvent.setup();
  const confirmMock = vi.fn().mockReturnValueOnce(false).mockReturnValueOnce(true);
  vi.stubGlobal("confirm", confirmMock);
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.clear(screen.getByRole("textbox", { name: "Title" }));
  await user.type(screen.getByRole("textbox", { name: "Title" }), "Changed title");

  await user.click(screen.getByRole("button", { name: "Cancel edits" }));
  expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("Changed title");

  await user.click(screen.getByRole("button", { name: "Cancel edits" }));
  expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("MirrorMask");
  expect(confirmMock).toHaveBeenCalledTimes(2);
});

test("protects an unsaved draft when opening another record", async () => {
  const user = userEvent.setup();
  const secondItem = {
    ...mediaItem,
    id: "anime-flcl",
    title: "FLCL",
    category: "anime_series",
  };
  const confirmMock = vi.fn().mockReturnValueOnce(false).mockReturnValueOnce(true);
  vi.stubGlobal("confirm", confirmMock);
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem, secondItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.clear(screen.getByRole("textbox", { name: "Title" }));
  await user.type(screen.getByRole("textbox", { name: "Title" }), "Changed title");

  await user.click(screen.getByRole("button", { name: /FLCL/ }));
  expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("Changed title");

  await user.click(screen.getByRole("button", { name: /FLCL/ }));
  expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("FLCL");
});

test("does not search away from an unsaved draft without confirmation", async () => {
  const user = userEvent.setup();
  const confirmMock = vi.fn().mockReturnValue(false);
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify([mediaItem]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("confirm", confirmMock);
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.clear(screen.getByRole("textbox", { name: "Title" }));
  await user.type(screen.getByRole("textbox", { name: "Title" }), "Unsaved search draft");
  await user.type(screen.getByRole("searchbox", { name: "Search library" }), "FLCL");
  await user.click(screen.getByRole("button", { name: "Search" }));

  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue(
    "Unsaved search draft",
  );
  expect(confirmMock).toHaveBeenCalledOnce();
});

test("asks before the home action discards an unsaved draft", async () => {
  const user = userEvent.setup();
  const confirmMock = vi.fn().mockReturnValue(false);
  vi.stubGlobal("confirm", confirmMock);
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.clear(screen.getByRole("textbox", { name: "Title" }));
  await user.type(screen.getByRole("textbox", { name: "Title" }), "Stay on this draft");
  await user.click(screen.getByRole("button", { name: "Concierge home" }));

  expect(confirmMock).toHaveBeenCalledOnce();
  expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue(
    "Stay on this draft",
  );
});

test("clears an unfinished alternate-title draft after confirmed home navigation", async () => {
  const user = userEvent.setup();
  vi.stubGlobal("confirm", vi.fn().mockReturnValue(true));
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify([mediaItem]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.type(
    screen.getByRole("textbox", { name: "New alternate title" }),
    "unfinished alias",
  );
  await user.click(screen.getByRole("button", { name: "Concierge home" }));

  await screen.findByRole("button", { name: /MirrorMask/ });
  expect(screen.getByRole("textbox", { name: "New alternate title" })).toHaveValue("");
});

test("reviews a composed cited taste report without generating personality claims", async () => {
  const user = userEvent.setup();
  const makeObservation = (id: string, polarity: string, dimension: string, text: string) => ({
    id, scope: "work", polarity, dimension, text, provenance: "user_explicit",
    privacy: "assistant_readable", review_state: "accepted", observed_on: "2026-07-15",
  });
  const visualSupport = makeObservation("obs-flcl-visual", "positive", "visual_style", "The animation crackles.");
  const dialogueConflict = makeObservation("obs-flcl-dialogue", "negative", "dialogue", "Some lines are rough.");
  const visualConflict = makeObservation("obs-mirror-visual", "negative", "visual_style", "The CG has dated oddly.");
  const progressHistory = [
    completeProgress({ status: "finished", recorded_on: "2026-07-14", amount_completed: 0.5, unit: "episode", started_on: "2026-07-10", ended_on: "2026-07-14" }),
    completeProgress({ status: "paused", recorded_on: "2026-07-15", amount_completed: 0 }),
    completeProgress({ status: "currently_consuming", recorded_on: "2026-07-16", unit: "chapter" }),
    completeProgress({ status: "rewatching", recorded_on: "2026-07-17", amount_completed: 2, unit: "episode", return_intent: true, reason: "Still crackles." }),
  ];
  const creatorCredits = [{ creator_id: "creator-anno", role: "writer" }, { creator_id: "creator-tsurumaki", role: "director" }];
  const flcl = { ...mediaItem, id: "anime-flcl-2000", title: "FLCL", category: "anime_series", status: "rewatching", rating: { score: 9, rated_on: "2026-07-15" }, rating_history: [{ score: 9, rated_on: "2026-07-15" }], progress_records: progressHistory, credits: creatorCredits, relationships: [{ relationship_type: "same_creator", target_media_item_id: "movie-mirrormask" }], observations: [visualSupport, dialogueConflict] };
  const mirrorMask = { ...mediaItem, observations: [visualConflict] };
  const ratingEntry = { media_item_id: flcl.id, title: flcl.title, category: flcl.category, current_rating: flcl.rating, rating_history: flcl.rating_history, supporting_evidence: [visualSupport], contradictory_evidence: [dialogueConflict], context_evidence: [] };
  const report = {
    rating_history: { entries: [ratingEntry] },
    progress_context: { entries: [{ media_item_id: flcl.id, title: flcl.title, category: flcl.category, current_status: flcl.status, progress_history: progressHistory }] },
    creator_context: { entries: [{ media_item_id: flcl.id, title: flcl.title, category: flcl.category, credits: [
      { creator_id: "creator-anno", creator_name: "Hideaki Anno", role: "writer" },
      { creator_id: "creator-tsurumaki", creator_name: "Kazuya Tsurumaki", role: "director" },
    ] }] },
    relationship_context: { entries: [{ media_item_id: flcl.id, title: flcl.title, category: flcl.category, relationships: [{
      relationship_type: "same_creator", target_media_item_id: mirrorMask.id, target_title: mirrorMask.title, target_category: mirrorMask.category,
    }] }] },
    dimensions: [
      { dimension: "dialogue", entries: [{ media_item_id: flcl.id, title: flcl.title, category: flcl.category, current_rating: flcl.rating, supporting_evidence: [], contradictory_evidence: [dialogueConflict], context_evidence: [] }] },
      { dimension: "visual_style", entries: [
        { media_item_id: flcl.id, title: flcl.title, category: flcl.category, current_rating: flcl.rating, supporting_evidence: [visualSupport], contradictory_evidence: [], context_evidence: [] },
        { media_item_id: mirrorMask.id, title: mirrorMask.title, category: mirrorMask.category, current_rating: null, supporting_evidence: [], contradictory_evidence: [visualConflict], context_evidence: [] },
      ] },
    ],
  };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([flcl, mirrorMask]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify(report), { status: 200, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /FLCL/ });
  await user.click(screen.getByRole("button", { name: "Review cited taste report" }));

  const review = await screen.findByRole("region", { name: "Cited taste report" });
  expect(within(review).getByRole("heading", { name: "Taste evidence report" })).toBeInTheDocument();
  expect(within(review).getByRole("heading", { name: "Rating history" })).toBeInTheDocument();
  expect(within(review).getByRole("heading", { name: "Progress context" })).toBeInTheDocument();
  expect(within(review).getByText("Current library status: Rewatching")).toBeInTheDocument();
  expect(within(review).getByText("2026-07-14: Finished · 0.5 episodes · started 2026-07-10 · ended 2026-07-14")).toBeInTheDocument();
  expect(within(review).getByText("2026-07-15: Paused · 0 completed")).toBeInTheDocument();
  expect(within(review).getByText("2026-07-16: Currently consuming · unit: Chapter")).toBeInTheDocument();
  expect(within(review).getByText(/2026-07-17: Rewatching · 2 episodes · plans to return · Still crackles\./)).toBeInTheDocument();
  expect(within(review).getByRole("heading", { name: "Creator credits" })).toBeInTheDocument();
  expect(within(review).getByText("Hideaki Anno · Writer")).toBeInTheDocument();
  expect(within(review).getByText("Kazuya Tsurumaki · Director")).toBeInTheDocument();
  expect(within(review).getByText("Recorded attribution only; no creator affinity or recommendation weight is inferred.")).toBeInTheDocument();
  const relationshipSection = within(review).getByRole("heading", { name: "Relationship context" }).closest("section");
  expect(relationshipSection).not.toBeNull();
  expect(within(relationshipSection as HTMLElement).getByRole("button", { name: "MirrorMask" }).closest("li")).toHaveTextContent("Same creator · MirrorMask · Movie");
  expect(within(review).getByText("Stored directed links only; no preference or unstated relationship is inferred.")).toBeInTheDocument();
  expect(within(review).getByRole("heading", { name: "Dialogue" })).toBeInTheDocument();
  expect(within(review).getByRole("heading", { name: "Visual style" })).toBeInTheDocument();
  expect(within(review).getByText("The animation crackles.")).toBeInTheDocument();
  expect(within(review).getByText("Some lines are rough.")).toBeInTheDocument();
  expect(within(review).getByText("The CG has dated oddly.")).toBeInTheDocument();
  expect(within(review).getByText(/does not generate a personality claim or taste score/i)).toBeInTheDocument();
  expect(fetchMock).toHaveBeenLastCalledWith("/api/profile/report");

  const confirmMock = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
  await user.type(screen.getByRole("textbox", { name: "Title" }), " changed");
  await user.click(within(review).getAllByRole("button", { name: "MirrorMask" })[0]);
  expect(screen.getByRole("heading", { name: "FLCL changed", level: 1 })).toBeInTheDocument();

  await user.click(within(review).getAllByRole("button", { name: "MirrorMask" })[0]);
  expect(screen.getByRole("heading", { name: "MirrorMask", level: 1 })).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(confirmMock).toHaveBeenCalled();
});

test("matches Python stripping and full Unicode case folding for report dimensions", async () => {
  const user = userEvent.setup();
  const evidence = { id: "obs-ligature", scope: "work", polarity: "positive", dimension: "\u001cﬃ", text: "Ligature dimension evidence.", provenance: "manual", privacy: "assistant_readable", review_state: "accepted", observed_on: "2026-07-15" };
  const item = { ...mediaItem, rating: undefined, rating_history: [], observations: [evidence] };
  const report = {
    rating_history: { entries: [] },
    progress_context: { entries: [] },
    creator_context: { entries: [] },
    relationship_context: { entries: [] },
    dimensions: [{ dimension: "ffi", entries: [{ media_item_id: item.id, title: item.title, category: item.category, current_rating: null, supporting_evidence: [evidence], contradictory_evidence: [], context_evidence: [] }] }],
  };
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([item]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify(report), { status: 200, headers: { "Content-Type": "application/json" } })));

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Review cited taste report" }));

  const review = await screen.findByRole("region", { name: "Cited taste report" });
  expect(within(review).getByRole("heading", { name: "Ffi" })).toBeInTheDocument();
  expect(within(review).getByText("Ligature dimension evidence.")).toBeInTheDocument();
  expect(within(review).queryByRole("alert")).not.toBeInTheDocument();
});

test("closes a completed taste report on archive reload before the archived re-query", async () => {
  const user = userEvent.setup();
  const blankItem = { ...mediaItem, rating: undefined, rating_history: [], observations: [] };
  const emptyReport = { rating_history: { entries: [] }, progress_context: { entries: [] }, creator_context: { entries: [] }, relationship_context: { entries: [] }, dimensions: [] };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([blankItem]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify(emptyReport), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify([blankItem]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify(emptyReport), { status: 200, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Review cited taste report" }));
  expect(await screen.findByRole("region", { name: "Cited taste report" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Show archived records" }));
  await waitFor(() => expect(screen.queryByRole("region", { name: "Cited taste report" })).not.toBeInTheDocument());
  await user.click(screen.getByRole("button", { name: "Review cited taste report" }));

  expect(await screen.findByRole("region", { name: "Cited taste report" })).toBeInTheDocument();
  expect(fetchMock).toHaveBeenLastCalledWith("/api/profile/report?include_archived=true");
  expect(fetchMock).toHaveBeenCalledTimes(4);
});

test("composes archived visibility into an empty cited taste report", async () => {
  const user = userEvent.setup();
  const blankItem = { ...mediaItem, rating: undefined, rating_history: [], observations: [] };
  const emptyReport = { rating_history: { entries: [] }, progress_context: { entries: [] }, creator_context: { entries: [] }, relationship_context: { entries: [] }, dimensions: [] };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([blankItem]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify([blankItem]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify(emptyReport), { status: 200, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Show archived records" }));
  await user.click(screen.getByRole("button", { name: "Review cited taste report" }));

  const review = await screen.findByRole("region", { name: "Cited taste report" });
  expect(within(review).getByText("No rated visible works.")).toBeInTheDocument();
  expect(within(review).getByText("No progress history in the visible library.")).toBeInTheDocument();
  expect(within(review).getByText("No creator credits in the visible library.")).toBeInTheDocument();
  expect(within(review).getByText("No accepted assistant-readable evidence in the visible library.")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenLastCalledWith("/api/profile/report?include_archived=true");
});

test.each([
  ["a non-object envelope", null],
  ["an undeclared report field", { rating_history: { entries: [] }, progress_context: { entries: [] }, creator_context: { entries: [] }, relationship_context: { entries: [] }, dimensions: [], affinity_summary: "invented" }],
  ["malformed rating history", { rating_history: { entries: "bad" }, progress_context: { entries: [] }, creator_context: { entries: [] }, relationship_context: { entries: [] }, dimensions: [] }],
  ["malformed progress context", { rating_history: { entries: [] }, progress_context: { entries: "bad" }, dimensions: [] }],
  ["an empty progress history", { rating_history: { entries: [] }, progress_context: { entries: [{ media_item_id: "movie-mirrormask", title: "MirrorMask", category: "movie", current_status: "finished", progress_history: [] }] }, dimensions: [] }],
  ["duplicate progress work IDs", { rating_history: { entries: [] }, progress_context: { entries: ["one", "two"].map(() => ({ media_item_id: "movie-mirrormask", title: "MirrorMask", category: "movie", current_status: "finished", progress_history: [completeProgress({ status: "finished", recorded_on: "2026-07-15" })] })) }, dimensions: [] }],
  ["negative progress", { rating_history: { entries: [] }, progress_context: { entries: [{ media_item_id: "movie-mirrormask", title: "MirrorMask", category: "movie", current_status: "finished", progress_history: [completeProgress({ status: "finished", recorded_on: "2026-07-15", amount_completed: -1 })] }] }, dimensions: [] }],
  ["an impossible progress date", { rating_history: { entries: [] }, progress_context: { entries: [{ media_item_id: "movie-mirrormask", title: "MirrorMask", category: "movie", current_status: "finished", progress_history: [completeProgress({ status: "finished", recorded_on: "2026-02-31" })] }] }, dimensions: [] }],
  ["an inverted progress lifecycle", { rating_history: { entries: [] }, progress_context: { entries: [{ media_item_id: "movie-mirrormask", title: "MirrorMask", category: "movie", current_status: "finished", progress_history: [completeProgress({ status: "finished", recorded_on: "2026-07-15", started_on: "2026-07-16", ended_on: "2026-07-14" })] }] }, dimensions: [] }],
  ["an omitted nullable progress field", { rating_history: { entries: [] }, progress_context: { entries: [{ media_item_id: "movie-mirrormask", title: "MirrorMask", category: "movie", current_status: "finished", progress_history: [{ status: "finished", recorded_on: "2026-07-15", amount_completed: null, started_on: null, ended_on: null, return_intent: null, reason: null }] }] }, dimensions: [] }],
  ["an inferred progress field", { rating_history: { entries: [] }, progress_context: { entries: [{ media_item_id: "movie-mirrormask", title: "MirrorMask", category: "movie", current_status: "finished", progress_history: [completeProgress({ status: "finished", recorded_on: "2026-07-15", motivation_score: 0.9 })] }] }, dimensions: [] }],
  ["malformed creator context", { rating_history: { entries: [] }, progress_context: { entries: [] }, creator_context: { entries: "bad" }, dimensions: [] }],
  ["an empty creator credit list", { rating_history: { entries: [] }, progress_context: { entries: [] }, creator_context: { entries: [{ media_item_id: "movie-mirrormask", title: "MirrorMask", category: "movie", credits: [] }] }, dimensions: [] }],
  ["duplicate creator work IDs", { rating_history: { entries: [] }, progress_context: { entries: [] }, creator_context: { entries: ["one", "two"].map(() => ({ media_item_id: "movie-mirrormask", title: "MirrorMask", category: "movie", credits: [{ creator_id: "creator-mckean", creator_name: "Dave McKean", role: "director" }] })) }, dimensions: [] }],
  ["duplicate typed creator credits", { rating_history: { entries: [] }, progress_context: { entries: [] }, creator_context: { entries: [{ media_item_id: "movie-mirrormask", title: "MirrorMask", category: "movie", credits: ["one", "two"].map(() => ({ creator_id: "creator-mckean", creator_name: "Dave McKean", role: "director" })) }] }, dimensions: [] }],
  ["an invalid creator role", { rating_history: { entries: [] }, progress_context: { entries: [] }, creator_context: { entries: [{ media_item_id: "movie-mirrormask", title: "MirrorMask", category: "movie", credits: [{ creator_id: "creator-mckean", creator_name: "Dave McKean", role: "favorite" }] }] }, dimensions: [] }],
  ["an inferred creator affinity field", { rating_history: { entries: [] }, progress_context: { entries: [] }, creator_context: { entries: [{ media_item_id: "movie-mirrormask", title: "MirrorMask", category: "movie", credits: [{ creator_id: "creator-mckean", creator_name: "Dave McKean", role: "director", affinity_score: 1 }] }] }, dimensions: [] }],
  ["inconsistent names for one creator ID", { rating_history: { entries: [] }, progress_context: { entries: [] }, creator_context: { entries: [
    { media_item_id: "a", title: "A", category: "movie", credits: [{ creator_id: "creator-same", creator_name: "First name", role: "director" }] },
    { media_item_id: "b", title: "B", category: "movie", credits: [{ creator_id: "creator-same", creator_name: "Second name", role: "writer" }] },
  ] }, dimensions: [] }],
  ["malformed relationship context", { rating_history: { entries: [] }, progress_context: { entries: [] }, relationship_context: { entries: "bad" }, dimensions: [] }],
  ["an empty relationship list", { rating_history: { entries: [] }, progress_context: { entries: [] }, relationship_context: { entries: [{ media_item_id: "movie-mirrormask", title: "MirrorMask", category: "movie", relationships: [] }] }, dimensions: [] }],
  ["malformed dimensions", { rating_history: { entries: [] }, progress_context: { entries: [] }, creator_context: { entries: [] }, relationship_context: { entries: [] }, dimensions: "bad" }],
  ["an evidence-free reported dimension", { rating_history: { entries: [] }, progress_context: { entries: [] }, creator_context: { entries: [] }, relationship_context: { entries: [] }, dimensions: [{ dimension: "visual_style", entries: [] }] }],
  ["duplicate normalized dimensions", { rating_history: { entries: [] }, progress_context: { entries: [] }, creator_context: { entries: [] }, relationship_context: { entries: [] }, dimensions: ["visual_style", "VISUAL_STYLE"].map((dimension) => ({
    dimension: dimension.toLowerCase(),
    entries: [{ media_item_id: "movie-mirrormask", title: "MirrorMask", category: "movie", current_rating: null, supporting_evidence: [{ id: `obs-${dimension}`, dimension, polarity: "positive", text: "Citation.", observed_on: "2026-07-15" }], contradictory_evidence: [], context_evidence: [] }],
  })) }],
])("rejects %s from a successful taste-report response", async (_label, payload) => {
  const user = userEvent.setup();
  const blankItem = { ...mediaItem, rating: undefined, rating_history: [], observations: [] };
  const completePayload = payload && typeof payload === "object" ? { creator_context: { entries: [] }, relationship_context: { entries: [] }, ...payload } : payload;
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([blankItem]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify(completePayload), { status: 200, headers: { "Content-Type": "application/json" } })));

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Review cited taste report" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("The cited taste report could not be verified.");
});

test("rejects out-of-order progress entries from a successful taste report", async () => {
  const user = userEvent.setup();
  const firstProgress = completeProgress({ status: "finished", recorded_on: "2026-07-14" });
  const secondProgress = completeProgress({ status: "paused", recorded_on: "2026-07-15" });
  const first = { ...mediaItem, id: "a-progress", title: "A progress", progress_records: [firstProgress] };
  const second = { ...mediaItem, id: "b-progress", title: "B progress", status: "paused", progress_records: [secondProgress] };
  const entry = (item: typeof first, progress: Record<string, unknown>) => ({ media_item_id: item.id, title: item.title, category: item.category, current_status: item.status, progress_history: [progress] });
  const report = { rating_history: { entries: [] }, progress_context: { entries: [entry(second, secondProgress), entry(first, firstProgress)] }, creator_context: { entries: [] }, relationship_context: { entries: [] }, dimensions: [] };
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([first, second]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify(report), { status: 200, headers: { "Content-Type": "application/json" } })));

  render(<App />);
  await screen.findByRole("button", { name: /A progress/ });
  await user.click(screen.getByRole("button", { name: "Review cited taste report" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("The cited taste report does not match the complete visible-library evidence.");
});

test.each(["omitted", "entry order", "creator ID", "role", "credit order", "title", "category"])("rejects a canonically mismatched %s creator projection in the taste report", async (kind) => {
  const user = userEvent.setup();
  const firstCredits = [{ creator_id: "creator-a", role: "writer" }, { creator_id: "creator-b", role: "director" }];
  const secondCredits = [{ creator_id: "creator-c", role: "composer" }];
  const first = { ...mediaItem, id: "a-creator-work", title: "A creator work", credits: firstCredits };
  const second = { ...mediaItem, id: "b-creator-work", title: "B creator work", credits: secondCredits };
  const entry = (item: typeof first, credits: Array<Record<string, unknown>>) => ({ media_item_id: item.id, title: item.title, category: item.category, credits });
  const firstResolved = [{ creator_id: "creator-a", creator_name: "Creator A", role: "writer" }, { creator_id: "creator-b", creator_name: "Creator B", role: "director" }];
  const secondResolved = [{ creator_id: "creator-c", creator_name: "Creator C", role: "composer" }];
  let entries = [entry(first, firstResolved), entry(second, secondResolved)];
  if (kind === "omitted") entries = [entries[0]];
  if (kind === "entry order") entries = [...entries].reverse();
  if (kind === "creator ID") entries[0] = { ...entries[0], credits: [{ ...firstResolved[0], creator_id: "creator-altered" }, firstResolved[1]] };
  if (kind === "role") entries[0] = { ...entries[0], credits: [{ ...firstResolved[0], role: "artist" }, firstResolved[1]] };
  if (kind === "credit order") entries[0] = { ...entries[0], credits: [...firstResolved].reverse() };
  if (kind === "title") entries[0] = { ...entries[0], title: "Altered title" };
  if (kind === "category") entries[0] = { ...entries[0], category: "game" };
  const report = { rating_history: { entries: [] }, progress_context: { entries: [] }, creator_context: { entries }, relationship_context: { entries: [] }, dimensions: [] };
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([first, second]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify(report), { status: 200, headers: { "Content-Type": "application/json" } })));

  render(<App />);
  await screen.findByRole("button", { name: /A creator work/ });
  await user.click(screen.getByRole("button", { name: "Review cited taste report" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("The cited taste report does not match the complete visible-library evidence.");
});

test("accepts creator entries in backend code-point stable-ID order", async () => {
  const user = userEvent.setup();
  const upper = { ...mediaItem, id: "Z-credit", title: "Upper", credits: [{ creator_id: "creator-z", role: "director" }] };
  const lower = { ...mediaItem, id: "a-credit", title: "Lower", credits: [{ creator_id: "creator-a", role: "writer" }] };
  const report = { rating_history: { entries: [] }, progress_context: { entries: [] }, creator_context: { entries: [
    { media_item_id: upper.id, title: upper.title, category: upper.category, credits: [{ creator_id: "creator-z", creator_name: "Creator Z", role: "director" }] },
    { media_item_id: lower.id, title: lower.title, category: lower.category, credits: [{ creator_id: "creator-a", creator_name: "Creator A", role: "writer" }] },
  ] }, relationship_context: { entries: [] }, dimensions: [] };
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([upper, lower]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify(report), { status: 200, headers: { "Content-Type": "application/json" } })));

  render(<App />);
  await screen.findByRole("button", { name: /Upper/ });
  await user.click(screen.getByRole("button", { name: "Review cited taste report" }));

  const review = await screen.findByRole("region", { name: "Cited taste report" });
  expect(within(review).queryByRole("alert")).not.toBeInTheDocument();
  expect(within(review).getByText("Creator Z · Director")).toBeInTheDocument();
});

test.each(["omitted", "status", "recorded date", "reason"])("rejects a canonically mismatched %s progress projection in the taste report", async (kind) => {
  const user = userEvent.setup();
  const canonicalProgress = completeProgress({ status: "finished", recorded_on: "2026-07-15", amount_completed: 1, unit: "episode", return_intent: true, reason: "Worth finishing." });
  const item = { ...mediaItem, progress_records: [canonicalProgress] };
  const projectedProgress = { ...canonicalProgress, ...(kind === "recorded date" ? { recorded_on: "2026-07-16" } : {}), ...(kind === "reason" ? { reason: "Altered reason." } : {}) };
  const report = {
    rating_history: { entries: [] },
    progress_context: { entries: kind === "omitted" ? [] : [{ media_item_id: item.id, title: item.title, category: item.category, current_status: kind === "status" ? "paused" : item.status, progress_history: [projectedProgress] }] },
    creator_context: { entries: [] },
    relationship_context: { entries: [] },
    dimensions: [],
  };
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([item]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify(report), { status: 200, headers: { "Content-Type": "application/json" } })));

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Review cited taste report" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("The cited taste report does not match the complete visible-library evidence.");
});

test("rejects a visible canonical relationship whose target does not exist", async () => {
  const user = userEvent.setup();
  const source = { ...mediaItem, id: "movie-orphan-source", title: "Orphan source", relationships: [{ relationship_type: "sequel", target_media_item_id: "missing-target" }] };
  const report = { rating_history: { entries: [] }, progress_context: { entries: [] }, creator_context: { entries: [] }, relationship_context: { entries: [] }, dimensions: [] };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([source]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify(report), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify([source]), { status: 200, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /Orphan source/ });
  await user.click(screen.getByRole("button", { name: "Review cited taste report" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("The cited taste report does not match the complete visible-library evidence.");
  expect(fetchMock).toHaveBeenLastCalledWith("/api/media?include_archived=true", { signal: undefined });
});

test("accepts an omitted relationship whose target is canonically archived", async () => {
  const user = userEvent.setup();
  const source = { ...mediaItem, id: "movie-active-source", title: "Active source", relationships: [{ relationship_type: "sequel", target_media_item_id: "movie-archived-target" }] };
  const archivedTarget = { ...mediaItem, id: "movie-archived-target", title: "Archived target", archived_on: "2026-07-16" };
  const report = { rating_history: { entries: [] }, progress_context: { entries: [] }, creator_context: { entries: [] }, relationship_context: { entries: [] }, dimensions: [] };
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([source]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify(report), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify([source, archivedTarget]), { status: 200, headers: { "Content-Type": "application/json" } })));

  render(<App />);
  await screen.findByRole("button", { name: /Active source/ });
  await user.click(screen.getByRole("button", { name: "Review cited taste report" }));

  const review = await screen.findByRole("region", { name: "Cited taste report" });
  expect(within(review).queryByRole("alert")).not.toBeInTheDocument();
  expect(within(review).getByText("No visible stored relationships.")).toBeInTheDocument();
});

test("rejects altered canonical relationship targets in the taste report", async () => {
  const user = userEvent.setup();
  const source = { ...mediaItem, id: "movie-source", title: "Source", relationships: [{ relationship_type: "sequel", target_media_item_id: "movie-target" }] };
  const target = { ...mediaItem, id: "movie-target", title: "Target" };
  const report = {
    rating_history: { entries: [] }, progress_context: { entries: [] }, creator_context: { entries: [] }, dimensions: [],
    relationship_context: { entries: [{ media_item_id: source.id, title: source.title, category: source.category, relationships: [{ relationship_type: "sequel", target_media_item_id: target.id, target_title: "Altered target", target_category: target.category }] }] },
  };
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([source, target]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify(report), { status: 200, headers: { "Content-Type": "application/json" } })));

  render(<App />);
  await screen.findByRole("button", { name: /Source/ });
  await user.click(screen.getByRole("button", { name: "Review cited taste report" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("The cited taste report does not match the complete visible-library evidence.");
});

test("composes archived progress into the archived taste report", async () => {
  const user = userEvent.setup();
  const blankItem = { ...mediaItem, progress_records: [] };
  const progress = completeProgress({ status: "paused", recorded_on: "2026-07-16", return_intent: false, reason: "Archive context." });
  const archived = { ...mediaItem, id: "movie-archived-progress", title: "Archived progress", status: "paused", archived_on: "2026-07-16", progress_records: [progress] };
  const report = {
    rating_history: { entries: [] },
    progress_context: { entries: [{ media_item_id: archived.id, title: archived.title, category: archived.category, current_status: archived.status, progress_history: [progress] }] },
    creator_context: { entries: [] },
    relationship_context: { entries: [] },
    dimensions: [],
  };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([blankItem]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify([blankItem, archived]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify(report), { status: 200, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Show archived records" }));
  await user.click(screen.getByRole("button", { name: "Review cited taste report" }));

  const review = await screen.findByRole("region", { name: "Cited taste report" });
  expect(within(review).getByText(/2026-07-16: Paused · does not plan to return · Archive context\./)).toBeInTheDocument();
  expect(fetchMock).toHaveBeenLastCalledWith("/api/profile/report?include_archived=true");
});

test("composes archived creator credits into the archived taste report", async () => {
  const user = userEvent.setup();
  const blankItem = { ...mediaItem, credits: [] };
  const archived = { ...mediaItem, id: "movie-archived-creator", title: "Archived creator work", archived_on: "2026-07-16", credits: [{ creator_id: "creator-mckean", role: "director" }] };
  const report = { rating_history: { entries: [] }, progress_context: { entries: [] }, creator_context: { entries: [{ media_item_id: archived.id, title: archived.title, category: archived.category, credits: [{ creator_id: "creator-mckean", creator_name: "Dave McKean", role: "director" }] }] }, relationship_context: { entries: [] }, dimensions: [] };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([blankItem]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify([blankItem, archived]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify(report), { status: 200, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Show archived records" }));
  await user.click(screen.getByRole("button", { name: "Review cited taste report" }));

  const review = await screen.findByRole("region", { name: "Cited taste report" });
  expect(within(review).getByText("Dave McKean · Director")).toBeInTheDocument();
  await user.click(within(review).getByRole("button", { name: "Archived creator work" }));
  expect(screen.getByRole("heading", { name: "Archived creator work", level: 1 })).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(3);
});

test.each(["rating", "dimension", "citation", "private"])("rejects an incomplete or untrusted %s projection in the taste report", async (kind) => {
  const user = userEvent.setup();
  const first = { id: "obs-1", scope: "work", polarity: "positive", dimension: "visual_style", text: "First citation.", provenance: "manual", privacy: "assistant_readable", review_state: "accepted", observed_on: "2026-07-15" };
  const second = { ...first, id: "obs-2", text: "Second citation." };
  const canonicalFirst = kind === "private" ? { ...first, privacy: "private" } : first;
  const item = { ...mediaItem, rating: { score: 8, rated_on: "2026-07-15" }, rating_history: [{ score: 8, rated_on: "2026-07-15" }], observations: [canonicalFirst, second] };
  const ratingEntry = { media_item_id: item.id, title: item.title, category: item.category, current_rating: item.rating, rating_history: item.rating_history, supporting_evidence: [first, second], contradictory_evidence: [], context_evidence: [] };
  const dimensionEntry = { media_item_id: item.id, title: item.title, category: item.category, current_rating: item.rating, supporting_evidence: kind === "citation" ? [first] : [first, second], contradictory_evidence: [], context_evidence: [] };
  const report = {
    rating_history: { entries: kind === "rating" ? [] : [ratingEntry] },
    progress_context: { entries: [] },
    creator_context: { entries: [] },
    relationship_context: { entries: [] },
    dimensions: kind === "dimension" ? [] : [{ dimension: "visual_style", entries: [dimensionEntry] }],
  };
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([item]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify(report), { status: 200, headers: { "Content-Type": "application/json" } })));

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Review cited taste report" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("The cited taste report does not match the complete visible-library evidence.");
});

test("claims the shared mutex before taste-report loading renders and releases it on failure", async () => {
  let resolveReport!: (response: Response) => void;
  const reportResponse = new Promise<Response>((resolve) => { resolveReport = resolve; });
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockReturnValueOnce(reportResponse);
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  const review = screen.getByRole("button", { name: "Review cited taste report" });
  const newEntry = screen.getByRole("button", { name: "New entry" });
  const archive = screen.getByRole("button", { name: "Archive record" });
  act(() => { review.click(); newEntry.click(); archive.click(); });

  expect(within(screen.getByRole("region", { name: "Cited taste report" })).getByRole("status")).toHaveTextContent("Loading complete visible-library evidence…");
  expect(fetchMock).toHaveBeenCalledTimes(2);
  resolveReport(new Response("unavailable", { status: 503 }));
  expect(await screen.findByRole("alert")).toHaveTextContent("The cited taste report could not be loaded.");
  expect(screen.getByRole("button", { name: "New entry" })).toBeEnabled();
});

test("closes the cited taste report when search reloads the shelf", async () => {
  const user = userEvent.setup();
  const blankItem = { ...mediaItem, rating: undefined, rating_history: [], observations: [] };
  const emptyReport = { rating_history: { entries: [] }, progress_context: { entries: [] }, creator_context: { entries: [] }, relationship_context: { entries: [] }, dimensions: [] };
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([blankItem]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify(emptyReport), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify([blankItem]), { status: 200, headers: { "Content-Type": "application/json" } })));

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Review cited taste report" }));
  expect(await screen.findByRole("region", { name: "Cited taste report" })).toBeInTheDocument();
  await user.type(screen.getByRole("searchbox", { name: "Search library" }), "Mirror");
  await user.click(screen.getByRole("button", { name: "Search" }));

  await waitFor(() => expect(screen.queryByRole("region", { name: "Cited taste report" })).not.toBeInTheDocument());
  expect(screen.getByRole("button", { name: "Clear search to review taste report" })).toBeDisabled();
});

test("reviews one cited dimension across visible canonical works", async () => {
  const user = userEvent.setup();
  const makeObservation = (id: string, polarity: string, text: string) => ({
    id,
    scope: "work",
    polarity,
    dimension: "visual_style",
    text,
    provenance: "user_explicit",
    privacy: "assistant_readable",
    review_state: "accepted",
    observed_on: "2026-07-15",
  });
  const flclEvidence = makeObservation("obs-flcl-visuals", "positive", "The animation crackles.");
  const mirrorEvidence = makeObservation("obs-mirror-visuals", "negative", "The CG has dated oddly.");
  const flcl = {
    ...mediaItem,
    id: "anime-flcl-2000",
    title: "FLCL",
    category: "anime_series",
    rating: { score: 9, rated_on: "2026-07-15" },
    rating_history: [{ score: 9, rated_on: "2026-07-15" }],
    observations: [flclEvidence],
  };
  const mirrorMask = { ...mediaItem, observations: [mirrorEvidence] };
  const profile = {
    dimension: "visual_style",
    entries: [
      {
        media_item_id: flcl.id,
        title: flcl.title,
        category: flcl.category,
        current_rating: flcl.rating,
        supporting_evidence: [flclEvidence],
        contradictory_evidence: [],
        context_evidence: [],
      },
      {
        media_item_id: mirrorMask.id,
        title: mirrorMask.title,
        category: mirrorMask.category,
        current_rating: null,
        supporting_evidence: [],
        contradictory_evidence: [mirrorEvidence],
        context_evidence: [],
      },
    ],
  };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([flcl, mirrorMask]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify(profile), { status: 200, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /FLCL/ });
  await user.type(screen.getByRole("textbox", { name: "Evidence dimension" }), "  VISUAL_STYLE  ");
  await user.click(screen.getByRole("button", { name: "Review cited dimension" }));

  const review = await screen.findByRole("region", { name: "Cited dimension profile" });
  expect(within(review).getByRole("heading", { name: "Visual style" })).toBeInTheDocument();
  expect(within(review).getAllByRole("button").map((button) => button.textContent)).toEqual(["FLCL", "MirrorMask"]);
  expect(within(review).getByText("The animation crackles.")).toBeInTheDocument();
  expect(within(review).getByText("The CG has dated oddly.")).toBeInTheDocument();
  expect(within(review).getByText("No rating recorded.")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenLastCalledWith("/api/profile/dimensions/VISUAL_STYLE");

  const confirmMock = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
  await user.type(screen.getByRole("textbox", { name: "Title" }), " changed");
  await user.click(within(review).getByRole("button", { name: "MirrorMask" }));
  expect(screen.getByRole("heading", { name: "FLCL changed" })).toBeInTheDocument();

  await user.click(within(review).getByRole("button", { name: "MirrorMask" }));
  expect(screen.getByRole("heading", { name: "MirrorMask" })).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(confirmMock).toHaveBeenCalled();
});

test("does not request a dimension profile for blank or whitespace input", async () => {
  const user = userEvent.setup();
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), { status: 200, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  const input = screen.getByRole("textbox", { name: "Evidence dimension" });
  const review = screen.getByRole("button", { name: "Enter a dimension to review" });
  expect(review).toBeDisabled();
  await user.type(input, "   ");
  expect(review).toBeDisabled();
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

test("closes a completed dimension profile on archive reload before the archived re-query", async () => {
  const user = userEvent.setup();
  const evidence = { id: "obs-1", scope: "work", polarity: "positive", dimension: "visual_style", text: "Canonical text.", provenance: "manual", privacy: "assistant_readable", review_state: "accepted", observed_on: "2026-07-15" };
  const item = { ...mediaItem, observations: [evidence] };
  const profile = { dimension: "visual_style", entries: [{ media_item_id: item.id, title: item.title, category: item.category, current_rating: null, supporting_evidence: [evidence], contradictory_evidence: [], context_evidence: [] }] };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([item]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify(profile), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify([item]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify(profile), { status: 200, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.type(screen.getByRole("textbox", { name: "Evidence dimension" }), "visual_style");
  await user.click(screen.getByRole("button", { name: "Review cited dimension" }));
  expect(await screen.findByRole("region", { name: "Cited dimension profile" })).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Show archived records" }));
  await waitFor(() => expect(screen.queryByRole("region", { name: "Cited dimension profile" })).not.toBeInTheDocument());
  await user.click(screen.getByRole("button", { name: "Review cited dimension" }));

  expect(await screen.findByRole("region", { name: "Cited dimension profile" })).toBeInTheDocument();
  expect(fetchMock).toHaveBeenLastCalledWith("/api/profile/dimensions/visual_style?include_archived=true");
  expect(fetchMock).toHaveBeenCalledTimes(4);
});

test("composes archived visibility and shows an empty cited dimension profile", async () => {
  const user = userEvent.setup();
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ dimension: "visual_style", entries: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Show archived records" }));
  await user.type(screen.getByRole("textbox", { name: "Evidence dimension" }), "visual_style");
  await user.click(screen.getByRole("button", { name: "Review cited dimension" }));

  expect(await screen.findByText("No accepted evidence found for this dimension in the visible library.")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenLastCalledWith("/api/profile/dimensions/visual_style?include_archived=true");
});

test.each([
  ["malformed entries", { dimension: "visual_style", entries: "bad" }],
  ["a mismatched normalized dimension", { dimension: "dialogue", entries: [] }],
  ["an evidence-free entry", {
    dimension: "visual_style",
    entries: [{ media_item_id: "movie-mirrormask", title: "MirrorMask", category: "movie", current_rating: null, supporting_evidence: [], contradictory_evidence: [], context_evidence: [] }],
  }],
  ["wrong bucket polarity", {
    dimension: "visual_style",
    entries: [{ media_item_id: "movie-mirrormask", title: "MirrorMask", category: "movie", current_rating: null, supporting_evidence: [{ id: "obs-1", dimension: "visual_style", polarity: "negative", text: "Wrong bucket.", observed_on: "2026-07-15" }], contradictory_evidence: [], context_evidence: [] }],
  }],
  ["an invalid citation date", {
    dimension: "visual_style",
    entries: [{ media_item_id: "movie-mirrormask", title: "MirrorMask", category: "movie", current_rating: null, supporting_evidence: [{ id: "obs-1", dimension: "visual_style", polarity: "positive", text: "Impossible date.", observed_on: "2026-02-31" }], contradictory_evidence: [], context_evidence: [] }],
  }],
  ["duplicate citation IDs within one record", {
    dimension: "visual_style",
    entries: [{ media_item_id: "movie-mirrormask", title: "MirrorMask", category: "movie", current_rating: null, supporting_evidence: [{ id: "obs-1", dimension: "visual_style", polarity: "positive", text: "First.", observed_on: "2026-07-15" }, { id: "obs-1", dimension: "visual_style", polarity: "positive", text: "Second.", observed_on: "2026-07-16" }], contradictory_evidence: [], context_evidence: [] }],
  }],
])("rejects %s from a successful dimension response", async (_label, payload) => {
  const user = userEvent.setup();
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } })));

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.type(screen.getByRole("textbox", { name: "Evidence dimension" }), "visual_style");
  await user.click(screen.getByRole("button", { name: "Review cited dimension" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("The cited dimension profile could not be verified.");
  expect(screen.queryByRole("region", { name: "Cited dimension profile" })?.querySelector("article")).toBeNull();
});

test.each([
  ["a missing canonical record", null, "accepted", "assistant_readable", "missing-work"],
  ["altered canonical citation text", "Altered text.", "accepted", "assistant_readable", "movie-mirrormask"],
  ["a private canonical citation", "Canonical text.", "accepted", "private", "movie-mirrormask"],
  ["an unreviewed canonical citation", "Canonical text.", "needs_review", "assistant_readable", "movie-mirrormask"],
])("rejects %s from a dimension profile", async (_label, profileText, reviewState, privacy, mediaId) => {
  const user = userEvent.setup();
  const canonicalEvidence = { id: "obs-1", scope: "work", polarity: "positive", dimension: "visual_style", text: "Canonical text.", provenance: "manual", privacy, review_state: reviewState, observed_on: "2026-07-15" };
  const canonicalItem = { ...mediaItem, observations: [canonicalEvidence] };
  const profileEvidence = { ...canonicalEvidence, text: profileText ?? canonicalEvidence.text, privacy: "assistant_readable", review_state: "accepted" };
  const profile = { dimension: "visual_style", entries: [{ media_item_id: mediaId, title: mediaId === "missing-work" ? "Missing Work" : mediaItem.title, category: "movie", current_rating: null, supporting_evidence: [profileEvidence], contradictory_evidence: [], context_evidence: [] }] };
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([canonicalItem]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify(profile), { status: 200, headers: { "Content-Type": "application/json" } })));

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.type(screen.getByRole("textbox", { name: "Evidence dimension" }), "visual_style");
  await user.click(screen.getByRole("button", { name: "Review cited dimension" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("The cited dimension profile refers to unverifiable visible-library evidence.");
});

test("claims the shared mutex before dimension loading renders and releases it on failure", async () => {
  const user = userEvent.setup();
  let resolveProfile!: (response: Response) => void;
  const profileResponse = new Promise<Response>((resolve) => { resolveProfile = resolve; });
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockReturnValueOnce(profileResponse);
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.type(screen.getByRole("textbox", { name: "Evidence dimension" }), "visual_style");
  const review = screen.getByRole("button", { name: "Review cited dimension" });
  const newEntry = screen.getByRole("button", { name: "New entry" });
  const archive = screen.getByRole("button", { name: "Archive record" });
  act(() => { review.click(); newEntry.click(); archive.click(); });

  expect(within(screen.getByRole("region", { name: "Cited dimension profile" })).getByRole("status"))
    .toHaveTextContent("Loading accepted evidence for this dimension…");
  expect(fetchMock).toHaveBeenCalledTimes(2);
  resolveProfile(new Response("unavailable", { status: 503 }));
  expect(await screen.findByRole("alert")).toHaveTextContent("The cited dimension profile could not be loaded.");
  expect(screen.getByRole("button", { name: "New entry" })).toBeEnabled();
});

test("closes a completed dimension profile when search reloads the shelf", async () => {
  const user = userEvent.setup();
  const evidence = { id: "obs-1", scope: "work", polarity: "positive", dimension: "visual_style", text: "Canonical text.", provenance: "manual", privacy: "assistant_readable", review_state: "accepted", observed_on: "2026-07-15" };
  const item = { ...mediaItem, observations: [evidence] };
  const profile = { dimension: "visual_style", entries: [{ media_item_id: item.id, title: item.title, category: item.category, current_rating: null, supporting_evidence: [evidence], contradictory_evidence: [], context_evidence: [] }] };
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([item]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify(profile), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify([item]), { status: 200, headers: { "Content-Type": "application/json" } })));

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.type(screen.getByRole("textbox", { name: "Evidence dimension" }), "visual_style");
  await user.click(screen.getByRole("button", { name: "Review cited dimension" }));
  expect(await screen.findByRole("region", { name: "Cited dimension profile" })).toBeInTheDocument();
  await user.type(screen.getByRole("searchbox", { name: "Search library" }), "Mirror");
  await user.click(screen.getByRole("button", { name: "Search" }));

  await waitFor(() => expect(screen.queryByRole("region", { name: "Cited dimension profile" })).not.toBeInTheDocument());
  expect(screen.getByRole("button", { name: "Clear search to review a dimension" })).toBeDisabled();
});

test("reviews cited rating history without generating an opaque score", async () => {
  const user = userEvent.setup();
  const supporting = {
    id: "obs-flcl-visuals",
    scope: "work",
    polarity: "positive",
    dimension: "visuals",
    text: "The visual language still lands.",
    provenance: "user_explicit",
    privacy: "assistant_readable",
    review_state: "accepted",
    observed_on: "2026-07-15",
  };
  const contradictory = {
    id: "obs-flcl-pacing",
    scope: "work",
    polarity: "negative",
    dimension: "pacing",
    text: "The pacing can feel exhausting.",
    provenance: "manual",
    privacy: "assistant_readable",
    review_state: "accepted",
    observed_on: "2026-07-14",
  };
  const context = {
    id: "obs-flcl-tone",
    scope: "work",
    polarity: "mixed",
    dimension: "tone",
    text: "The chaos is both the charm and the friction.",
    provenance: "manual",
    privacy: "assistant_readable",
    review_state: "accepted",
    observed_on: "2026-07-13",
  };
  const flcl = {
    ...mediaItem,
    id: "anime-flcl-2000",
    title: "FLCL",
    category: "anime_series",
    rating: { score: 9, rated_on: "2026-07-15" },
    rating_history: [
      { score: 8, rated_on: "2020-01-01", provisional: true },
      { score: 9, rated_on: "2026-07-15" },
    ],
    observations: [supporting, contradictory, context],
  };
  const mirrorMask = {
    ...mediaItem,
    rating: { score: 10, rated_on: "2026-07-16" },
    rating_history: [{ score: 10, rated_on: "2026-07-16" }],
    observations: [],
  };
  const profile = {
    entries: [
      {
        media_item_id: flcl.id,
        title: flcl.title,
        category: flcl.category,
        current_rating: flcl.rating,
        rating_history: flcl.rating_history,
        supporting_evidence: [supporting],
        contradictory_evidence: [contradictory],
        context_evidence: [context],
      },
      {
        media_item_id: mirrorMask.id,
        title: mirrorMask.title,
        category: mirrorMask.category,
        current_rating: mirrorMask.rating,
        rating_history: mirrorMask.rating_history,
        supporting_evidence: [],
        contradictory_evidence: [],
        context_evidence: [],
      },
    ],
  };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([flcl, mirrorMask]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify(profile), { status: 200, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /FLCL/ });
  await user.click(screen.getByRole("button", { name: "Review cited rating history" }));

  const review = await screen.findByRole("region", { name: "Cited rating history" });
  expect(within(review).getByText("The visual language still lands.")).toBeInTheDocument();
  expect(within(review).getByText("The pacing can feel exhausting.")).toBeInTheDocument();
  expect(within(review).getByText("The chaos is both the charm and the friction.")).toBeInTheDocument();
  expect(within(review).getAllByRole("button").map((button) => button.textContent)).toEqual(["FLCL", "MirrorMask"]);
  expect(fetchMock).toHaveBeenLastCalledWith("/api/profile/rating-history");

  const confirmMock = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
  await user.type(screen.getByRole("textbox", { name: "Title" }), " changed");
  await user.click(within(review).getByRole("button", { name: "MirrorMask" }));
  expect(screen.getByRole("heading", { name: "FLCL changed" })).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("FLCL changed");

  await user.click(within(review).getByRole("button", { name: "MirrorMask" }));
  expect(screen.getByRole("heading", { name: "MirrorMask" })).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(confirmMock).toHaveBeenCalled();
});

test("allows repeated titles and record-scoped citation IDs across distinct canonical works", async () => {
  const user = userEvent.setup();
  const makeRated = (id: string, text: string) => ({
    ...mediaItem,
    id,
    title: "Crash",
    rating: { score: 8, rated_on: "2026-07-15" },
    rating_history: [{ score: 8, rated_on: "2026-07-15" }],
    observations: [{
      id: "obs-1",
      scope: "work",
      polarity: "positive",
      dimension: "performances",
      text,
      provenance: "manual",
      privacy: "assistant_readable",
      review_state: "accepted",
      observed_on: "2026-07-15",
    }],
  });
  const first = makeRated("movie-crash-1996", "Cold and unsettling.");
  const second = makeRated("movie-crash-2004", "The ensemble holds it together.");
  const profile = {
    entries: [first, second].map((item) => ({
      media_item_id: item.id,
      title: item.title,
      category: item.category,
      current_rating: item.rating,
      rating_history: item.rating_history,
      supporting_evidence: item.observations,
      contradictory_evidence: [],
      context_evidence: [],
    })),
  };
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([first, second]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify(profile), { status: 200, headers: { "Content-Type": "application/json" } })));

  render(<App />);
  await screen.findAllByRole("button", { name: /Crash/ });
  await user.click(screen.getByRole("button", { name: "Review cited rating history" }));

  const review = await screen.findByRole("region", { name: "Cited rating history" });
  expect(within(review).getAllByRole("button", { name: "Crash" })).toHaveLength(2);
  expect(within(review).getByText("Cold and unsettling.")).toBeInTheDocument();
  expect(within(review).getByText("The ensemble holds it together.")).toBeInTheDocument();
});

test("composes archived visibility and shows an empty cited rating history", async () => {
  const user = userEvent.setup();
  const rated = {
    ...mediaItem,
    rating: { score: 9, rated_on: "2026-07-15" },
    rating_history: [{ score: 9, rated_on: "2026-07-15" }],
  };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([rated]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify([rated]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ entries: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Show archived records" }));
  await user.click(screen.getByRole("button", { name: "Review cited rating history" }));

  expect(await screen.findByText("No rated works found in the visible library.")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenLastCalledWith("/api/profile/rating-history?include_archived=true");
});

test.each([
  ["malformed transport", { entries: "not-an-array" }, "Cited rating history could not be verified."],
  ["calendar-invalid rating dates", {
    entries: [{
      media_item_id: "movie-mirrormask",
      title: "MirrorMask",
      category: "movie",
      current_rating: { score: 9, rated_on: "2026-02-31" },
      rating_history: [{ score: 9, rated_on: "2026-02-31" }],
      supporting_evidence: [], contradictory_evidence: [], context_evidence: [],
    }],
  }, "Cited rating history could not be verified."],
  ["nonchronological rating events", {
    entries: [{
      media_item_id: "movie-mirrormask",
      title: "MirrorMask",
      category: "movie",
      current_rating: { score: 8, rated_on: "2025-01-01" },
      rating_history: [{ score: 9, rated_on: "2026-01-01" }, { score: 8, rated_on: "2025-01-01" }],
      supporting_evidence: [], contradictory_evidence: [], context_evidence: [],
    }],
  }, "Cited rating history could not be verified."],
  ["a latest-rating projection mismatch", {
    entries: [{
      media_item_id: "movie-mirrormask",
      title: "MirrorMask",
      category: "movie",
      current_rating: { score: 8, rated_on: "2026-07-15" },
      rating_history: [{ score: 9, rated_on: "2026-07-15" }],
      supporting_evidence: [], contradictory_evidence: [], context_evidence: [],
    }],
  }, "Cited rating history could not be verified."],
  ["calendar-invalid citation dates", {
    entries: [{
      media_item_id: "movie-mirrormask",
      title: "MirrorMask",
      category: "movie",
      current_rating: { score: 9, rated_on: "2026-07-15" },
      rating_history: [{ score: 9, rated_on: "2026-07-15" }],
      supporting_evidence: [{ id: "obs-invalid-date", dimension: "visuals", text: "Impossible date.", polarity: "positive", observed_on: "2026-99-99" }],
      contradictory_evidence: [], context_evidence: [],
    }],
  }, "Cited rating history could not be verified."],
  ["wrong evidence-bucket polarity", {
    entries: [{
      media_item_id: "movie-mirrormask",
      title: "MirrorMask",
      category: "movie",
      current_rating: { score: 9, rated_on: "2026-07-15" },
      rating_history: [{ score: 9, rated_on: "2026-07-15" }],
      supporting_evidence: [{ id: "obs-wrong", dimension: "pacing", text: "Wrong bucket.", polarity: "negative", observed_on: "2026-07-15" }],
      contradictory_evidence: [], context_evidence: [],
    }],
  }, "Cited rating history could not be verified."],
  ["missing visible record", {
    entries: [{
      media_item_id: "missing-work",
      title: "Missing Work",
      category: "movie",
      current_rating: { score: 9, rated_on: "2026-07-15" },
      rating_history: [{ score: 9, rated_on: "2026-07-15" }],
      supporting_evidence: [],
      contradictory_evidence: [],
      context_evidence: [],
    }],
  }, "Cited rating history refers to unverifiable visible-library evidence."],
])("rejects %s in cited rating history", async (_label, payload, message) => {
  const user = userEvent.setup();
  const rated = {
    ...mediaItem,
    rating: { score: 9, rated_on: "2026-07-15" },
    rating_history: [{ score: 9, rated_on: "2026-07-15" }],
  };
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([rated]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } })));

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Review cited rating history" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(message);
  expect(screen.queryByRole("button", { name: "Missing Work" })).not.toBeInTheDocument();
});

test.each([
  ["altered canonical citation text", "assistant_readable", "accepted", "Altered citation text."],
  ["private canonical citation", "private", "accepted", "Canonical citation text."],
  ["unreviewed canonical citation", "assistant_readable", "needs_review", "Canonical citation text."],
])("rejects %s from cited rating history", async (_label, privacy, reviewState, profileText) => {
  const user = userEvent.setup();
  const canonicalEvidence = {
    id: "obs-canonical",
    scope: "work",
    polarity: "positive",
    dimension: "visuals",
    text: "Canonical citation text.",
    provenance: "manual",
    privacy,
    review_state: reviewState,
    observed_on: "2026-07-15",
  };
  const rated = {
    ...mediaItem,
    rating: { score: 9, rated_on: "2026-07-15" },
    rating_history: [{ score: 9, rated_on: "2026-07-15" }],
    observations: [canonicalEvidence],
  };
  const profile = {
    entries: [{
      media_item_id: rated.id,
      title: rated.title,
      category: rated.category,
      current_rating: rated.rating,
      rating_history: rated.rating_history,
      supporting_evidence: [{ ...canonicalEvidence, text: profileText, privacy: "assistant_readable", review_state: "accepted" }],
      contradictory_evidence: [],
      context_evidence: [],
    }],
  };
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([rated]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify(profile), { status: 200, headers: { "Content-Type": "application/json" } })));

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Review cited rating history" }));

  expect(await screen.findByRole("alert"))
    .toHaveTextContent("Cited rating history refers to unverifiable visible-library evidence.");
});

test("claims the shared mutex before cited rating history loading renders and releases it on failure", async () => {
  const user = userEvent.setup();
  let resolveProfile!: (response: Response) => void;
  const profileResponse = new Promise<Response>((resolve) => { resolveProfile = resolve; });
  const rated = {
    ...mediaItem,
    rating: { score: 9, rated_on: "2026-07-15" },
    rating_history: [{ score: 9, rated_on: "2026-07-15" }],
  };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([rated]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockReturnValueOnce(profileResponse);
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  const review = screen.getByRole("button", { name: "Review cited rating history" });
  const newEntry = screen.getByRole("button", { name: "New entry" });
  const archive = screen.getByRole("button", { name: "Archive record" });

  act(() => {
    review.click();
    newEntry.click();
    archive.click();
  });

  expect(within(screen.getByRole("region", { name: "Cited rating history" })).getByRole("status"))
    .toHaveTextContent("Loading ratings and accepted cited evidence…");
  expect(screen.getByRole("heading", { name: "MirrorMask" })).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(2);

  resolveProfile(new Response("unavailable", { status: 503 }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Cited rating history could not be loaded.");
  expect(screen.getByRole("button", { name: "New entry" })).toBeEnabled();
});

test("closes cited rating history when a search reloads the shelf", async () => {
  const user = userEvent.setup();
  const rated = {
    ...mediaItem,
    rating: { score: 9, rated_on: "2026-07-15" },
    rating_history: [{ score: 9, rated_on: "2026-07-15" }],
  };
  const profile = {
    entries: [{
      media_item_id: rated.id,
      title: rated.title,
      category: rated.category,
      current_rating: rated.rating,
      rating_history: rated.rating_history,
      supporting_evidence: [],
      contradictory_evidence: [],
      context_evidence: [],
    }],
  };
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([rated]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify(profile), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify([rated]), { status: 200, headers: { "Content-Type": "application/json" } })));

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Review cited rating history" }));
  expect(await screen.findByRole("region", { name: "Cited rating history" })).toBeInTheDocument();

  await user.type(screen.getByRole("searchbox", { name: "Search library" }), "Mirror");
  await user.click(screen.getByRole("button", { name: "Search" }));

  await waitFor(() => expect(screen.queryByRole("region", { name: "Cited rating history" })).not.toBeInTheDocument());
  expect(screen.getByRole("button", { name: "Clear search to review rating history" })).toBeDisabled();
});

test("reviews possible duplicate candidates without mutating either record", async () => {
  const user = userEvent.setup();
  const possibleDuplicate = {
    ...mediaItem,
    id: "movie-mirrormask-alt",
    title: "Mirror Mask",
    aliases: [],
  };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem, possibleDuplicate]), {
      status: 200, headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify([{
      media_item_id: mediaItem.id,
      candidate_media_item_id: possibleDuplicate.id,
      matched_titles: ["mirrormask"],
      certainty: "possible",
      rationale: "same-category records share normalized title or alias identity",
    }]), {
      status: 200, headers: { "Content-Type": "application/json" },
    }));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("heading", { name: "MirrorMask" });
  await user.click(screen.getByRole("button", { name: "Review possible duplicates" }));

  const review = await screen.findByRole("region", { name: "Duplicate candidates" });
  expect(within(review).getByText("Possible duplicate")).toBeInTheDocument();
  expect(within(review).getByText("Matched identity: mirrormask")).toBeInTheDocument();
  expect(within(review).getByText("same-category records share normalized title or alias identity"))
    .toBeInTheDocument();
  expect(fetchMock).toHaveBeenLastCalledWith("/api/duplicates/candidates");
  expect(fetchMock).toHaveBeenCalledTimes(2);

  await user.click(within(review).getByRole("button", { name: "Open Mirror Mask" }));
  expect(screen.getByRole("heading", { name: "Mirror Mask" })).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

test("uses archived visibility for an empty duplicate review", async () => {
  const user = userEvent.setup();
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), {
      status: 200, headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), {
      status: 200, headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify([]), {
      status: 200, headers: { "Content-Type": "application/json" },
    }));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("heading", { name: "MirrorMask" });
  await user.click(screen.getByRole("button", { name: "Show archived records" }));
  await user.click(screen.getByRole("button", { name: "Review possible duplicates" }));

  expect(await screen.findByText("No possible duplicates found in the visible library."))
    .toBeInTheDocument();
  expect(fetchMock).toHaveBeenLastCalledWith("/api/duplicates/candidates?include_archived=true");
});

test("rejects malformed duplicate evidence at the browser boundary", async () => {
  const user = userEvent.setup();
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), {
      status: 200, headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify([{
      media_item_id: mediaItem.id,
      candidate_media_item_id: "movie-bad-candidate",
      matched_titles: [],
      certainty: "definite",
      rationale: "",
    }]), {
      status: 200, headers: { "Content-Type": "application/json" },
    }));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("heading", { name: "MirrorMask" });
  await user.click(screen.getByRole("button", { name: "Review possible duplicates" }));

  expect(await screen.findByRole("alert"))
    .toHaveTextContent("Possible duplicate evidence could not be verified.");
  expect(screen.queryByText("Possible duplicate")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Close duplicate review" })).toBeEnabled();
});

test.each([
  ["first", "movie-missing-first", mediaItem.id],
  ["second", mediaItem.id, "movie-missing-second"],
])("rejects a missing %s duplicate record reference", async (_side, firstId, secondId) => {
  const user = userEvent.setup();
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), {
      status: 200, headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify([{
      media_item_id: firstId,
      candidate_media_item_id: secondId,
      matched_titles: ["mirrormask"],
      certainty: "possible",
      rationale: "same identity",
    }]), { status: 200, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("heading", { name: "MirrorMask" });
  await user.click(screen.getByRole("button", { name: "Review possible duplicates" }));

  expect(await screen.findByRole("alert"))
    .toHaveTextContent("Possible duplicate evidence refers to a record outside the visible library.");
  expect(screen.queryByRole("button", { name: /Open movie-missing/ })).not.toBeInTheDocument();
});

test("claims the navigation and write mutex before duplicate loading renders", async () => {
  let resolveCandidates!: (response: Response) => void;
  const candidateResponse = new Promise<Response>((resolve) => { resolveCandidates = resolve; });
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), {
      status: 200, headers: { "Content-Type": "application/json" },
    }))
    .mockReturnValueOnce(candidateResponse);
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("heading", { name: "MirrorMask" });
  const review = screen.getByRole("button", { name: "Review possible duplicates" });
  const newEntry = screen.getByRole("button", { name: "New entry" });
  const archive = screen.getByRole("button", { name: "Archive record" });

  act(() => {
    review.click();
    newEntry.click();
    archive.click();
  });

  expect(within(screen.getByRole("region", { name: "Duplicate candidates" })).getByRole("status"))
    .toHaveTextContent("Checking the visible library for possible duplicates…");
  expect(screen.getByRole("heading", { name: "MirrorMask" })).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(2);

  resolveCandidates(new Response("unavailable", { status: 503 }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Possible duplicates could not be loaded.");
  expect(screen.getByRole("button", { name: "New entry" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Close duplicate review" })).toBeEnabled();
});

test("closes duplicate evidence when a library search reloads the shelf", async () => {
  const user = userEvent.setup();
  const oldCandidate = { ...mediaItem, id: "movie-old-candidate", title: "Mirror Mask" };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem, oldCandidate]), {
      status: 200, headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify([{
      media_item_id: mediaItem.id,
      candidate_media_item_id: oldCandidate.id,
      matched_titles: ["mirrormask"],
      certainty: "possible",
      rationale: "old candidate",
    }]), { status: 200, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), {
      status: 200, headers: { "Content-Type": "application/json" },
    }));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("heading", { name: "MirrorMask" });
  await user.click(screen.getByRole("button", { name: "Review possible duplicates" }));
  await screen.findByText("old candidate");
  await user.type(screen.getByRole("searchbox", { name: "Search library" }), "mirror");
  await user.click(screen.getByRole("button", { name: "Search" }));

  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
  expect(screen.queryByRole("region", { name: "Duplicate candidates" })).not.toBeInTheDocument();
  expect(screen.queryByText("old candidate")).not.toBeInTheDocument();
});

test("locks every empty-library start for a dirty draft", async () => {
  const user = userEvent.setup();
  const onStartCreate = vi.fn();
  const onReviewPortableExport = vi.fn();
  const onRestoreLocalBackup = vi.fn();

  render(
    <EmptyLibraryState
      editorLocked={false}
      dirty
      onStartCreate={onStartCreate}
      onReviewPortableExport={onReviewPortableExport}
      onRestoreLocalBackup={onRestoreLocalBackup}
    />,
  );

  const actions = [
    screen.getByRole("button", { name: "Add your first entry" }),
    screen.getByRole("button", { name: "Review a portable export" }),
    screen.getByRole("button", { name: "Restore a local backup" }),
  ];
  actions.forEach((action) => expect(action).toBeDisabled());
  await user.click(actions[0]);
  await user.click(actions[1]);
  await user.click(actions[2]);
  expect(onStartCreate).not.toHaveBeenCalled();
  expect(onReviewPortableExport).not.toHaveBeenCalled();
  expect(onRestoreLocalBackup).not.toHaveBeenCalled();
});

test("gives a first-time empty library three safe ways to begin", async () => {
  const user = userEvent.setup();
  const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify([]), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }));
  const inputClick = vi.spyOn(HTMLInputElement.prototype, "click").mockImplementation(() => undefined);
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);

  expect(await screen.findByRole("heading", { name: "Your library begins here" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Add your first entry" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Review a portable export" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Restore a local backup" })).toBeEnabled();

  await user.click(screen.getByRole("button", { name: "Review a portable export" }));
  expect(inputClick).toHaveBeenCalledOnce();
  expect(fetchMock).toHaveBeenCalledTimes(1);

  await user.click(screen.getByRole("button", { name: "Add your first entry" }));
  expect(screen.getByText("New media record")).toBeInTheDocument();
});

test("runs exactly one empty-library restore through the guarded recovery flow and locks the other starts", async () => {
  let resolveRestore!: (response: Response) => void;
  const restoreResponse = new Promise<Response>((resolve) => { resolveRestore = resolve; });
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }))
    .mockReturnValueOnce(restoreResponse)
    .mockResolvedValueOnce(new Response(JSON.stringify([]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("prompt", vi.fn().mockReturnValue("RESTORE"));

  render(<App />);

  await screen.findByRole("heading", { name: "Your library begins here" });
  const restoreButton = screen.getByRole("button", { name: "Restore a local backup" });
  act(() => {
    restoreButton.click();
    restoreButton.click();
  });

  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(fetchMock).toHaveBeenLastCalledWith("/api/backup/restore", { method: "POST" });
  expect(screen.getByRole("button", { name: "Add your first entry" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Review a portable export" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Restore a local backup" })).toBeDisabled();

  resolveRestore(new Response(JSON.stringify({ items: 0 }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Restore a local backup" })).toBeEnabled());
});

test("archives the selected record through the lifecycle endpoint and removes it from the active shelf", async () => {
  const user = userEvent.setup();
  let resolveArchive!: (response: Response) => void;
  const archiveResponse = new Promise<Response>((resolve) => { resolveArchive = resolve; });
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }))
    .mockReturnValueOnce(archiveResponse)
    .mockResolvedValueOnce(new Response(JSON.stringify([]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
  vi.stubGlobal("confirm", vi.fn().mockReturnValue(true));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Archive record" }));

  expect(screen.getByRole("button", { name: "Archiving…" })).toBeDisabled();
  expect(screen.getByRole("textbox", { name: "Title" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Concierge home" })).toBeDisabled();
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/media/movie-mirrormask/archive",
    expect.objectContaining({ method: "POST" }),
  );

  resolveArchive(new Response(JSON.stringify({ ...mediaItem, archived_on: "2026-07-17" }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }));

  expect(await screen.findByRole("heading", { name: "Your library begins here" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /MirrorMaskMovie/ })).not.toBeInTheDocument();
});

test("allows only one lifecycle request before React commits the pending lock", async () => {
  let resolveArchive!: (response: Response) => void;
  const archiveResponse = new Promise<Response>((resolve) => { resolveArchive = resolve; });
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }))
    .mockReturnValueOnce(archiveResponse)
    .mockResolvedValueOnce(new Response(JSON.stringify([]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
  const confirmMock = vi.fn().mockReturnValue(true);
  vi.stubGlobal("confirm", confirmMock);
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  const archiveButton = screen.getByRole("button", { name: "Archive record" });
  archiveButton.click();
  archiveButton.click();

  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(confirmMock).toHaveBeenCalledOnce();
  resolveArchive(new Response(JSON.stringify({ ...mediaItem, archived_on: "2026-07-17" }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }));
  await screen.findByRole("heading", { name: "Your library begins here" });
});

test("shows archived records on demand and restores one through the lifecycle endpoint", async () => {
  const user = userEvent.setup();
  const archivedItem = { ...mediaItem, archived_on: "2026-07-16" };
  let resolveRestore!: (response: Response) => void;
  const restoreResponse = new Promise<Response>((resolve) => { resolveRestore = resolve; });
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify([archivedItem]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }))
    .mockReturnValueOnce(restoreResponse)
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("heading", { name: "Your library begins here" });
  await user.click(screen.getByRole("button", { name: "Show archived records" }));

  expect(await screen.findByRole("button", { name: /MirrorMaskMovie · Finished · Archived/ })).toBeInTheDocument();
  expect(fetchMock).toHaveBeenLastCalledWith("/api/media?include_archived=true", expect.anything());
  expect(screen.getByText(/Archived on 2026-07-16/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Restore record" }));

  expect(screen.getByRole("button", { name: "Restoring…" })).toBeDisabled();
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/media/movie-mirrormask/restore",
    expect.objectContaining({ method: "POST" }),
  );
  resolveRestore(new Response(JSON.stringify(mediaItem), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }));

  expect(await screen.findByRole("button", { name: "Archive record" })).toBeInTheDocument();
  expect(screen.queryByText("Archived on 2026-07-16")).not.toBeInTheDocument();
});

test("creates and verifies the fixed local backup while locking record-changing controls", async () => {
  const user = userEvent.setup();
  let resolveBackup!: (response: Response) => void;
  const backupResponse = new Promise<Response>((resolve) => { resolveBackup = resolve; });
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }))
    .mockReturnValueOnce(backupResponse);
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Create local backup" }));

  expect(screen.getByRole("button", { name: "Creating backup…" })).toBeDisabled();
  expect(screen.getByRole("textbox", { name: "Title" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Concierge home" })).toBeDisabled();
  expect(fetchMock).toHaveBeenLastCalledWith("/api/backup", { method: "POST" });

  resolveBackup(new Response(JSON.stringify({
    backup_version: "1.0",
    items: 1,
    verified: true,
  }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }));

  await waitFor(() => expect(
    within(screen.getByRole("region", { name: "Portability and recovery" })).getByRole("status"),
  ).toHaveTextContent("Verified backup created for 1 record."));
  expect(screen.getByRole("button", { name: "Create local backup" })).toBeEnabled();
});

test("allows only one backup request before React commits the pending lock", async () => {
  let resolveBackup!: (response: Response) => void;
  const backupResponse = new Promise<Response>((resolve) => { resolveBackup = resolve; });
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }))
    .mockReturnValueOnce(backupResponse);
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  const backupButton = screen.getByRole("button", { name: "Create local backup" });
  backupButton.click();
  backupButton.click();

  expect(fetchMock).toHaveBeenCalledTimes(2);
  resolveBackup(new Response(JSON.stringify({
    backup_version: "1.0", items: 1, verified: true,
  }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }));
  await waitFor(() => expect(backupButton).toBeEnabled());
});

test("blocks a same-tick archive after backup acquires the immediate lock", async () => {
  let resolveBackup!: (response: Response) => void;
  const backupResponse = new Promise<Response>((resolve) => { resolveBackup = resolve; });
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }))
    .mockReturnValueOnce(backupResponse);
  const confirmMock = vi.fn().mockReturnValue(true);
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("confirm", confirmMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  const backupButton = screen.getByRole("button", { name: "Create local backup" });
  const archiveButton = screen.getByRole("button", { name: "Archive record" });
  backupButton.click();
  archiveButton.click();

  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(confirmMock).not.toHaveBeenCalled();
  resolveBackup(new Response(JSON.stringify({
    backup_version: "1.0", items: 1, verified: true,
  }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }));
  await waitFor(() => expect(backupButton).toBeEnabled());
});

test("keeps backup failures visible and retryable", async () => {
  const user = userEvent.setup();
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "Backup disk is unavailable." }), {
      status: 503,
      headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify({
      backup_version: "1.0", items: 1, verified: true,
    }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Create local backup" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Backup disk is unavailable.");
  expect(screen.getByRole("button", { name: "Create local backup" })).toBeEnabled();
  await user.click(screen.getByRole("button", { name: "Create local backup" }));

  await waitFor(() => expect(
    within(screen.getByRole("region", { name: "Portability and recovery" })).getByRole("status"),
  ).toHaveTextContent("Verified backup created for 1 record."));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("rejects a truthy malformed backup receipt instead of announcing success", async () => {
  const user = userEvent.setup();
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify({
      backup_version: "1.0", items: "unknown", verified: "false",
    }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Create local backup" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "The local backup could not be verified.",
  );
  expect(within(screen.getByRole("region", { name: "Portability and recovery" })).queryByRole("status"))
    .not.toBeInTheDocument();
});

test("does not back up an unfinished draft", async () => {
  const user = userEvent.setup();
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify([mediaItem]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.type(screen.getByRole("textbox", { name: "Taxonomy value" }), "unfinished");

  expect(screen.getByRole("button", { name: "Create local backup" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Download portable export" })).toBeDisabled();
  expect(screen.getByLabelText("Choose portable export")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Restore latest backup" })).toBeDisabled();
  expect(fetchMock).toHaveBeenCalledOnce();
});

test("downloads the exact portable export with a dated filename", async () => {
  const user = userEvent.setup();
  const exportedDocument = {
    schema_version: "1.6",
    exported_on: "2026-07-17",
    creators: [],
    proposals: [],
    recommendations: [],
    media_items: [mediaItem],
  };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify(exportedDocument), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
  const createObjectURL = vi.fn().mockReturnValue("blob:portable-export");
  const revokeObjectURL = vi.fn();
  let downloaded = { href: "", filename: "" };
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    downloaded = { href: this.href, filename: this.download };
  });

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Download portable export" }));

  expect(fetchMock).toHaveBeenLastCalledWith("/api/export");
  expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
  const exportedBlob = createObjectURL.mock.calls[0][0] as Blob;
  const exportedText = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(exportedBlob);
  });
  expect(exportedBlob.type).toBe("application/json");
  expect(exportedText.endsWith("\n")).toBe(true);
  expect(JSON.parse(exportedText)).toEqual(exportedDocument);
  expect(downloaded).toEqual({
    href: "blob:portable-export",
    filename: "concierge-export-2026-07-17.json",
  });
  expect(revokeObjectURL).toHaveBeenCalledWith("blob:portable-export");
  expect(within(screen.getByRole("region", { name: "Portability and recovery" })).getByRole("status"))
    .toHaveTextContent("Portable export downloaded with 1 record.");
});

test("rejects a malformed portable export without starting a download", async () => {
  const user = userEvent.setup();
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify({
      schema_version: "1.5",
      exported_on: "not-a-date",
      media_items: "not-an-array",
    }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
  const createObjectURL = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("URL", { createObjectURL, revokeObjectURL: vi.fn() });

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Download portable export" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "The portable export could not be verified.",
  );
  expect(createObjectURL).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Download portable export" })).toBeEnabled();
});

test("removes the temporary export anchor when browser download initiation throws", async () => {
  const user = userEvent.setup();
  const exportedDocument = {
    schema_version: "1.6", exported_on: "2026-07-17",
    creators: [], proposals: [], recommendations: [], media_items: [mediaItem],
  };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), {
      status: 200, headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify(exportedDocument), {
      status: 200, headers: { "Content-Type": "application/json" },
    }));
  const revokeObjectURL = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("URL", {
    createObjectURL: vi.fn().mockReturnValue("blob:throwing-export"),
    revokeObjectURL,
  });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {
    throw new Error("Browser refused the download.");
  });

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Download portable export" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Browser refused the download.");
  expect(document.querySelector('a[download="concierge-export-2026-07-17.json"]')).toBeNull();
  expect(revokeObjectURL).toHaveBeenCalledWith("blob:throwing-export");
  expect(screen.getByRole("button", { name: "Download portable export" })).toBeEnabled();
});

test("does not export in the same tick after proposal review begins", async () => {
  const user = userEvent.setup();
  const proposal = {
    id: "proposal-export-race",
    target_media_item_id: "movie-mirrormask",
    kind: "observation",
    proposed_observation: {
      id: "obs-export-race", scope: "work", polarity: "positive",
      dimension: "portability", text: "Keep the reviewed state coherent.",
      provenance: "assistant_inferred", source_context: "test:export-race",
      confidence: 0.8, review_state: "needs_review", observed_on: "2026-07-17",
    },
    source_context: "test:export-race", confidence: 0.8,
    review_state: "needs_review", proposed_on: "2026-07-17",
  };
  let resolveReview!: (response: Response) => void;
  const reviewResponse = new Promise<Response>((resolve) => { resolveReview = resolve; });
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), {
      status: 200, headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify([proposal]), {
      status: 200, headers: { "Content-Type": "application/json" },
    }))
    .mockReturnValueOnce(reviewResponse);
  const createObjectURL = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("URL", { createObjectURL, revokeObjectURL: vi.fn() });

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Review inference proposals" }));
  const acceptButton = await screen.findByRole("button", { name: "Accept inference" });
  const exportButton = screen.getByRole("button", { name: "Download portable export" });
  acceptButton.click();
  exportButton.click();

  expect(fetchMock).toHaveBeenCalledTimes(3);
  expect(createObjectURL).not.toHaveBeenCalled();
  resolveReview(new Response(JSON.stringify({ ...proposal, review_state: "accepted" }), {
    status: 200, headers: { "Content-Type": "application/json" },
  }));
  await waitFor(() => expect(exportButton).toBeEnabled());
});

test("restores the fixed latest backup only after typed confirmation and refreshes the library", async () => {
  const user = userEvent.setup();
  const restoredItem = { ...mediaItem, title: "MirrorMask from backup" };
  let resolveRestore!: (response: Response) => void;
  const restoreResponse = new Promise<Response>((resolve) => { resolveRestore = resolve; });
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }))
    .mockReturnValueOnce(restoreResponse)
    .mockResolvedValueOnce(new Response(JSON.stringify([restoredItem]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
  vi.stubGlobal("prompt", vi.fn().mockReturnValue("RESTORE"));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Restore latest backup" }));

  expect(screen.getByRole("button", { name: "Restoring backup…" })).toBeDisabled();
  expect(screen.getByRole("textbox", { name: "Title" })).toBeDisabled();
  expect(fetchMock).toHaveBeenLastCalledWith("/api/backup/restore", { method: "POST" });

  resolveRestore(new Response(JSON.stringify({
    backup_version: "1.0", items: 1, verified: true,
  }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }));

  expect(await screen.findByRole("heading", { name: "MirrorMask from backup" })).toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "Portability and recovery" })).getByRole("status"))
    .toHaveTextContent("Verified backup restored with 1 record.");
});

test("does not restore when the destructive confirmation is not exact", async () => {
  const user = userEvent.setup();
  const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }));
  vi.stubGlobal("prompt", vi.fn().mockReturnValue("restore"));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Restore latest backup" }));

  expect(fetchMock).toHaveBeenCalledOnce();
  expect(screen.getByRole("heading", { name: "MirrorMask" })).toBeInTheDocument();
});

test("refreshes after a completed restore even when its receipt is malformed", async () => {
  const user = userEvent.setup();
  const restoredItem = { ...mediaItem, title: "Restored despite malformed receipt" };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify({
      backup_version: "1.0", items: "unknown", verified: "false",
    }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify([restoredItem]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
  vi.stubGlobal("prompt", vi.fn().mockReturnValue("RESTORE"));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Restore latest backup" }));

  expect(await screen.findByRole("heading", { name: "Restored despite malformed receipt" }))
    .toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent(
    "restore request completed and the library was refreshed, but its receipt could not be verified",
  );
  expect(fetchMock).toHaveBeenCalledTimes(3);
});

test("preserves the visible library when backup restore is rejected", async () => {
  const user = userEvent.setup();
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "no local backup exists" }), {
      status: 404,
      headers: { "Content-Type": "application/json" },
    }));
  vi.stubGlobal("prompt", vi.fn().mockReturnValue("RESTORE"));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.click(screen.getByRole("button", { name: "Restore latest backup" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("no local backup exists");
  expect(screen.getByRole("heading", { name: "MirrorMask" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Restore latest backup" })).toBeEnabled();
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

test("previews and merges a bounded portable document after exact confirmation", async () => {
  const user = userEvent.setup();
  const importedItem = { ...mediaItem, id: "movie-imported-library", title: "Imported Library Record" };
  const importDocument = {
    schema_version: "1.6",
    exported_on: "2026-07-16",
    creators: [{ id: "creator-import", name: "Import Creator" }],
    media_items: [importedItem],
    proposals: [{ id: "proposal-import" }],
    recommendations: [{ id: "recommendation-import" }],
  };
  const importReview = {
    review_schema_version: "1.0",
    schema_version: "1.6",
    review_token: "b".repeat(64),
    can_import: true,
    blocking_reasons: [],
    media_items: {
      mode: "merge",
      entries: [{
        id: importedItem.id, label: importedItem.title, action: "create", before: null,
        after: {
          ...importedItem, relationships: [], credits: [], rating_history: [],
          progress_records: [], observations: [],
        },
      }],
      preserved_ids: [mediaItem.id],
      current_ids: [mediaItem.id],
    },
    creators: {
      mode: "merge",
      entries: [{
        id: "creator-import", label: "Import Creator", action: "create", before: null,
        after: { id: "creator-import", name: "Import Creator", aliases: [] },
      }],
      preserved_ids: [],
      current_ids: [],
    },
    proposals: {
      mode: "replace",
      entries: [{
        id: "proposal-import", label: "proposal-import", action: "create", before: null,
        after: { id: "proposal-import", review_state: "needs_review" },
      }],
      preserved_ids: [],
      current_ids: [],
    },
    recommendations: {
      mode: "merge",
      entries: [{
        id: "recommendation-import", label: "recommendation-import", action: "create", before: null,
        after: { id: "recommendation-import", evidence: [], outcomes: [] },
      }],
      preserved_ids: [],
      current_ids: [],
    },
  };
  let resolveImport!: (response: Response) => void;
  const importResponse = new Promise<Response>((resolve) => { resolveImport = resolve; });
  const promptMock = vi.fn().mockReturnValue("MERGE");
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), {
      status: 200, headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify(importReview), {
      status: 200, headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(portableExportResponse([mediaItem]))
    .mockReturnValueOnce(importResponse)
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem, importedItem]), {
      status: 200, headers: { "Content-Type": "application/json" },
    }));
  vi.stubGlobal("prompt", promptMock);
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.upload(
    screen.getByLabelText("Choose portable export"),
    new File([JSON.stringify(importDocument)], "library.json", { type: "application/json" }),
  );

  const preview = await screen.findByRole("region", { name: "Import preview" });
  expect(within(preview).getByText("Schema 1.6")).toBeInTheDocument();
  expect(within(preview).getByText("1 media record")).toBeInTheDocument();
  expect(within(preview).getByText("1 creator")).toBeInTheDocument();
  expect(within(preview).getByText("Preserved outside this document")).toBeInTheDocument();
  expect(within(preview).getByText(mediaItem.id)).toBeInTheDocument();
  expect(within(preview).getByText("1 proposal")).toBeInTheDocument();
  expect(within(preview).getByText("1 recommendation")).toBeInTheDocument();
  expect(preview).toHaveTextContent("Recommendations merge create-only by stable ID");
  expect(within(preview).getByRole("heading", { name: "Deterministic change review" })).toBeInTheDocument();
  expect(within(preview).getByText("Create · Imported Library Record")).toBeInTheDocument();
  expect(within(preview).getByText("Create · Import Creator")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/import/review", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(importDocument),
  });
  expect(fetchMock).toHaveBeenNthCalledWith(3, "/api/export");
  expect(fetchMock).toHaveBeenCalledTimes(3);

  const mergeButton = within(preview).getByRole("button", { name: "Merge portable import" });
  const clearButton = within(preview).getByRole("button", { name: "Clear selected import" });
  const importInput = screen.getByLabelText("Choose portable export");
  const competingDocument = {
    ...importDocument,
    media_items: [{ ...importedItem, id: "movie-competing-import", title: "Competing Import" }],
  };
  act(() => {
    mergeButton.click();
    mergeButton.click();
    clearButton.click();
    fireEvent.change(importInput, {
      target: {
        files: [new File([JSON.stringify(competingDocument)], "competing.json", {
          type: "application/json",
        })],
      },
    });
  });
  expect(screen.getByRole("button", { name: "Merging import…" })).toBeDisabled();
  expect(screen.getByRole("textbox", { name: "Title" })).toBeDisabled();
  expect(fetchMock).toHaveBeenLastCalledWith(`/api/import?review_token=${"b".repeat(64)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(importDocument),
  });
  expect(promptMock).toHaveBeenCalledOnce();
  expect(fetchMock).toHaveBeenCalledTimes(4);
  expect(screen.getByRole("region", { name: "Import preview" })).toHaveTextContent("library.json");
  expect(screen.getByRole("region", { name: "Import preview" })).not.toHaveTextContent("competing.json");

  resolveImport(new Response(JSON.stringify({ imported: 1 }), {
    status: 200, headers: { "Content-Type": "application/json" },
  }));
  expect(await screen.findByRole("button", { name: /Imported Library Record/ })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "MirrorMask" })).toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "Portability and recovery" })).getByRole("status"))
    .toHaveTextContent("Merged and verified 1 imported record; unrelated media and creator records were preserved; proposal replacement and recommendation merge followed the previewed rules.");
  expect(screen.queryByRole("region", { name: "Import preview" })).not.toBeInTheDocument();
});

test("allows only one import review before React commits the pending lock", async () => {
  const documentToImport = {
    schema_version: "1.6", exported_on: "2026-07-18", creators: [],
    media_items: [{ id: "movie-new", title: "New Movie", category: "movie", status: "planned" }],
    proposals: [], recommendations: [],
  };
  const review = importReviewFor(documentToImport);
  let resolveReview!: (response: Response) => void;
  const reviewResponse = new Promise<Response>((resolve) => { resolveReview = resolve; });
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), {
      status: 200, headers: { "Content-Type": "application/json" },
    }))
    .mockReturnValueOnce(reviewResponse)
    .mockResolvedValueOnce(portableExportResponse([]));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  const input = screen.getByLabelText("Choose portable export") as HTMLInputElement;
  const file = new File([JSON.stringify(documentToImport)], "review.json", { type: "application/json" });
  Object.defineProperty(input, "files", { configurable: true, value: [file] });
  input.dispatchEvent(new Event("change", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));

  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  resolveReview(new Response(JSON.stringify(review), {
    status: 200, headers: { "Content-Type": "application/json" },
  }));
  expect(await screen.findByRole("region", { name: "Import preview" })).toHaveTextContent("review.json");
});


test("recalculates a stale reviewed import without applying it", async () => {
  const user = userEvent.setup();
  const documentToImport = {
    schema_version: "1.6", exported_on: "2026-07-18", creators: [],
    media_items: [{ id: "movie-new", title: "New Movie", category: "movie", status: "planned" }],
    proposals: [], recommendations: [],
  };
  const firstReview = importReviewFor(documentToImport);
  const refreshedReview = {
    ...firstReview,
    review_token: "d".repeat(64),
    media_items: {
      ...firstReview.media_items,
      preserved_ids: ["movie-concurrent"],
      current_ids: ["movie-concurrent"],
    },
  };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), {
      status: 200, headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify(firstReview), {
      status: 200, headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(portableExportResponse([]))
    .mockResolvedValueOnce(new Response(JSON.stringify({
      detail: "import review is stale; review the document again",
    }), { status: 409, headers: { "Content-Type": "application/json", "X-Error-Code": "import-review-stale" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify(refreshedReview), {
      status: 200, headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(portableExportResponse([{
      ...mediaItem, id: "movie-concurrent", title: "Concurrent Movie",
    }]));
  vi.stubGlobal("prompt", vi.fn().mockReturnValue("MERGE"));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.upload(
    screen.getByLabelText("Choose portable export"),
    new File([JSON.stringify(documentToImport)], "stale.json", { type: "application/json" }),
  );
  await user.click(await screen.findByRole("button", { name: "Merge portable import" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("change review was recalculated");
  expect(screen.getByText("movie-concurrent")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Merge portable import" })).toBeEnabled();
  expect(fetchMock).toHaveBeenCalledTimes(6);
  expect(fetchMock).toHaveBeenNthCalledWith(5, "/api/import/review", expect.objectContaining({
    method: "POST",
    body: JSON.stringify(documentToImport),
  }));
});


test("blocks a reviewed import with recommendation identity conflicts", async () => {
  const user = userEvent.setup();
  const documentToImport = {
    schema_version: "1.6", exported_on: "2026-07-18", creators: [],
    media_items: [mediaItem], proposals: [], recommendations: [{ id: "recommendation-conflict" }],
  };
  const conflictReview = {
    ...importReviewFor(documentToImport),
    can_import: false,
    blocking_reasons: ["recommendation id conflict: 'recommendation-conflict'"],
    media_items: {
      mode: "merge",
      entries: [{
        ...importReviewFor(documentToImport).media_items.entries[0],
        action: "unchanged",
        before: importReviewFor(documentToImport).media_items.entries[0].after,
      }],
      preserved_ids: [],
      current_ids: [mediaItem.id],
    },
    recommendations: {
      mode: "merge",
      entries: [{
        id: "recommendation-conflict",
        label: "recommendation-conflict",
        action: "conflict",
        before: { id: "recommendation-conflict", rationale: "Current", evidence: [], outcomes: [] },
        after: { id: "recommendation-conflict", evidence: [], outcomes: [] },
      }],
      preserved_ids: [],
      current_ids: ["recommendation-conflict"],
    },
  };
  const promptMock = vi.fn();
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), {
      status: 200, headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify(conflictReview), {
      status: 200, headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify({
      schema_version: "1.6", exported_on: "2026-07-18", creators: [],
      media_items: [mediaItem], proposals: [],
      recommendations: [{ id: "recommendation-conflict", rationale: "Current" }],
    }), {
      status: 200, headers: { "Content-Type": "application/json" },
    }));
  vi.stubGlobal("prompt", promptMock);
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.upload(
    screen.getByLabelText("Choose portable export"),
    new File([JSON.stringify(documentToImport)], "conflict.json", { type: "application/json" }),
  );

  expect(await screen.findByRole("alert")).toHaveTextContent("recommendation id conflict");
  expect(screen.getByText("Conflict · recommendation-conflict")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Merge portable import" })).toBeDisabled();
  expect(promptMock).not.toHaveBeenCalled();
  expect(fetchMock).toHaveBeenCalledTimes(3);
  expect(fetchMock).toHaveBeenLastCalledWith("/api/export");
});


test("rejects invalid and oversized import files before any request", async () => {
  const user = userEvent.setup();
  const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), {
    status: 200, headers: { "Content-Type": "application/json" },
  }));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  const input = screen.getByLabelText("Choose portable export");
  await user.upload(input, new File(["not json"], "broken.json", { type: "application/json" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("not valid JSON");
  expect(screen.queryByRole("region", { name: "Import preview" })).not.toBeInTheDocument();

  await user.upload(
    input,
    new File([JSON.stringify({
      schema_version: 1.6, exported_on: "2026-07-18", creators: [],
      media_items: [], proposals: [], recommendations: [],
    })], "numeric-version.json", { type: "application/json" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent("unsupported schema version");
  expect(screen.queryByRole("region", { name: "Import preview" })).not.toBeInTheDocument();

  await user.upload(
    input,
    new File([new Uint8Array(5 * 1024 * 1024 + 1)], "huge.json", { type: "application/json" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent("larger than the 5 MiB import limit");
  expect(fetchMock).toHaveBeenCalledOnce();
});

test("does not import without the exact destructive confirmation", async () => {
  const user = userEvent.setup();
  const documentToImport = {
    schema_version: "1.4", exported_on: "2026-07-16",
    creators: [], media_items: [mediaItem], proposals: [],
  };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), {
      status: 200, headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify(importReviewFor(documentToImport)), {
      status: 200, headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(portableExportResponse([]));
  vi.stubGlobal("prompt", vi.fn().mockReturnValue("import"));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.upload(
    screen.getByLabelText("Choose portable export"),
    new File([JSON.stringify(documentToImport)], "library.json", { type: "application/json" }),
  );
  const preview = await screen.findByRole("region", { name: "Import preview" });
  await user.click(within(preview).getByRole("button", { name: "Merge portable import" }));

  expect(fetchMock).toHaveBeenCalledTimes(3);
  expect(preview).toBeInTheDocument();
});

test("preserves the current library and preview when backend import validation rejects", async () => {
  const user = userEvent.setup();
  const documentToImport = {
    schema_version: "1.4", exported_on: "2026-07-16",
    creators: [], media_items: [{ ...mediaItem, title: "Rejected Import" }], proposals: [],
  };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), {
      status: 200, headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify(importReviewFor(documentToImport)), {
      status: 200, headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(portableExportResponse([]))
    .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "unknown field is not permitted" }), {
      status: 422, headers: { "Content-Type": "application/json" },
    }));
  vi.stubGlobal("prompt", vi.fn().mockReturnValue("MERGE"));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.upload(
    screen.getByLabelText("Choose portable export"),
    new File([JSON.stringify(documentToImport)], "rejected.json", { type: "application/json" }),
  );
  const preview = await screen.findByRole("region", { name: "Import preview" });
  await user.click(within(preview).getByRole("button", { name: "Merge portable import" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("unknown field is not permitted");
  expect(screen.getByRole("heading", { name: "MirrorMask" })).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Import preview" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Merge portable import" })).toBeEnabled();
  expect(fetchMock).toHaveBeenCalledTimes(4);
});

test("refreshes authoritatively when an import succeeds with an unverified receipt", async () => {
  const user = userEvent.setup();
  const importedItem = { ...mediaItem, id: "movie-ambiguous-import", title: "Ambiguously Imported Record" };
  const documentToImport = {
    schema_version: "1.4", exported_on: "2026-07-16",
    creators: [], media_items: [importedItem], proposals: [],
  };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem]), {
      status: 200, headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify(importReviewFor(documentToImport)), {
      status: 200, headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(portableExportResponse([]))
    .mockResolvedValueOnce(new Response(JSON.stringify({ imported: "1" }), {
      status: 200, headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify([mediaItem, importedItem]), {
      status: 200, headers: { "Content-Type": "application/json" },
    }));
  vi.stubGlobal("prompt", vi.fn().mockReturnValue("MERGE"));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.upload(
    screen.getByLabelText("Choose portable export"),
    new File([JSON.stringify(documentToImport)], "ambiguous.json", { type: "application/json" }),
  );
  await user.click(await screen.findByRole("button", { name: "Merge portable import" }));

  expect(await screen.findByRole("button", { name: /Ambiguously Imported Record/ })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "MirrorMask" })).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("receipt could not be verified");
  expect(screen.getByRole("alert")).toHaveTextContent("authoritative library was refreshed");
  expect(screen.queryByRole("region", { name: "Import preview" })).not.toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(5);
});

test("protects a dirty draft from browser refresh or tab close", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([mediaItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<App />);
  await screen.findByRole("button", { name: /MirrorMask/ });
  await user.clear(screen.getByRole("textbox", { name: "Title" }));
  await user.type(screen.getByRole("textbox", { name: "Title" }), "Refresh-safe draft");

  const event = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(event);

  expect(event.defaultPrevented).toBe(true);
});
