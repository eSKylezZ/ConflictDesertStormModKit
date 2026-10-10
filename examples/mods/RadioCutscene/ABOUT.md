# RadioCutscene

Mission 1's opening cutscene (the helicopter flight in) with everything the squad and the pilot say coming over a
crackly field radio. The music and sound effects are untouched, and the scene keeps its exact length.

Shows: replacing a cutscene's sound. Cutscenes are played by the game engine, and each scene's sound is one file -
music, effects and speech mixed together - per language in `<game>\Data\Sound`:

- `English\CS_M01_S01.psf` = the SAS voices -> this mod's `Sound\SAS\CS_M01_S01.psf`
- `American\CS_M01_S01.psf` = the Delta voices -> this mod's `Sound\Delta\CS_M01_S01.psf`

(A file directly in `Sound\` would replace the scene for every language.)

## How it was made

`examples/build_examples.py` does these steps with code (`radio_cutscene`); by hand they are:

1. Turn the scene into a WAV you can edit:
   `python cli/ds_sound.py stream "<game>\Data\Sound\English\CS_M01_S01.psf" sas.wav`
   (and the same for `American\` -> `delta.wav`).
2. Find the speech. Open both WAVs in an audio editor (Audacity, for example): the music and effects are the same in
   both, so wherever they differ someone is talking - here at about 6.7-11.4 s, 21.7-24.3 s and 29.4-33.1 s.
3. Edit only those stretches. Here: select a stretch, mix it to mono, apply a band-pass (about 450-1900 Hz), add a
   little distortion and hiss, and put a short burst of noise at its start and end (the radio's squelch).
   Instead you could mute the speech and record your own lines over it.
4. Keep the length. The pictures don't wait for the sound: a longer file plays on into the mission, a shorter one
   ends early.
5. Save it back in the game's format, into the mod folder:
   `python cli/ds_sound.py psf sas.wav Mods\RadioCutscene\Sound\SAS\CS_M01_S01.psf`
   (and `delta.wav` -> `Sound\Delta\`).

Start Mission 1: the cutscene after the loading screen plays with the radio voices. The plugin's report
(`Mods\DesertStormFix-report.txt`) lists the replaced scene once it has played.
