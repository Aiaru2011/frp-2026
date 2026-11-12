// Static hosting (GitHub Pages): there is no server.py and no FRP class server here.
// This file answers the page's storage API ("api/...") from the browser's own IndexedDB, so saved
// rehearsals, videos and feedback keep working, and it explains that speech-to-text and the AI coach
// need the class server. With server.py (localhost, port 8010, or the FRP /proxy/ links) it does nothing.
(function () {
  const p = location.pathname, h = location.hostname;
  const serverMode = /\/proxy\/[^/]+\//.test(p) || location.port === "8010" || h === "localhost" || h === "127.0.0.1";
  if (serverMode) return;

  const API_BASE = new URL("api/", location.href).pathname;
  const CLASS_SERVER = "/proxy/19040/";
  const CLASS_MSG = "Speech-to-text and the AI coach used the FRP class server, which closed after the program. " +
    "On this public page the camera feedback works and rehearsals are saved in your browser.";

  let dbp = null;
  function db() {
    if (!dbp) dbp = new Promise((res, rej) => {
      const r = indexedDB.open("presentationCoach", 1);
      r.onupgradeneeded = () => {
        for (const s of ["rehearsals", "videos", "feedback"]) r.result.createObjectStore(s);
      };
      r.onsuccess = () => res(r.result);
      r.onerror = () => rej(r.error);
    });
    return dbp;
  }
  async function op(store, mode, fn) {
    const d = await db();
    return new Promise((res, rej) => {
      const t = d.transaction(store, mode), s = t.objectStore(store);
      let out;
      Promise.resolve(fn(s)).then((v) => { out = v; });
      t.oncomplete = () => res(out);
      t.onerror = () => rej(t.error);
    });
  }
  const req = (r) => new Promise((res, rej) => { r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });
  const get = (store, key) => op(store, "readonly", (s) => req(s.get(key)));
  const put = (store, key, val) => op(store, "readwrite", (s) => req(s.put(val, key)));
  const del = (store, key) => op(store, "readwrite", (s) => req(s.delete(key)));
  const all = (store) => op(store, "readonly", async (s) => {
    const [keys, vals] = await Promise.all([req(s.getAllKeys()), req(s.getAll())]);
    return keys.map((k, i) => [k, vals[i]]);
  });
  const clear = (store) => op(store, "readwrite", (s) => req(s.clear()));

  const json = (code, data) => new Response(JSON.stringify(data),
    { status: code, headers: { "Content-Type": "application/json; charset=utf-8" } });
  const text = (code, msg) => new Response(msg, { status: code, headers: { "Content-Type": "text/plain; charset=utf-8" } });

  // Same names as server.py: the title, then a date stamp, then (2), (3)...
  function safeName(title) {
    let s = String(title || "").replace(/[\x00-\x1f\x7f/\\:*?"<>|]+/g, " ").replace(/\s+/g, " ").trim().replace(/^[ .]+|[ .]+$/g, "");
    s = s.slice(0, 70).trim().replace(/^[ .]+|[ .]+$/g, "");
    return s || "rehearsal";
  }
  async function uniqueFolder(title, when) {
    const taken = new Set((await all("rehearsals")).map(([k]) => k));
    const base = safeName(title);
    if (!taken.has(base)) return base;
    const stamp = String(when || "").slice(0, 16).replace(/[^0-9]/g, "-").replace(/-+/g, "-").replace(/^-|-$/g, "");
    const cand = safeName(stamp ? base + " " + stamp : base);
    if (!taken.has(cand)) return cand;
    let n = 2;
    while (taken.has(cand + " (" + n + ")")) n++;
    return cand + " (" + n + ")";
  }

  async function handle(url, method, init) {
    const path = url.pathname.slice(API_BASE.length - 1);  // "/rehearsals", "/video", ...
    const q = url.searchParams;
    if (path === "/rehearsals" && method === "GET") {
      const fb = new Set((await all("feedback")).map(([k]) => k));
      const vids = new Map((await all("videos")).map(([k, v]) => [k, v]));
      const list = (await all("rehearsals")).sort((a, b) => (a[0] < b[0] ? -1 : 1)).map(([k, rec]) => {
        const v = vids.get(k);
        return Object.assign({}, rec, { folder: k, id: k, hasFeedback: fb.has(rec.rehearsalId),
          hasVideo: !!v, videoBytes: v ? v.blob.size : 0 });
      });
      return json(200, { rehearsals: list });
    }
    if (path === "/rehearsals" && method === "DELETE") {
      const n = (await all("rehearsals")).length;
      await clear("rehearsals"); await clear("videos"); await clear("feedback");
      return json(200, { removed: n });
    }
    if (path === "/rehearsal" && method === "POST") {
      const rec = JSON.parse(await new Response(init.body).text());
      if (!rec || typeof rec !== "object") return text(400, "The rehearsal data must be an object");
      let name = rec.folder || "";
      const old = name ? await get("rehearsals", name) : undefined;
      if (!old) name = await uniqueFolder(rec.title, rec.when);
      rec.folder = name; rec.id = name;
      if (old) {
        if (old.videoFile && !rec.videoFile) rec.videoFile = old.videoFile;
        if (old.rehearsalId && !rec.rehearsalId) rec.rehearsalId = old.rehearsalId;
        if (old.hasVideo) rec.hasVideo = true;
      }
      rec.savedAt = rec.savedAt || "";
      await put("rehearsals", name, rec);
      return json(200, { folder: name, id: name, hasVideo: !!rec.hasVideo });
    }
    if (path === "/rehearsal" && method === "DELETE") {
      const name = q.get("folder") || "";
      const rec = await get("rehearsals", name);
      if (!rec) return text(404, "Rehearsal not found");
      await del("rehearsals", name); await del("videos", name);
      if (rec.rehearsalId) await del("feedback", rec.rehearsalId);
      return json(200, { removed: name, feedbackRemoved: true });
    }
    if (path === "/video" && method === "PUT") {
      const name = q.get("folder") || "";
      const rec = await get("rehearsals", name);
      if (!rec) return text(404, "Create the rehearsal before sending its video");
      const blob = init.body instanceof Blob ? init.body : await new Response(init.body).blob();
      if (!blob.size) return text(400, "The video is empty");
      const type = blob.type || (init.headers && init.headers["Content-Type"]) || "video/webm";
      const fname = /mp4/i.test(type) ? "video.mp4" : "video.webm";
      await put("videos", name, { blob, type });
      Object.assign(rec, { folder: name, id: name, hasVideo: true, videoFile: fname, videoBytes: blob.size });
      await put("rehearsals", name, rec);
      return json(200, { folder: name, file: fname, bytes: blob.size });
    }
    if (path === "/video" && method === "GET") {
      const v = await get("videos", q.get("folder") || "");
      if (!v) return text(404, "No video saved for this rehearsal");
      return new Response(v.blob, { status: 200, headers: { "Content-Type": v.type } });
    }
    if (path === "/feedback" && method === "GET") {
      const f = await get("feedback", q.get("id") || "");
      return f ? json(200, f) : text(404, "No feedback was saved for this rehearsal");
    }
    if (path === "/feedback" && method === "POST") {
      const rec = JSON.parse(await new Response(init.body).text());
      const regen = !!rec.regenerate; delete rec.regenerate;
      const rid = rec.rehearsalId || rec.id;
      if (!rid) return text(400, "A valid rehearsalId is required");
      const had = await get("feedback", rid);
      if (had && !regen) return json(200, { ok: true, id: rid, existing: true });
      rec.rehearsalId = rid;
      await put("feedback", rid, rec);
      return json(200, { ok: true, id: rid, existing: false });
    }
    if (path === "/feedback" && method === "DELETE") {
      const rid = q.get("id") || "";
      const had = !!(await get("feedback", rid));
      await del("feedback", rid);
      return json(200, { removed: rid, existed: had });
    }
    return text(404, "Not found");
  }

  const realFetch = window.fetch.bind(window);
  window.fetch = async function (input, init) {
    init = init || {};
    const url = new URL(typeof input === "string" || input instanceof URL ? input : input.url, location.href);
    const method = String(init.method || (input && input.method) || "GET").toUpperCase();
    if (url.origin === location.origin && url.pathname.includes(CLASS_SERVER)) return text(503, CLASS_MSG);
    if (url.origin === location.origin && url.pathname.startsWith(API_BASE)) {
      try { return await handle(url, method, init); }
      catch (e) { return text(500, "Browser storage error: " + ((e && e.message) || e)); }
    }
    return realFetch(input, init);
  };

  // <video src> cannot go through fetch: the page asks this for a blob: URL instead.
  window.PCLocalVideoUrl = async function (folder) {
    const v = await get("videos", folder || "");
    return v ? URL.createObjectURL(v.blob) : "";
  };

  document.addEventListener("DOMContentLoaded", () => {
    const n = document.createElement("div");
    n.textContent = "Public version: camera feedback works and your rehearsals are saved only in this browser. " +
      "Speech-to-text and the AI coach needed the FRP class server, so they are off here.";
    n.style.cssText = "background:#fff7d6;color:#5a4a00;font:14px/1.4 system-ui,sans-serif;padding:8px 14px;text-align:center";
    document.body.prepend(n);
  });
})();
