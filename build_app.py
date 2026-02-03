#!/usr/bin/env python3
"""
Build script for PHI Redactor macOS app

Creates a standalone .app bundle that can be distributed to users.
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path


def create_icon():
    """Create a simple app icon using PIL"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("PIL not available, skipping icon creation")
        return None

    # Create a 1024x1024 icon (macOS standard)
    size = 1024
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Background - rounded rectangle (blue gradient effect)
    margin = 80
    radius = 180

    # Draw rounded rectangle background
    bg_color = (37, 99, 235)  # #2563EB - accent blue
    draw.rounded_rectangle(
        [margin, margin, size - margin, size - margin],
        radius=radius,
        fill=bg_color
    )

    # Draw "PHI" text
    try:
        # Try to use system font
        font_size = 320
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", font_size)
    except:
        font = ImageFont.load_default()

    text = "PHI"
    # Get text bounding box
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    # Center text
    x = (size - text_width) // 2
    y = (size - text_height) // 2 - 40

    draw.text((x, y), text, fill='white', font=font)

    # Draw smaller "REDACTOR" text below
    try:
        small_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 100)
    except:
        small_font = font

    sub_text = "REDACTOR"
    bbox2 = draw.textbbox((0, 0), sub_text, font=small_font)
    sub_width = bbox2[2] - bbox2[0]
    sub_x = (size - sub_width) // 2
    sub_y = y + text_height + 20

    draw.text((sub_x, sub_y), sub_text, fill=(255, 255, 255, 200), font=small_font)

    # Save as PNG first
    icon_path = Path(__file__).parent / "icon.png"
    img.save(icon_path, 'PNG')

    # Convert to ICNS for macOS
    icns_path = Path(__file__).parent / "icon.icns"

    # Create iconset directory
    iconset_path = Path(__file__).parent / "icon.iconset"
    iconset_path.mkdir(exist_ok=True)

    # Generate all required sizes
    sizes = [16, 32, 64, 128, 256, 512, 1024]
    for s in sizes:
        resized = img.resize((s, s), Image.LANCZOS)
        resized.save(iconset_path / f"icon_{s}x{s}.png", 'PNG')
        if s <= 512:
            # Also save @2x versions
            resized_2x = img.resize((s * 2, s * 2), Image.LANCZOS)
            resized_2x.save(iconset_path / f"icon_{s}x{s}@2x.png", 'PNG')

    # Use iconutil to create .icns (macOS only)
    try:
        subprocess.run([
            'iconutil', '-c', 'icns', str(iconset_path), '-o', str(icns_path)
        ], check=True)
        print(f"Created icon: {icns_path}")
    except Exception as e:
        print(f"Could not create .icns file: {e}")
        icns_path = icon_path  # Fall back to PNG

    # Cleanup iconset directory
    shutil.rmtree(iconset_path, ignore_errors=True)

    return icns_path


def build_app():
    """Build the macOS app using PyInstaller"""
    project_dir = Path(__file__).parent
    os.chdir(project_dir)

    # Create icon
    print("Creating app icon...")
    icon_path = create_icon()

    # PyInstaller command
    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--name=PHI Redactor',
        '--windowed',  # No console window
        '--onedir',    # Create a directory bundle
        '--noconfirm', # Overwrite without asking
        '--clean',     # Clean cache before building

        # Add hidden imports that PyInstaller might miss
        '--hidden-import=PIL._tkinter_finder',
        '--hidden-import=pytesseract',
        '--hidden-import=pdf2image',
        '--hidden-import=pypdf',
        '--hidden-import=reportlab',

        # Collect all data files
        '--collect-all=pytesseract',
        '--collect-all=pdf2image',

        # Main script
        'phi_redactor_gui.py'
    ]

    # Add icon if available
    if icon_path and icon_path.exists():
        cmd.insert(-1, f'--icon={icon_path}')

    print("Building app with PyInstaller...")
    print(f"Command: {' '.join(cmd)}")

    try:
        subprocess.run(cmd, check=True)
        print("\n✓ Build complete!")

        # The app will be in dist/PHI Redactor.app
        app_path = project_dir / "dist" / "PHI Redactor.app"
        if app_path.exists():
            print(f"\nApp created at: {app_path}")
            print("\nTo install, drag 'PHI Redactor.app' to your Applications folder.")

            # Also create a DMG for easy distribution (optional)
            print("\nCreating distributable zip...")
            zip_path = project_dir / "PHI Redactor.zip"
            shutil.make_archive(
                str(project_dir / "PHI Redactor"),
                'zip',
                project_dir / "dist",
                "PHI Redactor.app"
            )
            print(f"Zip created at: {zip_path}")

    except subprocess.CalledProcessError as e:
        print(f"\n✗ Build failed: {e}")
        return False

    return True


if __name__ == '__main__':
    # Check for PyInstaller
    try:
        import PyInstaller
    except ImportError:
        print("PyInstaller not found. Installing...")
        subprocess.run([sys.executable, '-m', 'pip', 'install', 'pyinstaller'], check=True)

    build_app()
