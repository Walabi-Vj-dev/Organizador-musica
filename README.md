# Organizador de Música

Aplicación de escritorio para poner orden en tu biblioteca de música: edita etiquetas, encuentra y separa duplicados, organiza por carpetas, analiza el BPM, busca la información de cada tema por internet e incrusta la carátula del álbum. Pensada para colecciones grandes y desordenadas, con nombres inconsistentes y archivos sin datos.

![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![Plataforma](https://img.shields.io/badge/Windows-compatible-success)
![Licencia](https://img.shields.io/badge/Licencia-MIT-green)

> Hecho por **Walabi VJ**. Un solo archivo, sin base de datos, sin servicios pagos.

---

## Características

- **Multi-formato:** MP3, FLAC, M4A/MP4, OGG y Opus, con una interfaz unificada para todos.
- **Edición de etiquetas:** doble clic para abrir el editor, o clic derecho para acciones rápidas. Edición individual o por lote (por ejemplo, aplicar un género a varias pistas).
- **Detección de duplicados** por tres métodos: contenido exacto (hash), título + artista, o huella de audio. Mueve los duplicados a una carpeta aparte sin borrar nada.
- **Organización automática** en subcarpetas por Género, por Artista o por Artista/Álbum.
- **Análisis de BPM:** estima el tempo de cada tema y lo guarda en la etiqueta (ideal para música de pista).
- **Búsqueda de información online** en dos fuentes combinadas: **iTunes** (trae también el género) y **MusicBrainz**. Sin clave ni cuenta.
- **Identificación por huella de audio (AcoustID):** reconoce el tema por el audio mismo, incluso si el archivo no tiene ningún dato (`track1.mp3`).
- **Carátula del álbum:** descarga la portada en alta resolución desde iTunes y la incrusta en el archivo.
- **Adivinar etiquetas** a partir del nombre del archivo (`01_Take on me`, `Aha - Take on me`, etc.).
- **Portable:** se puede empaquetar como un `.exe` único que corre en cualquier Windows sin instalar Python.

---

## Capturas

Poné tus imágenes en una carpeta `capturas/` y enlazalas acá. Por ejemplo:

```
![Biblioteca](capturas/biblioteca.png)
![Editor de etiquetas](capturas/editor.png)
![Duplicados](capturas/duplicados.png)
```

---

## Requisitos

- **Python 3.8 o superior** (el instalador oficial de [python.org](https://www.python.org/downloads/) ya incluye Tkinter).
- La librería **mutagen**:

```bash
pip install mutagen
```

(o bien `pip install -r requirements.txt`)

---

## Uso desde el código

```bash
python organizador_musica.py
```

Se abre la ventana del programa. Elegí la carpeta de música, escaneá y a trabajar.

---

## Herramientas opcionales

Algunas funciones usan utilidades externas. No son obligatorias: el programa avisa cuando hace falta alguna, y el resto sigue funcionando sin ellas.

| Función | Necesita | Cómo obtenerlo |
|---|---|---|
| Análisis de BPM | `ffmpeg` | Descargá la build de Windows desde [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) y dejá `ffmpeg.exe` junto al programa, o instalalo en el PATH. |
| Duplicados por huella e identificación AcoustID | `fpcalc` (Chromaprint) | Descargá el paquete de Windows desde [los releases de Chromaprint](https://github.com/acoustid/chromaprint/releases) y dejá `fpcalc.exe` junto al programa. |
| Identificación AcoustID | Clave de API | Registrá una aplicación (gratis) en [acoustid.org/my-applications](https://acoustid.org/my-applications) y cargá la clave en **Ajustes → Clave de AcoustID**. |

> El programa detecta `ffmpeg.exe` y `fpcalc.exe` automáticamente si están en la misma carpeta que él.

---

## Generar el ejecutable (.exe)

Para distribuirlo sin que el usuario instale Python:

```bash
pip install pyinstaller
python -m PyInstaller --onefile --windowed --name OrganizadorMusica organizador_musica.py
```

El ejecutable queda en `dist/OrganizadorMusica.exe`. Ese archivo se publica en la sección **Releases** del repositorio, no dentro del código.

---

## Cómo usarlo

1. **Escanear:** elegí la carpeta raíz de tu música y presioná *Escanear*. Aparecen todas las pistas en la pestaña *Biblioteca*.
2. **Editar:** doble clic en una pista para abrir el editor. Clic derecho para acciones rápidas (editar, buscar online, identificar por huella, analizar BPM, reproducir, abrir ubicación, mover a duplicados).
3. **Duplicados:** en la pestaña *Duplicados*, elegí el método y buscá. Marcá cuál conservar y movés el resto a la carpeta `_Duplicados`.
4. **Organizar:** en la pestaña *Organizar*, elegí el esquema (género / artista / artista-álbum), previsualizás y aplicás.

> Consejo: antes de correrlo sobre toda tu biblioteca, probalo con una copia de una subcarpeta chica.

---

## Contribuir

¡Las contribuciones son bienvenidas! Mirá [CONTRIBUTING.md](CONTRIBUTING.md) para saber cómo levantar el proyecto y proponer cambios.

---

## Licencia

Distribuido bajo licencia [MIT](LICENSE). Usalo, modificalo y compartilo libremente.

---

## Autor

**Eric — Walabi VJ**
GitHub: [@Walabi-Vj-dev](https://github.com/Walabi-Vj-dev)
