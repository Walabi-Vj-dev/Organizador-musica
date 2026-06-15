#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Organizador de Musica
=====================
App de escritorio para ordenar tu biblioteca de audio (MP3, FLAC, M4A/MP4, OGG, Opus):
  - Escanea una carpeta (recursivo) y lee las etiquetas ID3.
  - Edita tags (titulo, artista, album, genero, anio, n.de pista), individual o en lote.
  - Adivina tags a partir del nombre del archivo ("01_Take on me", "Aha - Take on me").
  - Busca duplicados por: contenido exacto (hash), etiqueta (titulo+artista) o huella de audio (fpcalc).
  - Mueve los duplicados a una carpeta "_Duplicados" (NUNCA borra nada).
  - Organiza la biblioteca en subcarpetas por Genero, Artista o Artista/Album.

Requisitos:
  - Python 3.8+ (con Tkinter, que viene incluido en el instalador oficial de python.org)
  - pip install mutagen
  - (Opcional) Chromaprint/fpcalc en el PATH, para deduplicar por "huella de audio"
    (detecta el mismo tema aunque tenga distinto nombre/tags). Sin esto, igual
    funciona la deteccion por hash y por etiqueta.

Autor: hecho para Walabi VJ
"""

import os
import re
import sys
import json
import shutil
import hashlib
import array
import stat
import threading
import queue
import subprocess
import urllib.request
import urllib.parse
import urllib.error

# --- mutagen (lectura/escritura de tags, multi-formato) ---
try:
    import mutagen
    from mutagen import File as MutagenFile
except ImportError:
    sys.stderr.write(
        "Falta la libreria 'mutagen'. Instalala con:\n    pip install mutagen\n"
    )
    sys.exit(1)

# Formatos soportados. mutagen los maneja a todos con una interfaz "easy" uniforme,
# asi que los tags, los duplicados y la organizacion funcionan igual en cualquiera.
SUPPORTED_EXT = (".mp3", ".flac", ".m4a", ".mp4", ".ogg", ".opus")
DUP_FOLDER_NAME = "_Duplicados"    # carpeta destino de duplicados
NO_GENRE = "_SinGenero"
TAG_FIELDS = ["title", "artist", "album", "genre", "date", "tracknumber", "bpm"]


# =============================================================================
#  LOGICA PURA (sin interfaz) -- facil de testear
# =============================================================================

def human_duration(seconds):
    """Segundos -> 'mm:ss'."""
    if not seconds or seconds < 0:
        return "--:--"
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def norm_text(s):
    """Normaliza un texto para comparar (minusculas, sin parentesis ni adornos)."""
    s = (s or "").lower()
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s)                       # quita (...) y [...]
    s = re.sub(r"\b(feat|ft|featuring|remaster(ed)?|remix|"
               r"official|video|audio|lyrics|hd|hq|mp3)\b.*", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)                            # solo letras/numeros
    return s.strip()


def safe_name(s, fallback="Desconocido"):
    """Convierte un texto en un nombre de carpeta valido para Windows."""
    s = (s or "").strip()
    if not s:
        s = fallback
    s = re.sub(r'[<>:"/\\|?*]+', "_", s)        # caracteres prohibidos en Windows
    s = s.rstrip(". ")                          # Windows no permite terminar en . o espacio
    return s[:120] or fallback


def guess_from_filename(path):
    """
    Intenta deducir (artista, titulo) desde el nombre del archivo.
    Maneja casos como:  '01_Take on me'  ->  (None, 'Take on me')
                        'Aha - Take on me' -> ('Aha', 'Take on me')
                        'track1'          ->  (None, None)  (sin info util)
    Convencion asumida para 'X - Y':  'Artista - Titulo' (lo mas comun en descargas).
    """
    name = os.path.splitext(os.path.basename(path))[0]
    # quita prefijo de numero de pista:  "01 - ", "01_", "01.", "1)"
    name = re.sub(r"^\s*\d{1,3}\s*[-_.)\s]+", "", name)
    # "track1" / "pista 03" / "audio" -> sin informacion
    if re.fullmatch(r"(track|pista|audio|untitled|sin\s*nombre)\s*\d*", name, re.I):
        return (None, None)
    name = name.replace("_", " ").strip()
    if not name:
        return (None, None)
    if " - " in name:
        a, b = name.split(" - ", 1)
        return (a.strip() or None, b.strip() or None)
    return (None, name)


def read_track(path):
    """Lee un archivo de audio (cualquier formato soportado) y devuelve un dict con sus metadatos."""
    t = {
        "path": path,
        "title": "", "artist": "", "album": "", "genre": "",
        "date": "", "tracknumber": "", "bpm": "",
        "duration": 0.0, "bitrate": 0, "size": 0,
        "modified": False,           # marca interna si el usuario lo edito
    }
    try:
        t["size"] = os.path.getsize(path)
    except OSError:
        pass
    try:
        audio = MutagenFile(path, easy=True)
    except Exception:
        audio = None
    if audio is not None:
        info = getattr(audio, "info", None)
        length = float(getattr(info, "length", 0) or 0)
        bitrate = int(getattr(info, "bitrate", 0) or 0)
        # FLAC y Opus no exponen bitrate directo: lo estimamos (bytes*8/duracion).
        if not bitrate and length > 0 and t["size"]:
            bitrate = int(t["size"] * 8 / length)
        t["duration"] = length
        t["bitrate"] = bitrate
        for f in TAG_FIELDS:
            try:
                vals = audio.get(f)
            except Exception:
                vals = None
            if vals:
                t[f] = str(vals[0])
    return t


def ensure_writable(path):
    """Si el archivo esta marcado como 'solo lectura', le quita ese atributo."""
    try:
        if os.path.exists(path) and not os.access(path, os.W_OK):
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    except Exception:
        pass


def write_tags(path, fields):
    """Escribe etiquetas en el archivo (cualquier formato soportado). 'fields' es un dict {campo: valor}."""
    ensure_writable(path)
    audio = MutagenFile(path, easy=True)
    if audio is None:
        raise ValueError("Formato de audio no soportado para editar etiquetas.")
    if audio.tags is None:
        audio.add_tags()
    for k, v in fields.items():
        if k not in TAG_FIELDS:
            continue
        v = (v or "").strip()
        try:
            if v:
                audio[k] = [v]
            elif k in audio:
                del audio[k]
        except Exception:
            # Algun formato puede no admitir cierto campo concreto: lo ignoramos sin romper el resto.
            pass
    audio.save()


def file_md5(path, chunk=1024 * 1024):
    """MD5 del contenido completo del archivo."""
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


_FPCALC_PATH = None
_FPCALC_DONE = False


def _app_dir():
    """Carpeta del ejecutable (si esta empaquetado con PyInstaller) o del script."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def find_fpcalc():
    """
    Busca el binario fpcalc en este orden:
      1) junto al .exe / .py     2) dentro del paquete PyInstaller (_MEIPASS)
      3) en el PATH del sistema
    Asi funciona tanto en desarrollo como ya empaquetado como .exe; incluso si el
    usuario solo deja 'fpcalc.exe' en la misma carpeta del programa. Devuelve la
    ruta encontrada o None.
    """
    global _FPCALC_PATH, _FPCALC_DONE
    if _FPCALC_DONE:
        return _FPCALC_PATH
    names = ("fpcalc.exe", "fpcalc")
    dirs = [_app_dir()]
    mei = getattr(sys, "_MEIPASS", None)
    if mei:
        dirs.append(mei)
    for d in dirs:
        for n in names:
            cand = os.path.join(d, n)
            if os.path.isfile(cand):
                _FPCALC_PATH, _FPCALC_DONE = cand, True
                return _FPCALC_PATH
    _FPCALC_PATH, _FPCALC_DONE = shutil.which("fpcalc"), True
    return _FPCALC_PATH


def has_fpcalc():
    return find_fpcalc() is not None


def _no_window_kwargs():
    """Evita que los subprocesos (ffmpeg/fpcalc) abran una consola negra en el .exe."""
    if sys.platform.startswith("win"):
        return {"creationflags": 0x08000000}   # CREATE_NO_WINDOW
    return {}


def audio_fingerprint(path):
    """Huella de audio con Chromaprint (fpcalc). Devuelve string o None."""
    exe = find_fpcalc()
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "-json", path],
            capture_output=True, text=True, timeout=120, **_no_window_kwargs()
        )
        data = json.loads(out.stdout or "{}")
        return data.get("fingerprint")
    except Exception:
        return None


# --- Analisis de BPM (decodifica con ffmpeg, calcula en Python puro) ---
_FFMPEG_PATH = None
_FFMPEG_DONE = False


def find_ffmpeg():
    """Busca ffmpeg junto al .exe/.py, dentro del paquete, o en el PATH. Devuelve ruta o None."""
    global _FFMPEG_PATH, _FFMPEG_DONE
    if _FFMPEG_DONE:
        return _FFMPEG_PATH
    names = ("ffmpeg.exe", "ffmpeg")
    dirs = [_app_dir()]
    mei = getattr(sys, "_MEIPASS", None)
    if mei:
        dirs.append(mei)
    for d in dirs:
        for n in names:
            cand = os.path.join(d, n)
            if os.path.isfile(cand):
                _FFMPEG_PATH, _FFMPEG_DONE = cand, True
                return _FFMPEG_PATH
    _FFMPEG_PATH, _FFMPEG_DONE = shutil.which("ffmpeg"), True
    return _FFMPEG_PATH


def detect_bpm(path, ffmpeg=None, max_seconds=90, sr=11025):
    """
    Estima el BPM (tempo) de un archivo de audio. Decodifica los primeros
    'max_seconds' a PCM mono con ffmpeg y aplica autocorrelacion sobre la
    envolvente de energia. Devuelve un float (BPM) o None si no se pudo.
    Funciona mejor con musica de beat marcado (electronica, dance, etc.).
    """
    exe = ffmpeg or find_ffmpeg()
    if not exe:
        return None
    cmd = [exe, "-v", "quiet", "-i", path, "-t", str(max_seconds),
           "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]
    try:
        raw = subprocess.run(cmd, capture_output=True, **_no_window_kwargs()).stdout
    except Exception:
        return None
    if not raw or len(raw) < sr * 2 * 5:        # menos de 5 segundos de audio
        return None
    samples = array.array("h")
    samples.frombytes(raw[:len(raw) - (len(raw) % 2)])
    n = len(samples)
    hop = 128
    # envolvente de energia por cuadro
    energies = []
    for i in range(0, n - hop, hop):
        seg = samples[i:i + hop]
        s = 0
        for x in seg:
            s += x * x
        energies.append(float(s))
    if len(energies) < 10:
        return None
    # flujo de energia positivo (onset)
    onset = [e2 - e1 if e2 > e1 else 0.0 for e1, e2 in zip(energies, energies[1:])]
    mean = sum(onset) / len(onset)
    onset = [v - mean for v in onset]
    fps = sr / hop
    lag_min = int(fps * 60 / 200)               # hasta 200 BPM
    lag_max = int(fps * 60 / 60) + 1            # desde 60 BPM
    L = len(onset)
    ac = {}
    best_lag, best = None, -1.0
    for lag in range(lag_min, lag_max + 1):
        s = 0.0
        for i in range(L - lag):
            s += onset[i] * onset[i + lag]
        ac[lag] = s
        if s > best:
            best, best_lag = s, lag
    if not best_lag:
        return None
    # interpolacion parabolica para afinar el pico
    a, b, c = ac.get(best_lag - 1, 0.0), ac[best_lag], ac.get(best_lag + 1, 0.0)
    denom = (a - 2 * b + c)
    shift = 0.5 * (a - c) / denom if denom else 0.0
    lag = best_lag + shift
    if lag <= 0:
        return None
    bpm = 60.0 * fps / lag
    # plegar a un rango musical tipico (evita errores de octava)
    while bpm < 70:
        bpm *= 2
    while bpm > 180:
        bpm /= 2
    return round(bpm, 1)


def choose_keeper(group):
    """
    De un grupo de duplicados elige cual conservar:
    1) mayor bitrate, 2) tags mas completos, 3) nombre de archivo mas corto.
    Devuelve el dict elegido.
    """
    def completeness(t):
        return sum(1 for f in ("title", "artist", "album", "genre") if t.get(f))

    return sorted(
        group,
        key=lambda t: (
            -t.get("bitrate", 0),
            -completeness(t),
            len(os.path.basename(t["path"])),
        ),
    )[0]


def find_duplicates(tracks, method="hash", progress=None):
    """
    Agrupa duplicados segun el metodo.
      method='hash'        -> contenido byte a byte identico (rapido y seguro)
      method='tag'         -> mismo titulo+artista normalizados
      method='fingerprint' -> misma huella de audio (requiere fpcalc)
    'progress' es un callback opcional progress(i, total, mensaje).
    Devuelve lista de grupos (cada grupo = lista de tracks, len>=2).
    """
    groups = {}
    total = len(tracks)

    if method == "hash":
        # Primero agrupamos por tamanio (barato) y solo hasheamos donde hay coincidencia.
        by_size = {}
        for t in tracks:
            by_size.setdefault(t["size"], []).append(t)
        i = 0
        for size, items in by_size.items():
            if size and len(items) > 1:
                for t in items:
                    try:
                        key = file_md5(t["path"])
                        groups.setdefault(key, []).append(t)
                    except OSError:
                        pass
                    i += 1
                    if progress:
                        progress(i, total, os.path.basename(t["path"]))
            else:
                i += len(items)

    elif method == "tag":
        for i, t in enumerate(tracks):
            title = norm_text(t.get("title")) or norm_text(guess_from_filename(t["path"])[1])
            artist = norm_text(t.get("artist")) or norm_text(guess_from_filename(t["path"])[0])
            if not title:
                continue
            key = (title, artist)
            groups.setdefault(key, []).append(t)
            if progress:
                progress(i + 1, total, os.path.basename(t["path"]))

    elif method == "fingerprint":
        for i, t in enumerate(tracks):
            fp = audio_fingerprint(t["path"])
            if fp:
                # usamos un hash corto de la huella como clave
                groups.setdefault(hashlib.md5(fp.encode()).hexdigest(), []).append(t)
            if progress:
                progress(i + 1, total, os.path.basename(t["path"]))

    return [g for g in groups.values() if len(g) > 1]


def safe_move(src, dest_dir, copy=False):
    """Mueve (o copia) src a dest_dir resolviendo colisiones de nombre. Devuelve el destino."""
    os.makedirs(dest_dir, exist_ok=True)
    base = os.path.basename(src)
    target = os.path.join(dest_dir, base)
    if os.path.abspath(target) == os.path.abspath(src):
        return src
    stem, ext = os.path.splitext(base)
    i = 1
    while os.path.exists(target):
        target = os.path.join(dest_dir, f"{stem} ({i}){ext}")
        i += 1
    if copy:
        shutil.copy2(src, target)
    else:
        shutil.move(src, target)
    return target


def plan_organization(tracks, scheme, dest_root):
    """
    Calcula a donde iria cada archivo segun el esquema:
      'genre'        -> dest_root/<Genero>/archivo.mp3
      'artist'       -> dest_root/<Artista>/archivo.mp3
      'artist_album' -> dest_root/<Artista>/<Album>/archivo.mp3
    Devuelve lista de tuplas (track, carpeta_destino).
    """
    plan = []
    for t in tracks:
        if scheme == "genre":
            sub = safe_name(t.get("genre"), NO_GENRE)
        elif scheme == "artist":
            sub = safe_name(t.get("artist"), "Artista Desconocido")
        else:  # artist_album
            sub = os.path.join(
                safe_name(t.get("artist"), "Artista Desconocido"),
                safe_name(t.get("album"), "Album Desconocido"),
            )
        plan.append((t, os.path.join(dest_root, sub)))
    return plan


# --- Busqueda de informacion por internet (MusicBrainz: sin clave ni cuenta) ---
MB_UA = "OrganizadorMusicaMP3/1.1 ( https://github.com/Walabi-Vj-dev )"


def _mb_clean(s):
    """Quita caracteres especiales de Lucene para que la consulta a MusicBrainz no falle."""
    return re.sub(r'[+\-!(){}\[\]^"~*?:\\/]', " ", s or "").strip()


def musicbrainz_search(title, artist="", limit=6, timeout=15):
    """
    Busca una grabacion en MusicBrainz por titulo (y artista opcional).
    Usa coincidencia por tokens (mas tolerante que frase exacta).
    Devuelve [{title, artist, album, date, genre, source, score}, ...].
    """
    t, a = _mb_clean(title), _mb_clean(artist)
    parts = []
    if t:
        parts.append("recording:(%s)" % t)
    if a:
        parts.append("artist:(%s)" % a)
    query = " AND ".join(parts) if parts else t
    if not query:
        return []
    params = urllib.parse.urlencode({"query": query, "fmt": "json", "limit": str(limit)})
    url = "https://musicbrainz.org/ws/2/recording/?" + params
    req = urllib.request.Request(url, headers={"User-Agent": MB_UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    results = []
    for rec in data.get("recordings", []):
        ac = rec.get("artist-credit") or []
        artist_name = "".join(
            (p.get("name", "") + p.get("joinphrase", "")) for p in ac if isinstance(p, dict)
        ).strip()
        album, year = "", ""
        rels = rec.get("releases") or []
        if rels:
            album = rels[0].get("title", "") or ""
            year = (rels[0].get("date", "") or "")[:4]
        results.append({
            "title": rec.get("title", "") or "",
            "artist": artist_name,
            "album": album,
            "date": year,
            "genre": "",
            "source": "MusicBrainz",
            "score": rec.get("score", 0),
        })
    return results


def itunes_search(title, artist="", limit=6, timeout=15):
    """
    Busca en la API de iTunes (Apple). Gratis, sin clave ni cuenta.
    Trae tambien genero (que MusicBrainz no da). Devuelve la misma estructura.
    """
    term = (artist + " " + title).strip() or title
    if not term:
        return []
    params = urllib.parse.urlencode(
        {"term": term, "media": "music", "entity": "song", "limit": str(limit)})
    url = "https://itunes.apple.com/search?" + params
    req = urllib.request.Request(url, headers={"User-Agent": MB_UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    results = []
    for r in data.get("results", []):
        art = r.get("artworkUrl100", "") or ""
        if art:                                    # subir resolucion 100x100 -> 600x600
            art = art.replace("100x100bb", "600x600bb")
        results.append({
            "title": r.get("trackName", "") or "",
            "artist": r.get("artistName", "") or "",
            "album": r.get("collectionName", "") or "",
            "date": (r.get("releaseDate", "") or "")[:4],
            "genre": r.get("primaryGenreName", "") or "",
            "source": "iTunes",
            "artwork": art,
            "score": 100,
        })
    return results


def online_search(title, artist=""):
    """
    Consulta los dos buscadores (iTunes y MusicBrainz) y combina los resultados.
    Si uno falla, usa el otro. Solo lanza error si fallan los dos.
    """
    results, errors = [], []
    for fn, name in ((itunes_search, "iTunes"), (musicbrainz_search, "MusicBrainz")):
        try:
            results.extend(fn(title, artist))
        except Exception as e:
            errors.append(f"{name}: {e}")
    if not results and errors:
        raise RuntimeError(" | ".join(errors))
    return results


def musixmatch_search_lyrics(fragment, api_key, limit=8, timeout=15):
    """
    Busca un tema a partir de un fragmento de su letra usando Musixmatch.
    Requiere una clave gratuita (developer.musixmatch.com). Devuelve candidatos
    con la misma estructura que online_search. Solo se usa el titulo/artista que
    devuelve; NO se descarga ni se muestra la letra.
    """
    if not api_key:
        raise RuntimeError("Falta la clave de Musixmatch.")
    params = urllib.parse.urlencode({
        "q_lyrics": fragment,
        "page_size": str(limit),
        "page": "1",
        "s_track_rating": "desc",
        "apikey": api_key.strip(),
        "format": "json",
    })
    url = "https://api.musixmatch.com/ws/1.1/track.search?" + params
    req = urllib.request.Request(url, headers={"User-Agent": MB_UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    header = (data.get("message") or {}).get("header") or {}
    status = header.get("status_code")
    if status != 200:
        friendly = {401: "clave invalida", 402: "limite de uso alcanzado",
                    400: "consulta invalida"}.get(status, f"codigo {status}")
        raise RuntimeError("Musixmatch: " + friendly)
    body = (data.get("message") or {}).get("body") or {}
    out = []
    for item in body.get("track_list", []):
        tr = item.get("track") or {}
        out.append({
            "title": tr.get("track_name", "") or "",
            "artist": tr.get("artist_name", "") or "",
            "album": tr.get("album_name", "") or "",
            "date": (tr.get("first_release_date", "") or "")[:4],
            "genre": "",
            "source": "Musixmatch",
            "artwork": "",
            "score": 0,
        })
    return out


# --- Configuracion persistente (guarda, por ejemplo, la clave de AcoustID) ---
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".organizador_musica.json")


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
    except Exception:
        pass


# --- Caratula del album: descargar e incrustar en el archivo ---
def download_image(url, timeout=20):
    """Descarga una imagen y devuelve (bytes, mime)."""
    req = urllib.request.Request(url, headers={"User-Agent": MB_UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    mime = "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
    return data, mime


def embed_artwork(path, data, mime):
    """Incrusta la imagen 'data' como caratula del archivo, segun su formato."""
    ensure_writable(path)
    ext = os.path.splitext(path)[1].lower()
    if ext == ".mp3":
        from mutagen.id3 import ID3, APIC, ID3NoHeaderError
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        tags.delall("APIC")
        tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))
        tags.save(path)
    elif ext == ".flac":
        from mutagen.flac import FLAC, Picture
        f = FLAC(path)
        pic = Picture()
        pic.type, pic.mime, pic.desc, pic.data = 3, mime, "Cover", data
        f.clear_pictures()
        f.add_picture(pic)
        f.save()
    elif ext in (".m4a", ".mp4"):
        from mutagen.mp4 import MP4, MP4Cover
        fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
        a = MP4(path)
        a["covr"] = [MP4Cover(data, imageformat=fmt)]
        a.save()
    elif ext in (".ogg", ".opus"):
        import base64
        from mutagen.flac import Picture
        from mutagen.oggvorbis import OggVorbis
        from mutagen.oggopus import OggOpus
        pic = Picture()
        pic.type, pic.mime, pic.desc, pic.data = 3, mime, "Cover", data
        b64 = base64.b64encode(pic.write()).decode("ascii")
        audio = OggOpus(path) if ext == ".opus" else OggVorbis(path)
        audio["metadata_block_picture"] = [b64]
        audio.save()
    else:
        raise ValueError("Formato sin soporte de caratula: " + ext)


def download_and_embed_artwork(url, path):
    data, mime = download_image(url)
    embed_artwork(path, data, mime)
    return True


# --- Identificacion por huella de audio (AcoustID + fpcalc) ---
def acoustid_lookup(path, api_key, fpcalc=None, timeout=20):
    """
    Identifica un tema por su huella de audio. Requiere fpcalc (Chromaprint) y una
    clave gratuita de AcoustID. Devuelve candidatos con la misma estructura que online_search.
    """
    exe = fpcalc or find_fpcalc()
    if not exe:
        raise RuntimeError("Falta fpcalc (Chromaprint).")
    if not api_key:
        raise RuntimeError("Falta la clave de AcoustID.")
    out = subprocess.run([exe, "-json", path], capture_output=True, text=True,
                         timeout=120, **_no_window_kwargs())
    info = json.loads(out.stdout or "{}")
    fp, dur = info.get("fingerprint"), info.get("duration")
    if not fp or not dur:
        return []
    body = urllib.parse.urlencode({
        "format": "json",
        "client": api_key.strip(),
        "duration": str(int(round(dur))),
        "fingerprint": fp,
        "meta": "recordings releasegroups",   # separados por espacio (no por '+')
    }).encode()
    req = urllib.request.Request(
        "https://api.acoustid.org/v2/lookup", data=body,
        headers={"User-Agent": MB_UA, "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        # AcoustID manda el motivo real en el cuerpo aunque el status HTTP sea 400
        detail = ""
        try:
            err = json.loads(e.read().decode("utf-8", "replace"))
            detail = (err.get("error") or {}).get("message", "")
        except Exception:
            detail = ""
        raise RuntimeError("AcoustID: " + (detail or f"HTTP {e.code} {e.reason}"))
    if data.get("status") != "ok":
        msg = (data.get("error") or {}).get("message", "error desconocido")
        raise RuntimeError("AcoustID: " + str(msg))
    results = []
    for r in data.get("results", []):
        score = int(round(r.get("score", 0) * 100))
        for rec in (r.get("recordings") or []):
            artists = rec.get("artists") or []
            artist = ", ".join(a.get("name", "") for a in artists if isinstance(a, dict)).strip(", ")
            rgs = rec.get("releasegroups") or []
            album = rgs[0].get("title", "") if rgs else ""
            results.append({
                "title": rec.get("title", "") or "",
                "artist": artist,
                "album": album,
                "date": "",
                "genre": "",
                "source": "AcoustID",
                "artwork": "",
                "score": score,
            })
    return results


def open_with_default_app(path):
    """Abre el archivo con el reproductor predeterminado del sistema."""
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)               # solo existe en Windows
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


def reveal_in_file_manager(path):
    """Abre el explorador de archivos mostrando/seleccionando el archivo."""
    try:
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
    except Exception:
        pass


# =============================================================================
#  INTERFAZ GRAFICA (Tkinter)
# =============================================================================

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# Arrastre nativo de archivos hacia afuera (VirtualDJ, Explorador, etc.). Opcional.
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES, COPY
    HAS_DND = True
except Exception:
    HAS_DND = False
    DND_FILES, COPY = "DND_Files", "copy"


class ResultPicker(tk.Toplevel):
    """Ventanita para elegir uno de los candidatos devueltos por la busqueda online."""

    def __init__(self, parent, results, on_pick=None):
        super().__init__(parent)
        self.results = results
        self.on_pick = on_pick
        self.title("Resultados online  -  elegi el correcto")
        self.geometry("720x320")
        self.transient(parent)
        self.grab_set()

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)
        cols = ("title", "artist", "album", "year", "genre", "source")
        self.tv = ttk.Treeview(frm, columns=cols, show="headings", selectmode="browse")
        for c, h, w in zip(cols, ("Titulo", "Artista", "Album", "Anio", "Genero", "Fuente"),
                           (170, 130, 150, 45, 110, 80)):
            self.tv.heading(c, text=h)
            self.tv.column(c, width=w, anchor="w")
        for i, r in enumerate(results):
            self.tv.insert("", "end", iid=str(i),
                           values=(r["title"], r["artist"], r["album"], r["date"],
                                   r.get("genre", ""), r.get("source", "")))
        self.tv.pack(fill="both", expand=True)
        self.tv.bind("<Double-1>", lambda e: self._use())
        if results:
            self.tv.selection_set("0")

        b = ttk.Frame(frm)
        b.pack(fill="x", pady=(8, 0))
        ttk.Button(b, text="Cancelar", command=self.destroy).pack(side="right")
        ttk.Button(b, text="Usar este", command=self._use).pack(side="right", padx=6)

    def _use(self):
        sel = self.tv.selection()
        if not sel:
            return
        r = self.results[int(sel[0])]
        if self.on_pick:
            self.on_pick(r)
        self.destroy()


class TagEditor(tk.Toplevel):
    """Editor de etiquetas de una pista, con busqueda de info por internet."""

    def __init__(self, app, track, on_save=None):
        super().__init__(app)
        self.app = app
        self.track = track
        self.on_save = on_save
        self.pending_artwork = ""
        self.title("Editar etiquetas")
        self.geometry("620x430")
        self.transient(app)
        self.resizable(False, False)
        self.grab_set()
        self.vars = {f: tk.StringVar(value=track.get(f, "")) for f in TAG_FIELDS}
        self._build()

    def _build(self):
        frm = ttk.Frame(self, padding=14)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text=os.path.basename(self.track["path"]),
                  font=("", 9, "italic"), foreground="#666").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        labels = {"title": "Titulo", "artist": "Artista", "album": "Album",
                  "genre": "Genero", "date": "Anio", "tracknumber": "N. pista",
                  "bpm": "BPM"}
        for i, f in enumerate(TAG_FIELDS):
            ttk.Label(frm, text=labels[f] + ":").grid(row=i + 1, column=0, sticky="e", padx=6, pady=4)
            ttk.Entry(frm, textvariable=self.vars[f], width=46).grid(
                row=i + 1, column=1, sticky="w", padx=6, pady=4)
        self.status = ttk.Label(frm, text="", foreground="#0a7a55")
        self.status.grid(row=len(TAG_FIELDS) + 1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        actions = ttk.Frame(frm)
        actions.grid(row=len(TAG_FIELDS) + 2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self.online_btn = ttk.Button(actions, text="Buscar info online", command=self.search_online)
        self.online_btn.pack(side="left")
        self.acoustid_btn = ttk.Button(actions, text="Identificar (huella)", command=self.search_acoustid)
        self.acoustid_btn.pack(side="left", padx=6)
        ttk.Button(actions, text="Adivinar desde nombre", command=self.guess).pack(side="left")
        self.art_btn = ttk.Button(actions, text="Descargar caratula",
                                  command=self.download_artwork, state="disabled")
        self.art_btn.pack(side="left", padx=6)

        footer = ttk.Frame(frm)
        footer.grid(row=len(TAG_FIELDS) + 3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(footer, text="Cancelar", command=self.destroy).pack(side="right")
        ttk.Button(footer, text="Guardar", command=self.save).pack(side="right", padx=6)

    def guess(self):
        artist, title = guess_from_filename(self.track["path"])
        if title:
            self.vars["title"].set(title)
        if artist:
            self.vars["artist"].set(artist)
        self.status.config(text="Sugerencia cargada desde el nombre del archivo.")

    def search_online(self):
        title = self.vars["title"].get().strip() or (guess_from_filename(self.track["path"])[1] or "")
        artist = self.vars["artist"].get().strip() or (guess_from_filename(self.track["path"])[0] or "")
        if not title:
            messagebox.showinfo("Falta titulo",
                                "Necesito al menos un titulo para buscar (el nombre del archivo no da info util).",
                                parent=self)
            return
        self.online_btn.config(state="disabled")
        self.status.config(text="Buscando en iTunes y MusicBrainz...")
        self.app.run_async(lambda: online_search(title, artist), self._got_results)

    def search_acoustid(self):
        key = getattr(self.app, "acoustid_key", "")
        if not key:
            messagebox.showinfo(
                "Falta clave de AcoustID",
                "Configura tu clave gratuita en el menu Ajustes > Clave de AcoustID.\n"
                "Se obtiene gratis en acoustid.org.", parent=self)
            return
        if find_fpcalc() is None:
            messagebox.showwarning(
                "Falta fpcalc",
                "La identificacion por huella necesita 'fpcalc' (Chromaprint) junto al "
                "programa o en el PATH.", parent=self)
            return
        self.acoustid_btn.config(state="disabled")
        self.status.config(text="Identificando por huella de audio...")
        path = self.track["path"]
        self.app.run_async(lambda: acoustid_lookup(path, key), self._got_results)

    def _got_results(self, results, err):
        if not self.winfo_exists():
            return
        self.online_btn.config(state="normal")
        self.acoustid_btn.config(state="normal")
        if err is not None:
            self.status.config(text="Error de conexion: " + str(err))
            return
        if not results:
            self.status.config(text="Sin resultados.")
            return
        self.status.config(text=f"{len(results)} resultados. Elegi el correcto.")
        ResultPicker(self, results, on_pick=self._apply_result)

    def _apply_result(self, r):
        for f in ("title", "artist", "album", "date", "genre"):
            if r.get(f):
                self.vars[f].set(r[f])
        if r.get("artwork"):
            self.pending_artwork = r["artwork"]
            self.art_btn.config(state="normal")
            self.status.config(text="Datos cargados. Hay caratula: usa 'Descargar caratula', luego Guardar.")
        else:
            self.status.config(text="Datos cargados. Revisa y presiona Guardar.")

    def download_artwork(self):
        if not self.pending_artwork:
            messagebox.showinfo("Sin caratula",
                                "Primero elegi un resultado de iTunes que tenga caratula.", parent=self)
            return
        self.art_btn.config(state="disabled")
        self.status.config(text="Descargando e incrustando caratula...")
        url, path = self.pending_artwork, self.track["path"]
        self.app.run_async(lambda: download_and_embed_artwork(url, path), self._art_done)

    def _art_done(self, res, err):
        if not self.winfo_exists():
            return
        self.art_btn.config(state="normal")
        if err is not None:
            self.status.config(text="Error con la caratula: " + str(err))
            return
        self.track["modified"] = True
        self.status.config(text="Caratula incrustada en el archivo.")

    def save(self):
        fields = {f: self.vars[f].get() for f in TAG_FIELDS}
        try:
            write_tags(self.track["path"], fields)
        except PermissionError:
            messagebox.showerror(
                "No se pudo guardar",
                "Windows no dejo modificar el archivo:\n\n"
                + os.path.basename(self.track["path"]) + "\n\n"
                "Causas frecuentes:\n"
                "- El tema esta abierto o reproduciendose en otro programa (cerralo).\n"
                "- El archivo o la carpeta son de 'solo lectura'.\n"
                "- La carpeta esta protegida por OneDrive o por el 'Acceso a carpetas "
                "controlado' de Windows (Seguridad de Windows > Proteccion antivirus).\n\n"
                "Solucion simple: move la musica a una carpeta normal (por ejemplo C:\\Musica) "
                "y volve a intentar.", parent=self)
            return
        except Exception as e:
            messagebox.showerror("Error al guardar", str(e), parent=self)
            return
        self.track.update(fields)
        self.track["modified"] = True
        if self.on_save:
            self.on_save()
        self.destroy()


class LyricsSearch(tk.Toplevel):
    """Busca un tema por un fragmento de su letra y lista versiones de distintos artistas."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.results = []
        self.title("Buscar por letra")
        self.geometry("740x430")
        self.transient(app)
        self.grab_set()
        self._build()

    def _build(self):
        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        top = ttk.Frame(frm)
        top.pack(fill="x")
        ttk.Label(top, text="Escribi un pedazo de la letra:").pack(side="left")
        self.q = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.q)
        entry.pack(side="left", fill="x", expand=True, padx=6)
        entry.bind("<Return>", lambda ev: self.search())
        self.btn = ttk.Button(top, text="Buscar", command=self.search)
        self.btn.pack(side="left")
        self.status = ttk.Label(frm, text="Ej.: tengo clavado en el pecho",
                                foreground="#888")
        self.status.pack(fill="x", pady=(6, 4))
        cols = ("title", "artist", "album", "source")
        self.tv = ttk.Treeview(frm, columns=cols, show="headings", selectmode="browse")
        for c, h, w in zip(cols, ("Titulo", "Artista", "Album", "Fuente"), (230, 170, 180, 95)):
            self.tv.heading(c, text=h)
            self.tv.column(c, width=w, anchor="w")
        self.tv.pack(fill="both", expand=True)
        b = ttk.Frame(frm)
        b.pack(fill="x", pady=(8, 0))
        ttk.Button(b, text="Cerrar", command=self.destroy).pack(side="right")
        ttk.Button(b, text="Usar en la pista seleccionada",
                   command=self.apply_to_selected).pack(side="right", padx=6)
        entry.focus_set()

    def search(self):
        frag = self.q.get().strip()
        if not frag:
            return
        key = getattr(self.app, "musixmatch_key", "")
        if not key:
            messagebox.showinfo(
                "Falta clave de Musixmatch",
                "Configura tu clave gratuita en el menu Ajustes > Clave de Musixmatch.\n"
                "Se obtiene en developer.musixmatch.com.", parent=self)
            return
        self.btn.config(state="disabled")
        self.status.config(text="Buscando la letra...", foreground="#0a7a55")
        self.app.run_async(lambda: self._do_search(frag, key), self._got)

    def _do_search(self, frag, key):
        matches = musixmatch_search_lyrics(frag, key, limit=5)
        results = list(matches)
        # Sumar versiones de distintos artistas usando el titulo del mejor match (via iTunes)
        if matches:
            try:
                results.extend(itunes_search(matches[0]["title"], "", limit=8))
            except Exception:
                pass
        seen, uniq = set(), []
        for r in results:
            k = (r["title"].lower().strip(), r["artist"].lower().strip())
            if k in seen:
                continue
            seen.add(k)
            uniq.append(r)
        return uniq

    def _got(self, results, err):
        if not self.winfo_exists():
            return
        self.btn.config(state="normal")
        if err is not None:
            self.status.config(text="Error: " + str(err), foreground="#b00")
            return
        self.results = results or []
        self.tv.delete(*self.tv.get_children())
        for i, r in enumerate(self.results):
            self.tv.insert("", "end", iid=str(i),
                           values=(r["title"], r["artist"], r["album"], r["source"]))
        if self.results:
            self.tv.selection_set("0")
        self.status.config(
            text=f"{len(self.results)} resultado(s). El primero es la coincidencia por letra; "
                 "los demas, versiones de distintos artistas.", foreground="#0a7a55")

    def apply_to_selected(self):
        sel = self.tv.selection()
        if not sel:
            messagebox.showinfo("Elegi un resultado", "Selecciona una fila de la lista.", parent=self)
            return
        r = self.results[int(sel[0])]
        ok = self.app.apply_result_to_selected(r)
        if ok:
            self.status.config(text="Datos aplicados a la pista seleccionada.", foreground="#0a7a55")
        else:
            messagebox.showinfo(
                "Sin pista seleccionada",
                "Selecciona primero una pista en la Biblioteca para aplicarle estos datos.",
                parent=self)


_APP_BASE = TkinterDnD.Tk if HAS_DND else tk.Tk


class App(_APP_BASE):
    def __init__(self):
        super().__init__()
        self.title("Organizador de Musica MP3  -  Walabi VJ")
        self.geometry("1100x680")
        self.minsize(900, 560)

        self.tracks = []                 # lista de dicts (read_track)
        self.dup_groups = []             # grupos de duplicados detectados
        self.q = queue.Queue()           # comunicacion hilo -> GUI
        self.folder = tk.StringVar()
        self.busy = False

        self.app_config = load_config()
        self.acoustid_key = self.app_config.get("acoustid_key", "")
        self.musixmatch_key = self.app_config.get("musixmatch_key", "")

        self._build_menubar()
        self._build_topbar()
        self._build_tabs()
        self._build_statusbar()
        self.after(100, self._poll_queue)

    # ---------- construccion de UI ----------
    def _build_menubar(self):
        menubar = tk.Menu(self)
        ajustes = tk.Menu(menubar, tearoff=0)
        ajustes.add_command(label="Clave de AcoustID...", command=self.set_acoustid_key)
        ajustes.add_command(label="Clave de Musixmatch...", command=self.set_musixmatch_key)
        menubar.add_cascade(label="Ajustes", menu=ajustes)
        self.config(menu=menubar)

    def set_acoustid_key(self):
        from tkinter import simpledialog
        key = simpledialog.askstring(
            "Clave de AcoustID",
            "Pega tu clave de API de AcoustID (gratis en acoustid.org):",
            initialvalue=self.acoustid_key, parent=self)
        if key is not None:
            self.acoustid_key = key.strip()
            self.app_config["acoustid_key"] = self.acoustid_key
            save_config(self.app_config)
            self.status.set("Clave de AcoustID guardada.")

    def set_musixmatch_key(self):
        from tkinter import simpledialog
        key = simpledialog.askstring(
            "Clave de Musixmatch",
            "Pega tu clave de API de Musixmatch (gratis en developer.musixmatch.com):",
            initialvalue=self.musixmatch_key, parent=self)
        if key is not None:
            self.musixmatch_key = key.strip()
            self.app_config["musixmatch_key"] = self.musixmatch_key
            save_config(self.app_config)
            self.status.set("Clave de Musixmatch guardada.")

    # ---------- construccion de UI ----------
    def _build_topbar(self):
        bar = ttk.Frame(self, padding=8)
        bar.pack(fill="x")
        ttk.Label(bar, text="Carpeta:").pack(side="left")
        ttk.Entry(bar, textvariable=self.folder).pack(
            side="left", fill="x", expand=True, padx=6)
        ttk.Button(bar, text="Elegir...", command=self.pick_folder).pack(side="left")
        self.scan_btn = ttk.Button(bar, text="Escanear", command=self.start_scan)
        self.scan_btn.pack(side="left", padx=6)
        ttk.Button(bar, text="Buscar por letra", command=self.open_lyrics_search).pack(side="left")

    def open_lyrics_search(self):
        LyricsSearch(self)

    def _build_tabs(self):
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=8, pady=4)
        self._build_library_tab()
        self._build_dup_tab()
        self._build_organize_tab()

    def _build_statusbar(self):
        sb = ttk.Frame(self, padding=(8, 4))
        sb.pack(fill="x")
        self.status = tk.StringVar(value="Listo. Elegi una carpeta y escanea.")
        ttk.Label(sb, textvariable=self.status).pack(side="left")
        self.progress = ttk.Progressbar(sb, mode="determinate", length=240)
        self.progress.pack(side="right")

    # ----- Pestania BIBLIOTECA -----
    def _build_library_tab(self):
        tab = ttk.Frame(self.nb, padding=6)
        self.nb.add(tab, text="  Biblioteca  ")

        # filtro
        top = ttk.Frame(tab)
        top.pack(fill="x", pady=(0, 4))
        ttk.Label(top, text="Filtrar:").pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self.refresh_library())
        ttk.Entry(top, textvariable=self.filter_var, width=40).pack(side="left", padx=6)
        self.count_lbl = ttk.Label(top, text="")
        self.count_lbl.pack(side="right")

        hint = "Doble clic para editar una pista   .   clic derecho para mas acciones"
        if HAS_DND:
            hint += "   .   arrastra hacia VirtualDJ o el Explorador"
        ttk.Label(tab, foreground="#888", text=hint).pack(fill="x", pady=(0, 4))

        cols = ("title", "artist", "album", "genre", "date", "dur", "kbps", "bpm", "file")
        heads = ("Titulo", "Artista", "Album", "Genero", "Anio", "Dur", "kbps", "BPM", "Archivo")
        widths = (190, 140, 140, 100, 45, 45, 45, 45, 200)

        mid = ttk.Frame(tab)
        mid.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="extended")
        for c, h, w in zip(cols, heads, widths):
            self.tree.heading(c, text=h, command=lambda c=c: self.sort_by(c))
            self.tree.column(c, width=w, anchor="w")
        vs = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda e: self.open_editor())
        self.tree.bind("<Button-3>", self.show_context_menu)   # clic derecho (Windows/Linux)
        self.tree.bind("<Button-2>", self.show_context_menu)   # clic derecho (Mac)
        if HAS_DND:
            try:
                self.tree.drag_source_register(1, DND_FILES)
                self.tree.dnd_bind("<<DragInitCmd>>", self._on_drag_init)
            except Exception:
                pass
        self._build_context_menu()

        # barra de acciones rapidas
        bar = ttk.LabelFrame(tab, text="Acciones rapidas", padding=8)
        bar.pack(fill="x", pady=(6, 0))
        ttk.Button(bar, text="Editar seleccionado", command=self.open_editor).pack(side="left")
        ttk.Button(bar, text="Buscar info online", command=self.online_lookup_selected).pack(side="left", padx=6)
        ttk.Button(bar, text="Adivinar desde nombre", command=self.guess_selected).pack(side="left")
        ttk.Button(bar, text="Analizar BPM", command=self.analyze_bpm_selected).pack(side="left", padx=6)
        ttk.Label(bar, text="    Genero:").pack(side="left")
        self.batch_genre_var = tk.StringVar()
        ttk.Entry(bar, textvariable=self.batch_genre_var, width=16).pack(side="left", padx=4)
        ttk.Button(bar, text="Aplicar a seleccion", command=self.batch_genre).pack(side="left")

    def _build_context_menu(self):
        m = tk.Menu(self, tearoff=0)
        m.add_command(label="Editar etiquetas...", command=self.open_editor)
        m.add_command(label="Buscar info online (iTunes + MusicBrainz)", command=self.online_lookup_selected)
        m.add_command(label="Identificar por huella (AcoustID)", command=self.acoustid_lookup_selected)
        m.add_command(label="Adivinar tags desde el nombre", command=self.guess_selected)
        m.add_command(label="Analizar BPM", command=self.analyze_bpm_selected)
        m.add_separator()
        m.add_command(label="Reproducir", command=self.play_selected)
        m.add_command(label="Abrir ubicacion del archivo", command=self.reveal_selected)
        m.add_separator()
        m.add_command(label="Mover a _Duplicados", command=self.move_selected_to_dup)
        self.context_menu = m

    def show_context_menu(self, event):
        row = self.tree.identify_row(event.y)
        if row:
            if row not in self.tree.selection():
                self.tree.selection_set(row)
            try:
                self.context_menu.tk_popup(event.x_root, event.y_root)
            finally:
                self.context_menu.grab_release()

    # ----- Pestania DUPLICADOS -----
    def _build_dup_tab(self):
        tab = ttk.Frame(self.nb, padding=6)
        self.nb.add(tab, text="  Duplicados  ")

        top = ttk.Frame(tab)
        top.pack(fill="x", pady=(0, 6))
        ttk.Label(top, text="Metodo:").pack(side="left")
        self.dup_method = tk.StringVar(value="hash")
        ttk.Radiobutton(top, text="Contenido exacto (hash)", value="hash",
                        variable=self.dup_method).pack(side="left", padx=4)
        ttk.Radiobutton(top, text="Titulo + Artista", value="tag",
                        variable=self.dup_method).pack(side="left", padx=4)
        fp_state = "normal" if has_fpcalc() else "disabled"
        ttk.Radiobutton(top, text="Huella de audio (fpcalc)", value="fingerprint",
                        variable=self.dup_method, state=fp_state).pack(side="left", padx=4)
        ttk.Button(top, text="Buscar duplicados", command=self.start_dup_scan).pack(side="left", padx=10)

        info = ("Tilda el tema a CONSERVAR en cada grupo (ya viene sugerido el de mejor calidad). "
                "El resto se mueve a la carpeta '_Duplicados'. No se borra nada.")
        ttk.Label(tab, text=info, wraplength=1040, foreground="#555").pack(fill="x", pady=(0, 6))

        mid = ttk.Frame(tab)
        mid.pack(fill="both", expand=True)
        self.dup_tree = ttk.Treeview(mid, columns=("keep", "info"), show="tree headings")
        self.dup_tree.heading("#0", text="Grupo / Archivo")
        self.dup_tree.heading("keep", text="Conservar")
        self.dup_tree.heading("info", text="Detalle")
        self.dup_tree.column("#0", width=520)
        self.dup_tree.column("keep", width=90, anchor="center")
        self.dup_tree.column("info", width=380)
        vs = ttk.Scrollbar(mid, orient="vertical", command=self.dup_tree.yview)
        self.dup_tree.configure(yscrollcommand=vs.set)
        self.dup_tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.dup_tree.bind("<Button-1>", self.toggle_keep)

        bottom = ttk.Frame(tab)
        bottom.pack(fill="x", pady=(6, 0))
        self.dup_summary = ttk.Label(bottom, text="")
        self.dup_summary.pack(side="left")
        ttk.Button(bottom, text="Mover duplicados a '_Duplicados'",
                   command=self.move_duplicates).pack(side="right")

    # ----- Pestania ORGANIZAR -----
    def _build_organize_tab(self):
        tab = ttk.Frame(self.nb, padding=6)
        self.nb.add(tab, text="  Organizar  ")

        top = ttk.Frame(tab)
        top.pack(fill="x", pady=(0, 6))
        ttk.Label(top, text="Esquema:").pack(side="left")
        self.org_scheme = tk.StringVar(value="genre")
        ttk.Radiobutton(top, text="Por Genero", value="genre",
                        variable=self.org_scheme).pack(side="left", padx=4)
        ttk.Radiobutton(top, text="Por Artista", value="artist",
                        variable=self.org_scheme).pack(side="left", padx=4)
        ttk.Radiobutton(top, text="Artista / Album", value="artist_album",
                        variable=self.org_scheme).pack(side="left", padx=4)
        self.org_copy = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Copiar (en vez de mover)",
                        variable=self.org_copy).pack(side="left", padx=12)
        ttk.Button(top, text="Previsualizar", command=self.preview_organize).pack(side="left", padx=6)

        mid = ttk.Frame(tab)
        mid.pack(fill="both", expand=True)
        self.org_tree = ttk.Treeview(mid, columns=("dest",), show="tree headings")
        self.org_tree.heading("#0", text="Archivo")
        self.org_tree.heading("dest", text="Carpeta destino")
        self.org_tree.column("#0", width=480)
        self.org_tree.column("dest", width=520)
        vs = ttk.Scrollbar(mid, orient="vertical", command=self.org_tree.yview)
        self.org_tree.configure(yscrollcommand=vs.set)
        self.org_tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")

        bottom = ttk.Frame(tab)
        bottom.pack(fill="x", pady=(6, 0))
        self.org_summary = ttk.Label(bottom, text="")
        self.org_summary.pack(side="left")
        ttk.Button(bottom, text="Aplicar organizacion",
                   command=self.apply_organize).pack(side="right")

        self._org_plan = []

    # ---------- helpers de estado ----------
    def set_busy(self, busy, msg=None):
        self.busy = busy
        self.scan_btn.config(state="disabled" if busy else "normal")
        if msg:
            self.status.set(msg)

    def pick_folder(self):
        d = filedialog.askdirectory(title="Elegi la carpeta con tu musica")
        if d:
            self.folder.set(d)

    # ---------- ESCANEO (en hilo aparte) ----------
    def start_scan(self):
        folder = self.folder.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showwarning("Carpeta invalida", "Elegi una carpeta valida primero.")
            return
        self.set_busy(True, "Escaneando...")
        self.progress.config(mode="indeterminate")
        self.progress.start(12)
        threading.Thread(target=self._scan_worker, args=(folder,), daemon=True).start()

    def _scan_worker(self, folder):
        files = []
        for root, _, names in os.walk(folder):
            if os.path.basename(root) == DUP_FOLDER_NAME:
                continue
            for n in names:
                if n.lower().endswith(SUPPORTED_EXT):
                    files.append(os.path.join(root, n))
        total = len(files)
        tracks = []
        for i, p in enumerate(files):
            tracks.append(read_track(p))
            if i % 20 == 0:
                self.q.put(("progress", i + 1, total, os.path.basename(p)))
        self.q.put(("scan_done", tracks))

    # ---------- DEDUPLICACION (en hilo) ----------
    def start_dup_scan(self):
        if not self.tracks:
            messagebox.showinfo("Sin datos", "Primero escanea una carpeta.")
            return
        method = self.dup_method.get()
        self.set_busy(True, "Buscando duplicados...")
        self.progress.config(mode="determinate", maximum=len(self.tracks), value=0)
        threading.Thread(target=self._dup_worker, args=(method,), daemon=True).start()

    def _dup_worker(self, method):
        def prog(i, total, msg):
            self.q.put(("progress", i, total, msg))
        groups = find_duplicates(self.tracks, method=method, progress=prog)
        self.q.put(("dup_done", groups))

    # ---------- POLLING de la cola ----------
    def _poll_queue(self):
        try:
            while True:
                kind, *rest = self.q.get_nowait()
                if kind == "progress":
                    i, total, msg = rest
                    if self.progress["mode"] == "determinate":
                        self.progress.config(maximum=max(total, 1), value=i)
                    self.status.set(f"{i}/{total}  {msg}")
                elif kind == "scan_done":
                    self.progress.stop()
                    self.progress.config(mode="determinate", value=0)
                    self.tracks = rest[0]
                    self.set_busy(False, f"Escaneadas {len(self.tracks)} pistas.")
                    self.refresh_library()
                elif kind == "dup_done":
                    self.dup_groups = rest[0]
                    self.set_busy(False)
                    self.show_duplicates()
                elif kind == "async":
                    cb, res, err = rest
                    try:
                        cb(res, err)
                    except Exception:
                        pass
                elif kind == "bpm_done":
                    self.set_busy(False, "Analisis de BPM completado.")
                    self.progress.config(value=0)
                    self.refresh_library()
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def run_async(self, fn, on_done):
        """Ejecuta fn() en un hilo y llama on_done(resultado, error) en el hilo de la GUI."""
        def worker():
            try:
                res, err = fn(), None
            except Exception as e:
                res, err = None, e
            self.q.put(("async", on_done, res, err))
        threading.Thread(target=worker, daemon=True).start()

    # ---------- BIBLIOTECA: mostrar / editar ----------
    def refresh_library(self):
        flt = self.filter_var.get().strip().lower()
        self.tree.delete(*self.tree.get_children())
        shown = 0
        for t in self.tracks:
            hay = " ".join([t["title"], t["artist"], t["album"],
                            os.path.basename(t["path"])]).lower()
            if flt and flt not in hay:
                continue
            mark = "* " if t["modified"] else ""
            self.tree.insert(
                "", "end", iid=t["path"],
                values=(mark + t["title"], t["artist"], t["album"], t["genre"],
                        t["date"], human_duration(t["duration"]),
                        t["bitrate"] // 1000 if t["bitrate"] else "",
                        t["bpm"],
                        os.path.basename(t["path"])))
            shown += 1
        self.count_lbl.config(text=f"{shown} de {len(self.tracks)} pistas")

    def _track_by_path(self, path):
        for t in self.tracks:
            if t["path"] == path:
                return t
        return None

    def _selected_tracks(self):
        return [self._track_by_path(p) for p in self.tree.selection()
                if self._track_by_path(p)]

    def _on_drag_init(self, event):
        """Arrastre nativo: entrega las rutas de los archivos seleccionados a la app destino."""
        paths = [p for p in self.tree.selection() if os.path.exists(p)]
        if not paths:
            return None
        return (COPY, DND_FILES, tuple(paths))

    def apply_result_to_selected(self, r):
        """Aplica los datos de un resultado online a la pista seleccionada en la Biblioteca."""
        sel = self.tree.selection()
        if not sel:
            return False
        t = self._track_by_path(sel[0])
        if not t:
            return False
        fields = {f: r[f] for f in ("title", "artist", "album", "date", "genre") if r.get(f)}
        if not fields:
            return False
        try:
            write_tags(t["path"], fields)
            t.update(fields)
            t["modified"] = True
        except Exception as e:
            messagebox.showerror("Error al guardar", str(e))
            return True
        self.refresh_library()
        return True

    def open_editor(self, auto_search=False, auto_acoustid=False):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Seleccion", "Elegi una pista de la lista.")
            return None
        t = self._track_by_path(sel[0])
        if not t:
            return None
        ed = TagEditor(self, t, on_save=self._after_editor_save)
        if auto_search:
            ed.after(200, ed.search_online)
        elif auto_acoustid:
            ed.after(200, ed.search_acoustid)
        return ed

    def _after_editor_save(self):
        self.refresh_library()
        self.status.set("Etiquetas guardadas.")

    def online_lookup_selected(self):
        self.open_editor(auto_search=True)

    def acoustid_lookup_selected(self):
        self.open_editor(auto_acoustid=True)

    def guess_selected(self):
        tracks = self._selected_tracks()
        if not tracks:
            messagebox.showinfo("Seleccion", "Elegi una o varias pistas.")
            return
        n = 0
        for t in tracks:
            artist, title = guess_from_filename(t["path"])
            fields = {}
            if title and not t["title"]:
                fields["title"] = title
            if artist and not t["artist"]:
                fields["artist"] = artist
            if not fields:
                continue
            try:
                write_tags(t["path"], fields)
                t.update(fields)
                t["modified"] = True
                n += 1
            except Exception:
                pass
        self.refresh_library()
        self.status.set(f"Tags completados desde el nombre en {n} pista(s).")

    def analyze_bpm_selected(self):
        tracks = self._selected_tracks()
        if not tracks:
            messagebox.showinfo("Seleccion", "Elegi una o varias pistas.")
            return
        if find_ffmpeg() is None:
            messagebox.showwarning(
                "Falta ffmpeg",
                "Para analizar BPM necesito 'ffmpeg'. Ponelo junto al programa "
                "(ffmpeg.exe en la misma carpeta) o instalalo y agregalo al PATH.")
            return
        self.set_busy(True, "Analizando BPM...")
        self.progress.config(mode="determinate", maximum=len(tracks), value=0)
        threading.Thread(target=self._bpm_worker, args=(tracks,), daemon=True).start()

    def _bpm_worker(self, tracks):
        for i, t in enumerate(tracks):
            try:
                bpm = detect_bpm(t["path"])
            except Exception:
                bpm = None
            if bpm:
                value = str(int(round(bpm)))
                try:
                    write_tags(t["path"], {"bpm": value})
                    t["bpm"] = value
                    t["modified"] = True
                except Exception:
                    pass
            self.q.put(("progress", i + 1, len(tracks), os.path.basename(t["path"])))
        self.q.put(("bpm_done", None))

    def batch_genre(self):
        tracks = self._selected_tracks()
        if not tracks:
            messagebox.showinfo("Seleccion", "Elegi una o varias pistas (Ctrl/Shift).")
            return
        genre = self.batch_genre_var.get().strip()
        if not genre:
            messagebox.showinfo("Genero vacio", "Escribi un genero en el campo de al lado.")
            return
        n = 0
        for t in tracks:
            try:
                write_tags(t["path"], {"genre": genre})
                t["genre"] = genre
                t["modified"] = True
                n += 1
            except Exception:
                pass
        self.refresh_library()
        self.status.set(f"Genero '{genre}' aplicado a {n} pista(s).")

    def play_selected(self):
        sel = self.tree.selection()
        if sel:
            open_with_default_app(sel[0])

    def reveal_selected(self):
        sel = self.tree.selection()
        if sel:
            reveal_in_file_manager(sel[0])

    def move_selected_to_dup(self):
        tracks = self._selected_tracks()
        if not tracks:
            return
        if not messagebox.askyesno(
                "Confirmar",
                f"Mover {len(tracks)} archivo(s) a la carpeta '{DUP_FOLDER_NAME}'? No se borra nada."):
            return
        base = self.folder.get() or os.path.dirname(tracks[0]["path"])
        dest = os.path.join(base, DUP_FOLDER_NAME)
        n = 0
        for t in tracks:
            try:
                safe_move(t["path"], dest, copy=False)
                if t in self.tracks:
                    self.tracks.remove(t)
                n += 1
            except Exception:
                pass
        self.refresh_library()
        self.status.set(f"Movido(s) {n} archivo(s) a {DUP_FOLDER_NAME}.")

    def sort_by(self, col):
        keymap = {"title": "title", "artist": "artist", "album": "album",
                  "genre": "genre", "date": "date", "kbps": "bitrate",
                  "dur": "duration", "bpm": "bpm", "file": "path"}
        key = keymap.get(col, "title")
        self.tracks.sort(key=lambda t: (str(t.get(key, "")).lower()
                                        if isinstance(t.get(key), str) else t.get(key, 0)))
        self.refresh_library()

    # ---------- DUPLICADOS: mostrar / mover ----------
    def show_duplicates(self):
        self.dup_tree.delete(*self.dup_tree.get_children())
        if not self.dup_groups:
            self.dup_summary.config(text="No se encontraron duplicados con este metodo.")
            return
        self._keep = {}      # path -> bool (conservar)
        total_extra = 0
        for gi, group in enumerate(self.dup_groups):
            keeper = choose_keeper(group)
            parent = self.dup_tree.insert(
                "", "end", text=f"Grupo {gi + 1}  ({len(group)} copias)", open=True,
                values=("", ""))
            for t in group:
                keep = (t is keeper)
                self._keep[t["path"]] = keep
                kbps = f"{t['bitrate'] // 1000} kbps" if t["bitrate"] else "?"
                detail = f"{kbps} | {human_duration(t['duration'])} | {t['size'] // 1024} KB"
                self.dup_tree.insert(parent, "end", iid=t["path"],
                                     text=t["path"],
                                     values=("[X]" if keep else "[ ]", detail))
            total_extra += len(group) - 1
        self.dup_summary.config(
            text=f"{len(self.dup_groups)} grupos, {total_extra} archivos sobran (se moverian).")

    def toggle_keep(self, event):
        row = self.dup_tree.identify_row(event.y)
        col = self.dup_tree.identify_column(event.x)
        if not row or row not in getattr(self, "_keep", {}):
            return
        if col != "#1":   # solo la columna "Conservar"
            return
        # buscar a que grupo pertenece y hacer exclusivo (radio)
        parent = self.dup_tree.parent(row)
        for sib in self.dup_tree.get_children(parent):
            self._keep[sib] = (sib == row)
            self.dup_tree.set(sib, "keep", "[X]" if sib == row else "[ ]")

    def move_duplicates(self):
        if not self.dup_groups:
            return
        to_move = [p for p, keep in getattr(self, "_keep", {}).items() if not keep]
        if not to_move:
            messagebox.showinfo("Nada que mover", "Todos estan marcados para conservar.")
            return
        if not messagebox.askyesno(
                "Confirmar",
                f"Se moveran {len(to_move)} archivos a la carpeta '{DUP_FOLDER_NAME}'.\n"
                "No se borra nada. Continuar?"):
            return
        dest = os.path.join(self.folder.get(), DUP_FOLDER_NAME)
        moved = 0
        for path in to_move:
            try:
                safe_move(path, dest, copy=False)
                t = self._track_by_path(path)
                if t in self.tracks:
                    self.tracks.remove(t)
                moved += 1
            except Exception:
                pass
        messagebox.showinfo("Listo", f"Se movieron {moved} duplicados a:\n{dest}")
        self.dup_groups = []
        self.show_duplicates()
        self.refresh_library()

    # ---------- ORGANIZAR ----------
    def preview_organize(self):
        if not self.tracks:
            messagebox.showinfo("Sin datos", "Primero escanea una carpeta.")
            return
        dest_root = self.folder.get()
        self._org_plan = plan_organization(self.tracks, self.org_scheme.get(), dest_root)
        self.org_tree.delete(*self.org_tree.get_children())
        folders = set()
        for t, folder in self._org_plan:
            rel = os.path.relpath(folder, dest_root)
            folders.add(rel)
            self.org_tree.insert("", "end", text=os.path.basename(t["path"]), values=(rel,))
        verbo = "copiarian" if self.org_copy.get() else "moverian"
        self.org_summary.config(
            text=f"{len(self._org_plan)} archivos se {verbo} a {len(folders)} carpetas.")

    def apply_organize(self):
        if not self._org_plan:
            messagebox.showinfo("Previsualiza primero", "Presiona 'Previsualizar' antes de aplicar.")
            return
        copy = self.org_copy.get()
        verbo = "copiar" if copy else "mover"
        if not messagebox.askyesno("Confirmar", f"Se van a {verbo} {len(self._org_plan)} archivos. Continuar?"):
            return
        ok = 0
        for t, folder in self._org_plan:
            try:
                newpath = safe_move(t["path"], folder, copy=copy)
                if not copy:
                    t["path"] = newpath
                ok += 1
            except Exception:
                pass
        messagebox.showinfo("Listo", f"Se procesaron {ok} archivos.")
        self._org_plan = []
        self.org_tree.delete(*self.org_tree.get_children())
        self.refresh_library()


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
