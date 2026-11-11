// JDeck product page: OS-aware download links from GitHub Releases, and the live app previews.
"use strict";

const REPO = "TariqMK/JDeck";
const ASSET = { windows: /windows-x64\.exe$/i, macos: /macos-arm64\.zip$/i, linux: /linux-x64\.tar\.gz$/i };
const OS_NAME = { windows: "Windows", macos: "macOS", linux: "Linux" };
const $ = (id) => document.getElementById(id);
const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// ---------------------------------------------------------------- downloads

function detectOS() {
  const ua = navigator.userAgent;
  if (/Android|iPhone|iPad|iPod/i.test(ua)) return null;
  if (/Windows/i.test(ua)) return "windows";
  if (/Macintosh|Mac OS X/i.test(ua)) return "macos";
  if (/Linux|X11/i.test(ua)) return "linux";
  return null;
}

const os = detectOS();
const hero = $("hero-download");
if (os) {
  hero.textContent = `Download for ${OS_NAME[os]}`;
  document.querySelector(`.platform[data-os="${os}"]`)?.classList.add("mine");
} else {
  hero.href = "#download";
}

fetch(`https://api.github.com/repos/${REPO}/releases/latest`, { headers: { Accept: "application/vnd.github+json" } })
  .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
  .then((rel) => {
    for (const asset of rel.assets || []) {
      for (const [key, pattern] of Object.entries(ASSET)) {
        if (!pattern.test(asset.name)) continue;
        const card = document.querySelector(`.platform[data-os="${key}"]`);
        if (card) {
          card.href = asset.browser_download_url;
          card.querySelector(".size").textContent = ` · ${Math.max(1, Math.round(asset.size / 1048576))} MB`;
        }
        if (key === os) hero.href = asset.browser_download_url;
      }
    }
    const date = new Date(rel.published_at).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });
    $("dl-version").textContent = `${rel.tag_name} · ${date}`;
    $("hero-version").textContent = `${rel.tag_name} · Free and open source`;
    $("app-version").textContent = rel.tag_name;
  })
  .catch(() => { /* Offline, rate limited or no release yet: the links already go to the Releases page. */ });

// ---------------------------------------------------------------- helpers (same formats as the app)

function bytes(n) {
  if (!n || n < 0) return "–";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i === 0 ? 0 : n < 10 ? 1 : 0)} ${units[i]}`;
}
const speed = (n) => (n > 0 ? `${bytes(n)}/s` : "");
function eta(sec) {
  if (!sec || sec < 0 || !isFinite(sec)) return "";
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = Math.floor(sec % 60);
  return h ? `${h}h ${m}m left` : m ? `${m}m ${s}s left` : `${s}s left`;
}
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
const GB = 1024 ** 3, MB = 1024 ** 2;

// ---------------------------------------------------------------- live download list (hero)
// Story: Big Buck Bunny finishes, then Lecture notes starts and finishes. One package has failed.

function startState() {
  return [
    { name: "Big Buck Bunny (4K)", total: 3.4 * GB, loaded: 3.08 * GB, rate: 24 * MB, files: 1, state: "run" },
    { name: "Debian 13 DVD images", total: 7.8 * GB, loaded: 2.2 * GB, rate: 9 * MB, files: 2, state: "run" },
    { name: "Conference talks 2024", total: 1.1 * GB, loaded: 430 * MB, files: 6, state: "fail" },
    { name: "Field recordings, spring 2025", total: 812 * MB, loaded: 812 * MB, files: 24, state: "done" },
    { name: "Lecture notes", total: 240 * MB, loaded: 0, rate: 14 * MB, files: 12, state: "wait" },
  ];
}
let packages = startState();
let finishedAt = 0;

function tickDownloads() {
  for (const p of packages) {
    if (p.state !== "run") continue;
    p.speed = p.rate * (0.85 + Math.random() * 0.3);
    p.loaded = Math.min(p.total, p.loaded + p.speed);
    if (p.loaded >= p.total) {
      p.state = "done";
      const next = packages.find((q) => q.state === "wait");
      if (next) next.state = "run";
    }
  }
  const lecture = packages[4];
  if (lecture.state === "done") {
    finishedAt += 1;
    if (finishedAt > 8) { packages = startState(); finishedAt = 0; }
  }
}

function renderDownloads() {
  const total = packages.reduce((s, p) => s + (p.state === "run" ? p.speed || p.rate : 0), 0);
  $("dl-count").textContent = `· ${packages.length}`;
  $("dl-speed").textContent = speed(total);
  $("app-state").textContent = total > 0 ? "Downloading" : "Idle";
  $("dl-list").replaceChildren(...packages.map((p) => {
    const pct = Math.min(100, (p.loaded / p.total) * 100);
    const li = el("li", "item");
    const top = el("div", "item-top");
    top.append(el("span", "name", p.name), el("span", "link-remove", "Remove"));
    const bar = el("div", p.state === "done" ? "bar done" : "bar");
    const fill = el("span");
    fill.style.width = `${pct.toFixed(1)}%`;
    bar.append(fill);
    const meta = el("div", "item-meta");
    meta.append(el("span", null, `${bytes(p.loaded)} of ${bytes(p.total)}`), el("span", null, `${Math.floor(pct)}%`));
    if (p.state === "run") {
      meta.append(el("span", "opt", speed(p.speed || p.rate)), el("span", null, eta((p.total - p.loaded) / (p.speed || p.rate))));
    }
    const status = { run: ["Downloading", "opt"], done: ["Finished", "done-text"], fail: ["Download failed", "err"], wait: ["Waiting", null] }[p.state];
    meta.append(el("span", status[1], status[0]), el("span", "opt", `${p.files} file${p.files === 1 ? "" : "s"}`));
    li.append(top, bar, meta);
    return li;
  }));
}

// ---------------------------------------------------------------- link finder

const PASTES = [
  "Here are the files from Saturday:\nhttps://example.com/videos/tears-of-steel-4k.mkv\n\nMirror for the photos: https://files.example.net/share/a7Kq2/holiday-photos.zip\n\nSlides are in the same folder as before (https://example.com/videos/tears-of-steel-4k.mkv).\nNotes: ftp://mirror.example.org/pub/lecture-notes.pdf",
  "Season list, parts 1 to 3\n1. https://cdn.example.org/series/s01e01.mkv\n2. https://cdn.example.org/series/s01e02.mkv\n3. https://cdn.example.org/series/s01e03.mkv\nPart 2 again, in case the first one fails: https://cdn.example.org/series/s01e02.mkv",
];
const URL_RE = /(?:https?|ftp):\/\/[^\s<>"'`]+/gi;
const TRAIL = ".,;:!?)]}>'\"";
let pasteIndex = 0;

function cleanLink(raw) {
  let link = raw;
  while (link && TRAIL.includes(link[link.length - 1])) link = link.slice(0, -1);
  return link;
}

function runFinder() {
  const text = PASTES[pasteIndex % PASTES.length];
  pasteIndex += 1;
  const box = $("finder-paste"), list = $("finder-list");
  box.replaceChildren();
  list.replaceChildren();
  $("finder-count").textContent = "";
  const marks = [];
  let last = 0;
  for (const m of text.matchAll(URL_RE)) {
    const link = cleanLink(m[0]);
    box.append(text.slice(last, m.index));
    const mark = el("mark", "pending", link);
    box.append(mark);
    marks.push({ mark, link });
    last = m.index + link.length;
  }
  box.append(text.slice(last));
  const seen = new Set();
  const steps = marks.map(({ mark, link }) => () => {
    if (seen.has(link)) { mark.className = "dup"; return; }
    seen.add(link);
    mark.className = "";
    list.append(el("li", null, link));
    $("finder-count").textContent = `${seen.size} link${seen.size === 1 ? "" : "s"}`;
  });
  if (reduced) { steps.forEach((s) => s()); return; }
  steps.forEach((s, i) => setTimeout(s, 700 + i * 650));
}

// ---------------------------------------------------------------- LinkGrabber

const GRABBER_POOL = [
  { name: "Sintel (1080p)", files: 2, size: 1.3 * GB, host: "example.com" },
  { name: "Photo archive 2023", files: 14, size: 4.6 * GB, host: "files.example.net", offline: 1 },
  { name: "Tears of Steel (4K)", files: 1, size: 6.2 * GB, host: "example.com" },
  { name: "Lecture notes", files: 12, size: 240 * MB, host: "mirror.example.org" },
];
let grabber = GRABBER_POOL.slice(0, 3);
let grabberNext = 3;

function grabberItem(p) {
  const li = el("li", "item");
  const top = el("div", "item-top");
  top.append(el("span", "name", p.name), el("span", "fake-btn", "Start"));
  const meta = el("div", "item-meta");
  meta.append(el("span", null, `${p.files} file${p.files === 1 ? "" : "s"}`), el("span", null, bytes(p.size)));
  if (p.offline) meta.append(el("span", "err", `${p.offline} offline`));
  meta.append(el("span", "opt", p.host));
  li.append(top, meta);
  return li;
}
function renderGrabber() {
  $("lg-count").textContent = `· ${grabber.length}`;
  $("lg-list").replaceChildren(...grabber.map(grabberItem));
}
function cycleGrabber() {
  const first = $("lg-list").firstElementChild;
  if (first) first.classList.add("leaving");  // The first package is started and leaves the list.
  setTimeout(() => {
    grabber.shift();
    grabber.push(GRABBER_POOL[grabberNext % GRABBER_POOL.length]);
    grabberNext += 1;
    renderGrabber();
  }, 320);
}

// ---------------------------------------------------------------- run

renderDownloads();
renderGrabber();
runFinder();

if (!reduced) {
  let seconds = 0;
  setInterval(() => {
    if (document.hidden) return;
    seconds += 1;
    tickDownloads();
    renderDownloads();
    if (seconds % 9 === 0) runFinder();
    if (seconds % 4 === 0) cycleGrabber();
  }, 1000);
}
