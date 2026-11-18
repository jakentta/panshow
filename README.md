# Panshow

A full-screen **pan & zoom photo slideshow** for Windows. Every photo gets a slow, smooth camera
move (the "Ken Burns effect"), drawn live by the graphics card, so it stays sharp and smooth
even on a 4K screen. When a photo has faces in it, the camera moves towards them; photos
without faces get a gentle move towards their most interesting part.

## Download and install

Download **Panshow-Setup-x.y.z.exe** from the
[Releases page](https://github.com/jakentta/panshow/releases/latest) and run it.
It installs for your own Windows account; no administrator rights are needed.

Windows may say *"Windows protected your PC"*, because Panshow is not signed by a paid
certificate. Click **More info**, then **Run anyway**.

Needs Windows 10 or 11 (64-bit).

## Using it

The first time, the settings window opens: choose your photo folder and press
**Start slideshow**. After that Panshow starts the slideshow straight away.

| Key | What it does |
|---|---|
| Esc | quit |
| S | settings |
| Space | pause / continue |
| Left / Right arrow | previous / next photo |

Only the photos are shown, with no text and no mouse pointer. Photos are shown in random order:
every photo once before any comes again.

Settings include: photo folder, which screen to use (automatically the biggest one), seconds per
photo, crossfade length, order, amount of movement, sharpness versus movement, and how to show
tall (portrait) photos.

## How the camera works

- Faces are found on your own computer (OpenCV YuNet). The camera pushes in or pulls out on a
  face, frames a group, or drifts from one face to another. Faces sit near the rule-of-thirds
  lines with room above them, and are never cut by the edge of the frame at the start or end of
  a move.
- Zoom changes at an even pace to the eye (logarithmic), moves ease in and out gently, and they
  overlap the crossfades so the picture never stops and restarts.
- Photos are never enlarged beyond their real pixels unless you allow it, so they stay sharp.
- The next photo is prepared in a separate background process, so loading never makes the
  motion stutter.

## Updates

Panshow checks GitHub for a newer version about a minute after it starts, downloads it in the
background, and installs it quietly when you quit. Every update is digitally signed; Panshow
refuses anything without a valid signature. If a new version does not start properly, Panshow
goes back to the previous one. Automatic updates can be switched off in the settings.

## Privacy

- Your photos never leave your computer.
- Settings and remembered face positions are stored only on your computer
  (`%APPDATA%\Panshow` and `%LOCALAPPDATA%\Panshow`).
- The only internet connection is the update check, which asks GitHub for the latest version
  file. Nothing about you or your photos is sent (GitHub sees your IP address, as with any web
  request). Switch automatic updates off to stop it.

## Licences

Panshow is free to use. It is built with open-source components (Python, OpenCV, NumPy,
Pillow, pi-heif/libheif, moderngl, GLFW, cryptography and others); their licences are in
`THIRD-PARTY-NOTICES.txt` in the program folder.
