# SCC CoD HUD

An overlay for Call of Duty broadcasts that sits on top of the game's own
CoD caster HUD, laid out as on CDL broadcasts:

- **Name plates** over the HUD's team bars: each team's name, with a strip
  of its logo at the outer end (the NEW COD OVERLAY vMix title).
- **Score box**: the teams' logos over the game's, and the series score in
  place of the game's pips (with "BEST OF n").
- **Webcam**: the spectated player's cam, left of the game's player card.
  Players connect their webcams with the SCC Player Cam app (Call of Duty
  mode, with their in-game name).
- **Sponsors**, one after another, under the middle of the screen.
- **Auto hide**: everything disappears when the CoD caster HUD goes off
  screen (killcams, menus, between maps, the caster HUD turned off, the game
  closed or minimised), and comes back with it.
- **Show HUD on screen** (top of the control panel): the overlay in a
  see-through window over the whole screen, on top of the game, without
  vMix or OBS. Clicks go through it to the game. Run Call of Duty in
  Windowed Fullscreen or Windowed (exclusive Fullscreen covers it).

The teams, logos, colours, series score and sponsors follow the SCC GFX
Engine's Global information (GFX Engine page). **Swap sides** (Match &
Teams) swaps the teams' names, logos, colours and series scores, in the
engine too.

The players' in-game names are read off the scoreboards (Windows text
recognition). On the **Players** page, *Save IGN to player* links each name
to their player in the GFX Engine's team roster, as in the VALORANT HUD.
The webcam shown is the cam of the player whose row is highlighted (or
whose name is on the player card): matched by their in-game name, the
roster player it's linked to, or a cam picked by hand on the Webcams page.

## Install

Download **SCC-COD-HUD-Setup.exe** from the
[latest release](https://github.com/owenbparker/COD_SCC_HUD/releases/latest)
and run it. It installs to `C:\SCC CoD HUD` and adds a desktop shortcut.
The app checks for updates when it starts and can update itself.

## Use

1. Run SCC CoD HUD on the PC with the Call of Duty caster client. It finds
   the Call of Duty window by its title and reads it, even while other
   windows are on top of it. If the window can't be read, switch the **Game
   picture** setting to *Whole screen*.
2. In vMix or OBS, add a **browser source** at `http://localhost:8780/overlay`,
   sized 1920×1080, and put it above the game feed.
   - If vMix or OBS runs on another PC, or players connect webcams, tick
     *Let other PCs on the network load the overlay and connect their
     cams* and use the network address shown in the control panel.
3. Connect to the GFX Engine (GFX Engine page) for the teams and sponsors.
   **Demo** plays the CoD caster HUD screenshot, so you can see the look
   without the game running.

The game can run at any resolution: its picture is scaled to 1920×1080
before it's read, and every position in the settings is in 1920×1080
pixels. Click the preview in the control panel to read any pixel's
position and colour.

The app's icon is the skull: white on a dark taskbar, black on a light one.

## From source

```
pip install -r requirements-windows.txt
python -m codhud.gui            # the desktop app
python -m codhud --demo         # just the server, with a made-up match
python -m unittest discover -s tests
```
