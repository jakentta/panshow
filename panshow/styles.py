"""The two energetic movement styles, in one place so they are easy to tune.
(tools\\wild_stats.py measures them: speeds, how even the speed is, and the beat pulses.)

motion    how big the moves are (Normal = 1.0): zoom range, travel and bend all grow with it
extend    each move also travels this much further in its own direction (0.6 = 60% longer),
          where the photo has room and no face that was in view leaves it
tilt      total turn of the photo over one move, in degrees (random between the two values)
stretch   at least this much enlargement is allowed (room for big tilts; fast moves hide it)
punch     timing curve: each move starts at (1 + punch) x its average speed right at the cut,
          and ends at (1 - punch) x. Kept modest: a big surge at every cut followed by an
          almost-stop looks like pulsing.
cut       crossfade length in seconds (quick cuts)
kick_beat zoom pulse on every beat of the music (0.012 = 1.2% closer at the beat); each pulse
          swells up smoothly just before the beat and eases away after it (show.pulse_shape)
kick_bar  zoom pulse on the first beat of each bar
twist     on each bar start the picture also turns a little and back, alternating left and
          right (its angle is always covered by the zoom pulse, so no photo edge can show)

Version 1.5.0: Wild has no beat pulses at all; Hyperdrive's are much smaller and smooth (they
used to jump 9% in a single frame). Both move faster and more evenly instead.
"""
WILD_STYLES = {
    "wild": dict(motion=2.9, extend=0.5, tilt=(5.0, 9.0), stretch=1.45, punch=0.25, cut=0.2,
                 kick_beat=0.0, kick_bar=0.0, twist=False),
    "hyperdrive": dict(motion=3.4, extend=0.8, tilt=(7.0, 12.0), stretch=1.6, punch=0.4, cut=0.12,
                       kick_beat=0.012, kick_bar=0.035, twist=True),
}
