# WebJam User Guide
## Complete Guide to Creative Collaboration with WebJam

---

## Table of Contents

1. [Introduction](#introduction)
2. [Installation](#installation)
3. [First Time Setup](#first-time-setup)
4. [Using the Virtual Mixer](#using-the-virtual-mixer)
5. [Session Management](#session-management)
6. [Troubleshooting](#troubleshooting)
7. [Advanced Tips](#advanced-tips)
8. [Keyboard Shortcuts](#keyboard-shortcuts)

---

## Introduction

### What is WebJam?

WebJam is a creative collaboration platform that combines:
- **Jamulus**: Ultra-low latency audio (<50ms) for real-time musical performance
- **Webex**: Professional video conferencing for face-to-face collaboration
- **Virtual Mixer**: Professional-grade mixing console built into the interface
- **Shared Session Canvas**: Artifacts, references, and live collaboration notes

### Who is WebJam For?

- 🎸 **Musicians** rehearsing remotely with their band
- 🎹 **Music Teachers** giving online lessons
- 🎤 **Singers** practicing with accompaniment
- 🥁 **Jam Session Enthusiasts** connecting with musicians worldwide
- 🎺 **Ensembles** maintaining musical connection when apart
- 🎨 **Visual artists** running critique circles
- ✍️ **Writers** collaborating on drafts
- 🧩 **Design teams** reviewing work with decisions captured
- 🎬 **Storyboard/film creators** planning scenes together

### Creative Modes

WebJam supports one unified experience with mode templates:
- Music Jam
- Visual Studio
- Writer's Room
- Design Critique
- Storyboard/Film Room

Modes set defaults and prompts; collaboration primitives stay shared.

### Key Features

✅ **Low-Latency Audio** - Play together in real-time with minimal delay  
✅ **Individual Mix Control** - Create your own custom mix of all musicians  
✅ **Video Integration** - See everyone while playing together  
✅ **Save Mix Presets** - Recall your perfect mix for different songs  
✅ **Professional Interface** - Intuitive mixer layout familiar to musicians  

---

## Installation

### System Requirements

- **Operating System**: Windows 10/11 (64-bit)
- **Processor**: Intel Core i5 or equivalent
- **RAM**: 8GB minimum, 16GB recommended
- **Internet**: Broadband with <30ms latency (test at speedtest.net)
- **Audio**: Any audio interface (ASIO-compatible recommended)

### Installation Steps

1. **Get WebJam**
   - **Option A (recommended)**: Download the built app from [GitHub Actions](https://github.com/rupret007/webjam/actions) (artifacts after a successful run) or [Releases](https://github.com/rupret007/webjam/releases). Use `WebJam.exe` on Windows or the macOS app/zip for your architecture.
   - **Option B**: Clone the repo and run the Python installer script `webjam_installer.py` (run as Administrator) to install VB-Cable, Jamulus, Webex, and create shortcuts.

2. **Follow Installation Wizard** (if using Option B)
   - The installer will set up:
     - VB-Cable virtual audio device
     - Jamulus audio client
     - Webex desktop app (official Cisco MSI by architecture)
     - WebJam GUI and desktop shortcut
   - Click "Install" when prompted for VB-Cable driver.

3. **Restart Computer** (if prompted)
   - Required if VB-Cable was just installed.

4. **Launch WebJam**
   - Use the desktop shortcut, or run `WebJam.exe` (built app) or `python webjam_app_enhanced.py` (from source).

---

## First Time Setup

### Step 1: Test Your Audio

Before joining a session:

1. **Check Audio Devices**
   - Open Windows Sound Settings
   - Verify "CABLE Input" and "CABLE Output" are present
   - These enable audio routing between Jamulus and Webex

2. **Connect Your Instrument/Microphone**
   - Plug in your audio interface
   - Set your interface as input in Jamulus
   - Keep speakers/headphones connected to your interface

3. **Test Microphone Levels**
   - Speak/play into your mic
   - Ensure levels are strong but not clipping (red)

### Step 2: Join Your First Session

1. **Launch WebJam**
   - Double-click the Desktop shortcut
   - The application window opens

2. **Connect to Audio Server**
   - Click "🎵 Launch Jamulus" button
   - Jamulus connects to the server
   - Wait for other participants to appear

3. **Join Video Meeting**
   - Click "📹 Launch Webex" button
   - Your browser opens to the Webex meeting
   - Join the meeting with video/audio

4. **Configure Your Mix**
   - Other musicians appear as channels in the mixer
   - Adjust faders to create your personal mix
   - Everyone creates their own mix independently!

5. **Use Shared Session Canvas**
   - Choose a Creative Mode, template, and session goal from the top control bar
   - Pin links/references/artifacts for the session
   - Capture live notes and set review state (`draft`, `review`, `final`)

### Step 3: Run Preflight + Diagnostics (Recommended)

Before your first real rehearsal, run:

1. **Session -> Run Ready Check**
   - Quick pass/fail summary with latency quality + participant count
   - Direct actions to open diagnostics, run setup wizard, or export a support bundle
2. **Help -> Run Setup Wizard**
   - Verifies Jamulus path and endpoint
   - Validates Webex URL format
   - Captures audio diagnostics state
3. **Session -> Open Diagnostics Panel**
   - Confirms current runtime state and quick recovery actions
4. **Help -> View Usage Metrics**
   - View local counters to track successful launches and recurring issues

### Step 4: Use Session Canvas + Mode Defaults (Recommended)

Before each room starts:
- choose your creative mode
- confirm template and session goal
- pin references in Session Canvas
- capture notes and review state for continuity

---

## Using the Virtual Mixer

### Understanding the Mixer Interface

The WebJam mixer gives you complete control over what you hear. Think of it as having your own personal sound engineer!

#### Channel Strip Anatomy

Each musician gets their own **channel strip** with these controls:

```
┌─────────────────┐
│   GUITARIST     │ ← Musician Name
│       ●         │ ← Status (● = connected)
├─────────────────┤
│ ▓▓▓▓▓▓▓░░░░░░░ │ ← VU Meter (audio level)
│                 │
│    |||||||      │ ← Fader (volume)
│    |||||||      │
│    |||||||      │
│    |||||||      │
│                 │
│    0.0 dB       │ ← dB Level Display
│                 │
│   ◄────►        │ ← Pan Control (L-C-R)
│       C         │
│                 │
│    [MUTE]       │ ← Mute Button
│    [SOLO]       │ ← Solo Button
└─────────────────┘
```

### Control Reference

#### 🎚️ Fader (Volume Control)

The vertical slider controls volume for that musician:

- **Top (+0 dB)**: Full volume, no reduction
- **Middle (-12 dB)**: Half volume
- **Bottom (-∞)**: Completely silent

**Tips:**
- Start with all faders at 0dB, then adjust down
- Leave headroom - don't push everything to maximum
- Match levels: drums and bass often need less than vocals

#### 📊 VU Meter

The green/yellow/red bar shows real-time audio activity:

- **Green**: Good signal level
- **Yellow**: Approaching maximum
- **Red**: Too loud! Risk of distortion
- **"PEAK!" indicator**: Signal is clipping - reduce their fader

### Status + Latency Indicators

The bottom status area now shows:

- **Connection Summary**: current Jamulus and Webex states
- **Mixer Readiness**: whether participants are available for mixing
- **Latency Quality**: estimated endpoint quality (Good/Fair/Poor)

Hover over key controls to see quick tooltips for what each action does.

#### 🔄 Pan Control

The horizontal slider positions the musician in stereo:

- **L**: Far left speaker
- **C**: Center (both speakers equally)
- **R**: Far right speaker

**Creative Uses:**
- Pan guitars left and right for width
- Keep vocals and bass in center
- Create space for each instrument

#### 🔇 MUTE Button

Click to silence a musician temporarily:

- **Gray**: Normal (playing)
- **Red**: Muted (silent)

**When to Use:**
- Someone is taking a break
- Reducing distractions during teaching
- Focusing on specific parts during practice

#### ⭐ SOLO Button

Click to hear ONLY that musician:

- **Gray**: Normal
- **Green**: Solo (all others muted)

**When to Use:**
- Checking if someone is in tune
- Focusing on one part to learn it
- Troubleshooting audio issues

### Master Controls

At the top of the mixer:

- **Reset All Faders**: Returns all volumes to 0dB
- **Unmute All**: Clears all mute buttons
- **Center All Pans**: Returns all pan controls to center

---

## Session Management

Before each session, set your mode, template, and goal in the top bar. WebJam stores this context so repeated teams can keep momentum across sessions.

### Saving Your Mix

Once you've created the perfect mix:

1. Click "💾 Save Mix" button
2. Settings are saved to your profile
3. Loads automatically next session

**What Gets Saved:**
- All fader positions
- Pan settings
- Mute/solo states (for each participant)

### Loading a Saved Mix

Your mix loads automatically when you start WebJam, but you can also:

1. Go to **File** → **Load Mix**
2. Select from saved presets
3. Mix applies to matching participants

**Tip:** Save different mixes for different songs or configurations!

### Creating Mix Presets

For advanced users:

1. Create your mix
2. Save with a descriptive name
3. Example presets:
   - "Quiet Practice" - lower volume overall
   - "Lead Vocal Focus" - vocals up, others down
   - "Learning Drums" - drums solo, others lower

---

## Troubleshooting

### Common Issues

### In-App Troubleshooting Flow

Use this order for fastest recovery:

1. `Session -> Run Ready Check`
2. `Session -> Open Diagnostics Panel`
3. `Help -> Run Setup Wizard`
4. Retry `Launch Jamulus` then `Launch Webex`
5. If still failing, open `Help -> Quick Start Guide` (troubleshooting path)

### Diagnostics Export and Metrics Reset

From either Diagnostics Panel or Usage Metrics:
- **Export Snapshot** writes a timestamped local JSON report with states, diagnostics, and counters.
- **Export Bundle** writes a timestamped ZIP with snapshot + settings + room context + environment + available logs/support files.
- **Reset Metrics** clears local counters when starting a new observation window.

For pilot programs, use the `Validation` menu:
- **Set Cohort Name** to tag creator groups (visual_artists, writers, designers, mixed_discipline)
- **Record Session Complete** when a room finishes

This enables local cohort-level tracking for activation and cross-mode adoption.

#### ❌ "No Audio from Other Musicians"

**Check:**
1. VB-Cable is installed (look for CABLE Input/Output in Sound Settings)
2. Jamulus is connected (green dot in status bar)
3. Other musicians are not muted in your mix
4. Your internet connection is stable

**Fix:**
```
1. Close WebJam and Jamulus
2. Open Windows Sound Settings
3. Set CABLE Output as default recording device
4. Set CABLE Input as default playback device
5. Restart WebJam
```

#### ❌ "I Can't Hear Myself"

This is **normal**! Jamulus doesn't send your own audio back to you (prevents echo).

**To hear yourself:**
- Enable "Local Monitor" in your audio interface
- Or use Jamulus settings to enable local audio

#### ❌ "Too Much Latency/Delay"

**Optimize Your Connection:**
1. Use wired Ethernet (not WiFi)
2. Close other apps using internet (Netflix, downloads)
3. In Jamulus settings, reduce buffer size
4. Choose a closer server geographically

**Target Latency:** <30ms total (shown in Jamulus)

#### ❌ "Video Lag in Webex"

Video lag is okay! Remember:

- **Audio timing is critical** (handled by Jamulus)
- **Video timing is not** (slight delay doesn't affect playing)
- Reduce Webex video quality to 720p if needed

#### ❌ "Crackling or Distortion"

**Audio Problems:**
1. Check VU meters - are they hitting red?
2. Lower faders that show "PEAK!" indicator
3. Check audio interface buffer settings
4. Restart Jamulus with lower buffer size

#### ❌ "Participants Not Showing Up"

**Troubleshooting:**
1. Click **Session** → **Add Test Participants** to verify mixer works
2. Ensure you're connected to Jamulus server
3. Wait 30 seconds - participants appear gradually
4. Check Jamulus window to see if they're connected there

#### ❌ "Jamulus Launch Failed"

**Try:**
1. Open `Session -> Open Diagnostics Panel`
2. Confirm Jamulus path is found
3. Run `Help -> Run Setup Wizard` and re-run checks
4. Retry launch from main controls

#### ❌ "Webex Open Failed"

**Try:**
1. Open `Session -> Open Diagnostics Panel`
2. Confirm Webex URL is valid and browser can open links
3. Retry from the actionable error dialog

---

## Advanced Tips

### Getting the Best Audio Quality

#### For Recording Sessions

1. **All players should:**
   - Use audio interfaces (not built-in sound cards)
   - Enable ASIO drivers
   - Set buffer to 64 samples
   - Use wired internet

2. **In WebJam:**
   - Keep faders between -6dB and 0dB
   - Avoid soloing/muting during takes
   - Save mix before starting
   - Record locally + in Jamulus for redundancy

#### For Teaching Music

**Teacher Setup:**
- Keep student volume at comfortable level
- Use solo when demonstrating specific parts
- Save a "teaching mix" preset
- Keep Webex screen share ready for sheet music

**Student Setup:**
- Teacher fader at 0dB (full volume)
- Your own instrument slightly lower
- Save settings before each lesson

#### For Jam Sessions

**Create Energy:**
- Pan guitars left/right
- Keep rhythm section (drums/bass) centered
- Boost whoever's soloing
- Save different mixes for different song vibes

### Professional Mixing Techniques

#### The "Less is More" Principle

Don't push everything to maximum:
- If everything is loud, nothing is loud
- Start at 0dB and adjust DOWN
- Leave 3-6dB headroom at all times

#### Frequency Space

Position instruments to avoid "masking":
- **Low frequencies** (bass, kick): Center, high fader
- **Mid frequencies** (guitars, keys): Pan left/right
- **High frequencies** (vocals, cymbals): Center, up front

#### Balance Exercise

1. Start with all faders at -∞ (bottom)
2. Bring up rhythm section first (drums, bass)
3. Add melodic instruments
4. Bring vocals up last
5. Adjust from there

---

## Keyboard Shortcuts

### Windows/Navigation

| Shortcut | Action |
|----------|--------|
| `Ctrl + S` | Save Current Mix |
| `Ctrl + O` | Load Mix |
| `Ctrl + Q` | Quit WebJam |
| `F1` | Open Help |
| `Ctrl + H` | Toggle High Contrast |
| `Ctrl + +` | Increase Text Size |
| `Ctrl + -` | Decrease Text Size |

### Mixer Controls

| Shortcut | Action |
|----------|--------|
| `Space` | Unmute All |
| `Ctrl + R` | Reset All Faders |
| `Ctrl + P` | Center All Pans |
| `M` | Mute Selected Channel |
| `S` | Solo Selected Channel |

### Session Management

| Shortcut | Action |
|----------|--------|
| `Ctrl + J` | Launch Jamulus |
| `Ctrl + W` | Launch Webex |

### Accessibility Controls

WebJam is built for diverse creators—musicians, artists, writers, and designers of all abilities. Use `View` menu for:
- High Contrast Mode
- Large Text Mode
- Fine-grained text size adjustments

### Startup Behavior Controls

Use `Startup` menu for:
- Run Setup Wizard automatically on launch
- Auto reconnect services with bounded backoff retries
- Reset all UI preferences to defaults
- Reset window size/position to defaults

These preferences persist across sessions.

---

## Quick Reference Card

### 🚀 Quick Start Checklist

- [ ] WebJam installed and launched
- [ ] Audio interface connected
- [ ] Headphones plugged in
- [ ] Clicked "Launch Jamulus"
- [ ] Clicked "Launch Webex"
- [ ] Other musicians visible in mixer
- [ ] Adjusted faders for good balance
- [ ] Saved mix settings

### 🎛️ Mixer Quick Guide

| Problem | Solution |
|---------|----------|
| Can't hear someone | Raise their fader |
| Someone too loud | Lower their fader |
| Too much clutter | Use mute or solo |
| Want stereo width | Pan left/right |
| Distortion/crackle | Lower red channels |
| Need to start over | Click "Reset All" |

### 📞 Getting Help

- **Email**: support@webjam.io
- **Discord**: [WebJam Community](https://discord.gg/webjam)
- **GitHub**: [Report Issues](https://github.com/rupret007/webjam/issues)
- **Jamulus Help**: [jamulus.io](https://jamulus.io)
- **Webex Help**: [help.webex.com](https://help.webex.com)

---

## Appendix: Technical Details

### Audio Signal Flow

```
Your Instrument
    ↓
Audio Interface Input
    ↓
Jamulus Client → Server → Other Clients
    ↓
CABLE Input (Virtual Device)
    ↓
Your Headphones/Speakers

Simultaneously:
Your Microphone
    ↓
Webex (for talking)
    ↓
Your Speakers (for video chat)
```

### Latency Breakdown

Typical latency components:

- **Audio Interface**: 3-5ms (ASIO) or 10-30ms (WDM)
- **Network Upload**: 5-15ms
- **Server Processing**: 1-2ms
- **Network Download**: 5-15ms
- **Audio Interface Output**: 3-5ms
- **Total**: 17-72ms

Target: Under 30ms for comfortable playing

### Port Requirements

If behind a firewall:

- **Jamulus**: UDP port 22124
- **Webex**: TCP ports 443, 5004, 33434

---

## Glossary

**Buffer Size**: Amount of audio data processed at once. Smaller = lower latency but higher CPU usage.

**dB (Decibel)**: Unit of audio level. 0dB = maximum, -∞ = silence.

**Fader**: Sliding control for volume.

**Latency**: Delay between sound creation and hearing it. Critical for music!

**Pan**: Position of sound in stereo field (left-center-right).

**VU Meter**: Visual display of audio level.

**Clipping**: Distortion from too-loud signal (BAD - avoid!).

**ASIO**: Professional audio driver standard (low latency).

---

**Welcome to the future of music collaboration! 🎵**

For the latest updates and tips, visit: **[webjam.io](https://webjam.io)**

