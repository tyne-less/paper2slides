// k6 load test: submit PDFs at a constant arrival rate and read each job's status once.
// Env: BASE_URL, PDF_PATH, RATE (requests/s), DURATION (e.g. 60s).
import http from "k6/http";
import { check } from "k6";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8080";
const RATE = Number(__ENV.RATE || 5);
const DURATION = __ENV.DURATION || "60s";
const pdf = new Uint8Array(open(__ENV.PDF_PATH, "b"));

export const options = {
  scenarios: {
    submit: {
      executor: "constant-arrival-rate",
      rate: RATE,
      timeUnit: "1s",
      duration: DURATION,
      preAllocatedVUs: 20,
      maxVUs: 200,
    },
  },
  // Per-endpoint thresholds also make k6 report latency for each endpoint separately.
  thresholds: {
    http_req_failed: ["rate<0.01"],
    "http_req_duration{name:POST /jobs}": ["p(95)<1000"],
    "http_req_duration{name:GET /jobs/{id}}": ["p(95)<500"],
  },
  summaryTrendStats: ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max"],
};

// A PDF comment appended after %%EOF makes every upload's bytes (and so its SHA-256) unique,
// so each request creates a new job instead of returning a deduplicated one.
function uniquePdf() {
  const suffix = `\n% k6 ${__VU}-${__ITER}-${Date.now()}\n`;
  const body = new Uint8Array(pdf.length + suffix.length);
  body.set(pdf);
  for (let i = 0; i < suffix.length; i++) body[pdf.length + i] = suffix.charCodeAt(i);
  return body.buffer;
}

export default function () {
  const res = http.post(
    `${BASE_URL}/jobs`,
    { file: http.file(uniquePdf(), "paper.pdf", "application/pdf") },
    { tags: { name: "POST /jobs" } },
  );
  check(res, { "POST /jobs is 201": (r) => r.status === 201 });
  if (res.status !== 201) {
    console.warn(`POST /jobs -> ${res.status} ${res.error || ""} ${String(res.body).slice(0, 200)}`);
    return;
  }

  const status = http.get(`${BASE_URL}/jobs/${res.json("id")}`, {
    tags: { name: "GET /jobs/{id}" },
  });
  check(status, { "GET /jobs/{id} is 200": (r) => r.status === 200 });
}
