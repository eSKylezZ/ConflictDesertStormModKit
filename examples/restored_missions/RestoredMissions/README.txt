RestoredMissions - cut content put back into the training missions and Mission 1
=================================================================================

Needs DesertStormFix 3.3 or later (it applies these patches to Training1-3.dll and Mission1.dll as readable Lua).
Mission 1's changes make it a little harder.
Replaces the earlier TrainingRestored mod (remove that one).

Training 1 (Basic Training)
  - The cut intro film: the parade ground with the marching cadence, then the commander's office ("Enter!" ...
    "Okay then soldier, get moving."), with its own actors, cameras, date / place cards and subtitles.
  - The "switch between targets" lesson on the pistol range ("Ahead of you there are two targets! Try switching
    aim between them!").
  - The armourer gets your kit from the shelf ("Hold on a minute, I'll just get your kit.").
  - A Black Hawk flies over as the assault course starts.
  - Camera fly-bys over the assault course ("ahead of you is my assault course") and the shooting range ("Now step
    up to the firing plate behind you!") - the scene's own unused cameras.
  - CONGRATULATIONS before the end screen.

Training 2 (Advanced Weapons Training)
  - Master Sergeant Johnson walks to the observation tower and climbs its ladder (the scene had him standing on it
    from the start).
  - The cut radio call before the truck ("About time, you better hustle. Franks is waiting for you in the next
    training area. Take the truck, okay?").
  - The SAS commander pacing his office, the office guard and the off-map support instructor at the viewport.
  - A chase shot of the A-10's attack run after your second laser lock.
  - The briefing's full story text.
  - A guard patrols beside the start gate.

Training 3 (Squad Training)
  - Master Sergeant Franks leaves his platform and watches the command arena from above with his loudspeaker.
  - The Medi-Kit lesson asks you to select the Medi-Kit yourself (Inventory) before you heal.
  - The shooting range tells you when you hit a civilian target.
  - The Black Hawk leaves at the end of its route instead of hanging outside the map.
  - The briefing's full story text, CONGRATULATIONS before the end screen.

Mission 1 (Rescue) - from the PS2 demo build's script
  - The guard at the small bridge who waves the truck on runs up to the base afterwards and raises the alarm when
    he gets there (about 25 seconds). Stop him first. If he makes it, he also joins the hunters going after you.
  - Foley takes the Medi-Kit from the table along with his rifle and ammo (in co-op it stays for his player to pick
    up).

How it works: the four .patch.lua files are mission patches (DesertStormFix 3.3 or later). DesertStormFix turns
your own Training1-3.dll and Mission1.dll into readable Lua, applies these patches to it and runs the result instead
of the DLL. Nothing of the game is in this folder, so it can be shared as it is. TRAINING1_STREAMS.sch adds the sound
cache entries for Training 1's cut intro film (the films themselves, CS_T01_S01/S02.psf, are on your disc).
To install, copy the RestoredMissions folder into <game>\Mods. To switch it off, rename the folder to
_RestoredMissions (or use the game's MODS menu). DesertStormFix.log and Mods\DesertStormFix-report.txt say
"Training1.dll runs as readable Lua with 1 mission patch(es): RestoredMissions\Training1.patch.lua".
