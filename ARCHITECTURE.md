# WebJam Architecture Documentation

## System Overview

WebJam is a music collaboration platform that integrates three core technologies:
1. **Jamulus** - Ultra-low latency audio streaming
2. **Webex** - Professional video conferencing  
3. **VB-Cable** - Virtual audio routing

## High-Level Architecture

```
┌───────────────────────────────────────────────────────────────────┐
│                    WebJam Enhanced Application                    │
│                    (webjam_app_enhanced.py)                       │
├───────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              Main Application Window                      │   │
│  ├─────────────────────────────────────────────────────────┤   │
│  │  Top Bar: Controls, Status, Launch Buttons              │   │
│  │  ┌───────────────────────────────────────────────────┐ │   │
│  │  │                                                     │ │   │
│  │  │        Virtual Mixing Console                       │ │   │
│  │  │                                                     │ │   │
│  │  │  ┌────┐ ┌────┐ ┌────┐ ┌────┐ ┌────┐ ┌────┐      │ │   │
│  │  │  │CH 1│ │CH 2│ │CH 3│ │CH 4│ │CH 5│ │CH 6│ ...  │ │   │
│  │  │  ├────┤ ├────┤ ├────┤ ├────┤ ├────┤ ├────┤      │ │   │
│  │  │  │ ●  │ │ ●  │ │ ○  │ │ ●  │ │ ●  │ │ ●  │      │ │   │
│  │  │  │▓▓▓ │ │▓▓  │ │    │ │▓▓▓▓│ │▓   │ │▓▓▓ │      │ │   │
│  │  │  │||||│ │||||│ │||||│ │||||│ │||||│ │||||│      │ │   │
│  │  │  │||||│ │||||│ │||||│ │||||│ │||||│ │||||│      │ │   │
│  │  │  │    │ │    │ │    │ │    │ │    │ │    │      │ │   │
│  │  │  │Pan │ │Pan │ │Pan │ │Pan │ │Pan │ │Pan │      │ │   │
│  │  │  │[M]S│ │M[S]│ │MS  │ │[M]S│ │MS  │ │MS  │      │ │   │
│  │  │  └────┘ └────┘ └────┘ └────┘ └────┘ └────┘      │ │   │
│  │  │                                                     │ │   │
│  │  └───────────────────────────────────────────────────┘ │   │
│  │  Status Bar: Participant Count, Server Info            │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                   │
└───────────────────────────────────────────────────────────────────┘
           │                                  │
           ▼                                  ▼
┌──────────────────────┐          ┌──────────────────────┐
│  JamulusController   │          │  WebexController     │
│  (jamulus_controller.py)        │  (webex_integration.py)
├──────────────────────┤          ├──────────────────────┤
│ • Participant Mgmt   │          │ • Meeting Control    │
│ • Mixer Control      │          │ • Participant Sync   │
│ • Audio Monitoring   │          │ • Browser Launch     │
│ • Settings Save/Load │          │ • Config Management  │
└──────────┬───────────┘          └──────────┬───────────┘
           │                                  │
           ▼                                  ▼
┌──────────────────────┐          ┌──────────────────────┐
│   Jamulus Client     │          │   Webex Meeting      │
│   (jamulus.exe)      │          │   (Browser)          │
├──────────────────────┤          ├──────────────────────┤
│ • Audio Streaming    │          │ • Video Conferencing │
│ • Server Connection  │          │ • Screen Sharing     │
│ • Buffer Management  │          │ • Chat               │
│ • Native Mixer       │          │ • Recording          │
└──────────┬───────────┘          └──────────┬───────────┘
           │                                  │
           └─────────┬────────────────────────┘
                     ▼
         ┌─────────────────────┐
         │  VB-Cable           │
         │  (Virtual Audio)    │
         ├─────────────────────┤
         │ CABLE Input  (Out)  │
         │ CABLE Output (In)   │
         └─────────┬───────────┘
                   │
                   ▼
         ┌─────────────────────┐
         │  Audio Interface    │
         │  (Headphones/Mic)   │
         └─────────────────────┘
```

## Component Details

### 1. WebJam Enhanced Application

**File**: `webjam_app_enhanced.py`  
**Lines**: ~850  
**Language**: Python with tkinter/customtkinter

#### Responsibilities
- User interface rendering and event handling
- Mixer panel management
- Channel strip creation and updates
- Session state management
- User interactions and feedback

#### Key Classes

##### `WebJamEnhancedApp`
Main application controller
```python
class WebJamEnhancedApp:
    def __init__(self):
        # Initialize window
        # Create controllers
        # Setup UI
    
    def setup_ui(self):
        # Build interface
    
    def launch_jamulus(self):
        # Start Jamulus client
    
    def launch_webex(self):
        # Open Webex meeting
```

##### `EnhancedMixerChannel`
Individual channel strip UI
```python
class EnhancedMixerChannel:
    def __init__(self, participant, controller):
        # Create channel UI
    
    def on_fader_change(self, value):
        # Update level
    
    def update_vu_meter(self, level):
        # Refresh meter display
```

#### Data Flow
```
User Input → GUI Event → Controller Method → State Update → UI Refresh
```

### 2. Jamulus Controller

**File**: `jamulus_controller.py`  
**Lines**: ~350  
**Language**: Python

#### Responsibilities
- Manage Jamulus client connections
- Track participants and their states
- Control mixer settings (fader, pan, mute, solo)
- Monitor audio levels
- Persist and restore configurations

#### Key Classes

##### `JamulusController`
Main Jamulus interface
```python
class JamulusController:
    def __init__(self, host, port):
        # Setup connection
        self.participants = {}
    
    def set_fader_level(self, channel_id, level):
        # Control volume
    
    def set_pan(self, channel_id, pan):
        # Control stereo position
```

##### `JamulusParticipant`
Participant data model
```python
@dataclass
class JamulusParticipant:
    channel_id: int
    name: str
    fader_level: int  # 0-100
    pan: int          # 0-100
    muted: bool
    solo: bool
```

##### `JamulusAudioMonitor`
Audio level monitoring
```python
class JamulusAudioMonitor:
    def start(self):
        # Begin monitoring
    
    def get_level(self, channel_id) -> float:
        # Return current level (0.0-1.0)
```

#### Integration Points
- **Future**: UDP protocol for direct Jamulus control
- **Current**: Process management and state tracking

### 3. Webex Integration

**File**: `webex_integration.py`  
**Lines**: ~350  
**Language**: Python

#### Responsibilities
- Launch and manage Webex meetings
- Track video participants
- Synchronize with Jamulus participants
- Manage meeting configuration

#### Key Classes

##### `WebexController`
Main Webex interface
```python
class WebexController:
    def __init__(self, meeting_url):
        # Setup meeting connection
    
    def join_meeting(self, name, email):
        # Join Webex meeting
    
    def get_participants(self):
        # List meeting participants
```

##### `WebexParticipant`
Participant data model
```python
@dataclass
class WebexParticipant:
    id: str
    name: str
    email: str
    is_video_on: bool
    is_audio_on: bool
```

##### `WebexParticipantSync`
Sync Webex and Jamulus participants
```python
class WebexParticipantSync:
    def sync_participants(self):
        # Match participants by name
    
    def get_jamulus_id(self, webex_id):
        # Map Webex → Jamulus
```

#### Integration Points
- **Future**: Webex Embedded SDK for in-app video
- **Current**: Browser-based with participant tracking

### 4. Installation System

**File**: `webjam_installer.py`  
**Lines**: ~500  
**Language**: Python

#### Responsibilities
- Install VB-Cable audio driver
- Install Jamulus client
- Configure audio devices
- Install WebJam application files
- Create shortcuts

#### Installation Flow
```
Start
  ↓
Check Admin Privileges
  ↓
Install VB-Cable
  ├─ Try INF (silent)
  └─ Fallback to EXE (interactive)
  ↓
Install Jamulus
  ├─ Try silent switches
  └─ Fallback to interactive
  ↓
Configure Audio Devices
  ├─ Install AudioDeviceCmdlets
  └─ Set CABLE as default
  ↓
Install Application Files
  ├─ Copy to LocalAppData
  └─ Install Python deps
  ↓
Create Shortcuts
  ├─ Desktop
  └─ Start Menu
  ↓
Complete
```

### 5. Build System

**File**: `build_webjam.py`  
**Lines**: ~250  
**Language**: Python

#### Build Process
```
Start
  ↓
Clean Previous Builds
  ├─ Remove build/
  ├─ Remove dist/
  └─ Remove *.spec
  ↓
Build Installer
  ├─ PyInstaller with --onefile
  ├─ Bundle VB/ directory
  └─ Bundle Jamulus installer
  ↓
Build Application
  ├─ PyInstaller with --windowed
  └─ Include controllers
  ↓
Create Distribution Package
  ├─ Copy executables
  ├─ Copy source files
  ├─ Create README
  └─ (Optional) Create ZIP
  ↓
Complete
```

## Data Models

### Participant State
```python
{
    "channel_id": 0,
    "name": "Guitarist",
    "fader_level": 75,      # 0-100
    "pan": 50,              # 0=left, 50=center, 100=right
    "muted": false,
    "solo": false,
    "audio_level": 0.65,    # 0.0-1.0 (current)
    "is_connected": true
}
```

### Mix Configuration
```json
{
    "version": "2.0",
    "saved_at": "2024-10-09T12:30:00",
    "participants": {
        "0": {
            "name": "Guitarist",
            "fader_level": 75,
            "pan": 25,
            "muted": false,
            "solo": false
        },
        "1": {
            "name": "Drummer",
            "fader_level": 60,
            "pan": 50,
            "muted": false,
            "solo": false
        }
    }
}
```

## Communication Patterns

### Event-Driven Updates

```
User Action
    ↓
GUI Event Handler
    ↓
Controller Method
    ↓
State Update
    ↓
Notify Callbacks
    ↓
UI Refresh
```

### Example: Fader Movement
```python
# 1. User moves fader
EnhancedMixerChannel.on_fader_change(value)
    ↓
# 2. Update controller
JamulusController.set_fader_level(channel_id, value)
    ↓
# 3. Update participant state
participant.fader_level = value
    ↓
# 4. Apply to Jamulus (future)
# send_jamulus_command(channel_id, value)
    ↓
# 5. Notify callbacks
_notify_callbacks()
    ↓
# 6. UI updates (if needed)
update_status_display()
```

### Threading Model

```
Main Thread (GUI)
  ├─ UI Rendering
  ├─ Event Handling
  └─ User Interactions

Background Thread 1 (Jamulus Monitor)
  ├─ Connection monitoring
  ├─ Participant detection
  └─ State updates

Background Thread 2 (Audio Monitor)
  ├─ Audio level reading
  ├─ VU meter data
  └─ Peak detection

Background Thread 3 (Webex Monitor)
  ├─ Meeting state
  ├─ Participant tracking
  └─ Synchronization
```

## File Structure

```
WebJam/
├── Core Application Files
│   ├── webjam_app_enhanced.py      # Main GUI (Enhanced)
│   ├── webjam_app.py               # Main GUI (Basic)
│   ├── jamulus_controller.py       # Jamulus integration
│   ├── webex_integration.py        # Webex integration
│   ├── webjam_installer.py         # Enhanced installer
│   └── build_webjam.py             # Build automation
│
├── Legacy Files (Preserved)
│   ├── webjam_launch_session.py    # Original launcher
│   └── webjam_win_oneclick.py      # Original installer
│
├── Dependencies
│   ├── VB/                         # VB-Cable drivers
│   │   ├── VBCABLE_Setup_x64.exe
│   │   ├── *.inf (driver files)
│   │   └── *.sys (driver files)
│   └── jamulus_3.11.0_win.exe     # Jamulus installer
│
├── Configuration Files
│   ├── requirements.txt            # Python dependencies
│   └── ~/.webjam_config.json      # User settings (runtime)
│
└── Documentation
    ├── README.md                   # Project overview
    ├── USER_GUIDE.md              # User manual
    ├── CHANGELOG.md               # Version history
    ├── ARCHITECTURE.md            # This file
    └── PROJECT_SUMMARY.md         # Project summary
```

## Configuration Files

### Application Config
**Location**: `~/.webjam_config.json`
```json
{
    "participants": {
        "demo_0": {
            "name": "You",
            "level": 0.8,
            "muted": false,
            "solo": false,
            "pan": 0.0
        }
    }
}
```

### Webex Config
**Location**: `~/.webjam_webex_config.json`
```json
{
    "default_meeting_url": "https://...",
    "auto_join": false,
    "default_name": "Musician",
    "embedded_mode": false,
    "sync_with_jamulus": true
}
```

## Audio Signal Flow

```
Musician's Instrument
        ↓
Audio Interface Input
        ↓
Jamulus Client (Encoding)
        ↓
Network → Jamulus Server
        ↓
Network ← Jamulus Server (Mixed Audio)
        ↓
Jamulus Client (Decoding)
        ↓
VB-Cable Input (Virtual Device)
        ↓
        ├─→ WebJam Mixer (Monitoring Only)
        └─→ Webex (Optional)
        ↓
VB-Cable Output (Virtual Device)
        ↓
Audio Interface Output
        ↓
Headphones/Speakers
```

## Network Architecture

```
Your Computer                    Internet                Remote Musicians
┌─────────────────┐           ┌─────────┐              ┌─────────────────┐
│ Jamulus Client  │◄─────────►│ Jamulus │◄────────────►│ Jamulus Client  │
│                 │   UDP      │ Server  │   UDP        │                 │
│                 │   22124    │         │   22124      │                 │
└─────────────────┘           └─────────┘              └─────────────────┘
        ↓                                                        ↓
┌─────────────────┐           ┌─────────┐              ┌─────────────────┐
│ Webex Browser   │◄─────────►│ Webex   │◄────────────►│ Webex Browser   │
│                 │   HTTPS    │ Cloud   │   HTTPS      │                 │
│                 │   443      │         │   443        │                 │
└─────────────────┘           └─────────┘              └─────────────────┘
```

## Extension Points

### For Future Development

#### 1. Jamulus Protocol Implementation
```python
class JamulusProtocol:
    """Full UDP protocol implementation"""
    
    def send_message(self, msg_type, data):
        # Pack and send UDP packet
        pass
    
    def receive_message(self):
        # Receive and parse UDP packet
        pass
    
    def parse_client_list(self, data):
        # Extract participant info
        pass
```

#### 2. Real Audio Monitoring
```python
import pyaudio

class RealAudioMonitor:
    """Actual audio level analysis"""
    
    def __init__(self):
        self.audio = pyaudio.PyAudio()
    
    def get_channel_level(self, channel_id):
        # Read audio stream
        # Analyze RMS level
        # Return 0.0-1.0
        pass
```

#### 3. Webex SDK Integration
```python
from webexteamssdk import WebexTeamsAPI

class WebexSDKController:
    """Direct Webex API integration"""
    
    def __init__(self, access_token):
        self.api = WebexTeamsAPI(access_token)
    
    def get_participants(self):
        # Use API to get real participants
        pass
```

#### 4. Effects Processing
```python
class EffectsProcessor:
    """Per-channel audio effects"""
    
    def apply_eq(self, audio, low, mid, high):
        # Apply EQ
        pass
    
    def apply_compression(self, audio, threshold, ratio):
        # Apply dynamics
        pass
```

## Performance Considerations

### Optimization Targets

1. **UI Responsiveness**
   - VU meter updates: 20 FPS (50ms interval)
   - Fader response: <10ms
   - Button clicks: Immediate feedback

2. **Audio Latency**
   - Target total latency: <30ms
   - Buffer size: 64-128 samples
   - Network latency: <20ms

3. **Memory Usage**
   - Application base: ~50MB
   - Per participant: ~5MB
   - Total target: <200MB for 20 participants

4. **CPU Usage**
   - Idle: <2%
   - Active monitoring: <5%
   - With effects: <15%

## Security Considerations

### Current Implementation
- ✅ No sensitive data stored
- ✅ Config files in user directory
- ✅ HTTPS for Webex
- ✅ No authentication tokens in code

### Future Enhancements
- 🔄 OAuth for Webex API
- 🔄 Encrypted config storage
- 🔄 Secure credential management
- 🔄 User authentication

## Testing Strategy

### Unit Tests (Future)
```python
def test_fader_control():
    controller = JamulusController("localhost", 22124)
    controller.add_participant("Test", 0)
    controller.set_fader_level(0, 75)
    assert controller.participants[0].fader_level == 75
```

### Integration Tests (Future)
```python
def test_mixer_ui():
    app = WebJamEnhancedApp()
    app.add_test_participants()
    assert len(app.mixer_channels) == 6
```

### Manual Testing Checklist
- [ ] Install VB-Cable
- [ ] Install Jamulus
- [ ] Launch application
- [ ] Create test participants
- [ ] Move faders
- [ ] Toggle mute/solo
- [ ] Save and load mix
- [ ] Launch Webex

## Deployment

### Distribution Package Contents
```
WebJam_Distribution.zip
├── WebJam_Installer.exe        # Main installer
├── VB/                         # VB-Cable files
├── jamulus_3.11.0_win.exe     # Jamulus installer
├── webjam_app_enhanced.py     # Application source
├── jamulus_controller.py       # Controller source
├── webex_integration.py        # Integration source
├── requirements.txt            # Dependencies
├── README.md                   # Quick start
└── USER_GUIDE.md              # Full manual
```

### Installation Process
1. User runs `WebJam_Installer.exe` as Administrator
2. Installer extracts bundled resources
3. VB-Cable installed via INF or EXE
4. Jamulus installed silently or interactively
5. Python dependencies installed
6. Application files copied to LocalAppData
7. Shortcuts created
8. User launches WebJam from Desktop

## Maintenance

### Log Locations
- **Application logs**: Console output (future: file logging)
- **Installer logs**: Console output during installation
- **Jamulus logs**: Jamulus application directory
- **Windows logs**: Event Viewer for driver installation

### Common Issues
See USER_GUIDE.md Troubleshooting section

## Version History

### v2.0 (Current)
- Complete rewrite with GUI
- Virtual mixer implementation
- Jamulus and Webex integration modules
- Professional installer
- Comprehensive documentation

### v1.0 (Legacy)
- Basic command-line launcher
- Simple installation scripts
- Manual configuration

---

**Document Version**: 2.0  
**Last Updated**: October 9, 2024  
**Maintainer**: WebJam Development Team

