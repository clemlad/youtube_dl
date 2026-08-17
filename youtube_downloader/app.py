import os
import json
import uuid
import threading
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_from_directory

from downloader import (
    search_videos, search_playlists, get_playlist_info,
    download_by_url, find_existing_mp3, MUSIC_FOLDER,
)

app = Flask(__name__)

_MUSIC_ABS = os.path.abspath(MUSIC_FOLDER)

_jobs:             dict[str, dict]             = {}
_pl_jobs:          dict[str, dict]             = {}
_cancel_events:    dict[str, threading.Event]  = {}
_pl_cancel_events: dict[str, threading.Event]  = {}

PLAYLISTS_FILE = Path("playlists.json")

# Protège toute séquence lire-modifier-écrire sur playlists.json.
# Sans ça, deux téléchargements de playlist simultanés s'écrasent mutuellement.
_playlists_lock = threading.Lock()


# ── Playlist file helpers ────────────────────────────────────────────────────

def _load() -> dict:
    if PLAYLISTS_FILE.exists():
        try:
            return json.loads(PLAYLISTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save(data: dict) -> None:
    PLAYLISTS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _ensure_playlist(name: str) -> None:
    """Crée la playlist si absente, de façon atomique."""
    with _playlists_lock:
        data = _load()
        if name not in data:
            data[name] = []
            _save(data)


def _append_track(name: str, filename: str) -> None:
    """Ajoute un titre à une playlist, de façon atomique."""
    if not name or not filename:
        return
    with _playlists_lock:
        data = _load()
        data.setdefault(name, [])
        if filename not in data[name]:
            data[name].append(filename)
            _save(data)


# ── Pages ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ── Search ───────────────────────────────────────────────────────────────────

@app.route("/search")
def search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify([])
    return jsonify(search_videos(query, count=12))


@app.route("/search-playlists")
def search_playlists_route():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify([])
    return jsonify(search_playlists(query, count=4))


# ── Single-track download ────────────────────────────────────────────────────

@app.route("/download", methods=["POST"])
def start_download():
    data  = request.get_json(silent=True) or {}
    url   = data.get("url",   "").strip()
    title = data.get("title", "").strip()
    if not url:
        return jsonify({"error": "URL manquante"}), 400

    if title:
        existing = find_existing_mp3(title)
        if existing:
            return jsonify({"already_done": True, "title": title, "filename": existing})

    job_id       = str(uuid.uuid4())
    cancel_event = threading.Event()
    _jobs[job_id]          = {"status": "pending", "title": None, "error": None}
    _cancel_events[job_id] = cancel_event

    def run():
        try:
            result = download_by_url(url, cancel_event=cancel_event)
            if result:
                _jobs[job_id] = {"status": "done", "title": result["title"], "error": None}
            elif cancel_event.is_set():
                _jobs[job_id] = {"status": "cancelled", "title": None, "error": None}
            else:
                _jobs[job_id] = {"status": "error", "title": None, "error": "Téléchargement échoué"}
        except Exception as e:
            # Sans ce filet, une exception tue le thread et le job reste
            # bloqué en "pending" : le front boucle indéfiniment.
            _jobs[job_id] = {"status": "error", "title": None, "error": str(e)}

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/status/<job_id>")
def job_status(job_id):
    job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job introuvable"}), 404
    return jsonify(job)


# ── Playlist download ────────────────────────────────────────────────────────

@app.route("/download-playlist", methods=["POST"])
def download_playlist():
    data  = request.get_json(silent=True) or {}
    url   = data.get("url", "").strip()
    name  = data.get("name", "").strip()
    if not url:
        return jsonify({"error": "URL manquante"}), 400

    job_id       = str(uuid.uuid4())
    cancel_event = threading.Event()
    _pl_jobs[job_id] = {
        "status": "pending", "name": name, "total": 0, "done": 0,
        "current": "", "tracks": [], "failed": [], "error": None,
    }
    _pl_cancel_events[job_id] = cancel_event

    def run():
        try:
            pl_info = get_playlist_info(url)
            entries = pl_info.get("entries", [])
            pl_name = name or pl_info.get("title") or "Playlist importée"

            _pl_jobs[job_id].update({
                "status": "downloading", "name": pl_name, "total": len(entries),
            })

            _ensure_playlist(pl_name)

            for i, entry in enumerate(entries):
                if cancel_event.is_set():
                    break

                title = entry.get("title") or "Titre inconnu"
                _pl_jobs[job_id]["current"] = title
                _pl_jobs[job_id]["done"]    = i

                existing = find_existing_mp3(title)
                if existing:
                    _append_track(pl_name, existing)
                    _pl_jobs[job_id]["tracks"].append(existing)
                    _pl_jobs[job_id]["done"] = i + 1
                    continue

                result = download_by_url(entry["url"], cancel_event=cancel_event)
                if result and not cancel_event.is_set():
                    filename = result["filename"]
                    _append_track(pl_name, filename)
                    _pl_jobs[job_id]["tracks"].append(filename)
                    _pl_jobs[job_id]["done"] = i + 1
                elif not cancel_event.is_set():
                    _pl_jobs[job_id]["failed"].append(title)

            _pl_jobs[job_id]["status"] = "cancelled" if cancel_event.is_set() else "done"
            _pl_jobs[job_id]["done"]   = len(_pl_jobs[job_id]["tracks"])

        except Exception as e:
            _pl_jobs[job_id]["status"] = "error"
            _pl_jobs[job_id]["error"]  = str(e)

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/playlist-status/<job_id>")
def playlist_status(job_id):
    job = _pl_jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job introuvable"}), 404
    return jsonify(job)


@app.route("/cancel/<job_id>", methods=["POST"])
def cancel_job(job_id):
    event = _cancel_events.get(job_id)
    if event:
        event.set()
    return jsonify({"ok": True})


@app.route("/cancel-playlist/<job_id>", methods=["POST"])
def cancel_playlist_job(job_id):
    event = _pl_cancel_events.get(job_id)
    if event:
        event.set()
    return jsonify({"ok": True})


# ── Library ──────────────────────────────────────────────────────────────────

@app.route("/library")
def library():
    if not os.path.exists(_MUSIC_ABS):
        return jsonify([])
    files = sorted(f for f in os.listdir(_MUSIC_ABS) if f.lower().endswith(".mp3"))
    return jsonify(files)


@app.route("/music/<path:filename>")
def serve_music(filename):
    return send_from_directory(_MUSIC_ABS, filename)


# ── Local playlists CRUD ─────────────────────────────────────────────────────

@app.route("/playlists")
def get_playlists():
    with _playlists_lock:
        return jsonify(_load())


@app.route("/playlists", methods=["POST"])
def create_playlist():
    name = (request.get_json(silent=True) or {}).get("name", "").strip()
    if not name:
        return jsonify({"error": "Nom vide"}), 400
    with _playlists_lock:
        data = _load()
        if name in data:
            return jsonify({"error": "Playlist déjà existante"}), 409
        data[name] = []
        _save(data)
    return jsonify({"ok": True})


@app.route("/playlists/<name>", methods=["DELETE"])
def delete_playlist(name):
    with _playlists_lock:
        data = _load()
        data.pop(name, None)
        _save(data)
    return jsonify({"ok": True})


@app.route("/playlists/<name>/tracks", methods=["POST"])
def add_to_playlist(name):
    track = (request.get_json(silent=True) or {}).get("track", "").strip()
    with _playlists_lock:
        data = _load()
        if name not in data:
            return jsonify({"error": "Playlist introuvable"}), 404
        if track and track not in data[name]:
            data[name].append(track)
            _save(data)
    return jsonify({"ok": True})


@app.route("/playlists/<name>/tracks", methods=["DELETE"])
def remove_from_playlist(name):
    track = (request.get_json(silent=True) or {}).get("track", "").strip()
    with _playlists_lock:
        data = _load()
        if name in data and track in data[name]:
            data[name].remove(track)
            _save(data)
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
