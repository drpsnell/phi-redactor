# PHI Redactor - Cursor Development Guide

## Quick Start in Cursor

1. Open this folder in Cursor
2. Open the Terminal in Cursor (View → Terminal)
3. Run: `pip3 install pillow pytesseract pypdf reportlab pdf2image`
4. Run: `python3 phi_redactor_gui.py` to test

## File Overview

| File | What it does |
|------|--------------|
| `phi_redactor.py` | **Core engine** - all the PHI detection patterns are here |
| `phi_redactor_gui.py` | The visual interface (buttons, windows) |
| `phi_redactor_launcher.py` | Wrapper for packaged app (not needed for development) |

## Where to Make Changes

### To improve PHI detection (MOST IMPORTANT):
Edit `phi_redactor.py`, look for the `PHIPatterns` class around line 30.

Key sections:
- `_compile_patterns()` method (line ~45) - all the regex patterns
- `_load_common_names()` method (line ~200) - list of common names
- `find_phi()` method (line ~210) - the main detection logic

### To add a "enter patient name" feature:
Edit `phi_redactor_gui.py`, add input fields in `_setup_ui()` method around line 50.

### To change how redaction looks:
Edit `phi_redactor.py`, find `ImageRedactor` class around line 240.
- `self.redaction_color = (0, 0, 0)` - change color here

## Known Issues to Fix

1. **Names not caught** - Need better name detection
2. **Gender not redacted** - Need to add gender/sex patterns
3. **Nicknames missed** - Need nickname handling

## Suggested Improvements

### 1. Add manual name input (HIGH PRIORITY)
Let user type the patient's name, then search for all variations:
```python
def redact_specific_name(self, text: str, name: str) -> str:
    """Redact a specific name and its variations"""
    first, *rest = name.split()
    last = rest[-1] if rest else ""
    
    variations = [
        name,                    # John Smith
        f"{last}, {first}",      # Smith, John
        first,                   # John
        last,                    # Smith
        f"Mr. {last}",          # Mr. Smith
        f"Ms. {last}",          # Ms. Smith
        f"Mrs. {last}",         # Mrs. Smith
        f"Dr. {last}",          # Dr. Smith
    ]
    
    for var in variations:
        text = re.sub(re.escape(var), "[NAME]", text, flags=re.IGNORECASE)
    
    return text
```

### 2. Add gender redaction (HIGH PRIORITY)
Add this pattern to `_compile_patterns()`:
```python
# Gender/Sex
patterns.append((
    re.compile(
        r'\b(?:Sex|Gender)[\s:]+(?:Male|Female|M|F|Non-binary|Transgender|Trans)\b',
        re.IGNORECASE
    ),
    'GENDER'
))

# Standalone gender words after common labels
patterns.append((
    re.compile(
        r'\b(?:Male|Female)\b',
        re.IGNORECASE
    ),
    'GENDER'
))
```

### 3. Add nickname support
In the GUI, add a nickname field and pass it to the redactor.

## Testing

Create a test file with fake PHI:
```
Patient: John "Johnny" Smith
DOB: 01/15/1980
Sex: Male
MRN: 12345678
```

Run: `python3 phi_redactor.py test.txt`

Check the output file to see what got redacted.

## Building the App

After making changes, rebuild with:
```bash
python3 -m PyInstaller --onefile --windowed --name "PHI-Redactor" \
    --add-data "phi_redactor.py:." \
    --add-data "phi_redactor_gui.py:." \
    phi_redactor_launcher.py
```

App will be in `dist/PHI-Redactor.app`

## Dependencies

- Python 3.9+
- Tesseract OCR: `brew install tesseract`
- Poppler: `brew install poppler`
- Python packages: `pip3 install pillow pytesseract pypdf reportlab pdf2image`

## Need Help?

Ask Cursor's AI to help! Try prompts like:
- "Add a text field for patient name input in the GUI"
- "Improve the name detection regex to catch more names"
- "Add gender redaction patterns"
