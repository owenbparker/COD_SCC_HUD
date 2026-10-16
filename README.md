# SCC CoD HUD

An overlay for Call of Duty broadcasts that sits on top of the game's own
CoD caster HUD:

- **Name highlights**: a glow in each scoreboard row, around the player's
  name, in their team's colour. It brightens while the player is on the
  objective and dims while they're dead.
- **Score box**: the score box's background is recoloured with a gradient
  between the two teams' colours. The side of the team being spectated
  lights up.
- **Auto hide**: everything disappears when the CoD caster HUD goes off
  screen (killcams, menus, between maps), and comes back with it.

Only the HUD's background is painted, so its names, numbers and icons show
through untouched. The look and the screen positions come from the CoD
Scoreboard Suite, and its settings file can be imported from the control
panel.

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
2. In vMix or OBS, add a **browser source** at `http://localhost:8779/overlay`,
   sized 1920×1080, and put it above the game feed.
   - If vMix or OBS runs on another PC, tick *Let other PCs on the network
     load the overlay* and use the network address shown in the control
     panel.
3. Set the teams' colours in the control panel. **Demo** plays a made-up
   match, so you can set up the look without the game running.

The game can run at any resolution: its picture is scaled to 1920×1080
before it's read, and every position in the settings is in 1920×1080
pixels. Click the preview in the control panel to read any pixel's
position and colour.

## From source

```
pip install -r requirements-windows.txt
python -m codhud.gui            # the desktop app
python -m codhud --demo         # just the server, with a made-up match
python -m unittest discover -s tests
```
