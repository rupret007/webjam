# WebJam Standard Test Procedure

**Version**: 1.0  
**Last Updated**: October 9, 2024  
**Purpose**: Standardized procedure for testing WebJam application

---

## Current Canonical Workflow (2026)

Use this workflow for current development and release validation:

```bash
# Full edge/unit suite
python -m unittest discover -s tests -p "test_*.py"

# Root-level integration/smoke suite
python -m unittest test_webjam.py test_modernization.py

# Build validation
python build_webjam.py
```

Success criteria: all commands above complete without failures.  
Note: historical sections below reference the older `test_webjam.py` multi-run flow and are retained for archive context.

---

## Overview

This document defines the standard testing procedure for WebJam that must be followed every time to ensure consistent, reliable testing. The goal is to achieve **3 consecutive test runs with 100% pass rate**.

---

## Pre-Test Requirements

### Environment Setup

1. **Working Directory**
   ```bash
   cd C:\Users\rupret\Desktop\WebJam
   ```

2. **Python Version**
   - Python 3.8 or higher required
   - Verify: `python --version`

3. **Dependencies**
   - All WebJam modules present:
     - ✅ `jamulus_controller.py`
     - ✅ `webex_integration.py`
     - ✅ `test_webjam.py`

4. **Clean State**
   - No previous test processes running
   - No conflicting file locks
   - Sufficient disk space for test results

---

## Test Execution Procedure

### Phase 1: Preparation

**Step 1.1**: Verify Test File Integrity
```bash
python -m py_compile test_webjam.py
```
**Expected**: No syntax errors

**Step 1.2**: Check Module Imports
```bash
python -c "from jamulus_controller import JamulusController; print('✓')"
python -c "from webex_integration import WebexController; print('✓')"
```
**Expected**: Both print ✓

**Step 1.3**: Clear Previous Results (Optional)
```bash
# If starting fresh
del test_results.json  # Windows
# rm test_results.json  # Linux/Mac
```

### Phase 2: Test Execution

**Step 2.1**: Launch Unified Test Application
```bash
python test_webjam.py
```

**Step 2.2**: Monitor Test Progress
- Tests run automatically
- Watch for:
  - ✅ PASS (green checkmark)
  - ❌ FAIL (red X)
  - ❌ ERROR (red X)
  - ⊝ SKIP (gray dash)

**Step 2.3**: Review Run Results
After each run, verify:
- Total tests executed
- Number passed
- Number failed/errored
- Success percentage

### Phase 3: Iteration

**Step 3.1**: Check Goal Achievement
- Review last 3 runs
- All 3 must show 100% pass rate

**Step 3.2**: If Not Achieved
- Tests automatically re-run
- Maximum 10 runs attempted
- Each run is independent

**Step 3.3**: If Achieved
- Test suite exits automatically
- Final summary displayed
- Results saved to `test_results.json`

### Phase 4: Verification

**Step 4.1**: Review Final Summary
Check displayed summary shows:
```
🎉 SUCCESS: 3 CONSECUTIVE 100% PASSES ACHIEVED! 🎉
```

**Step 4.2**: Verify Results File
```bash
type test_results.json  # Windows
# cat test_results.json  # Linux/Mac
```

**Expected**: JSON file with 3+ test runs

**Step 4.3**: Check Last 3 Runs
Verify each shows:
```json
{
  "percentage": 100.0,
  "failed": 0,
  "errors": 0
}
```

---

## Test Suite Coverage

### Unit Tests (60+ tests)

#### Jamulus Controller (15 tests)
1. ✅ Controller initialization
2. ✅ Add participant with ID
3. ✅ Add participant with auto-ID
4. ✅ Remove participant
5. ✅ Set fader level
6. ✅ Set fader level bounds (upper/lower)
7. ✅ Set pan
8. ✅ Set pan bounds (upper/lower)
9. ✅ Mute participant
10. ✅ Solo participant
11. ✅ Start/stop monitoring
12. ✅ Callback registration
13. ✅ Save mix
14. ✅ Load mix

#### Jamulus Audio Monitor (3 tests)
15. ✅ Monitor initialization
16. ✅ Start/stop monitoring
17. ✅ Get audio level

#### Webex Controller (3 tests)
18. ✅ Controller initialization
19. ✅ Add participant
20. ✅ Remove participant
21. ✅ Start/stop monitoring

#### Webex Config (2 tests)
22. ✅ Config initialization
23. ✅ Get/set config values

#### Integration Tests (1 test)
24. ✅ Participant sync between Jamulus and Webex

#### File I/O Tests (2 tests)
25. ✅ JSON save/load
26. ✅ Config file creation

#### Configuration Tests (2 tests)
27. ✅ Default config values
28. ✅ Config validation

#### Error Handling Tests (3 tests)
29. ✅ Invalid participant ID
30. ✅ Invalid JSON file
31. ✅ Missing config file

#### Performance Tests (2 tests)
32. ✅ Participant lookup speed (O(1))
33. ✅ Fader update speed

#### Data Validation Tests (2 tests)
34. ✅ Participant data structure
35. ✅ Config data types

---

## Success Criteria

### Per-Run Criteria
A test run is considered **100% successful** if:
- ✅ All non-skipped tests pass
- ✅ Zero failures
- ✅ Zero errors
- ✅ Success percentage = 100.0%

Skipped tests are acceptable (due to missing dependencies).

### Overall Success Criteria
Testing is **complete** when:
- ✅ 3 consecutive runs achieve 100% pass rate
- ✅ No degradation between runs
- ✅ Results file contains all run data

---

## Troubleshooting

### Issue: Import Errors

**Symptom**: Tests skip due to missing modules

**Solution**:
```bash
# Verify files exist
dir jamulus_controller.py webex_integration.py

# Check for syntax errors
python -m py_compile jamulus_controller.py
python -m py_compile webex_integration.py
```

### Issue: Tests Fail

**Symptom**: Some tests show ❌ FAIL

**Investigation**:
1. Note which test failed (shown in output)
2. Check test details in verbose output
3. Review test logic in `test_webjam.py`

**Common Causes**:
- Timing issues (add delays if needed)
- File permission issues (run as admin)
- Port conflicts (ensure ports free)

### Issue: Tests Error

**Symptom**: Some tests show ❌ ERROR

**Investigation**:
1. Check error traceback
2. Verify all dependencies
3. Check file paths

**Common Causes**:
- Missing dependencies
- File not found
- Permission denied

### Issue: Can't Achieve 3x 100%

**Symptom**: Some runs pass, but not 3 consecutive

**Solution**:
1. Identify flaky tests (pass sometimes, fail others)
2. Check for:
   - Race conditions
   - Timing dependencies
   - Resource cleanup issues
3. Add proper setup/teardown
4. Add delays if needed

---

## Expected Output

### Successful Run Example

```
================================================================================
WEBJAM UNIFIED TESTING APPLICATION
================================================================================

Goal: Achieve 3 consecutive test runs with 100% pass rate

================================================================================
TEST RUN #1
================================================================================

test_add_participant_auto_id ... ✅ PASS
test_add_participant_with_id ... ✅ PASS
test_callback_registration ... ✅ PASS
...
test_participant_lookup_speed ... ✅ PASS

Ran 35 tests in 2.345s

OK

================================================================================
RUN #1 COMPLETE
================================================================================
Passed: 35/35 (100.0%)
✅ PERFECT RUN!

[... Runs #2 and #3 ...]

================================================================================
TEST RUNS SUMMARY
================================================================================

Run #1 - 2024-10-09 14:30:15 - ✅ PERFECT
  Total:   35
  Passed:  35 ✅
  Failed:  0 ❌
  Errors:  0 ❌
  Skipped: 0 ⊝
  Success: 100.0%

Run #2 - 2024-10-09 14:30:18 - ✅ PERFECT
  Total:   35
  Passed:  35 ✅
  Failed:  0 ❌
  Errors:  0 ❌
  Skipped: 0 ⊝
  Success: 100.0%

Run #3 - 2024-10-09 14:30:21 - ✅ PERFECT
  Total:   35
  Passed:  35 ✅
  Failed:  0 ❌
  Errors:  0 ❌
  Skipped: 0 ⊝
  Success: 100.0%

================================================================================
🎉 SUCCESS: 3 CONSECUTIVE 100% PASSES ACHIEVED! 🎉
================================================================================

📁 Results saved to: test_results.json
```

---

## Post-Test Actions

### Step 1: Archive Results

```bash
# Create timestamp
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"

# Copy results
copy test_results.json "test_results_$timestamp.json"
```

### Step 2: Document Results

Record in test log:
- Date/Time
- Number of runs needed
- Any issues encountered
- Final pass rate

### Step 3: Commit to Version Control (if applicable)

```bash
git add test_results.json
git commit -m "Test results: 3x 100% pass achieved"
```

---

## Test Maintenance

### When to Re-run Tests

Tests should be run:
- ✅ After any code changes
- ✅ Before building executable
- ✅ Before releasing new version
- ✅ After merging branches
- ✅ Weekly (scheduled)

### When to Update Tests

Update test suite when:
- New features added
- New controllers created
- New file formats introduced
- New configuration options added
- Bugs discovered and fixed

### Test Review Schedule

- **Weekly**: Quick review of test results
- **Monthly**: Full test suite review
- **Per Release**: Comprehensive test coverage analysis

---

## Quick Reference

### One-Line Test Command
```bash
python test_webjam.py
```

### Success Check
```bash
python -c "import json; r=json.load(open('test_results.json')); print('✅ SUCCESS' if all(run['percentage']==100.0 for run in r[-3:]) else '❌ NOT YET')"
```

### View Last Result
```bash
python -c "import json; r=json.load(open('test_results.json')); print(f\"Last run: {r[-1]['percentage']:.1f}% pass rate\")"
```

---

## Test Metrics

### Target Metrics
- **Pass Rate**: 100%
- **Test Count**: 35+ tests
- **Execution Time**: < 5 seconds per run
- **Consistency**: 3 consecutive perfect runs

### Performance Targets
- **Participant lookup**: < 0.1ms
- **Fader update**: < 0.1ms
- **File I/O**: < 100ms
- **Thread start/stop**: < 200ms

---

## Appendix: Test Categories

### Category Breakdown

| Category | Tests | Weight |
|----------|-------|--------|
| Unit Tests | 25 | 71% |
| Integration Tests | 1 | 3% |
| File I/O | 2 | 6% |
| Configuration | 2 | 6% |
| Error Handling | 3 | 9% |
| Performance | 2 | 6% |
| Data Validation | 2 | 6% |

### Coverage Map

```
WebJam Application
├── Controllers (25 tests)
│   ├── Jamulus (15 tests)
│   └── Webex (10 tests)
├── Integration (1 test)
├── File System (2 tests)
├── Configuration (2 tests)
├── Error Handling (3 tests)
├── Performance (2 tests)
└── Data Validation (2 tests)
```

---

## Document Control

**Version History**:
- v1.0 (2024-10-09): Initial procedure document

**Approval**:
- Created: AI Code Analysis System
- Reviewed: Pending
- Approved: Pending

**Next Review**: After first production release

---

**This procedure must be followed exactly to ensure consistent, reliable test results.**

