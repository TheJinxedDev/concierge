import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";

import {
  appendRecommendationOutcome,
  listMedia,
  listRecommendations,
  type RecommendationOutcomeEvent,
  type RecommendationOutcomeKind,
  type RecommendationRecord,
} from "./api";
import { type MediaItem, type ObservationValue } from "./media";

function labelFor(value: string) {
  return value.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
}

function localCalendarDate() {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function observationsFor(item: MediaItem): ObservationValue[] {
  return Array.isArray(item.observations) ? item.observations : [];
}

function nextOutcomeId(recommendation: RecommendationRecord, kind: RecommendationOutcomeKind) {
  const existing = new Set((recommendation.outcomes ?? []).map((event) => event.id));
  let sequence = 1;
  let candidate = `outcome-${recommendation.id}-${kind}-${sequence}`;
  while (existing.has(candidate)) {
    sequence += 1;
    candidate = `outcome-${recommendation.id}-${kind}-${sequence}`;
  }
  return candidate;
}

interface OutcomeDraft {
  recommendationId: string;
  kind: RecommendationOutcomeKind;
  recordedOn: string;
  text: string;
  successful: "" | "true" | "false";
}

function availableOutcomeKinds(recommendation: RecommendationRecord): RecommendationOutcomeKind[] {
  const tried = (recommendation.outcomes ?? []).some((outcome) => outcome.kind === "tried");
  const initial = (recommendation.outcomes ?? []).some((outcome) => outcome.kind === "initial_response");
  return [
    ...(!initial ? ["initial_response" as const] : []),
    ...(!tried ? ["tried" as const] : []),
    ...(tried ? ["opinion" as const, "success_assessment" as const] : []),
  ];
}

function emptyDraft(recommendation: RecommendationRecord): OutcomeDraft {
  return {
    recommendationId: recommendation.id,
    kind: availableOutcomeKinds(recommendation)[0],
    recordedOn: localCalendarDate(),
    text: "",
    successful: "",
  };
}

interface RecommendationJournalProps {
  disabled?: boolean;
  onPendingChange?: (pending: boolean) => boolean | void;
}

export function RecommendationJournal({ disabled = false, onPendingChange }: RecommendationJournalProps) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [recommendations, setRecommendations] = useState<RecommendationRecord[]>([]);
  const [media, setMedia] = useState<MediaItem[]>([]);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [draft, setDraft] = useState<OutcomeDraft | null>(null);
  const [draftAttempted, setDraftAttempted] = useState(false);
  const [pending, setPending] = useState(false);
  const loadRequestIdRef = useRef(0);
  const loadingRef = useRef(false);
  const pendingRef = useRef(false);

  const mediaById = useMemo(() => new Map(media.map((item) => [item.id, item])), [media]);
  const draftDirty = Boolean(draft && (
    draft.kind !== "initial_response" || draft.text || draft.successful || draft.recordedOn !== localCalendarDate()
  ));

  useEffect(() => {
    if (!draftDirty) return undefined;
    const protectDraft = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", protectDraft);
    return () => window.removeEventListener("beforeunload", protectDraft);
  }, [draftDirty]);

  async function loadJournal() {
    const requestId = ++loadRequestIdRef.current;
    if (onPendingChange?.(true) === false) {
      setError("Another library operation is already in progress.");
      return;
    }
    loadingRef.current = true;
    setLoading(true);
    setError("");
    setMessage("");
    try {
      const [history, allMedia] = await Promise.all([
        listRecommendations(),
        listMedia("", undefined, true),
      ]);
      const canonical = new Map(allMedia.map((item) => [item.id, item]));
      for (const recommendation of history) {
        if (!canonical.has(recommendation.media_item_id)) {
          throw new Error("A recommendation target could not be resolved.");
        }
        for (const reference of recommendation.evidence ?? []) {
          const item = canonical.get(reference.media_item_id);
          const observation = item && observationsFor(item).find(
            (candidate) => candidate.id === reference.observation_id,
          );
          if (!observation ||
              (observation.review_state ?? "accepted") !== "accepted" ||
              (observation.privacy ?? "assistant_readable") !== "assistant_readable") {
            throw new Error("Recommendation evidence could not be resolved exactly.");
          }
        }
      }
      if (loadRequestIdRef.current !== requestId) return;
      setRecommendations(history);
      setMedia(allMedia);
    } catch (caught) {
      if (loadRequestIdRef.current !== requestId) return;
      setError(caught instanceof Error ? caught.message : "Recommendation history could not be loaded.");
    } finally {
      if (loadRequestIdRef.current === requestId) {
        loadingRef.current = false;
        setLoading(false);
        onPendingChange?.(false);
      }
    }
  }

  function toggleJournal() {
    if (pending) return;
    if (open) {
      if (draftDirty && !window.confirm("Discard the unfinished outcome draft?")) return;
      loadRequestIdRef.current += 1;
      if (loadingRef.current) {
        loadingRef.current = false;
        onPendingChange?.(false);
      }
      setLoading(false);
      setOpen(false);
      setDraft(null);
      setDraftAttempted(false);
      setError("");
      setMessage("");
      return;
    }
    setOpen(true);
    void loadJournal();
  }

  function startOutcome(recommendation: RecommendationRecord) {
    setDraft(emptyDraft(recommendation));
    setDraftAttempted(false);
    setError("");
    setMessage("");
  }

  async function recordOutcome(event: FormEvent) {
    event.preventDefault();
    if (!draft || pendingRef.current) return;
    setDraftAttempted(true);
    const recommendation = recommendations.find((candidate) => candidate.id === draft.recommendationId);
    if (!recommendation) return;
    const latestDate = recommendation.outcomes?.at(-1)?.recorded_on ?? recommendation.recommended_on;
    const textRequired = draft.kind === "initial_response" || draft.kind === "opinion";
    const successRequired = draft.kind === "success_assessment";
    if (!draft.recordedOn || draft.recordedOn < latestDate ||
        (textRequired && !draft.text.trim()) || (successRequired && !draft.successful)) return;

    const outcome: RecommendationOutcomeEvent = {
      id: nextOutcomeId(recommendation, draft.kind),
      kind: draft.kind,
      recorded_on: draft.recordedOn,
      ...(textRequired || (draft.kind === "success_assessment" && draft.text.trim())
        ? { text: draft.text.trim() }
        : {}),
      ...(successRequired ? { successful: draft.successful === "true" } : {}),
    };
    pendingRef.current = true;
    if (onPendingChange?.(true) === false) {
      pendingRef.current = false;
      return;
    }
    setPending(true);
    setError("");
    setMessage("");
    try {
      const receipt = await appendRecommendationOutcome(recommendation.id, outcome);
      setRecommendations((current) => current.map((candidate) =>
        candidate.id === receipt.recommendation.id ? receipt.recommendation : candidate
      ));
      setDraft(null);
      setDraftAttempted(false);
      setMessage(receipt.created ? "Outcome recorded in recommendation history." : "That exact outcome was already recorded.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The recommendation outcome could not be recorded.");
    } finally {
      pendingRef.current = false;
      setPending(false);
      onPendingChange?.(false);
    }
  }

  return (
    <section className="recommendation-journal-panel" aria-label="Recommendation history">
      <button
        className="recommendation-journal-toggle"
        type="button"
        aria-expanded={open}
        disabled={pending || (disabled && !loading)}
        onClick={toggleJournal}
      >
        {loading ? "Loading recommendation journal…" : open ? "Close recommendation journal" : "Open recommendation journal"}
      </button>
      {open ? (
        <div className="recommendation-journal" role="region" aria-label="Recommendation journal">
          <div className="recommendation-journal-heading">
            <div>
              <p className="eyebrow">Factual history</p>
              <h2>Recommendation journal</h2>
            </div>
            <span>{recommendations.length}</span>
          </div>
          <p>Recorded occurrences and user-confirmed outcomes. Nothing here generates or ranks recommendations.</p>
          {loading ? <p role="status">Resolving canonical targets and exact evidence…</p> : null}
          {error ? <p className="field-error" role="alert">{error}</p> : null}
          {message ? <p className="recommendation-status" role="status">{message}</p> : null}
          {!loading && !error && recommendations.length === 0 ? <p>No recommendations recorded yet.</p> : null}
          <ol className="recommendation-list">
            {recommendations.map((recommendation) => {
              const target = mediaById.get(recommendation.media_item_id)!;
              const availableKinds = availableOutcomeKinds(recommendation);
              const activeDraft = draft?.recommendationId === recommendation.id ? draft : null;
              const latestOutcomeDate = recommendation.outcomes?.at(-1)?.recorded_on ?? recommendation.recommended_on;
              const dateInvalid = Boolean(draftAttempted && activeDraft && (
                !activeDraft.recordedOn || activeDraft.recordedOn < latestOutcomeDate
              ));
              const noteInvalid = Boolean(draftAttempted && activeDraft &&
                (activeDraft.kind === "initial_response" || activeDraft.kind === "opinion") &&
                !activeDraft.text.trim());
              const assessmentInvalid = Boolean(draftAttempted && activeDraft &&
                activeDraft.kind === "success_assessment" && !activeDraft.successful);
              return (
                <li key={recommendation.id}>
                  <article className="recommendation-card">
                    <header>
                      <div>
                        <span>{recommendation.recommended_on} · {labelFor(recommendation.source)}</span>
                        <h3>{target.title}</h3>
                      </div>
                      {target.archived_on ? <span className="recommendation-archived">Archived target</span> : null}
                    </header>
                    <p>{recommendation.rationale}</p>
                    {recommendation.confidence !== undefined ? (
                      <p className="recommendation-meta">Stated confidence {Math.round(recommendation.confidence * 100)}%</p>
                    ) : null}
                    {(recommendation.evidence ?? []).length ? (
                      <div className="recommendation-evidence">
                        <h4>Recorded basis</h4>
                        <ul>
                          {(recommendation.evidence ?? []).map((reference) => {
                            const evidenceItem = mediaById.get(reference.media_item_id)!;
                            const observation = observationsFor(evidenceItem).find(
                              (candidate) => candidate.id === reference.observation_id,
                            )!;
                            return (
                              <li key={`${reference.media_item_id}:${reference.observation_id}`}>
                                <span>{evidenceItem.title} · {labelFor(observation.dimension)}</span>
                                <p>{observation.text}</p>
                              </li>
                            );
                          })}
                        </ul>
                      </div>
                    ) : null}
                    <div className="recommendation-outcomes">
                      <h4>Outcome history</h4>
                      {(recommendation.outcomes ?? []).length ? (
                        <ol>
                          {(recommendation.outcomes ?? []).map((outcome) => (
                            <li key={outcome.id}>
                              <strong>{labelFor(outcome.kind)} · {outcome.recorded_on}</strong>
                              {outcome.text ? <p>{outcome.text}</p> : null}
                              {outcome.successful !== undefined ? <span>{outcome.successful ? "Successful" : "Not successful"}</span> : null}
                            </li>
                          ))}
                        </ol>
                      ) : <p>No outcomes recorded.</p>}
                    </div>
                    {draft?.recommendationId === recommendation.id ? (
                      <form className="recommendation-outcome-form" onSubmit={(event) => void recordOutcome(event)}>
                        <div className="field">
                          <label htmlFor={`outcome-kind-${recommendation.id}`}>Outcome kind</label>
                          <select
                            id={`outcome-kind-${recommendation.id}`}
                            value={draft.kind}
                            disabled={pending || disabled}
                            onChange={(event) => setDraft({
                              ...draft,
                              kind: event.target.value as RecommendationOutcomeKind,
                              text: "",
                              successful: "",
                            })}
                          >
                            {availableKinds.map((kind) => <option key={kind} value={kind}>{labelFor(kind)}</option>)}
                          </select>
                        </div>
                        <div className="field">
                          <label htmlFor={`outcome-date-${recommendation.id}`}>Recorded date</label>
                          <input
                            id={`outcome-date-${recommendation.id}`}
                            type="date"
                            value={draft.recordedOn}
                            min={latestOutcomeDate}
                            disabled={pending || disabled}
                            aria-invalid={dateInvalid}
                            aria-describedby={dateInvalid ? `outcome-date-error-${recommendation.id}` : undefined}
                            onChange={(event) => setDraft({ ...draft, recordedOn: event.target.value })}
                          />
                          {dateInvalid ? (
                            <p id={`outcome-date-error-${recommendation.id}`} className="field-error">
                              {draft.recordedOn ? `Use ${latestOutcomeDate} or a later date.` : "Choose a recorded date."}
                            </p>
                          ) : null}
                        </div>
                        {draft.kind === "initial_response" || draft.kind === "opinion" || draft.kind === "success_assessment" ? (
                          <div className="field field-wide">
                            <label htmlFor={`outcome-text-${recommendation.id}`}>
                              {draft.kind === "success_assessment" ? "Explanation (optional)" : "Outcome note"}
                            </label>
                            <textarea
                              id={`outcome-text-${recommendation.id}`}
                              value={draft.text}
                              disabled={pending || disabled}
                              aria-invalid={noteInvalid}
                              aria-describedby={noteInvalid ? `outcome-text-error-${recommendation.id}` : undefined}
                              onChange={(event) => setDraft({ ...draft, text: event.target.value })}
                            />
                            {noteInvalid ? (
                              <p id={`outcome-text-error-${recommendation.id}`} className="field-error">
                                Enter an outcome note.
                              </p>
                            ) : null}
                          </div>
                        ) : null}
                        {draft.kind === "success_assessment" ? (
                          <div className="field">
                            <label htmlFor={`outcome-success-${recommendation.id}`}>Assessment</label>
                            <select
                              id={`outcome-success-${recommendation.id}`}
                              value={draft.successful}
                              disabled={pending || disabled}
                              aria-invalid={assessmentInvalid}
                              aria-describedby={assessmentInvalid ? `outcome-success-error-${recommendation.id}` : undefined}
                              onChange={(event) => setDraft({ ...draft, successful: event.target.value as OutcomeDraft["successful"] })}
                            >
                              <option value="">Choose an assessment</option>
                              <option value="true">Successful</option>
                              <option value="false">Not successful</option>
                            </select>
                            {assessmentInvalid ? (
                              <p id={`outcome-success-error-${recommendation.id}`} className="field-error">
                                Choose whether the recommendation was successful.
                              </p>
                            ) : null}
                          </div>
                        ) : null}
                        <div className="recommendation-outcome-actions">
                          <button className="primary" type="submit" disabled={pending || disabled}>
                            {pending ? "Recording outcome…" : "Record outcome"}
                          </button>
                          <button className="secondary" type="button" disabled={pending || disabled} onClick={() => setDraft(null)}>
                            Cancel
                          </button>
                        </div>
                      </form>
                    ) : (
                      <button
                        className="secondary recommendation-record-outcome"
                        type="button"
                        disabled={pending || disabled || availableKinds.length === 0}
                        aria-label={`Record outcome for ${target.title}`}
                        onClick={() => startOutcome(recommendation)}
                      >
                        Record outcome
                      </button>
                    )}
                  </article>
                </li>
              );
            })}
          </ol>
        </div>
      ) : null}
    </section>
  );
}
