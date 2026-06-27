from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import cv2


NON_PASS_RESULTS = {"FAIL", "UNCERTAIN", "NEED_REVIEW"}


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_name(value: str) -> str:
    keep = []
    for ch in value:
        if ch.isalnum() or ch in {"_", "-", "."}:
            keep.append(ch)
        else:
            keep.append("_")
    return "".join(keep).strip("_") or "item"


def relpath(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def copy_artifact(src: Path, dst_root: Path, final_root: Path, check_id: str) -> str | None:
    if not src.exists() or not src.is_file():
        return None
    dst = dst_root / "assets" / check_id / safe_name(src.name)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return relpath(dst, final_root)


def video_duration_sec(video_path: Path) -> float:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return 0.0
    fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30.0
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    cap.release()
    return frames / fps if fps > 0 else 0.0


def read_frame_at(video_path: Path, time_sec: float) -> tuple[bool, Any, float]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return False, None, 0.0
    fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30.0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    idx = max(0, min(frame_count - 1, int(round(time_sec * fps)))) if frame_count > 0 else 0
    cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
    ok, frame = cap.read()
    actual_time = idx / fps if fps > 0 else time_sec
    cap.release()
    return ok, frame, actual_time


def draw_context_overlay(frame: Any, result: str, check_type: str, time_sec: float | None) -> Any:
    out = frame.copy()
    color = (42, 42, 230) if result == "FAIL" else (0, 170, 255)
    h, w = out.shape[:2]
    cv2.rectangle(out, (0, 0), (w - 1, h - 1), color, 8)
    label = f"{result} | {check_type}"
    if time_sec is not None:
        label += f" | t={time_sec:.1f}s"
    cv2.rectangle(out, (0, 0), (min(w, 1120), 58), (0, 0, 0), -1)
    cv2.putText(out, label, (24, 39), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)
    return out


def make_thumbnail(
    *,
    video_path: Path,
    dst_root: Path,
    final_root: Path,
    check_id: str,
    time_sec: float,
    result: str,
    check_type: str,
) -> str | None:
    ok, frame, actual_time = read_frame_at(video_path, time_sec)
    if not ok:
        return None
    frame = draw_context_overlay(frame, result, check_type, actual_time)
    dst = dst_root / "assets" / check_id / "generated_thumbnail.jpg"
    dst.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(dst), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    return relpath(dst, final_root)


def make_clip(
    *,
    video_path: Path,
    dst_root: Path,
    final_root: Path,
    check_id: str,
    center_sec: float,
    result: str,
    check_type: str,
    window_sec: float = 6.0,
    out_fps: float = 12.0,
    max_width: int = 960,
) -> str | None:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return None
    src_fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30.0
    src_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    duration = src_frames / src_fps if src_fps > 0 else 0.0
    start = max(0.0, center_sec - window_sec / 2.0)
    end = min(duration, center_sec + window_sec / 2.0) if duration > 0 else center_sec + window_sec / 2.0
    start_idx = max(0, int(round(start * src_fps)))
    end_idx = max(start_idx + 1, int(round(end * src_fps)))
    step = max(1, int(round(src_fps / out_fps)))

    cap.set(cv2.CAP_PROP_POS_FRAMES, start_idx)
    ok, first = cap.read()
    if not ok:
        cap.release()
        return None
    scale = min(1.0, max_width / float(first.shape[1]))
    size = (int(round(first.shape[1] * scale)), int(round(first.shape[0] * scale)))
    dst = dst_root / "assets" / check_id / "generated_clip.mp4"
    dst.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(dst), cv2.VideoWriter_fourcc(*"mp4v"), out_fps, size)
    if not writer.isOpened():
        cap.release()
        return None

    idx = start_idx
    while idx < end_idx:
        if idx == start_idx:
            frame = first
            ok = True
        else:
            ok, frame = cap.read()
        if not ok:
            break
        if (idx - start_idx) % step == 0:
            frame = draw_context_overlay(frame, result, check_type, idx / src_fps if src_fps > 0 else None)
            if scale != 1.0:
                frame = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
            writer.write(frame)
        idx += 1
    cap.release()
    writer.release()
    return relpath(dst, final_root)


def parse_time_from_check(check: dict[str, Any], evidence_item: dict[str, Any] | None, video_path: Path) -> float:
    if evidence_item and isinstance(evidence_item.get("time"), (int, float)):
        return float(evidence_item["time"])
    tr = check.get("time_range_sec")
    if isinstance(tr, list) and tr:
        nums = [float(v) for v in tr if isinstance(v, (int, float))]
        if nums:
            return sum(nums) / len(nums)
    if isinstance(tr, dict):
        for key in ("start", "start_sec", "time", "center_sec"):
            if isinstance(tr.get(key), (int, float)):
                return float(tr[key])
        start = tr.get("start_sec", tr.get("start"))
        end = tr.get("end_sec", tr.get("end"))
        if isinstance(start, (int, float)) and isinstance(end, (int, float)):
            return (float(start) + float(end)) / 2.0
    duration = video_duration_sec(video_path)
    if duration <= 0:
        return 8.0
    check_num = 0
    check_id = str(check.get("check_id", ""))
    digits = "".join(ch for ch in check_id if ch.isdigit())
    if digits:
        check_num = int(digits)
    fractions = [0.18, 0.34, 0.50, 0.66, 0.82]
    focus = duration * fractions[check_num % len(fractions)]
    return min(max(1.0, focus), max(1.0, duration - 1.0))


def load_first_evidence_item(final_root: Path, check: dict[str, Any]) -> dict[str, Any] | None:
    for item in check.get("evidence", []) or []:
        p = final_root / item
        if p.name == "evidence_item.json" and p.exists():
            return load_json(p)
    return None


def collect_existing_artifacts(
    *,
    final_root: Path,
    web_root: Path,
    check_id: str,
    check: dict[str, Any],
    evidence_item: dict[str, Any] | None,
) -> list[dict[str, str]]:
    artifacts: list[dict[str, str]] = []
    seen: set[str] = set()
    paths: list[tuple[str, Path]] = []

    for value in check.get("evidence", []) or []:
        p = final_root / value
        kind = "json" if p.suffix == ".json" else "clip" if p.suffix == ".mp4" else "image"
        paths.append((kind, p))

    if evidence_item:
        for art in evidence_item.get("artifacts", []) or []:
            p_value = art.get("path")
            if not p_value:
                continue
            p = Path(p_value)
            kind = str(art.get("kind", "artifact"))
            paths.append((kind, p))

    for kind, src in paths:
        key = str(src.resolve()) if src.exists() else str(src)
        if key in seen:
            continue
        seen.add(key)
        copied = copy_artifact(src, web_root, web_root, check_id)
        if copied:
            media_type = "video" if copied.lower().endswith(".mp4") else "image" if copied.lower().endswith((".jpg", ".jpeg", ".png")) else "json"
            artifacts.append({"kind": kind, "type": media_type, "path": copied})
    return artifacts


def build_report(final_root: Path, web_root: Path) -> dict[str, Any]:
    final_report = load_json(final_root / "final_report.json")
    web_root.mkdir(parents=True, exist_ok=True)

    videos_by_id = {v["video_id"]: v for v in final_report.get("input_videos", [])}
    checks = [c for c in final_report.get("qa_checks", []) if c.get("result") in NON_PASS_RESULTS]

    cards = []
    for check in checks:
        check_id = check["check_id"]
        video = videos_by_id.get(check.get("video_id"), {})
        video_path = Path(video.get("video_path", ""))
        evidence_item = load_first_evidence_item(final_root, check)
        focus_time = parse_time_from_check(check, evidence_item, video_path)
        artifacts = collect_existing_artifacts(
            final_root=final_root,
            web_root=web_root,
            check_id=check_id,
            check=check,
            evidence_item=evidence_item,
        )

        has_image = any(a["type"] == "image" for a in artifacts)
        has_video = any(a["type"] == "video" for a in artifacts)
        generated_thumb = None
        generated_clip = None
        if video_path.exists():
            if not has_image:
                generated_thumb = make_thumbnail(
                    video_path=video_path,
                    dst_root=web_root,
                    final_root=web_root,
                    check_id=check_id,
                    time_sec=focus_time,
                    result=check.get("result", ""),
                    check_type=check.get("check_type", ""),
                )
                if generated_thumb:
                    artifacts.insert(0, {"kind": "generated_thumbnail", "type": "image", "path": generated_thumb})
            if not has_video:
                generated_clip = make_clip(
                    video_path=video_path,
                    dst_root=web_root,
                    final_root=web_root,
                    check_id=check_id,
                    center_sec=focus_time,
                    result=check.get("result", ""),
                    check_type=check.get("check_type", ""),
                )
                if generated_clip:
                    artifacts.append({"kind": "generated_clip", "type": "video", "path": generated_clip})

        conditions = (check.get("decision_trace") or {}).get("conditions", [])
        cards.append(
            {
                "check_id": check_id,
                "video_id": check.get("video_id"),
                "video_name": Path(video.get("video_path", check.get("video_id", ""))).name,
                "source_video_path": video.get("video_path"),
                "case_name": Path(video.get("video_path", "")).parent.name if video.get("video_path") else None,
                "check_type": check.get("check_type"),
                "result": check.get("result"),
                "severity": check.get("severity"),
                "confidence": check.get("confidence"),
                "reason": check.get("reason"),
                "focus_time_sec": focus_time,
                "rule": check.get("rule", {}),
                "observed": check.get("observed", {}),
                "conditions": conditions,
                "thresholds_used": (check.get("decision_trace") or {}).get("thresholds_used", {}),
                "final_decision_reason": (check.get("decision_trace") or {}).get("final_decision_reason"),
                "trace_links": check.get("trace_links", {}),
                "uncertainty": check.get("uncertainty"),
                "artifacts": artifacts,
                "generated": {
                    "thumbnail": generated_thumb,
                    "clip": generated_clip,
                },
            }
        )

    result_counts = Counter(c["result"] for c in cards)
    type_counts: dict[str, Counter] = defaultdict(Counter)
    case_counts: dict[str, Counter] = defaultdict(Counter)
    for c in cards:
        type_counts[c["check_type"]][c["result"]] += 1
        case_counts[c["case_name"] or "unknown"][c["result"]] += 1

    return {
        "title": "CrossFire QA Visual Report",
        "source_final_report": str((final_root / "final_report.json").resolve()),
        "overall_result": final_report.get("overall_result"),
        "summary": final_report.get("summary", {}),
        "web_summary": {
            "shown_checks": len(cards),
            "shown_result_counts": dict(result_counts),
            "shown_by_check_type": {k: dict(v) for k, v in sorted(type_counts.items())},
            "shown_by_case": {k: dict(v) for k, v in sorted(case_counts.items())},
            "excluded_results": ["PASS"],
        },
        "run_reproducibility": final_report.get("run_reproducibility", {}),
        "checks": cards,
    }


INDEX_HTML = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CrossFire QA Visual Report</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <header class="topbar">
    <div>
      <p class="eyebrow">CrossFire QA</p>
      <h1>Visual Failure Report</h1>
    </div>
    <div class="overall" id="overallResult"></div>
  </header>
  <main>
    <section class="summary-grid" id="summaryGrid"></section>
    <section class="toolbar">
      <div class="segmented" role="tablist" aria-label="Result filter">
        <button class="active" data-filter="ALL">All</button>
        <button data-filter="FAIL">Fail</button>
        <button data-filter="UNCERTAIN">Uncertain</button>
        <button data-filter="NEED_REVIEW">Needs Review</button>
      </div>
      <span id="visibleCount"></span>
    </section>
    <section class="cards" id="cards"></section>
  </main>
  <script src="data/report_web.js"></script>
  <script src="app.js"></script>
</body>
</html>
"""


STYLE_CSS = """:root {
  color-scheme: dark;
  --bg: #111315;
  --panel: #1a1d20;
  --panel-2: #20252a;
  --text: #f2f3f5;
  --muted: #a8b0ba;
  --line: #343a40;
  --fail: #ff5c5c;
  --uncertain: #ffbf47;
  --review: #5cc8ff;
  --pass: #58d68d;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 14px/1.5 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
.topbar {
  min-height: 156px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 32px clamp(20px, 4vw, 56px);
  border-bottom: 1px solid var(--line);
  background: #171a1d;
}
.eyebrow { margin: 0 0 8px; color: var(--muted); text-transform: uppercase; letter-spacing: .08em; font-size: 12px; }
h1 { margin: 0; font-size: clamp(32px, 5vw, 56px); line-height: 1; letter-spacing: 0; }
main { width: min(1440px, 100%); margin: 0 auto; padding: 24px clamp(16px, 3vw, 40px) 56px; }
.overall {
  min-width: 148px;
  text-align: center;
  border: 1px solid var(--line);
  background: var(--panel);
  padding: 18px 22px;
  border-radius: 8px;
  font-size: 24px;
  font-weight: 800;
}
.overall.FAIL { color: var(--fail); border-color: color-mix(in srgb, var(--fail), var(--line) 45%); }
.summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 18px;
}
.metric {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 16px;
}
.metric strong { display: block; font-size: 28px; line-height: 1.1; }
.metric span { color: var(--muted); }
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin: 20px 0;
}
.segmented { display: inline-flex; gap: 4px; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 4px; }
button {
  appearance: none;
  border: 0;
  color: var(--muted);
  background: transparent;
  padding: 9px 12px;
  border-radius: 6px;
  cursor: pointer;
}
button.active { color: var(--text); background: var(--panel-2); }
.cards { display: grid; gap: 18px; }
.card {
  display: grid;
  grid-template-columns: minmax(320px, 48%) minmax(0, 1fr);
  gap: 18px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 16px;
}
.media {
  display: grid;
  gap: 10px;
  align-content: start;
}
.media img, .media video {
  width: 100%;
  max-height: 480px;
  object-fit: contain;
  background: #050607;
  border: 1px solid var(--line);
  border-radius: 6px;
}
.thumb-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(100px, 1fr));
  gap: 8px;
}
.thumb-row img { max-height: 100px; }
.details { min-width: 0; }
.card-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
.badge { border-radius: 999px; padding: 5px 9px; font-weight: 800; font-size: 12px; }
.badge.FAIL { color: #220000; background: var(--fail); }
.badge.UNCERTAIN { color: #231600; background: var(--uncertain); }
.badge.NEED_REVIEW { color: #001822; background: var(--review); }
.check-id { color: var(--muted); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
h2 { margin: 0 0 8px; font-size: 22px; letter-spacing: 0; }
.reason { color: var(--text); font-size: 16px; margin: 10px 0 14px; }
.meta {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin: 12px 0;
}
.meta div, .condition, .trace {
  background: var(--panel-2);
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 10px;
}
.label { display: block; color: var(--muted); font-size: 12px; margin-bottom: 2px; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; overflow-wrap: anywhere; }
.conditions { display: grid; gap: 8px; margin-top: 12px; }
.condition strong { display: block; margin-bottom: 4px; }
.condition .result { color: var(--uncertain); }
.trace a { color: #9ad7ff; text-decoration: none; }
.trace a:hover { text-decoration: underline; }
pre {
  overflow: auto;
  white-space: pre-wrap;
  background: #111417;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 10px;
  color: var(--muted);
}
@media (max-width: 900px) {
  .topbar { align-items: flex-start; flex-direction: column; }
  .summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .card { grid-template-columns: 1fr; }
}
@media (max-width: 560px) {
  .summary-grid, .meta { grid-template-columns: 1fr; }
  .toolbar { align-items: flex-start; flex-direction: column; }
  .segmented { width: 100%; overflow-x: auto; }
}
"""


APP_JS = """const report = window.QA_WEB_REPORT;
let activeFilter = "ALL";

function fmt(value, digits = 3) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toFixed(digits);
}

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  Object.entries(attrs).forEach(([key, value]) => {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  });
  children.forEach(child => node.appendChild(typeof child === "string" ? document.createTextNode(child) : child));
  return node;
}

function renderSummary() {
  const overall = document.getElementById("overallResult");
  overall.textContent = report.overall_result || "UNKNOWN";
  overall.className = `overall ${report.overall_result || ""}`;

  const counts = report.web_summary?.shown_result_counts || {};
  const grid = document.getElementById("summaryGrid");
  grid.innerHTML = "";
  [
    ["Shown Checks", report.web_summary?.shown_checks ?? 0],
    ["Fail", counts.FAIL || 0],
    ["Uncertain", counts.UNCERTAIN || 0],
    ["Needs Review", counts.NEED_REVIEW || 0],
  ].forEach(([label, value]) => {
    grid.appendChild(el("div", {class: "metric"}, [
      el("strong", {text: String(value)}),
      el("span", {text: label}),
    ]));
  });
}

function primaryMedia(artifacts) {
  const video = artifacts.find(a => a.type === "video");
  const image = artifacts.find(a => a.type === "image");
  const media = el("div", {class: "media"});
  if (video) {
    media.appendChild(el("video", {src: video.path, controls: "", preload: "metadata"}));
  } else if (image) {
    media.appendChild(el("img", {src: image.path, alt: "Evidence image"}));
  }
  const images = artifacts.filter(a => a.type === "image").slice(0, 4);
  if (images.length) {
    const row = el("div", {class: "thumb-row"});
    images.forEach(item => row.appendChild(el("img", {src: item.path, alt: item.kind})));
    media.appendChild(row);
  }
  return media;
}

function renderConditions(conditions) {
  const wrap = el("div", {class: "conditions"});
  conditions.forEach(c => {
    wrap.appendChild(el("div", {class: "condition"}, [
      el("strong", {text: c.condition || "condition"}),
      el("span", {class: "label", text: `expected: ${JSON.stringify(c.expected)} / observed: ${JSON.stringify(c.observed)}`}),
      el("span", {class: "result", text: `${c.result || "-"} · confidence ${fmt(c.confidence)}`}),
      c.note ? el("div", {class: "label", text: c.note}) : el("span"),
    ]));
  });
  return wrap;
}

function renderTraceLinks(trace) {
  const reports = trace?.module_reports || {};
  const wrap = el("div", {class: "trace"});
  wrap.appendChild(el("span", {class: "label", text: "Module Reports"}));
  Object.entries(reports).forEach(([name, path]) => {
    wrap.appendChild(el("div", {}, [el("a", {href: `../${path}`, target: "_blank", text: name})]));
  });
  return wrap;
}

function renderCards() {
  const cards = document.getElementById("cards");
  cards.innerHTML = "";
  const filtered = report.checks.filter(c => activeFilter === "ALL" || c.result === activeFilter);
  document.getElementById("visibleCount").textContent = `${filtered.length} checks`;

  filtered.forEach(check => {
    const details = el("div", {class: "details"}, [
      el("div", {class: "card-head"}, [
        el("span", {class: `badge ${check.result}`, text: check.result}),
        el("span", {class: "check-id", text: check.check_id}),
      ]),
      el("h2", {text: check.check_type}),
      el("p", {class: "reason", text: check.reason || ""}),
      el("div", {class: "meta"}, [
        el("div", {}, [el("span", {class: "label", text: "Case"}), el("span", {text: check.case_name || "-"})]),
        el("div", {}, [el("span", {class: "label", text: "Confidence"}), el("span", {text: fmt(check.confidence)})]),
        el("div", {}, [el("span", {class: "label", text: "Focus Time"}), el("span", {text: `${fmt(check.focus_time_sec, 1)}s`})]),
        el("div", {}, [el("span", {class: "label", text: "Severity"}), el("span", {text: check.severity || "-"})]),
      ]),
      el("div", {class: "meta"}, [
        el("div", {}, [el("span", {class: "label", text: "Video"}), el("span", {class: "mono", text: check.video_name || "-"})]),
        el("div", {}, [el("span", {class: "label", text: "Rule"}), el("span", {text: check.rule?.rule_id || "-"})]),
      ]),
      renderConditions(check.conditions || []),
      renderTraceLinks(check.trace_links || {}),
      el("pre", {text: JSON.stringify({observed: check.observed, uncertainty: check.uncertainty}, null, 2)}),
    ]);
    cards.appendChild(el("article", {class: "card"}, [primaryMedia(check.artifacts || []), details]));
  });
}

document.querySelectorAll("[data-filter]").forEach(button => {
  button.addEventListener("click", () => {
    activeFilter = button.dataset.filter;
    document.querySelectorAll("[data-filter]").forEach(b => b.classList.toggle("active", b === button));
    renderCards();
  });
});

renderSummary();
renderCards();
"""


def write_web_files(web_root: Path, report: dict[str, Any]) -> None:
    (web_root / "data").mkdir(parents=True, exist_ok=True)
    write_json(web_root / "data" / "report_web.json", report)
    (web_root / "data" / "report_web.js").write_text(
        "window.QA_WEB_REPORT = " + json.dumps(report, ensure_ascii=False, indent=2) + ";\n",
        encoding="utf-8",
    )
    (web_root / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (web_root / "style.css").write_text(STYLE_CSS, encoding="utf-8")
    (web_root / "app.js").write_text(APP_JS, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a static visual web report for non-PASS QA checks.")
    parser.add_argument("--run-dir", required=True, help="Pipeline output directory containing final_report.json.")
    parser.add_argument("--out", default="", help="Output web directory. Defaults to RUN_DIR/web_report.")
    args = parser.parse_args()

    final_root = Path(args.run_dir).expanduser().resolve()
    web_root = Path(args.out).expanduser().resolve() if args.out else final_root / "web_report"
    report = build_report(final_root, web_root)
    write_web_files(web_root, report)
    print(json.dumps({
        "web_report": str(web_root / "index.html"),
        "data": str(web_root / "data" / "report_web.json"),
        "shown_checks": report["web_summary"]["shown_checks"],
        "shown_result_counts": report["web_summary"]["shown_result_counts"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
