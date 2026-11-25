PANSHOW
A full-screen pan & zoom photo slideshow. Each photo gets a slow, smooth camera move
(the "Ken Burns effect"); when there are faces in a photo, the camera moves towards them.


FIRST START
- The settings window opens. Choose your photo folder and press "Start slideshow".
- "Show on screen: Automatic" uses your biggest screen (for example a TV or 4K monitor).
- On a 4K screen, if photos move too little, set "Sharpness or movement" to
  "Allow slight enlargement (1.2x)".
- "Amount of movement" has two energetic styles. They work best with music and short photo
  times (2-4 seconds):
    * Wild: much bigger, faster moves that keep going right up to the next cut, big tilts
      (5-9 degrees, also on horizons, on purpose) and quick 0.2-second cuts instead of slow
      crossfades. No pulsing on the beat.
    * Hyperdrive: the fastest moves, tilts up to 12 degrees and flash cuts (0.12 s). With music,
      a gentle zoom pulse swells on each beat (a little more at each bar start, where the
      picture also leans slightly left or right).
  Both allow photos to be enlarged a little more than usual at their closest point (fast
  motion hides it), to make room for the faster moves.
- "Order": shuffled, by date taken (oldest first) or by file name. "By date taken" uses when
  each photo was taken (from the photo itself, or the date in its file name, as phones name
  them). The slideshow starts from the beginning each time; tick "Continue where the last
  slideshow stopped" to carry on after the last photo you saw instead.
  "Stop after the last photo": after every photo has been shown once, the picture and the
  music fade out and the slideshow closes (otherwise it starts again from the beginning).
  Next to the photo folder, Panshow shows how many photos it found and how long one round
  takes with your "seconds per photo".
- "Camera path": gently curved (the camera travels on a slight arc, like a real camera
  crane) or straight lines (the classic Ken Burns move).
- "Gentle tilt": the photo also turns very slightly (under one degree) while it moves. It is
  never used for photos with horizons or buildings, which would look crooked.
- "Flowing moves": each photo's camera movement continues the direction of the previous
  one, so the crossfades feel like one continuous camera move.
- "Music", one of:
    * Songs from a folder (mp3, flac, wav, ogg), played by Panshow in random order.
    * Listen to this computer's sound: play music in any app (Spotify, YouTube Music, a web
      radio...) and Panshow follows its beat. Space can also pause that app.
    * Listen through the microphone: for music from another speaker in the room.
  With "Change photos on the beat", each photo changes on a beat: the time per photo then
  varies a little (at most 15%) around your setting. When listening, Panshow needs a few
  seconds of each song to find its beat. "Test listening..." checks that it hears the music.
- "Tall (portrait) photos": the whole photo on a blurred background, filling the screen with
  a slow pan, or two tall photos side by side, each with its own camera move (a tall photo
  waits a moment for a partner; if none comes, it is shown on a blurred background).

The camera aims at faces first; when there are none, at pets and other animals, and at
people whose face is not visible (for example seen from behind).


KEYS WHILE THE SLIDESHOW RUNS
  Esc           quit
  S             settings
  Space         pause / continue
  Left arrow    previous photo
  Right arrow   next photo

Only the photos are shown: no text and no mouse pointer. Photos are shown in random order;
every photo is shown once before any photo comes again.

For the smoothest motion, leave Panshow as the active window. If you click on another
program, click on the slideshow again afterwards.


SAVE AS VIDEO
"Save as video..." in the settings window makes an MP4 video file of the slideshow (4K or
Full HD, 60 frames per second), for example to play on a TV from a USB stick or to share.
- It uses the settings in the settings window (photos, timing, movement...).
- Music: none, the songs in your song folder, or songs you choose (mp3, flac, wav, ogg).
  The songs are built into the video, and photo changes land on their beats. The length can
  follow the music, be "every photo once" (the video ends after the last photo), or be set
  in minutes.
- Music from Spotify, YouTube Music and similar services cannot be put into a video: those
  apps do not hand out their songs. Make a video without music and play music with it, or
  use your own song files.
- If you share a video online, use music you are allowed to share: video sites may mute or
  block videos with commercial songs.
- Making a video takes a while (a 4K video roughly as long as it plays). A small window
  shows how far it is, and you can keep using your computer. 4K takes about 300 MB per
  minute, Full HD about 100 MB. If a TV cannot play the 4K file, make a Full HD one.
- Videos are made with Windows' own video encoder (H.264 with AAC sound).


UPDATES
Panshow looks for new versions on GitHub (github.com/jakentta/panshow) and installs them
quietly when you quit it. Every update is digitally signed and checked before it is used.
If a new version does not start properly, Panshow goes back to the previous one.
You can switch automatic updates off in the settings.


PRIVACY
- Your photos never leave your computer. Face finding happens on your own computer.
- Panshow keeps its settings and the remembered face positions on your computer only:
    %APPDATA%\Panshow          (settings)
    %LOCALAPPDATA%\Panshow     (face positions, file list, log files, downloaded updates)
- When Panshow listens (to this computer's sound or through the microphone), the sound is
  only analysed on the fly to find the beat. It is never recorded, stored or sent anywhere.
  Windows shows its usual microphone symbol while the microphone is in use.
- The only internet connection Panshow makes is the update check: it asks GitHub for the
  latest version file. Nothing about you or your photos is sent. Like any web request, GitHub
  sees your internet (IP) address. Switch automatic updates off in the settings to stop it.


REMOVING PANSHOW
Windows Settings > Apps > Installed apps > Panshow > Uninstall.
Settings and face positions stay in the folders above; delete them too if you want
everything gone.


IF SOMETHING GOES WRONG
The log file says what happened: %LOCALAPPDATA%\Panshow\panshow.log
(and panshow-video.log for making videos)


LICENCES
Panshow is free to use. It is built with open-source components; their licences are
listed in THIRD-PARTY-NOTICES.txt in the program folder.
