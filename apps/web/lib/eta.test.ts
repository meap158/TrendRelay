import assert from "node:assert/strict";
import { test } from "node:test";

import {
  estimateBatch,
  estimateOne,
  etaLabel,
  expectedSeconds,
  fit,
  readableSeconds,
  type Finished,
} from "./eta.ts";

/**
 * This workspace's own transcription history, as measured.
 *
 * Not invented numbers: these are real `started_at`/`completed_at` pairs
 * against the real duration of the clip each one read. They are the reason the
 * model is `fixed + rate x media` and not a single ratio - the first row runs
 * at 1.14x and the second at 0.36x, and the difference between them is
 * startup, not noise.
 */
const REAL: Finished[] = [
  { shape: "speech+ocr", mediaSeconds: 12.42, workSeconds: 14.2 },
  { shape: "speech+ocr", mediaSeconds: 220.52, workSeconds: 78.6 },
  { shape: "speech+ocr", mediaSeconds: 144.75, workSeconds: 55.0 },
  { shape: "ocr", mediaSeconds: 144.75, workSeconds: 69.2 },
];

test("a single ratio would be wrong, and the fit knows it", () => {
  const model = fit(REAL, "speech+ocr");

  // The 12s clip cost more than its length; the 220s clip cost a third of it.
  // Only a fixed cost explains both.
  assert.ok(model.fixedSeconds > 5, `expected real startup cost, got ${model.fixedSeconds}`);
  assert.ok(model.perMediaSecond > 0.2 && model.perMediaSecond < 0.6, `${model.perMediaSecond}`);

  // And it reproduces what actually happened, within a few seconds.
  assert.ok(Math.abs(expectedSeconds(model, 12.42)! - 14.2) < 6);
  assert.ok(Math.abs(expectedSeconds(model, 220.52)! - 78.6) < 12);
});

test("shapes are never mixed, because they do not run at the same rate", () => {
  const speech = fit(REAL, "speech+ocr");
  const ocr = fit(REAL, "ocr");

  assert.equal(speech.samples, 3);
  assert.equal(ocr.samples, 1);
  // The one OCR sample is 0.48x and must not have been diluted by the others.
  assert.ok(Math.abs(expectedSeconds(ocr, 144.75)! - 69.2) < 1);
});

test("the user's own example: sixty seconds of video, ten seconds of work", () => {
  // A steady machine with no meaningful startup cost - which is the case the
  // request described, and the fit should land exactly on it.
  const steady: Finished[] = [
    { shape: "burn", mediaSeconds: 60, workSeconds: 10 },
    { shape: "burn", mediaSeconds: 120, workSeconds: 20 },
    { shape: "burn", mediaSeconds: 30, workSeconds: 5 },
  ];

  const model = fit(steady, "burn");

  assert.ok(Math.abs(expectedSeconds(model, 90)! - 15) < 0.5);
  assert.ok(Math.abs(expectedSeconds(model, 240)! - 40) < 1);
});

test("the estimate sharpens as more of the batch finishes", () => {
  // The point of the whole thing: it should get closer to the truth, not just
  // change. Truth here is 0.5x with a 4s startup.
  const truth = (media: number) => 4 + media * 0.5;
  const done: Finished[] = [];
  const errors: number[] = [];

  for (const media of [30, 60, 90, 120, 45, 75]) {
    const model = fit(done, "burn");
    const guess = expectedSeconds(model, media);
    if (guess !== null) errors.push(Math.abs(guess - truth(media)));
    done.push({ shape: "burn", mediaSeconds: media, workSeconds: truth(media) });
  }

  assert.ok(errors.length >= 4, "expected several estimates to compare");
  // The last guess is better than the first, and the tail is close.
  assert.ok(errors.at(-1)! < errors[0]!, `${errors[0]} -> ${errors.at(-1)}`);
  assert.ok(errors.at(-1)! < 2, `still out by ${errors.at(-1)}`);
});

test("one warm-cache outlier cannot make a longer video finish sooner", () => {
  // Two runs of the same clip, one cold and one with the model already loaded.
  // A least-squares line through this data slopes downward.
  const mixed: Finished[] = [
    { shape: "speech", mediaSeconds: 144.75, workSeconds: 22.4 },
    { shape: "speech", mediaSeconds: 220.52, workSeconds: 8.6 },
    { shape: "speech", mediaSeconds: 220.52, workSeconds: 60.0 },
  ];

  const model = fit(mixed, "speech");

  assert.ok(model.perMediaSecond >= 0, "a negative rate was allowed through");
  assert.ok(expectedSeconds(model, 300)! >= expectedSeconds(model, 100)!,
    "a longer clip was estimated to finish sooner");
});

test("nothing finished and no progress means no estimate, not a guess", () => {
  const model = fit([], "burn");

  assert.equal(estimateOne({ shape: "burn", mediaSeconds: 60 }, model), null);
  assert.equal(expectedSeconds(model, 60), null);
});

test("a job with no measured length still gets the median of its kind", () => {
  const done: Finished[] = [
    { shape: "burn", mediaSeconds: null, workSeconds: 30 },
    { shape: "burn", mediaSeconds: null, workSeconds: 40 },
    { shape: "burn", mediaSeconds: null, workSeconds: 50 },
  ];

  const model = fit(done, "burn");

  assert.equal(expectedSeconds(model, null), 40);
  assert.equal(
    estimateOne({ shape: "burn", mediaSeconds: null, elapsedSeconds: 10 }, model)!
      .remainingSeconds,
    30,
  );
});

test("a job's own pace takes over from the model as it progresses", () => {
  // The model expects 100s. This job is visibly slower than that.
  const done: Finished[] = [
    { shape: "burn", mediaSeconds: 100, workSeconds: 100 },
    { shape: "burn", mediaSeconds: 50, workSeconds: 50 },
    { shape: "burn", mediaSeconds: 200, workSeconds: 200 },
  ];
  const model = fit(done, "burn");

  // A tenth done after 40 seconds: on its own pace that is 360s left.
  const early = estimateOne(
    { shape: "burn", mediaSeconds: 100, progress: 0.1, elapsedSeconds: 40 }, model,
  )!;
  // Half done after 200 seconds: 200s left on its own pace.
  const late = estimateOne(
    { shape: "burn", mediaSeconds: 100, progress: 0.5, elapsedSeconds: 200 }, model,
  )!;

  // Early, the model still dominates - closer to 60s left than to 360.
  assert.ok(early.remainingSeconds < 150, `${early.remainingSeconds}`);
  // Late, the job's own pace has won outright.
  assert.ok(Math.abs(late.remainingSeconds - 200) < 1, `${late.remainingSeconds}`);
});

test("a progress fraction near zero is not divided by", () => {
  const model = fit(
    [{ shape: "burn", mediaSeconds: 60, workSeconds: 30 }], "burn",
  );

  const barelyStarted = estimateOne(
    { shape: "burn", mediaSeconds: 60, progress: 0.001, elapsedSeconds: 20 }, model,
  )!;

  // 20s at 0.1% would extrapolate to five and a half hours.
  assert.ok(barelyStarted.remainingSeconds < 60, `${barelyStarted.remainingSeconds}`);
});

test("a finished job has nothing left, whatever the model thinks", () => {
  const model = fit([{ shape: "burn", mediaSeconds: 60, workSeconds: 600 }], "burn");

  const done = estimateOne(
    { shape: "burn", mediaSeconds: 60, progress: 1, elapsedSeconds: 5 }, model,
  )!;

  assert.equal(done.remainingSeconds, 0);
});

// --- a whole batch ------------------------------------------------------------

test("a batch divides by its workers rather than summing", () => {
  const done: Finished[] = [
    { shape: "burn", mediaSeconds: 60, workSeconds: 60 },
    { shape: "burn", mediaSeconds: 120, workSeconds: 120 },
    { shape: "burn", mediaSeconds: 30, workSeconds: 30 },
  ];
  const waiting = Array.from({ length: 8 }, () => ({ shape: "burn", mediaSeconds: 60 }));

  const alone = estimateBatch(waiting, done, 1)!;
  const four = estimateBatch(waiting, done, 4)!;

  assert.ok(Math.abs(alone.remainingSeconds - 480) < 5, `${alone.remainingSeconds}`);
  assert.ok(Math.abs(four.remainingSeconds - 120) < 5, `${four.remainingSeconds}`);
});

test("a batch never finishes before its slowest single item", () => {
  const done: Finished[] = [
    { shape: "burn", mediaSeconds: 60, workSeconds: 60 },
    { shape: "burn", mediaSeconds: 120, workSeconds: 120 },
    { shape: "burn", mediaSeconds: 600, workSeconds: 600 },
  ];

  // One very long item and many workers: parallelism cannot help it.
  const batch = estimateBatch(
    [{ shape: "burn", mediaSeconds: 600 }, { shape: "burn", mediaSeconds: 10 }], done, 16,
  )!;

  assert.ok(batch.remainingSeconds >= 590, `${batch.remainingSeconds}`);
});

test("items nobody can estimate are charged, not treated as free", () => {
  const done: Finished[] = [
    { shape: "burn", mediaSeconds: 60, workSeconds: 60 },
    { shape: "burn", mediaSeconds: 120, workSeconds: 120 },
    { shape: "burn", mediaSeconds: 30, workSeconds: 30 },
  ];

  const known = estimateBatch([{ shape: "burn", mediaSeconds: 60 }], done, 1)!;
  const withStranger = estimateBatch(
    [{ shape: "burn", mediaSeconds: 60 }, { shape: "never-seen", mediaSeconds: null }],
    done, 1,
  )!;

  assert.ok(withStranger.remainingSeconds > known.remainingSeconds,
    "an unestimatable item made the batch look shorter");
});

test("an empty batch says nothing", () => {
  assert.equal(estimateBatch([], [], 1), null);
});

// --- saying it ----------------------------------------------------------------

test("a duration is rounded to the precision it actually has", () => {
  assert.equal(readableSeconds(3), "a few seconds");
  assert.equal(readableSeconds(42), "45s");
  assert.equal(readableSeconds(90), "1m 30s");
  assert.equal(readableSeconds(553), "9m 15s");
  assert.equal(readableSeconds(1800), "30m");
  assert.equal(readableSeconds(7200), "2h");
});

test("the wording says whether it is a measurement or a guess", () => {
  assert.equal(etaLabel(null), "");
  assert.equal(
    etaLabel({ remainingSeconds: 90, confidence: "good", basis: "" }), "1m 30s left",
  );
  assert.equal(
    etaLabel({ remainingSeconds: 90, confidence: "rough", basis: "" }), "about 1m 30s left",
  );
});
