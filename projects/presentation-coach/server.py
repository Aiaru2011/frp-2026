#!/usr/bin/env python3
import json
import mimetypes
import os
import re
import shutil
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
STORAGE = os.path.join(ROOT, "storage")
FEEDBACK = os.path.join(ROOT, "feedback")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
MAX_UPLOAD = 800 * 1024 * 1024
INDEX = "index.html"


def safe_name(title):
    s = re.sub(r'[\x00-\x1f\x7f/\\:*?"<>|]+', " ", str(title or ""))
    s = re.sub(r"\s+", " ", s).strip(" .")
    s = s[:70].strip(" .")
    return s or "rehearsal"


def feedback_path(rehearsal_id):
    rid = str(rehearsal_id or "")
    if not ID_RE.match(rid):
        return None
    return os.path.join(FEEDBACK, "rehearsal-%s.json" % rid)


def folder_path(name):
    p = os.path.realpath(os.path.join(STORAGE, name))
    if os.path.dirname(p) != os.path.realpath(STORAGE):
        return None
    return p


def unique_folder(title, when):
    base = safe_name(title)
    if not os.path.exists(folder_path(base)):
        return base
    stamp = re.sub(r"[^0-9]", "-", str(when or "")[:16]).strip("-")
    stamp = re.sub(r"-+", "-", stamp)
    cand = safe_name((base + " " + stamp) if stamp else base)
    if not os.path.exists(folder_path(cand)):
        return cand
    n = 2
    while os.path.exists(folder_path(cand + " (" + str(n) + ")")):
        n += 1
    return cand + " (" + str(n) + ")"


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def list_rehearsals():
    out = []
    if not os.path.isdir(STORAGE):
        return out
    for name in sorted(os.listdir(STORAGE)):
        p = folder_path(name)
        if not p or not os.path.isdir(p):
            continue
        meta = os.path.join(p, "rehearsal.json")
        if not os.path.isfile(meta):
            continue
        try:
            rec = read_json(meta)
        except (OSError, ValueError):
            continue
        rec["folder"] = name
        rec["id"] = name
        fp = feedback_path(rec.get("rehearsalId"))
        rec["hasFeedback"] = bool(fp and os.path.isfile(fp))
        try:
            rec["videoBytes"] = os.path.getsize(os.path.join(p, rec.get("videoFile") or "video.webm"))
        except OSError:
            rec["hasVideo"] = False
            rec["videoBytes"] = 0
        out.append(rec)
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "PresentationCoach/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if self.path.startswith("/api/"):
            sys.stderr.write("%s %s\n" % (self.command, self.path))

    def end_headers(self):
        origin = self.headers.get("Origin") or ""
        allowed = bool(re.match(r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$", origin)) or \
            origin == "https://kitan-a.com"
        if allowed:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Range")
        self.send_header("Access-Control-Expose-Headers", "Content-Length, Content-Range, Accept-Ranges")
        self.send_header("Access-Control-Max-Age", "600")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self):
        path = urlparse(self.path).path
        code = 200 if path in ("/", "/api/rehearsals") else 404
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def send_json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, code, text):
        body = str(text).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def query_folder(self):
        q = parse_qs(urlparse(self.path).query)
        name = (q.get("folder") or [""])[0]
        p = folder_path(name) if name else None
        if not p or not os.path.isdir(p):
            return None
        return name, p

    def read_body(self):
        length = self.headers.get("Content-Length")
        if length is None:
            return None
        try:
            length = int(length)
        except ValueError:
            return None
        if length < 0 or length > MAX_UPLOAD:
            return None
        chunks = []
        left = length
        while left > 0:
            chunk = self.rfile.read(min(1024 * 1024, left))
            if not chunk:
                break
            chunks.append(chunk)
            left -= len(chunk)
        return b"".join(chunks)

    def serve_file(self, path, ctype=None):
        try:
            size = os.path.getsize(path)
            f = open(path, "rb")
        except OSError:
            self.send_text(404, "Not found")
            return
        ctype = ctype or mimetypes.guess_type(path)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with f:
            shutil.copyfileobj(f, self.wfile)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/rehearsals":
            self.send_json(200, {"rehearsals": list_rehearsals()})
            return
        if path == "/api/feedback":
            q = parse_qs(urlparse(self.path).query)
            fp = feedback_path((q.get("id") or [""])[0])
            if not fp or not os.path.isfile(fp):
                self.send_text(404, "No feedback was saved for this rehearsal")
                return
            self.serve_file(fp, "application/json; charset=utf-8")
            return
        if path == "/api/video":
            found = self.query_folder()
            if not found:
                self.send_text(404, "Rehearsal not found")
                return
            name, p = found
            try:
                rec = read_json(os.path.join(p, "rehearsal.json"))
            except (OSError, ValueError):
                self.send_text(404, "Rehearsal not found")
                return
            fname = rec.get("videoFile") or "video.webm"
            video = os.path.join(p, os.path.basename(fname))
            if not os.path.isfile(video):
                self.send_text(404, "No video saved for this rehearsal")
                return
            ctype = "video/mp4" if fname.endswith(".mp4") else "video/webm"
            self.serve_file(video, ctype)
            return
        rel = unquote(path).lstrip("/") or INDEX
        if rel.startswith("storage") or rel.startswith(".") or "/." in rel:
            self.send_text(404, "Not found")
            return
        full = os.path.realpath(os.path.join(ROOT, rel))
        if full != os.path.realpath(ROOT) and not full.startswith(os.path.realpath(ROOT) + os.sep):
            self.send_text(404, "Not found")
            return
        if os.path.isdir(full):
            full = os.path.join(full, INDEX)
        self.serve_file(full)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/feedback":
            self.save_feedback()
            return
        if path != "/api/rehearsal":
            self.send_text(404, "Not found")
            return
        body = self.read_body()
        if body is None:
            self.send_text(411, "A Content-Length body is required")
            return
        try:
            rec = json.loads(body.decode("utf-8"))
        except ValueError:
            self.send_text(400, "The rehearsal data is not valid JSON")
            return
        if not isinstance(rec, dict):
            self.send_text(400, "The rehearsal data must be an object")
            return
        name = rec.get("folder") or ""
        p = folder_path(name) if name else None
        if not p or not os.path.isdir(p):
            name = unique_folder(rec.get("title"), rec.get("when"))
            p = folder_path(name)
            os.makedirs(p, exist_ok=True)
        rec["folder"] = name
        rec["id"] = name
        meta = os.path.join(p, "rehearsal.json")
        if os.path.isfile(meta):
            try:
                old = read_json(meta)
            except (OSError, ValueError):
                old = {}
            if old.get("videoFile") and not rec.get("videoFile"):
                rec["videoFile"] = old["videoFile"]
            if old.get("rehearsalId") and not rec.get("rehearsalId"):
                rec["rehearsalId"] = old["rehearsalId"]
            if old.get("hasVideo"):
                rec["hasVideo"] = True
        rec["savedAt"] = rec.get("savedAt") or ""
        write_json(meta, rec)
        self.send_json(200, {"folder": name, "id": name, "hasVideo": bool(rec.get("hasVideo"))})

    def save_feedback(self):
        body = self.read_body()
        if body is None:
            self.send_text(411, "A Content-Length body is required")
            return
        try:
            rec = json.loads(body.decode("utf-8"))
        except ValueError:
            self.send_text(400, "The feedback data is not valid JSON")
            return
        if not isinstance(rec, dict):
            self.send_text(400, "The feedback data must be an object")
            return
        regen = bool(rec.pop("regenerate", False))
        rid = rec.get("rehearsalId") or rec.get("id")
        fp = feedback_path(rid)
        if not fp:
            self.send_text(400, "A valid rehearsalId is required")
            return
        if os.path.isfile(fp) and not regen:
            try:
                size = os.path.getsize(fp)
            except OSError:
                size = 0
            self.send_json(200, {"ok": True, "id": rid, "existing": True,
                                 "file": os.path.basename(fp), "bytes": size})
            return
        os.makedirs(FEEDBACK, exist_ok=True)
        rec["rehearsalId"] = rid
        write_json(fp, rec)
        self.send_json(200, {"ok": True, "id": rid, "existing": False,
                             "file": os.path.basename(fp), "bytes": os.path.getsize(fp)})

    def do_PUT(self):
        path = urlparse(self.path).path
        if path != "/api/video":
            self.send_text(404, "Not found")
            return
        found = self.query_folder()
        if not found:
            self.send_text(404, "Create the rehearsal before sending its video")
            return
        name, p = found
        body = self.read_body()
        if body is None:
            self.send_text(411, "A Content-Length body is required")
            return
        if not body:
            self.send_text(400, "The video is empty")
            return
        ctype = (self.headers.get("Content-Type") or "video/webm").lower()
        fname = "video.mp4" if "mp4" in ctype else "video.webm"
        tmp = os.path.join(p, fname + ".part")
        with open(tmp, "wb") as f:
            f.write(body)
        os.replace(tmp, os.path.join(p, fname))
        meta = os.path.join(p, "rehearsal.json")
        try:
            rec = read_json(meta)
        except (OSError, ValueError):
            rec = {"title": name, "when": ""}
        rec["folder"] = name
        rec["id"] = name
        rec["hasVideo"] = True
        rec["videoFile"] = fname
        rec["videoBytes"] = len(body)
        write_json(meta, rec)
        self.send_json(200, {"folder": name, "file": fname, "bytes": len(body)})

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path == "/api/rehearsals":
            removed = 0
            if os.path.isdir(STORAGE):
                for name in os.listdir(STORAGE):
                    p = folder_path(name)
                    if p and os.path.isdir(p):
                        shutil.rmtree(p, ignore_errors=True)
                        removed += 1
            if os.path.isdir(FEEDBACK):
                for name in os.listdir(FEEDBACK):
                    fp = os.path.join(FEEDBACK, name)
                    if os.path.isfile(fp) and name.startswith("rehearsal-") and name.endswith(".json"):
                        try:
                            os.remove(fp)
                        except OSError:
                            pass
            self.send_json(200, {"removed": removed})
            return
        if path == "/api/feedback":
            q = parse_qs(urlparse(self.path).query)
            fp = feedback_path((q.get("id") or [""])[0])
            if not fp:
                self.send_text(400, "A valid rehearsalId is required")
                return
            existed = os.path.isfile(fp)
            if existed:
                os.remove(fp)
            self.send_json(200, {"removed": os.path.basename(fp), "existed": existed})
            return
        if path != "/api/rehearsal":
            self.send_text(404, "Not found")
            return
        found = self.query_folder()
        if not found:
            self.send_text(404, "Rehearsal not found")
            return
        name, p = found
        rid = ""
        try:
            with open(os.path.join(p, "rehearsal.json"), encoding="utf-8") as f:
                rid = (json.load(f) or {}).get("rehearsalId") or ""
        except (OSError, ValueError):
            rid = ""
        shutil.rmtree(p, ignore_errors=True)
        fp = feedback_path(rid)
        if fp and os.path.isfile(fp):
            try:
                os.remove(fp)
            except OSError:
                pass
        self.send_json(200, {"removed": name, "feedbackRemoved": bool(fp and not os.path.isfile(fp))})


def main():
    port = 8010
    for arg in sys.argv[1:]:
        if arg.isdigit():
            port = int(arg)
    os.makedirs(STORAGE, exist_ok=True)
    os.makedirs(FEEDBACK, exist_ok=True)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    print("Presentation Coach: http://127.0.0.1:%d/  (storage: %s)" % (port, STORAGE), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
