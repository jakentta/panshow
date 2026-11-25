# How Panshow works inside

Panshow draws a photo slideshow live on the graphics card: every photo gets a slow camera move
(pan and zoom) that is aware of faces, people and animals. The design goals are, in order:

1. **Smooth.** Every frame on time, with the screen's own refresh. Positions and zoom are floating
   point in every frame, never rounded to whole pixels.
2. **Sharp.** The photo is never enlarged beyond its real pixels unless the user allows it. It is
   filtered in linear light, with a 4-tap filter when shrinking and a Catmull-Rom filter when
   enlarging.
3. **Natural camera.** Faces first (then pets, people, interesting areas), near the rule-of-thirds
   lines, never cut off, with soft starts and stops.

## Processes

| Process | Job |
|---|---|
| main (`show.py`) | window, frame clock, time line, textures, keys |
| worker (`worker.py`) | finds and orders photos, decodes, detects faces, plans the camera move, pre-scales, hands pixels over through shared memory; also checks for updates |
| music (`music.py`, optional) | plays songs from a folder, or listens to the computer's sound / the microphone, and finds the beat |
| video (`video.py`, optional) | makes an MP4 of the slideshow frame by frame |

The main process must never wait for a disk, a decoder or a detector. The worker therefore
prepares the next photos while the current one is on screen.

## Files

- `app.py` start-up, command-line options; `config.py` settings (JSON in `%APPDATA%\Panshow`)
- `planner.py` chooses the shot (push in, pull out, group, drift, pan...), composes it and limits
  the zoom by the photo's resolution; `camera.py` the move itself
- `analysis.py` face detection (OpenCV YuNet), people and animals (YOLOX), interesting areas,
  straight lines, and the cache of what was found
- `imaging.py` EXIF rotation, colour profiles, dates, high quality pre-scaling
- `render.py` the shader; `styles.py` numbers of the energetic styles
- `music.py` beat finder; `video.py` / `mp4writer.py` save as video (Windows Media Foundation)
- `update.py` signed automatic updates; `version.py` version and update address
- `installer/panshow.iss`, `build.ps1` the installer; `tools/` tests, measurements, release scripts

## The camera move

A frame is a rectangle `(cx, cy, w)` in photo pixels. Between the start and end rectangle the
camera moves as one smooth similarity transform with log-scaled zoom. On top of that a plan may
have a sideways *bow* (a gentle arc), a small *tilt*, and, for the energetic styles, a different
timing curve. Before a plan is used, 25 points along the path are checked so that the photo's edge
never shows and the subjects stay whole.

*Flowing moves:* ten candidate plans are made and the one whose start best continues the previous
photo's end is used, so a crossfade feels like one camera move.

Two tall photos can share the screen. Each half gets its own plan for its own aspect ratio, and
the textures for the halves are made before the show starts.

## Smoothness: what matters

- Animation time advances in exact multiples of the measured refresh period.
- Textures are made once at the start and reused: creating one costs milliseconds. Every new
  photo is drawn once into a tiny hidden buffer before it is shown.
- The worker is never asked for the next photo during a crossfade: on a laptop chip the CPU load
  slows the graphics.
- Late frames are written to the log together with what happened in that frame.

## Music and beat timing

Songs are analysed with a band-balanced onset signal, a tempo estimate by autocorrelation and beat
tracking by dynamic programming. The raw beat times come out about 29 ms early (the onset enters
the analysis window before its centre); `ONSET_LAG` corrects that. Photo changes are moved onto a
beat within 15% of the chosen time. Live listening has its own calibration, because captured sound
arrives late.

## Save as video

The same worker and drawing code run without a clock. Frames are drawn in 16-bit float, converted
to NV12 (BT.709, TV levels) on the graphics card, and written by Windows Media Foundation
(H.264 + AAC). FFmpeg/x264 are not used: GPL encoders would not fit Panshow's licence.

## Updates

Each release has an installer, a `manifest.json` (version, size, SHA-256) and its Ed25519
signature. Panshow checks GitHub, verifies the signature against the public key in `version.py`
and the checksum of the installer, then installs when you quit. The private key never leaves the
maintainer's computer. A copy that fails to start twice returns to the version before it.

## Tests

No camera, sound card or screen is needed for `tools/test_planner.py`, `test_kicks.py`,
`test_beats.py`, `test_order.py <scratch folder>` and `test_update.py <scratch folder>`.
`test_video.py` needs PyAV as a reader for the test only.
