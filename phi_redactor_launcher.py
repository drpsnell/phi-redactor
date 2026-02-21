#!/usr/bin/env python3
"""
PHI Redactor Launcher - Bundled Version
This wrapper configures paths for bundled Tesseract and Poppler before launching the app.
"""

import os
import sys

def get_bundle_dir():
    """Get the directory where the bundled app is located"""
    if getattr(sys, 'frozen', False):
        # Running as compiled executable
        return os.path.dirname(sys.executable)
    else:
        # Running as script
        return os.path.dirname(os.path.abspath(__file__))

def configure_environment():
    """Set up paths for bundled Tesseract and Poppler"""
    bundle_dir = get_bundle_dir()
    
    # Configure Tesseract
    if sys.platform == 'win32':
        tesseract_path = os.path.join(bundle_dir, 'tesseract', 'tesseract.exe')
        tessdata_path = os.path.join(bundle_dir, 'tesseract', 'tessdata')
        poppler_path = os.path.join(bundle_dir, 'poppler')
    elif sys.platform == 'darwin':
        tesseract_path = os.path.join(bundle_dir, 'tesseract', 'bin', 'tesseract')
        tessdata_path = os.path.join(bundle_dir, 'tesseract', 'share', 'tessdata')
        poppler_path = os.path.join(bundle_dir, 'poppler', 'bin')
    else:  # Linux
        tesseract_path = os.path.join(bundle_dir, 'tesseract', 'tesseract')
        tessdata_path = os.path.join(bundle_dir, 'tesseract', 'tessdata')
        poppler_path = os.path.join(bundle_dir, 'poppler')
    
    # Set Tesseract command path
    if os.path.exists(tesseract_path):
        import pytesseract
        pytesseract.pytesseract.tesseract_cmd = tesseract_path
        os.environ['TESSDATA_PREFIX'] = tessdata_path

    # Add Poppler to PATH for pdf2image
    if os.path.exists(poppler_path):
        current_path = os.environ.get('PATH', '')
        os.environ['PATH'] = poppler_path + os.pathsep + current_path

    # Ensure Homebrew paths are in PATH (covers Apple Silicon & Intel Macs)
    homebrew_paths = ['/opt/homebrew/bin', '/usr/local/bin', '/usr/bin', '/bin']
    current_path = os.environ.get('PATH', '')
    existing = set(current_path.split(os.pathsep))
    extras = [p for p in homebrew_paths if p not in existing]
    if extras:
        os.environ['PATH'] = os.pathsep.join(extras) + os.pathsep + current_path

def main():
    """Configure environment and launch the GUI"""
    configure_environment()
    
    # Import and run the GUI
    from phi_redactor_gui import PHIRedactorGUI
    app = PHIRedactorGUI()
    app.run()

if __name__ == '__main__':
    main()
