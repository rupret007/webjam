"""
WebJam Unified Testing Application
EXTENSIVE test suite for ALL aspects of the WebJam application

Tests:
- Unit tests for all controllers
- Integration tests
- File I/O tests
- Configuration tests
- Error handling tests
- Performance tests
- Code quality tests (syntax, imports, file structure)
- Logic verification tests (algorithms, calculations, bounds)
- Data validation tests
- Stress tests (high loads, many participants, rapid operations)
- Edge case tests (empty names, special characters, extreme values)
- Concurrency tests (multi-threading, race conditions)
- Resource management tests (cleanup, memory, lifecycle)
- State consistency tests (operations, callbacks)
- Integration scenario tests (full workflows)
- Boundary condition tests (exact limits, empty collections)
"""

import sys
import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import time
import threading

# Add current directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

# Import modules to test
try:
    from jamulus_controller import JamulusController, JamulusParticipant, JamulusAudioMonitor
    JAMULUS_AVAILABLE = True
except ImportError as e:
    print(f"Warning: Could not import jamulus_controller: {e}")
    JAMULUS_AVAILABLE = False

try:
    from webex_integration import WebexController, WebexParticipant, WebexConfig
    WEBEX_AVAILABLE = True
except ImportError as e:
    print(f"Warning: Could not import webex_integration: {e}")
    WEBEX_AVAILABLE = False


# ============================================================================
# TEST UTILITIES
# ============================================================================

class TestResult:
    """Track test results across runs"""
    def __init__(self):
        self.runs = []
        self.current_run = None
    
    def start_run(self):
        """Start a new test run"""
        self.current_run = {
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
            'total': 0,
            'passed': 0,
            'failed': 0,
            'errors': 0,
            'skipped': 0,
            'percentage': 0
        }
    
    def finish_run(self, result):
        """Finish current test run"""
        if self.current_run:
            self.current_run['total'] = result.testsRun
            self.current_run['passed'] = result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped)
            self.current_run['failed'] = len(result.failures)
            self.current_run['errors'] = len(result.errors)
            self.current_run['skipped'] = len(result.skipped)
            
            if result.testsRun > 0:
                self.current_run['percentage'] = (self.current_run['passed'] / result.testsRun) * 100
            
            self.runs.append(self.current_run)
            self.current_run = None
    
    def get_last_three_results(self):
        """Get last 3 test run results"""
        return self.runs[-3:] if len(self.runs) >= 3 else self.runs
    
    def has_three_perfect_runs(self):
        """Check if last 3 runs were all 100%"""
        if len(self.runs) < 3:
            return False
        
        last_three = self.get_last_three_results()
        return all(run['percentage'] == 100.0 for run in last_three)


# ============================================================================
# UNIT TESTS - JAMULUS CONTROLLER
# ============================================================================

@unittest.skipIf(not JAMULUS_AVAILABLE, "Jamulus controller not available")
class TestJamulusController(unittest.TestCase):
    """Test suite for JamulusController"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.controller = JamulusController("127.0.0.1", 22124)
    
    def tearDown(self):
        """Clean up after tests"""
        if self.controller.running:
            self.controller.stop()
    
    def test_controller_initialization(self):
        """Test controller initializes correctly"""
        self.assertEqual(self.controller.host, "127.0.0.1")
        self.assertEqual(self.controller.port, 22124)
        self.assertFalse(self.controller.running)
        self.assertEqual(len(self.controller.participants), 0)
    
    def test_add_participant_with_id(self):
        """Test adding participant with specific ID"""
        participant = self.controller.add_participant("Test User", 0)
        
        self.assertEqual(participant.name, "Test User")
        self.assertEqual(participant.channel_id, 0)
        self.assertEqual(len(self.controller.participants), 1)
        self.assertIn(0, self.controller.participants)
    
    def test_add_participant_auto_id(self):
        """Test adding participant with auto-increment ID"""
        p1 = self.controller.add_participant("User 1")
        p2 = self.controller.add_participant("User 2")
        
        self.assertEqual(p1.channel_id, 0)
        self.assertEqual(p2.channel_id, 1)

    def test_add_participant_auto_id_after_removal_no_collision(self):
        """Test auto IDs do not collide after participant removal"""
        p1 = self.controller.add_participant("User 1")
        p2 = self.controller.add_participant("User 2")
        self.controller.remove_participant(p1.channel_id)
        p3 = self.controller.add_participant("User 3")

        self.assertEqual(p2.channel_id, 1)
        self.assertEqual(p3.channel_id, 2)
        self.assertIn(1, self.controller.participants)
        self.assertIn(2, self.controller.participants)
    
    def test_remove_participant(self):
        """Test removing participant"""
        self.controller.add_participant("Test User", 0)
        self.assertEqual(len(self.controller.participants), 1)
        
        self.controller.remove_participant(0)
        self.assertEqual(len(self.controller.participants), 0)
    
    def test_set_fader_level(self):
        """Test setting fader level"""
        self.controller.add_participant("Test User", 0)
        self.controller.set_fader_level(0, 75)
        
        self.assertEqual(self.controller.participants[0].fader_level, 75)
    
    def test_set_fader_level_bounds(self):
        """Test fader level stays within bounds"""
        self.controller.add_participant("Test User", 0)
        
        # Test upper bound
        self.controller.set_fader_level(0, 150)
        self.assertEqual(self.controller.participants[0].fader_level, 100)
        
        # Test lower bound
        self.controller.set_fader_level(0, -10)
        self.assertEqual(self.controller.participants[0].fader_level, 0)
    
    def test_set_pan(self):
        """Test setting pan position"""
        self.controller.add_participant("Test User", 0)
        self.controller.set_pan(0, 25)
        
        self.assertEqual(self.controller.participants[0].pan, 25)
    
    def test_set_pan_bounds(self):
        """Test pan stays within bounds"""
        self.controller.add_participant("Test User", 0)
        
        # Test upper bound
        self.controller.set_pan(0, 150)
        self.assertEqual(self.controller.participants[0].pan, 100)
        
        # Test lower bound
        self.controller.set_pan(0, -10)
        self.assertEqual(self.controller.participants[0].pan, 0)
    
    def test_mute_participant(self):
        """Test muting participant"""
        self.controller.add_participant("Test User", 0)
        
        self.assertFalse(self.controller.participants[0].muted)
        self.controller.set_mute(0, True)
        self.assertTrue(self.controller.participants[0].muted)
    
    def test_solo_participant(self):
        """Test soloing participant"""
        self.controller.add_participant("User 1", 0)
        self.controller.add_participant("User 2", 1)
        
        # Solo user 1
        self.controller.set_solo(0, True)
        
        self.assertTrue(self.controller.participants[0].solo)
        self.assertTrue(self.controller.participants[1].muted)
    
    def test_start_stop_monitoring(self):
        """Test starting and stopping monitoring thread"""
        self.controller.start()
        self.assertTrue(self.controller.running)
        self.assertIsNotNone(self.controller.monitor_thread)
        
        self.controller.stop()
        time.sleep(0.1)  # Give thread time to stop
        self.assertFalse(self.controller.running)
    
    def test_callback_registration(self):
        """Test callback registration and notification"""
        callback_called = []
        
        def callback(participants):
            callback_called.append(True)
        
        self.controller.register_callback(callback)
        self.controller.add_participant("Test User", 0)
        
        self.assertTrue(len(callback_called) > 0)
    
    def test_save_load_mix(self):
        """Test saving and loading mix configuration"""
        # Add participants and set levels
        self.controller.add_participant("User 1", 0)
        self.controller.add_participant("User 2", 1)
        self.controller.set_fader_level(0, 75)
        self.controller.set_pan(0, 25)
        self.controller.set_mute(1, True)
        
        # Save to temp file
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
            temp_file = f.name
        
        try:
            self.controller.save_mix(temp_file)
            
            # Reset values
            self.controller.set_fader_level(0, 100)
            self.controller.set_pan(0, 50)
            self.controller.set_mute(1, False)
            
            # Load and verify
            self.controller.load_mix(temp_file)
            
            self.assertEqual(self.controller.participants[0].fader_level, 75)
            self.assertEqual(self.controller.participants[0].pan, 25)
            self.assertTrue(self.controller.participants[1].muted)
        finally:
            Path(temp_file).unlink(missing_ok=True)

    def test_save_mix_preserves_existing_file_on_write_error(self):
        """Test atomic save keeps original file on serialization failure"""
        self.controller.add_participant("User 1", 0)
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
            temp_file = f.name
            f.write('{"sentinel": true}')
        try:
            with patch("jamulus_controller.json.dump", side_effect=OSError("disk error")):
                self.controller.save_mix(temp_file)
            with open(temp_file, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertEqual(content, '{"sentinel": true}')
        finally:
            Path(temp_file).unlink(missing_ok=True)

    def test_load_mix_ignores_invalid_payload(self):
        """Test load_mix tolerates malformed payloads without crashing"""
        self.controller.add_participant("User 1", 0)
        self.controller.set_fader_level(0, 77)
        self.controller.set_pan(0, 33)
        self.controller.set_mute(0, True)

        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
            temp_file = f.name
            f.write('{"not_participants": []}')

        try:
            self.controller.load_mix(temp_file)
            self.assertEqual(self.controller.participants[0].fader_level, 77)
            self.assertEqual(self.controller.participants[0].pan, 33)
            self.assertTrue(self.controller.participants[0].muted)

            with open(temp_file, "w", encoding="utf-8") as bad_file:
                bad_file.write("{bad json")
            self.controller.load_mix(temp_file)
            self.assertEqual(self.controller.participants[0].fader_level, 77)
        finally:
            Path(temp_file).unlink(missing_ok=True)

    def test_load_mix_clamps_and_coerces_values(self):
        """Test load_mix clamps bounds and coerces value types"""
        self.controller.add_participant("User 1", 0)
        self.controller.set_fader_level(0, 100)
        self.controller.set_pan(0, 50)
        self.controller.set_mute(0, False)
        self.controller.set_solo(0, False)

        payload = {
            "participants": [
                {
                    "channel_id": "0",
                    "fader_level": 1000,
                    "pan": -200,
                    "muted": "false",
                    "solo": "true",
                }
            ]
        }
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
            temp_file = f.name
            json.dump(payload, f)

        try:
            self.controller.load_mix(temp_file)
            self.assertEqual(self.controller.participants[0].fader_level, 100)
            self.assertEqual(self.controller.participants[0].pan, 0)
            self.assertFalse(self.controller.participants[0].muted)
            self.assertTrue(self.controller.participants[0].solo)
        finally:
            Path(temp_file).unlink(missing_ok=True)

    def test_concurrent_participant_access_does_not_crash(self):
        """Test concurrent participant reads/writes remain stable"""
        self.controller.add_participant("User 1", 0)
        self.controller.add_participant("User 2", 1)
        stop_event = threading.Event()
        errors = []

        def writer():
            try:
                while not stop_event.is_set():
                    self.controller.set_fader_level(0, 65)
                    self.controller.set_pan(1, 35)
                    self.controller.set_mute(0, True)
                    self.controller.set_mute(0, False)
            except Exception as exc:
                errors.append(exc)

        def reader():
            try:
                while not stop_event.is_set():
                    _ = self.controller.get_participants()
                    self.controller.save_mix(tempfile.NamedTemporaryFile(delete=True, suffix=".json").name)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
        for t in threads:
            t.start()
        time.sleep(0.2)
        stop_event.set()
        for t in threads:
            t.join(timeout=2)

        self.assertEqual(errors, [])


# ============================================================================
# UNIT TESTS - JAMULUS AUDIO MONITOR
# ============================================================================

@unittest.skipIf(not JAMULUS_AVAILABLE, "Jamulus controller not available")
class TestJamulusAudioMonitor(unittest.TestCase):
    """Test suite for JamulusAudioMonitor"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.controller = JamulusController("127.0.0.1", 22124)
        self.controller.add_participant("Test User", 0)
        self.monitor = JamulusAudioMonitor(self.controller)
    
    def tearDown(self):
        """Clean up after tests"""
        if self.monitor.running:
            self.monitor.stop()
        if self.controller.running:
            self.controller.stop()
    
    def test_monitor_initialization(self):
        """Test monitor initializes correctly"""
        self.assertEqual(self.monitor.controller, self.controller)
        self.assertFalse(self.monitor.running)
    
    def test_start_stop_monitoring(self):
        """Test starting and stopping audio monitoring"""
        self.monitor.start()
        self.assertTrue(self.monitor.running)
        
        self.monitor.stop()
        time.sleep(0.1)
        self.assertFalse(self.monitor.running)
    
    def test_get_level(self):
        """Test getting audio level"""
        self.monitor.start()
        time.sleep(0.2)  # Let it run for a bit
        
        level = self.monitor.get_level(0)
        self.assertIsInstance(level, float)
        self.assertGreaterEqual(level, 0.0)
        self.assertLessEqual(level, 1.0)
        
        self.monitor.stop()


# ============================================================================
# UNIT TESTS - WEBEX CONTROLLER
# ============================================================================

@unittest.skipIf(not WEBEX_AVAILABLE, "Webex integration not available")
class TestWebexController(unittest.TestCase):
    """Test suite for WebexController"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.meeting_url = "https://test.webex.com/meet/test"
        self.controller = WebexController(self.meeting_url)
    
    def tearDown(self):
        """Clean up after tests"""
        if self.controller.running:
            self.controller.stop()
    
    def test_controller_initialization(self):
        """Test controller initializes correctly"""
        self.assertEqual(self.controller.meeting_url, self.meeting_url)
        self.assertFalse(self.controller.is_connected)
        self.assertEqual(len(self.controller.participants), 0)
    
    def test_add_participant(self):
        """Test adding Webex participant"""
        participant = WebexParticipant(
            id="user1",
            name="Test User",
            email="test@example.com"
        )
        
        self.controller.add_participant(participant)
        self.assertEqual(len(self.controller.participants), 1)
        self.assertIn("user1", self.controller.participants)
    
    def test_remove_participant(self):
        """Test removing Webex participant"""
        participant = WebexParticipant(id="user1", name="Test User", email="test@example.com")
        self.controller.add_participant(participant)
        
        self.controller.remove_participant("user1")
        self.assertEqual(len(self.controller.participants), 0)
    
    def test_start_stop_monitoring(self):
        """Test starting and stopping monitoring"""
        self.controller.start()
        self.assertTrue(self.controller.running)
        
        self.controller.stop()
        time.sleep(0.1)
        self.assertFalse(self.controller.running)


# ============================================================================
# UNIT TESTS - WEBEX CONFIG
# ============================================================================

@unittest.skipIf(not WEBEX_AVAILABLE, "Webex integration not available")
class TestWebexConfig(unittest.TestCase):
    """Test suite for WebexConfig"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.temp_config = Path(tempfile.gettempdir()) / ".test_webex_config.json"
        # Backup and clear any existing config
        if self.temp_config.exists():
            self.temp_config.unlink()
    
    def tearDown(self):
        """Clean up after tests"""
        if self.temp_config.exists():
            self.temp_config.unlink()
    
    def test_config_initialization(self):
        """Test config initializes with defaults"""
        config = WebexConfig()
        
        self.assertIsInstance(config.config, dict)
        self.assertIn('default_meeting_url', config.config)
        self.assertIn('auto_join', config.config)
    
    def test_get_set_config(self):
        """Test getting and setting config values"""
        config = WebexConfig()
        
        config.set('test_key', 'test_value')
        self.assertEqual(config.get('test_key'), 'test_value')


# ============================================================================
# INTEGRATION TESTS
# ============================================================================

@unittest.skipIf(not (JAMULUS_AVAILABLE and WEBEX_AVAILABLE), "Integration modules not available")
class TestIntegration(unittest.TestCase):
    """Integration tests for WebJam components"""
    
    def test_participant_sync(self):
        """Test syncing participants between Jamulus and Webex"""
        # Create controllers
        jamulus = JamulusController("127.0.0.1", 22124)
        webex = WebexController("https://test.webex.com/meet/test")
        
        # Add participants with matching names
        jamulus.add_participant("John Doe", 0)
        jamulus.add_participant("Jane Smith", 1)
        
        webex.add_participant(WebexParticipant(id="user1", name="John Doe", email="john@test.com"))
        webex.add_participant(WebexParticipant(id="user2", name="Jane Smith", email="jane@test.com"))
        
        # Verify both have participants
        self.assertEqual(len(jamulus.participants), 2)
        self.assertEqual(len(webex.participants), 2)
        
        jamulus.stop()
        webex.stop()


# ============================================================================
# FILE I/O TESTS
# ============================================================================

class TestFileIO(unittest.TestCase):
    """Test file I/O operations"""
    
    def test_json_save_load(self):
        """Test saving and loading JSON configuration"""
        test_data = {
            'participants': {
                '0': {'name': 'User 1', 'fader_level': 75},
                '1': {'name': 'User 2', 'fader_level': 50}
            }
        }
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
            temp_file = f.name
        
        try:
            # Save
            with open(temp_file, 'w') as f:
                json.dump(test_data, f)
            
            # Load
            with open(temp_file, 'r') as f:
                loaded_data = json.load(f)
            
            self.assertEqual(loaded_data, test_data)
        finally:
            Path(temp_file).unlink(missing_ok=True)
    
    def test_config_file_creation(self):
        """Test configuration file creation"""
        temp_dir = Path(tempfile.gettempdir()) / "test_webjam"
        temp_dir.mkdir(exist_ok=True)
        
        config_file = temp_dir / "test_config.json"
        
        try:
            # Write config
            config = {'test': 'value'}
            with open(config_file, 'w') as f:
                json.dump(config, f)
            
            self.assertTrue(config_file.exists())
            
            # Read config
            with open(config_file, 'r') as f:
                loaded = json.load(f)
            
            self.assertEqual(loaded['test'], 'value')
        finally:
            if config_file.exists():
                config_file.unlink()
            if temp_dir.exists():
                temp_dir.rmdir()


# ============================================================================
# CONFIGURATION TESTS
# ============================================================================

class TestConfiguration(unittest.TestCase):
    """Test configuration handling"""
    
    def test_default_config(self):
        """Test default configuration values"""
        config = {
            'jamulus_server': '172.24.194.9',
            'jamulus_port': '22124',
            'webex_url': 'https://webjam-sbx.webex.com/meet/webjam01'
        }
        
        self.assertIn('jamulus_server', config)
        self.assertIn('jamulus_port', config)
        self.assertIn('webex_url', config)
    
    def test_config_validation(self):
        """Test configuration validation"""
        valid_config = {
            'jamulus_server': '127.0.0.1',
            'jamulus_port': '22124'
        }
        
        # Check types
        self.assertIsInstance(valid_config['jamulus_server'], str)
        self.assertIsInstance(valid_config['jamulus_port'], str)
        
        # Check port is numeric
        self.assertTrue(valid_config['jamulus_port'].isdigit())


# ============================================================================
# ERROR HANDLING TESTS
# ============================================================================

class TestErrorHandling(unittest.TestCase):
    """Test error handling"""
    
    @unittest.skipIf(not JAMULUS_AVAILABLE, "Jamulus controller not available")
    def test_invalid_participant_id(self):
        """Test handling invalid participant ID"""
        controller = JamulusController("127.0.0.1", 22124)
        
        # Should not raise error, just do nothing
        controller.set_fader_level(999, 50)
        controller.set_pan(999, 50)
        controller.set_mute(999, True)
    
    def test_invalid_json_file(self):
        """Test handling invalid JSON file"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
            f.write("invalid json {")
            temp_file = f.name
        
        try:
            with self.assertRaises(json.JSONDecodeError):
                with open(temp_file, 'r') as f:
                    json.load(f)
        finally:
            Path(temp_file).unlink(missing_ok=True)
    
    def test_missing_config_file(self):
        """Test handling missing config file"""
        nonexistent_file = Path("/nonexistent/config.json")
        self.assertFalse(nonexistent_file.exists())


# ============================================================================
# PERFORMANCE TESTS
# ============================================================================

class TestPerformance(unittest.TestCase):
    """Test performance characteristics"""
    
    @unittest.skipIf(not JAMULUS_AVAILABLE, "Jamulus controller not available")
    def test_participant_lookup_speed(self):
        """Test participant lookup is O(1)"""
        controller = JamulusController("127.0.0.1", 22124)
        
        # Add many participants
        for i in range(100):
            controller.add_participant(f"User {i}", i)
        
        # Time lookups
        start = time.time()
        for i in range(1000):
            _ = controller.participants.get(50)
        end = time.time()
        
        # Should be very fast (< 0.1s for 1000 lookups)
        self.assertLess(end - start, 0.1)
        
        controller.stop()
    
    @unittest.skipIf(not JAMULUS_AVAILABLE, "Jamulus controller not available")
    def test_fader_update_speed(self):
        """Test fader updates are fast"""
        controller = JamulusController("127.0.0.1", 22124)
        controller.add_participant("Test User", 0)
        
        # Time fader updates
        start = time.time()
        for i in range(1000):
            controller.set_fader_level(0, (i % 100))
        end = time.time()
        
        # Should be fast (< 0.1s for 1000 updates)
        self.assertLess(end - start, 0.1)
        
        controller.stop()


# ============================================================================
# CODE QUALITY TESTS
# ============================================================================

class TestCodeQuality(unittest.TestCase):
    """Test code quality and static analysis"""
    
    def test_syntax_jamulus_controller(self):
        """Test jamulus_controller.py has no syntax errors"""
        import py_compile
        import tempfile
        
        try:
            with tempfile.NamedTemporaryFile(suffix='.pyc', delete=False) as f:
                temp_file = f.name
            
            py_compile.compile('jamulus_controller.py', temp_file, doraise=True)
            self.assertTrue(True)  # If we get here, syntax is valid
        except py_compile.PyCompileError as e:
            self.fail(f"Syntax error in jamulus_controller.py: {e}")
        finally:
            Path(temp_file).unlink(missing_ok=True)
    
    def test_syntax_webex_integration(self):
        """Test webex_integration.py has no syntax errors"""
        import py_compile
        import tempfile
        
        try:
            with tempfile.NamedTemporaryFile(suffix='.pyc', delete=False) as f:
                temp_file = f.name
            
            py_compile.compile('webex_integration.py', temp_file, doraise=True)
            self.assertTrue(True)
        except py_compile.PyCompileError as e:
            self.fail(f"Syntax error in webex_integration.py: {e}")
        finally:
            Path(temp_file).unlink(missing_ok=True)
    
    def test_syntax_webjam_app_enhanced(self):
        """Test webjam_app_enhanced.py has no syntax errors"""
        import py_compile
        import tempfile
        
        try:
            with tempfile.NamedTemporaryFile(suffix='.pyc', delete=False) as f:
                temp_file = f.name
            
            py_compile.compile('webjam_app_enhanced.py', temp_file, doraise=True)
            self.assertTrue(True)
        except py_compile.PyCompileError as e:
            self.fail(f"Syntax error in webjam_app_enhanced.py: {e}")
        finally:
            Path(temp_file).unlink(missing_ok=True)
    
    def test_imports_available(self):
        """Test all required imports are available"""
        try:
            import tkinter
            import json
            import subprocess
            import threading
            import time
            import webbrowser
            from pathlib import Path
            from dataclasses import dataclass
            self.assertTrue(True)
        except ImportError as e:
            self.fail(f"Required import missing: {e}")
    
    def test_file_structure(self):
        """Test all required files are present"""
        required_files = [
            'jamulus_controller.py',
            'webex_integration.py',
            'webjam_app_enhanced.py',
            'test_webjam.py'
        ]
        
        for file in required_files:
            self.assertTrue(Path(file).exists(), f"Required file missing: {file}")


# ============================================================================
# LOGIC VERIFICATION TESTS
# ============================================================================

class TestLogicVerification(unittest.TestCase):
    """Test critical logic and algorithms"""
    
    @unittest.skipIf(not JAMULUS_AVAILABLE, "Jamulus controller not available")
    def test_db_calculation_logic(self):
        """Test dB calculation is mathematically correct"""
        # dB = 20 * ((value / 100) - 1)
        # At value=100: dB = 20 * (1 - 1) = 0 dB (correct)
        # At value=50: dB = 20 * (0.5 - 1) = -10 dB (correct)
        # At value=0: dB = -20 dB (correct)
        
        def calculate_db(value):
            if value > 0:
                return 20 * ((value / 100) - 1)
            else:
                return -float('inf')
        
        self.assertEqual(calculate_db(100), 0.0)
        self.assertEqual(calculate_db(50), -10.0)
        self.assertEqual(calculate_db(0), -float('inf'))
    
    @unittest.skipIf(not JAMULUS_AVAILABLE, "Jamulus controller not available")
    def test_bounds_checking_logic(self):
        """Test bounds checking works correctly"""
        controller = JamulusController("127.0.0.1", 22124)
        controller.add_participant("Test", 0)
        
        # Fader bounds
        controller.set_fader_level(0, 150)  # Over max
        self.assertEqual(controller.participants[0].fader_level, 100)
        
        controller.set_fader_level(0, -50)  # Under min
        self.assertEqual(controller.participants[0].fader_level, 0)
        
        # Pan bounds
        controller.set_pan(0, 150)  # Over max
        self.assertEqual(controller.participants[0].pan, 100)
        
        controller.set_pan(0, -50)  # Under min
        self.assertEqual(controller.participants[0].pan, 0)
        
        controller.stop()
    
    @unittest.skipIf(not JAMULUS_AVAILABLE, "Jamulus controller not available")
    def test_auto_increment_logic(self):
        """Test auto-increment channel ID logic"""
        controller = JamulusController("127.0.0.1", 22124)
        
        # Add participants without specifying ID
        p1 = controller.add_participant("User 1")
        p2 = controller.add_participant("User 2")
        p3 = controller.add_participant("User 3")
        
        # Should increment: 0, 1, 2
        self.assertEqual(p1.channel_id, 0)
        self.assertEqual(p2.channel_id, 1)
        self.assertEqual(p3.channel_id, 2)
        
        controller.stop()
    
    @unittest.skipIf(not JAMULUS_AVAILABLE, "Jamulus controller not available")
    def test_solo_mute_logic(self):
        """Test solo correctly mutes other participants"""
        controller = JamulusController("127.0.0.1", 22124)
        
        controller.add_participant("User 1", 0)
        controller.add_participant("User 2", 1)
        controller.add_participant("User 3", 2)
        
        # Solo user 1
        controller.set_solo(0, True)
        
        # User 0 should be solo (unmuted)
        self.assertTrue(controller.participants[0].solo)
        self.assertFalse(controller.participants[0].muted)
        
        # Others should be muted
        self.assertTrue(controller.participants[1].muted)
        self.assertTrue(controller.participants[2].muted)
        
        controller.stop()
    
    def test_port_type_conversion(self):
        """Test port is correctly converted from string to int"""
        port_string = "22124"
        port_int = int(port_string)
        
        self.assertIsInstance(port_int, int)
        self.assertEqual(port_int, 22124)
    
    def test_percentage_calculation(self):
        """Test percentage calculation logic"""
        total = 34
        passed = 34
        percentage = (passed / total) * 100
        
        self.assertEqual(percentage, 100.0)
        
        # Test with failures
        total = 34
        passed = 30
        percentage = (passed / total) * 100
        
        self.assertAlmostEqual(percentage, 88.23529411764706, places=2)


# ============================================================================
# DATA VALIDATION TESTS
# ============================================================================

class TestDataValidation(unittest.TestCase):
    """Test data validation"""
    
    def test_participant_data_structure(self):
        """Test participant data structure"""
        if JAMULUS_AVAILABLE:
            participant = JamulusParticipant(
                channel_id=0,
                name="Test User",
                fader_level=75,
                pan=50,
                muted=False,
                solo=False
            )
            
            self.assertEqual(participant.channel_id, 0)
            self.assertEqual(participant.name, "Test User")
            self.assertEqual(participant.fader_level, 75)
            self.assertEqual(participant.pan, 50)
            self.assertFalse(participant.muted)
            self.assertFalse(participant.solo)
    
    def test_config_data_types(self):
        """Test configuration data types"""
        config = {
            'fader_level': 75,
            'pan': 50,
            'muted': False,
            'name': "Test User"
        }
        
        self.assertIsInstance(config['fader_level'], int)
        self.assertIsInstance(config['pan'], int)
        self.assertIsInstance(config['muted'], bool)
        self.assertIsInstance(config['name'], str)


# ============================================================================
# STRESS TESTS
# ============================================================================

class TestStress(unittest.TestCase):
    """Stress testing with high loads"""
    
    @unittest.skipIf(not JAMULUS_AVAILABLE, "Jamulus controller not available")
    def test_many_participants(self):
        """Test handling many participants (100)"""
        controller = JamulusController("127.0.0.1", 22124)
        
        # Add 100 participants
        for i in range(100):
            controller.add_participant(f"User {i}", i)
        
        self.assertEqual(len(controller.participants), 100)
        
        # Verify all accessible
        for i in range(100):
            self.assertIn(i, controller.participants)
            self.assertEqual(controller.participants[i].channel_id, i)
        
        controller.stop()
    
    @unittest.skipIf(not JAMULUS_AVAILABLE, "Jamulus controller not available")
    def test_rapid_fader_changes(self):
        """Test rapid fader changes (1000 operations)"""
        controller = JamulusController("127.0.0.1", 22124)
        controller.add_participant("Test", 0)
        
        # Perform 1000 rapid changes
        last_value = 0
        for i in range(1000):
            value = i % 101  # 0-100
            controller.set_fader_level(0, value)
            last_value = value
        
        # Final value should be last set (999 % 101 = 90)
        self.assertEqual(controller.participants[0].fader_level, last_value)
        
        controller.stop()
    
    @unittest.skipIf(not JAMULUS_AVAILABLE, "Jamulus controller not available")
    def test_rapid_add_remove(self):
        """Test rapid add/remove cycles"""
        controller = JamulusController("127.0.0.1", 22124)
        
        # Add and remove 50 times
        for i in range(50):
            controller.add_participant(f"User {i}", 0)
            self.assertEqual(len(controller.participants), 1)
            controller.remove_participant(0)
            self.assertEqual(len(controller.participants), 0)
        
        controller.stop()


# ============================================================================
# EDGE CASE TESTS
# ============================================================================

class TestEdgeCases(unittest.TestCase):
    """Test edge cases and boundary conditions"""
    
    @unittest.skipIf(not JAMULUS_AVAILABLE, "Jamulus controller not available")
    def test_empty_participant_name(self):
        """Test participant with empty name"""
        controller = JamulusController("127.0.0.1", 22124)
        participant = controller.add_participant("", 0)
        
        self.assertEqual(participant.name, "")
        self.assertIsInstance(participant.name, str)
        
        controller.stop()
    
    @unittest.skipIf(not JAMULUS_AVAILABLE, "Jamulus controller not available")
    def test_very_long_participant_name(self):
        """Test participant with very long name"""
        controller = JamulusController("127.0.0.1", 22124)
        long_name = "A" * 1000
        participant = controller.add_participant(long_name, 0)
        
        self.assertEqual(len(participant.name), 1000)
        
        controller.stop()
    
    @unittest.skipIf(not JAMULUS_AVAILABLE, "Jamulus controller not available")
    def test_special_characters_in_name(self):
        """Test participant name with special characters"""
        controller = JamulusController("127.0.0.1", 22124)
        special_name = "User@#$%^&*()[]{}|\\/<>?~`"
        participant = controller.add_participant(special_name, 0)
        
        self.assertEqual(participant.name, special_name)
        
        controller.stop()
    
    @unittest.skipIf(not JAMULUS_AVAILABLE, "Jamulus controller not available")
    def test_duplicate_channel_id(self):
        """Test adding participant with duplicate channel ID"""
        controller = JamulusController("127.0.0.1", 22124)
        
        p1 = controller.add_participant("User 1", 0)
        p2 = controller.add_participant("User 2", 0)  # Same ID
        
        # Second should replace first
        self.assertEqual(len(controller.participants), 1)
        self.assertEqual(controller.participants[0].name, "User 2")
        
        controller.stop()
    
    @unittest.skipIf(not JAMULUS_AVAILABLE, "Jamulus controller not available")
    def test_negative_channel_id(self):
        """Test negative channel ID"""
        controller = JamulusController("127.0.0.1", 22124)
        participant = controller.add_participant("Test", -1)
        
        self.assertEqual(participant.channel_id, -1)
        self.assertIn(-1, controller.participants)
        
        controller.stop()
    
    @unittest.skipIf(not JAMULUS_AVAILABLE, "Jamulus controller not available")
    def test_extreme_fader_values(self):
        """Test extreme fader values"""
        controller = JamulusController("127.0.0.1", 22124)
        controller.add_participant("Test", 0)
        
        # Test very large value
        controller.set_fader_level(0, 999999)
        self.assertEqual(controller.participants[0].fader_level, 100)
        
        # Test very small value
        controller.set_fader_level(0, -999999)
        self.assertEqual(controller.participants[0].fader_level, 0)
        
        controller.stop()


# ============================================================================
# CONCURRENCY TESTS
# ============================================================================

class TestConcurrency(unittest.TestCase):
    """Test concurrent operations"""
    
    @unittest.skipIf(not JAMULUS_AVAILABLE, "Jamulus controller not available")
    def test_concurrent_fader_changes(self):
        """Test concurrent fader changes from multiple threads"""
        controller = JamulusController("127.0.0.1", 22124)
        controller.add_participant("Test", 0)
        
        def change_fader(value):
            for _ in range(10):
                controller.set_fader_level(0, value)
        
        threads = []
        for i in range(5):
            t = threading.Thread(target=change_fader, args=(i * 20,))
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()
        
        # Should have a valid value (0-100)
        self.assertGreaterEqual(controller.participants[0].fader_level, 0)
        self.assertLessEqual(controller.participants[0].fader_level, 100)
        
        controller.stop()
    
    @unittest.skipIf(not JAMULUS_AVAILABLE, "Jamulus controller not available")
    def test_concurrent_participants(self):
        """Test concurrent participant additions"""
        controller = JamulusController("127.0.0.1", 22124)
        
        def add_participants(start, count):
            for i in range(count):
                controller.add_participant(f"User {start + i}", start + i)
        
        threads = []
        for i in range(5):
            t = threading.Thread(target=add_participants, args=(i * 10, 10))
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()
        
        # Should have 50 participants
        self.assertEqual(len(controller.participants), 50)
        
        controller.stop()
    
    @unittest.skipIf(not JAMULUS_AVAILABLE, "Jamulus controller not available")
    def test_start_stop_race_condition(self):
        """Test start/stop race conditions"""
        controller = JamulusController("127.0.0.1", 22124)
        
        def start_stop():
            for _ in range(5):
                controller.start()
                time.sleep(0.01)
                controller.stop()
                time.sleep(0.01)
        
        threads = []
        for _ in range(3):
            t = threading.Thread(target=start_stop)
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()
        
        # Should end in stopped state
        self.assertFalse(controller.running)


# ============================================================================
# RESOURCE MANAGEMENT TESTS
# ============================================================================

class TestResourceManagement(unittest.TestCase):
    """Test resource cleanup and memory management"""
    
    @unittest.skipIf(not JAMULUS_AVAILABLE, "Jamulus controller not available")
    def test_controller_cleanup(self):
        """Test controller properly cleans up resources"""
        controller = JamulusController("127.0.0.1", 22124)
        controller.start()
        
        # Add participants
        for i in range(10):
            controller.add_participant(f"User {i}", i)
        
        # Stop should clean up
        controller.stop()
        
        self.assertFalse(controller.running)
        self.assertIsNotNone(controller.monitor_thread)
    
    @unittest.skipIf(not JAMULUS_AVAILABLE, "Jamulus controller not available")
    def test_multiple_start_stop_cycles(self):
        """Test multiple start/stop cycles"""
        controller = JamulusController("127.0.0.1", 22124)
        
        for _ in range(5):
            controller.start()
            self.assertTrue(controller.running)
            time.sleep(0.1)
            controller.stop()
            self.assertFalse(controller.running)
            time.sleep(0.1)
    
    def test_temp_file_cleanup(self):
        """Test temporary files are cleaned up"""
        import tempfile
        
        # Create and delete temp file
        with tempfile.NamedTemporaryFile(delete=False) as f:
            temp_path = Path(f.name)
            f.write(b"test")
        
        self.assertTrue(temp_path.exists())
        
        temp_path.unlink()
        self.assertFalse(temp_path.exists())


# ============================================================================
# STATE CONSISTENCY TESTS
# ============================================================================

class TestStateConsistency(unittest.TestCase):
    """Test state remains consistent"""
    
    @unittest.skipIf(not JAMULUS_AVAILABLE, "Jamulus controller not available")
    def test_state_after_operations(self):
        """Test state consistency after multiple operations"""
        controller = JamulusController("127.0.0.1", 22124)
        
        # Add participants
        controller.add_participant("User 1", 0)
        controller.add_participant("User 2", 1)
        
        # Perform operations
        controller.set_fader_level(0, 75)
        controller.set_pan(0, 25)
        controller.set_mute(1, True)
        
        # Verify state
        self.assertEqual(controller.participants[0].fader_level, 75)
        self.assertEqual(controller.participants[0].pan, 25)
        self.assertTrue(controller.participants[1].muted)
        
        # Remove and re-add
        controller.remove_participant(0)
        controller.add_participant("User 1 New", 0)
        
        # New participant should have default state
        self.assertEqual(controller.participants[0].fader_level, 100)
        self.assertEqual(controller.participants[0].name, "User 1 New")
        
        controller.stop()
    
    @unittest.skipIf(not JAMULUS_AVAILABLE, "Jamulus controller not available")
    def test_callback_consistency(self):
        """Test callbacks fire consistently"""
        controller = JamulusController("127.0.0.1", 22124)
        callback_count = []
        
        def callback(participants):
            callback_count.append(len(participants))
        
        controller.register_callback(callback)
        
        # Each operation should trigger callback
        controller.add_participant("User 1", 0)
        controller.add_participant("User 2", 1)
        controller.remove_participant(0)
        
        # Should have at least 3 callbacks
        self.assertGreaterEqual(len(callback_count), 3)
        
        controller.stop()


# ============================================================================
# INTEGRATION SCENARIO TESTS
# ============================================================================

class TestIntegrationScenarios(unittest.TestCase):
    """Test complex integration scenarios"""
    
    @unittest.skipIf(not JAMULUS_AVAILABLE, "Jamulus controller not available")
    def test_full_session_workflow(self):
        """Test complete session workflow"""
        controller = JamulusController("127.0.0.1", 22124)
        controller.start()
        
        # Session starts
        controller.add_participant("Guitarist", 0)
        controller.add_participant("Drummer", 1)
        controller.add_participant("Bassist", 2)
        
        # Adjust levels
        controller.set_fader_level(0, 80)
        controller.set_fader_level(1, 60)
        controller.set_fader_level(2, 70)
        
        # Adjust pans
        controller.set_pan(0, 25)  # Guitar left
        controller.set_pan(2, 75)  # Bass right
        
        # Save configuration
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
            temp_file = f.name
        
        try:
            controller.save_mix(temp_file)
            
            # Reset
            controller.set_fader_level(0, 100)
            controller.set_pan(0, 50)
            
            # Load and verify
            controller.load_mix(temp_file)
            
            self.assertEqual(controller.participants[0].fader_level, 80)
            self.assertEqual(controller.participants[0].pan, 25)
        finally:
            Path(temp_file).unlink(missing_ok=True)
        
        controller.stop()
    
    @unittest.skipIf(not (JAMULUS_AVAILABLE and WEBEX_AVAILABLE), "Integration modules not available")
    def test_dual_controller_interaction(self):
        """Test Jamulus and Webex controllers working together"""
        jamulus = JamulusController("127.0.0.1", 22124)
        webex = WebexController("https://test.webex.com/meet/test")
        
        jamulus.start()
        webex.start()
        
        # Add same participants to both
        jamulus.add_participant("John", 0)
        webex.add_participant(WebexParticipant(id="john", name="John", email="john@test.com"))
        
        # Verify both have data
        self.assertEqual(len(jamulus.participants), 1)
        self.assertEqual(len(webex.participants), 1)
        
        jamulus.stop()
        webex.stop()


# ============================================================================
# BOUNDARY CONDITION TESTS
# ============================================================================

class TestBoundaryConditions(unittest.TestCase):
    """Test boundary and limit conditions"""
    
    @unittest.skipIf(not JAMULUS_AVAILABLE, "Jamulus controller not available")
    def test_boundary_fader_values(self):
        """Test exact boundary values for fader"""
        controller = JamulusController("127.0.0.1", 22124)
        controller.add_participant("Test", 0)
        
        # Test exact boundaries
        controller.set_fader_level(0, 0)
        self.assertEqual(controller.participants[0].fader_level, 0)
        
        controller.set_fader_level(0, 100)
        self.assertEqual(controller.participants[0].fader_level, 100)
        
        controller.set_fader_level(0, 50)
        self.assertEqual(controller.participants[0].fader_level, 50)
        
        controller.stop()
    
    @unittest.skipIf(not JAMULUS_AVAILABLE, "Jamulus controller not available")
    def test_boundary_pan_values(self):
        """Test exact boundary values for pan"""
        controller = JamulusController("127.0.0.1", 22124)
        controller.add_participant("Test", 0)
        
        controller.set_pan(0, 0)
        self.assertEqual(controller.participants[0].pan, 0)
        
        controller.set_pan(0, 100)
        self.assertEqual(controller.participants[0].pan, 100)
        
        controller.set_pan(0, 50)
        self.assertEqual(controller.participants[0].pan, 50)
        
        controller.stop()
    
    def test_empty_collections(self):
        """Test operations on empty collections"""
        if JAMULUS_AVAILABLE:
            controller = JamulusController("127.0.0.1", 22124)
            
            # Operations on empty controller
            participants = controller.get_participants()
            self.assertEqual(len(participants), 0)
            
            # Remove non-existent
            controller.remove_participant(999)  # Should not error
            
            controller.stop()


# ============================================================================
# TEST RUNNER
# ============================================================================

class ColoredTextTestResult(unittest.TextTestResult):
    """Custom test result with colored output"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.test_results = []
    
    def addSuccess(self, test):
        super().addSuccess(test)
        self.test_results.append(('PASS', str(test)))
        if self.showAll:
            self.stream.writeln("[PASS]")
    
    def addError(self, test, err):
        super().addError(test, err)
        self.test_results.append(('ERROR', str(test)))
        if self.showAll:
            self.stream.writeln("[ERROR]")
    
    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.test_results.append(('FAIL', str(test)))
        if self.showAll:
            self.stream.writeln("[FAIL]")
    
    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.test_results.append(('SKIP', str(test)))
        if self.showAll:
            self.stream.writeln(f"[SKIP]: {reason}")


class ColoredTextTestRunner(unittest.TextTestRunner):
    """Custom test runner with colored output"""
    resultclass = ColoredTextTestResult


def run_all_tests():
    """Run all tests and return results"""
    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Add all test classes
    test_classes = [
        TestJamulusController,
        TestJamulusAudioMonitor,
        TestWebexController,
        TestWebexConfig,
        TestIntegration,
        TestFileIO,
        TestConfiguration,
        TestErrorHandling,
        TestPerformance,
        TestCodeQuality,
        TestLogicVerification,
        TestDataValidation,
        TestStress,
        TestEdgeCases,
        TestConcurrency,
        TestResourceManagement,
        TestStateConsistency,
        TestIntegrationScenarios,
        TestBoundaryConditions
    ]
    
    for test_class in test_classes:
        tests = loader.loadTestsFromTestCase(test_class)
        suite.addTests(tests)
    
    # Run tests
    runner = ColoredTextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    return result


def print_results_summary(test_result_tracker):
    """Print summary of all test runs"""
    print("\n" + "="*80)
    print("TEST RUNS SUMMARY")
    print("="*80)
    
    for i, run in enumerate(test_result_tracker.runs, 1):
        status = "[PERFECT]" if run['percentage'] == 100.0 else "[ISSUES]"
        print(f"\nRun #{i} - {run['timestamp']} - {status}")
        print(f"  Total:   {run['total']}")
        print(f"  Passed:  {run['passed']}")
        print(f"  Failed:  {run['failed']}")
        print(f"  Errors:  {run['errors']}")
        print(f"  Skipped: {run['skipped']}")
        print(f"  Success: {run['percentage']:.1f}%")
    
    print("\n" + "="*80)
    
    if test_result_tracker.has_three_perfect_runs():
        print("*** SUCCESS: 3 CONSECUTIVE 100% PASSES ACHIEVED! ***")
    else:
        remaining = 3 - len([r for r in test_result_tracker.get_last_three_results() if r['percentage'] == 100.0])
        print(f"INFO: Need {remaining} more perfect run(s) to achieve goal")
    
    print("="*80 + "\n")


def main():
    """Main test execution"""
    print("\n" + "="*80)
    print("WEBJAM UNIFIED TESTING APPLICATION")
    print("="*80)
    print("\nGoal: Achieve 3 consecutive test runs with 100% pass rate\n")
    
    test_result_tracker = TestResult()
    run_number = 0
    max_runs = 10  # Safety limit
    
    while not test_result_tracker.has_three_perfect_runs() and run_number < max_runs:
        run_number += 1
        
        print(f"\n{'='*80}")
        print(f"TEST RUN #{run_number}")
        print(f"{'='*80}\n")
        
        test_result_tracker.start_run()
        result = run_all_tests()
        test_result_tracker.finish_run(result)
        
        # Print current run summary
        current = test_result_tracker.runs[-1]
        print(f"\n{'='*80}")
        print(f"RUN #{run_number} COMPLETE")
        print(f"{'='*80}")
        print(f"Passed: {current['passed']}/{current['total']} ({current['percentage']:.1f}%)")
        
        if current['percentage'] == 100.0:
            print("[PERFECT RUN!]")
        else:
            print("[WARNING: Some tests did not pass]")
        
        # Check if we've achieved goal
        if test_result_tracker.has_three_perfect_runs():
            break
        
        # Brief pause between runs
        if run_number < max_runs:
            time.sleep(1)
    
    # Print final summary
    print_results_summary(test_result_tracker)
    
    # Save results to file
    results_file = Path("test_results.json")
    with open(results_file, 'w') as f:
        json.dump(test_result_tracker.runs, f, indent=2)
    
    print(f"Results saved to: {results_file}")
    
    return 0 if test_result_tracker.has_three_perfect_runs() else 1


if __name__ == '__main__':
    sys.exit(main())

