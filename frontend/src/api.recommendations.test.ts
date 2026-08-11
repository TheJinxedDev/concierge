import { afterEach, describe, expect, it, vi } from "vitest";

import {
  appendRecommendationOutcome,
  listRecommendations,
  type RecommendationOutcomeEvent,
} from "./api";

const recommendation = {
  id: "recommendation-mind-game-2026-07-18",
  media_item_id: "movie-mind-game-2004",
  recommended_on: "2026-07-18",
  source: "assistant",
  source_context: "conversation:42",
  rationale: "Its elastic visual language may fit.",
  evidence: [
    {
      media_item_id: "anime-flcl-2000",
      observation_id: "obs-flcl-visuals",
    },
  ],
  confidence: 0.72,
  outcomes: [
    { id: "outcome-tried", kind: "tried", recorded_on: "2026-07-19" },
  ],
};

afterEach(() => vi.unstubAllGlobals());

describe("recommendation transport", () => {
  it("loads exact chronological immutable recommendation history", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify([recommendation]), { status: 200 }),
    ));

    await expect(listRecommendations()).resolves.toEqual([recommendation]);
    expect(fetch).toHaveBeenCalledWith("/api/recommendations");
  });

  it("rejects malformed, duplicate, and out-of-order recommendation history", async () => {
    for (const payload of [
      [{ ...recommendation, mystery: true }],
      [recommendation, recommendation],
      [
        { ...recommendation, id: "recommendation-later", recommended_on: "2026-07-20" },
        { ...recommendation, id: "recommendation-earlier", recommended_on: "2026-07-19" },
      ],
      [{ ...recommendation, outcomes: [{ id: "opinion", kind: "opinion", recorded_on: "2026-07-19", text: "Untried." }] }],
    ]) {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
        new Response(JSON.stringify(payload), { status: 200 }),
      ));
      await expect(listRecommendations()).rejects.toThrow(
        "Recommendation history could not be verified.",
      );
    }
  });

  it("appends one exact outcome and verifies the evolved receipt", async () => {
    const outcome: RecommendationOutcomeEvent = {
      id: "outcome-opinion",
      kind: "opinion",
      recorded_on: "2026-07-20",
      text: "Inventive and exhausting in equal measure.",
    };
    const evolved = { ...recommendation, outcomes: [...recommendation.outcomes, outcome] };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ created: true, recommendation: evolved }), { status: 201 }),
    ));

    await expect(
      appendRecommendationOutcome(recommendation.id, outcome),
    ).resolves.toEqual({ created: true, recommendation: evolved });
    expect(fetch).toHaveBeenCalledWith(
      `/api/recommendations/${recommendation.id}/outcomes`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(outcome),
      },
    );
  });

  it("rejects an impossible append acknowledgement", async () => {
    const outcome: RecommendationOutcomeEvent = {
      id: "outcome-opinion",
      kind: "opinion",
      recorded_on: "2026-07-20",
      text: "Inventive and exhausting in equal measure.",
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ created: true, recommendation }), { status: 201 }),
    ));

    await expect(
      appendRecommendationOutcome(recommendation.id, outcome),
    ).rejects.toThrow("The recommendation outcome receipt could not be verified.");
  });
});
