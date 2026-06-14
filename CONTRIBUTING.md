# Cómo contribuir

¡Gracias por querer aportar al Organizador de Música! Acá va lo necesario para empezar.

## Levantar el proyecto

1. Cloná el repositorio:
   ```bash
   git clone https://github.com/Walabi-Vj-dev/organizador-musica.git
   cd organizador-musica
   ```
2. Instalá las dependencias:
   ```bash
   pip install -r requirements.txt
   ```
3. Corré el programa:
   ```bash
   python organizador_musica.py
   ```

## Proponer cambios

1. Hacé un *fork* del repositorio y creá una rama para tu cambio:
   ```bash
   git checkout -b mi-mejora
   ```
2. Hacé tus cambios y probalos.
3. Confirmá (commit) con un mensaje claro de lo que hiciste:
   ```bash
   git commit -m "Agrega previsualización de carátula en el editor"
   ```
4. Subí la rama y abrí un *Pull Request* explicando el qué y el por qué.

## Reportar errores o pedir funciones

Abrí un *Issue* describiendo:

- Qué hiciste (pasos para reproducir).
- Qué esperabas que pasara.
- Qué pasó en realidad (con el mensaje de error si lo hay).
- Tu versión de Windows y de Python.

## Estilo de código

- El proyecto es un único archivo de Python con la lógica separada de la interfaz; tratá de mantener esa separación.
- Nombres de variables y comentarios en español, claros y concisos.
- Seguí el estilo del código existente (cercano a PEP 8).

## Ideas para sumar

- Previsualización de la carátula antes de incrustarla.
- Autocompletar etiquetas en lote (identificar y etiquetar muchos archivos de una).
- Soporte para más formatos de audio.
- Traducción de la interfaz a otros idiomas.

¡Toda ayuda suma!
