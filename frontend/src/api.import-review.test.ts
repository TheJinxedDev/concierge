import { describe, expect, it, vi } from "vitest";

import { ImportReviewStaleError, importLibrary, parsePortableImportDocument, reviewImportLibrary } from "./api";

const document = {
  schema_version: "1.6" as const,
  exported_on: "2026-07-18",
  creators: [],
  media_items: [{ id: "movie-a", title: "Movie A", category: "movie", status: "planned" }],
  proposals: [],
  recommendations: [],
};

const emptyCurrentExport = {
  schema_version: "1.6", exported_on: "2026-07-18",
  creators: [], media_items: [], proposals: [], recommendations: [],
};

function responsePayload() {
  return {
    review_schema_version: "1.0",
    schema_version: "1.6",
    review_token: "a".repeat(64),
    can_import: true,
    blocking_reasons: [],
    media_items: {
      mode: "merge",
      entries: [{
        id: "movie-a",
        label: "Movie A",
        action: "create",
        before: null,
        after: {
          id: "movie-a", title: "Movie A", category: "movie", status: "planned",
          aliases: [], terms: [], relationships: [], credits: [], rating_history: [],
          progress_records: [], observations: [],
        },
      }],
      preserved_ids: ["movie-preserved"],
      current_ids: ["movie-preserved"],
    },
    creators: { mode: "merge", entries: [], preserved_ids: [], current_ids: [] },
    proposals: { mode: "replace", entries: [], preserved_ids: [], current_ids: [] },
    recommendations: { mode: "merge", entries: [], preserved_ids: [], current_ids: [] },
  };
}

describe("portable import review", () => {
  it("rejects noncanonical surrounding whitespace before review", () => {
    expect(() => parsePortableImportDocument({
      ...document,
      media_items: [{ ...document.media_items[0], title: " Movie A " }],
    })).toThrow("canonical surrounding whitespace");
  });

  it.each([
    ["1.0", "preserve", "preserve"],
    ["1.1", "preserve", "preserve"],
    ["1.2", "preserve", "preserve"],
    ["1.3", "preserve", "preserve"],
    ["1.4", "replace", "preserve"],
    ["1.5", "replace", "preserve"],
    ["1.6", "replace", "merge"],
    ["1.7", "replace", "merge"],
    ["1.8", "replace", "merge"],
  ] as const)("accepts schema %s collection modes", async (schemaVersion, proposalMode, recommendationMode) => {
    const versionedDocument: Record<string, unknown> = {
      schema_version: schemaVersion, exported_on: "2026-07-18", media_items: [],
    };
    if (["1.3", "1.4", "1.5", "1.6", "1.7", "1.8"].includes(schemaVersion)) versionedDocument.creators = [];
    if (["1.4", "1.5", "1.6", "1.7", "1.8"].includes(schemaVersion)) versionedDocument.proposals = [];
    if (["1.6", "1.7", "1.8"].includes(schemaVersion)) versionedDocument.recommendations = [];
    if (schemaVersion === "1.8") versionedDocument.capture_proposals = [];
    const payload: Record<string, unknown> = responsePayload();
    payload.schema_version = schemaVersion;
    payload.media_items = { mode: "merge", entries: [], preserved_ids: [], current_ids: [] };
    payload.creators = { mode: "merge", entries: [], preserved_ids: [], current_ids: [] };
    payload.proposals = { mode: proposalMode, entries: [], preserved_ids: [], current_ids: [] };
    payload.recommendations = { mode: recommendationMode, entries: [], preserved_ids: [], current_ids: [] };
    if (schemaVersion === "1.8") payload.capture_proposals = { mode: "replace", entries: [], preserved_ids: [], current_ids: [] };
    const currentExport = schemaVersion === "1.8"
      ? { ...emptyCurrentExport, schema_version: "1.8", capture_proposals: [] }
      : emptyCurrentExport;
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(payload), {
        status: 200, headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify(currentExport), {
        status: 200, headers: { "Content-Type": "application/json" },
      })));

    await expect(reviewImportLibrary(versionedDocument as never)).resolves.toMatchObject({
      schema_version: schemaVersion,
    });
  });

  it("preserves nonempty v1.8 typed capture proposals during review snapshot binding", async () => {
    const captureProposal = {
      id: "capture-rating-1",
      kind: "rating_event",
      target_media_item_id: "movie-a",
      rating_event: { event_id: "rating-event-1", score: 8, rated_on: "2026-07-18", provisional: false },
      source_context: "fixture:typed-rating",
      provenance: "user_explicit",
      confidence: 1,
      review_state: "needs_review",
      conflict_state: "none",
      idempotency_key: "capture:typed-rating-1",
      proposed_on: "2026-07-18",
    };
    const typedDocument = {
      ...document,
      schema_version: "1.8" as const,
      capture_proposals: [captureProposal],
    };
    const payload = responsePayload() as Record<string, unknown>;
    payload.schema_version = "1.8";
    payload.capture_proposals = {
      mode: "replace",
      entries: [{ id: captureProposal.id, label: captureProposal.id, action: "create", before: null, after: captureProposal }],
      preserved_ids: [],
      current_ids: [],
    };
    const currentExport = {
      ...emptyCurrentExport,
      schema_version: "1.8",
      media_items: [{ id: "movie-preserved", title: "Preserved", category: "movie", status: "planned" }],
      capture_proposals: [],
    };
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(payload), {
        status: 200, headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify(currentExport), {
        status: 200, headers: { "Content-Type": "application/json" },
      })));

    await expect(reviewImportLibrary(typedDocument)).resolves.toMatchObject({
      schema_version: "1.8",
      capture_proposals: { entries: [{ id: captureProposal.id }] },
    });
  });

  it("posts the exact document and accepts a strict deterministic review", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(responsePayload()), {
        status: 200, headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        schema_version: "1.6", exported_on: "2026-07-18", creators: [],
        media_items: [{ id: "movie-preserved", title: "Preserved", category: "movie", status: "planned" }],
        proposals: [], recommendations: [],
      }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const review = await reviewImportLibrary(document);

    expect(review.review_token).toBe("a".repeat(64));
    expect(review.media_items.entries[0]).toMatchObject({ id: "movie-a", action: "create" });
    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/import/review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(document),
    });
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/export");
  });

  it("rejects fabricated current IDs even when every incoming effect is create-only", async () => {
    const payload = responsePayload();
    payload.media_items.preserved_ids = ["movie-fabricated"];
    payload.media_items.current_ids = ["movie-fabricated"];
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(payload), {
        status: 200, headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        schema_version: "1.6", exported_on: "2026-07-18", creators: [],
        media_items: [], proposals: [], recommendations: [],
      }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(reviewImportLibrary(document)).rejects.toThrow("could not be verified");
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/export");
  });

  it.each([
    ["media", "media_items", "movie-current", "Current movie", "Fabricated movie"],
    ["creator", "creators", "creator-current", "Current creator", "Fabricated creator"],
    ["proposal removal", "proposals", "proposal-current", "Current proposal", "Fabricated proposal"],
    ["recommendation replay", "recommendations", "recommendation-current", "Current recommendation", "Fabricated recommendation"],
  ] as const)("rejects a fabricated canonical %s before snapshot", async (_name, collectionName, id, currentText, fabricatedText) => {
    const currentRecords: Record<string, Record<string, unknown>[]> = {
      media_items: [{ id, title: currentText, category: "movie", status: "planned" }],
      creators: [{ id, name: currentText }],
      proposals: [{ id, target_media_item_id: "movie-current", kind: "metadata", metadata_field: "title", metadata_value: currentText, source_context: "sheet:1", confidence: 0.8, proposed_on: "2026-07-18" }],
      recommendations: [{ id, media_item_id: "movie-current", recommended_on: "2026-07-18", source: "user", rationale: currentText }],
    };
    const normalizedBefore: Record<string, unknown> = collectionName === "media_items"
      ? { ...currentRecords[collectionName][0], title: fabricatedText, aliases: [], terms: [], relationships: [], credits: [], rating_history: [], progress_records: [], observations: [] }
      : collectionName === "creators"
        ? { ...currentRecords[collectionName][0], name: fabricatedText, aliases: [] }
        : collectionName === "proposals"
          ? { ...currentRecords[collectionName][0], metadata_value: fabricatedText, review_state: "needs_review" }
          : { ...currentRecords[collectionName][0], rationale: fabricatedText, evidence: [], outcomes: [] };
    const action = collectionName === "proposals" ? "remove" : collectionName === "recommendations" ? "replay" : "unchanged";
    const payload = responsePayload() as unknown as Record<string, unknown>;
    payload.media_items = collectionName === "media_items"
      ? { mode: "merge", entries: [{ id, label: fabricatedText, action, before: normalizedBefore, after: normalizedBefore }], preserved_ids: [], current_ids: [id] }
      : { mode: "merge", entries: responsePayload().media_items.entries, preserved_ids: ["movie-current"], current_ids: ["movie-current"] };
    payload.creators = collectionName === "creators"
      ? { mode: "merge", entries: [{ id, label: fabricatedText, action, before: normalizedBefore, after: normalizedBefore }], preserved_ids: [], current_ids: [id] }
      : { mode: "merge", entries: [], preserved_ids: ["creator-current"], current_ids: ["creator-current"] };
    payload.proposals = collectionName === "proposals"
      ? { mode: "replace", entries: [{ id, label: id, action, before: normalizedBefore, after: null }], preserved_ids: [], current_ids: [id] }
      : { mode: "replace", entries: [], preserved_ids: [], current_ids: ["proposal-current"] };
    payload.recommendations = collectionName === "recommendations"
      ? { mode: "merge", entries: [{ id, label: id, action, before: normalizedBefore, after: normalizedBefore }], preserved_ids: [], current_ids: [id] }
      : { mode: "merge", entries: [], preserved_ids: ["recommendation-current"], current_ids: ["recommendation-current"] };
    const currentExport = { schema_version: "1.6", exported_on: "2026-07-18", creators: currentRecords.creators, media_items: currentRecords.media_items, proposals: currentRecords.proposals, recommendations: currentRecords.recommendations };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify(currentExport), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(reviewImportLibrary(document)).rejects.toThrow("could not be verified");
    if (collectionName === "proposals") {
      expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/export");
    }
  });

  it("sends the bound review token when applying the reviewed document", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ imported: 1 }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await importLibrary(document, "review token");

    expect(fetchMock).toHaveBeenCalledWith("/api/import?review_token=review%20token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(document),
    });
  });

  it("classifies a stale reviewed import for safe re-review", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: "import review is stale; review the document again",
    }), { status: 409, headers: { "Content-Type": "application/json", "X-Error-Code": "import-review-stale" } })));

    await expect(importLibrary(document, "a".repeat(64))).rejects.toBeInstanceOf(ImportReviewStaleError);
  });

  it("does not misclassify an unmarked import conflict as stale", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: "recommendation identity conflict",
    }), { status: 409, headers: { "Content-Type": "application/json" } })));

    const request = importLibrary(document, "a".repeat(64));
    await expect(request).rejects.toThrow("recommendation identity conflict");
    await expect(request).rejects.not.toBeInstanceOf(ImportReviewStaleError);
  });

  it("accepts backend code-point ordering for Unicode stable IDs", async () => {
    const unicodeItems = ["\uE000", "😀"].map((id) => ({
      id, title: id, category: "movie", status: "planned",
    }));
    const unicodeDocument = { ...document, media_items: unicodeItems };
    const payload = responsePayload();
    payload.media_items.entries = unicodeItems.map((item) => ({
      id: item.id, label: item.title, action: "create", before: null,
      after: {
        ...item, aliases: [], terms: [], relationships: [], credits: [], rating_history: [],
        progress_records: [], observations: [],
      },
    }));
    payload.media_items.preserved_ids = [];
    payload.media_items.current_ids = [];
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(payload), {
        status: 200, headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify(emptyCurrentExport), {
        status: 200, headers: { "Content-Type": "application/json" },
      })));

    await expect(reviewImportLibrary(unicodeDocument)).resolves.toMatchObject({ review_schema_version: "1.0" });
  });

  it.each([
    ["wrong review schema", { review_schema_version: "2.0" }],
    ["wrong schema", { schema_version: "1.5" }],
    ["bad token", { review_token: "not-a-token" }],
    ["undeclared field", { surprise: true }],
    ["omitted incoming record", {
      media_items: {
        mode: "merge", entries: [], preserved_ids: ["movie-preserved"], current_ids: ["movie-preserved"],
      },
    }],
    ["fabricated extra incoming record", {
      media_items: {
        mode: "merge",
        entries: [
          ...responsePayload().media_items.entries,
          { id: "movie-extra", label: "Extra", action: "create", before: null, after: { id: "movie-extra", title: "Extra" } },
        ],
        preserved_ids: ["movie-preserved"], current_ids: ["movie-preserved"],
      },
    }],
    ["fabricated incoming snapshot field", {
      media_items: {
        ...responsePayload().media_items,
        entries: [{
          ...responsePayload().media_items.entries[0],
          after: { ...responsePayload().media_items.entries[0].after, fabricated: true },
        }],
      },
    }],
    ["altered incoming snapshot", {
      media_items: {
        ...responsePayload().media_items,
        entries: [{
          id: "movie-a", label: "Movie A", action: "create", before: null,
          after: { id: "movie-a", title: "Fabricated" },
        }],
      },
    }],
    ["incomplete current partition", {
      media_items: {
        ...responsePayload().media_items,
        current_ids: ["movie-hidden", "movie-preserved"],
      },
    }],
    ["fabricated removal label", {
      proposals: {
        mode: "replace",
        entries: [{
          id: "proposal-remove", label: "Fabricated label", action: "remove",
          before: { id: "proposal-remove" }, after: null,
        }],
        preserved_ids: [], current_ids: ["proposal-remove"],
      },
    }],
    ["mismatched snapshot ID", {
      media_items: {
        mode: "merge",
        entries: [{
          id: "movie-a", label: "Movie A", action: "create", before: null,
          after: { id: "movie-other", title: "Movie A" },
        }],
        preserved_ids: [],
        current_ids: [],
      },
    }],
    ["duplicate preserved ID", {
      media_items: {
        mode: "merge",
        entries: responsePayload().media_items.entries,
        preserved_ids: ["movie-preserved", "movie-preserved"],
        current_ids: ["movie-preserved"],
      },
    }],
    ["blocked review without a reason", {
      can_import: false,
      blocking_reasons: [],
      recommendations: {
        mode: "merge",
        entries: [{
          id: "recommendation-conflict", label: "recommendation-conflict", action: "conflict",
          before: { id: "recommendation-conflict", rationale: "Current" },
          after: { id: "recommendation-conflict", rationale: "Incoming" },
        }],
        preserved_ids: [],
        current_ids: ["recommendation-conflict"],
      },
    }],
    ["conflict marked importable", {
      can_import: true,
      blocking_reasons: ["recommendation id conflict: 'recommendation-conflict'"],
      recommendations: {
        mode: "merge",
        entries: [{
          id: "recommendation-conflict",
          label: "recommendation-conflict",
          action: "conflict",
          before: { id: "recommendation-conflict", rationale: "Current" },
          after: { id: "recommendation-conflict", rationale: "Incoming" },
        }],
        preserved_ids: [],
        current_ids: ["recommendation-conflict"],
      },
    }],
  ])("rejects %s", async (_name, override) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ...responsePayload(),
      ...override,
    }), { status: 200, headers: { "Content-Type": "application/json" } })));

    await expect(reviewImportLibrary(document)).rejects.toThrow("could not be verified");
  });
});
