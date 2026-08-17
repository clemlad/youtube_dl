import os
import urllib.parse
import static_ffmpeg
import yt_dlp
from yt_dlp.utils import sanitize_filename

static_ffmpeg.add_paths()

MUSIC_FOLDER = "./musiques"


def find_existing_mp3(title: str | None, output_folder: str = MUSIC_FOLDER) -> str | None:
    """Return the filename if an MP3 for this title already exists, else None."""
    if not title or not os.path.isdir(output_folder):
        return None
    candidate = sanitize_filename(title, restricted=False) + ".mp3"
    if os.path.isfile(os.path.join(output_folder, candidate)):
        return candidate
    lower = candidate.lower()
    for f in os.listdir(output_folder):
        if f.lower().endswith(".mp3") and f.lower() == lower:
            return f
    return None


def search_videos(query: str, count: int = 12) -> list[dict]:
    ydl_opts = {"quiet": True, "no_warnings": True, "extract_flat": True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch{count}:{query}", download=False)
            entries = (info.get("entries") or []) if info else []
            results = []
            for e in entries:
                if not e:
                    continue
                video_id = e.get("id") or ""
                if not video_id:
                    continue
                results.append({
                    "id": video_id,
                    "title": e.get("title") or "Inconnu",
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "duration": _fmt_duration(e.get("duration")),
                    "channel": e.get("channel") or e.get("uploader") or "",
                    "thumbnail": f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
                    "views": _fmt_views(e.get("view_count")),
                })
            return results
    except Exception as e:
        print(f"Erreur de recherche : {e}")
        return []


def search_playlists(query: str, count: int = 4) -> list[dict]:
    """Search YouTube for playlists matching the query."""
    url = (
        "https://www.youtube.com/results"
        f"?search_query={urllib.parse.quote(query)}"
        "&sp=EgIQAw%3D%3D"  # YouTube playlist filter
    )
    ydl_opts = {"extract_flat": True, "quiet": True, "no_warnings": True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return []
            entries = (info.get("entries") or [])[:count]
            results = []
            for e in entries:
                if not e:
                    continue
                pl_id = e.get("id") or ""
                if not pl_id:
                    continue
                thumbnail = ""
                thumbs = e.get("thumbnails") or []
                if thumbs:
                    thumbnail = (thumbs[-1] or {}).get("url") or ""
                elif e.get("thumbnail"):
                    thumbnail = e["thumbnail"]
                results.append({
                    "id": pl_id,
                    "title": e.get("title") or "Playlist",
                    "url": f"https://www.youtube.com/playlist?list={pl_id}",
                    "channel": e.get("channel") or e.get("uploader") or "",
                    "count": e.get("playlist_count") or e.get("n_entries") or "?",
                    "thumbnail": thumbnail,
                })
            return results
    except Exception as e:
        print(f"Erreur recherche playlists : {e}")
        return []


def get_playlist_info(url: str) -> dict:
    """Return playlist title and list of entries without downloading."""
    ydl_opts = {"extract_flat": True, "quiet": True, "no_warnings": True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return {"title": "Playlist", "entries": []}
            entries = [
                {
                    "id": e.get("id") or "",
                    "title": e.get("title") or "Vidéo indisponible",
                    "url": f"https://www.youtube.com/watch?v={e.get('id')}",
                }
                for e in (info.get("entries") or []) if e and e.get("id")
            ]
            return {
                "title": info.get("title") or "Playlist importée",
                "entries": entries,
            }
    except Exception as e:
        print(f"Erreur extraction playlist : {e}")
        return {"title": "Playlist", "entries": []}


def download_by_url(url: str, output_folder: str = MUSIC_FOLDER, cancel_event=None) -> dict | None:
    """Download a track. Returns {"title": ..., "filename": ...} or None."""
    os.makedirs(output_folder, exist_ok=True)

    def _hook(d):
        if cancel_event and cancel_event.is_set():
            raise yt_dlp.utils.DownloadError("Annulé par l'utilisateur")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(output_folder, "%(title)s.%(ext)s"),
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [_hook],
        "retries": 10,
        "fragment_retries": 10,
        "extractor_retries": 3,
        "sleep_interval": 1,
        "max_sleep_interval": 4,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                print("Erreur : aucune information extraite.")
                return None
            title = info.get("title") or "inconnu"
            try:
                raw = ydl.prepare_filename(info)
                filename = os.path.splitext(os.path.basename(raw))[0] + ".mp3"
            except Exception:
                filename = title + ".mp3"
            print(f"Téléchargé : {filename}")
            return {"title": title, "filename": filename}
    except yt_dlp.utils.DownloadError as e:
        if cancel_event and cancel_event.is_set():
            print("Téléchargement annulé.")
        else:
            print(f"Erreur de téléchargement : {e}")
        return None
    except Exception as e:
        print(f"Erreur inattendue : {e}")
        return None


def download_by_name(query: str, output_folder: str = MUSIC_FOLDER) -> str | None:
    os.makedirs(output_folder, exist_ok=True)
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(output_folder, "%(title)s.%(ext)s"),
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
        "quiet": True,
        "no_warnings": True,
        "default_search": "ytsearch",
        "retries": 10,
        "fragment_retries": 10,
        "extractor_retries": 3,
        "sleep_interval": 1,
        "max_sleep_interval": 4,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch1:{query}", download=True)
            if not info:
                return None
            entries = info.get("entries") or [info]
            first = entries[0] if entries else None
            title = (first.get("title") if first else None) or "inconnu"
            print(f"Téléchargé : {title}.mp3")
            return title
    except yt_dlp.utils.DownloadError as e:
        print(f"Erreur de téléchargement : {e}")
        return None
    except Exception as e:
        print(f"Erreur inattendue : {e}")
        return None


def _fmt_duration(seconds) -> str:
    if not seconds:
        return ""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _fmt_views(n) -> str:
    if not n:
        return ""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M vues"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K vues"
    return f"{n} vues"


if __name__ == "__main__":
    print("=== Téléchargeur MP3 ===")
    print(f"Dossier de destination : {os.path.abspath(MUSIC_FOLDER)}\n")

    while True:
        try:
            query = input("Nom de la musique (ou 'quit') > ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nAu revoir !")
            break

        if not query:
            continue
        if query.lower() in ("quit", "exit", "q"):
            print("Au revoir !")
            break

        print(f"Recherche de '{query}'...")
        download_by_name(query)
