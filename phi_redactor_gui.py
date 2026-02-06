#!/usr/bin/env python3
"""
PHI Redactor GUI - Modern graphical interface for the PHI Redactor tool

Designed for busy clinicians who need quick, easy PHI redaction.
Clean, professional design optimized for healthcare workflows.
"""

import os
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from pathlib import Path

# Image processing for manual redaction preview
from PIL import Image, ImageDraw, ImageTk

# PDF page extraction for multi-page preview
from pdf2image import convert_from_path

# Import the core redactor
from phi_redactor import PHIRedactor


# ============================================================================
# Color Palette - Modern, clean design
# ============================================================================
COLORS = {
    'bg': '#F8FAFC',           # Soft off-white background
    'surface': '#FFFFFF',       # White cards/surfaces
    'primary': '#1E293B',       # Slate for headers
    'secondary': '#475569',     # Slate gray for body text
    'accent': '#6366F1',        # Indigo for buttons (modern)
    'accent_hover': '#4F46E5',  # Deeper indigo on hover
    'success': '#10B981',       # Emerald for success
    'warning': '#F59E0B',       # Amber for warnings
    'danger': '#EF4444',        # Red for errors/selections
    'border': '#E2E8F0',        # Light border
    'muted': '#64748B',         # Slate muted text
    'button_text': '#FFFFFF',   # White text on buttons
    'disabled_bg': '#CBD5E1',   # Soft gray for disabled buttons
    'disabled_fg': '#94A3B8',   # Muted text for disabled
}

# ============================================================================
# Font sizes - Modern typography
# ============================================================================
FONTS = {
    'header': ('SF Pro Display', 14, 'bold'),
    'title': ('SF Pro Display', 20, 'bold'),
    'body': ('SF Pro Text', 12),
    'body_bold': ('SF Pro Text', 12, 'bold'),
    'button': ('SF Pro Text', 13, 'bold'),
    'small': ('SF Pro Text', 11),
    'mono': ('SF Mono', 12),
}


class ColorButton(tk.Frame):
    """
    A button widget that properly displays background colors on macOS.
    Uses a Frame with a Label to ensure colors render correctly.
    """
    def __init__(self, parent, text, command, bg='#6366F1', fg='#FFFFFF',
                 font=('Helvetica', 13, 'bold'), padx=20, pady=10, **kwargs):
        super().__init__(parent, bg=bg, cursor='arrow')

        self.command = command
        self.bg_color = bg
        self.fg_color = fg
        self.disabled = False

        self.label = tk.Label(
            self,
            text=text,
            font=font,
            fg=fg,
            bg=bg,
            padx=padx,
            pady=pady,
            cursor='arrow'
        )
        self.label.pack()

        # Bind click events
        self.bind('<Button-1>', self._on_click)
        self.label.bind('<Button-1>', self._on_click)

        # Hover effects
        self.bind('<Enter>', self._on_enter)
        self.label.bind('<Enter>', self._on_enter)
        self.bind('<Leave>', self._on_leave)
        self.label.bind('<Leave>', self._on_leave)

    def _on_click(self, event):
        if not self.disabled and self.command:
            self.command()

    def _on_enter(self, event):
        if not self.disabled:
            # Darken the color slightly on hover
            self.config(bg=COLORS['accent_hover'])
            self.label.config(bg=COLORS['accent_hover'])

    def _on_leave(self, event):
        if not self.disabled:
            self.config(bg=self.bg_color)
            self.label.config(bg=self.bg_color)

    def config(self, **kwargs):
        if 'state' in kwargs:
            if kwargs['state'] == tk.DISABLED:
                self.disabled = True
                self.configure(bg=COLORS['disabled_bg'])
                self.label.config(bg=COLORS['disabled_bg'], fg=COLORS['disabled_fg'], cursor='arrow')
                self['cursor'] = 'arrow'
            else:
                self.disabled = False
                self.configure(bg=self.bg_color)
                self.label.config(bg=self.bg_color, fg=self.fg_color, cursor='arrow')
                self['cursor'] = 'arrow'
            del kwargs['state']
        if 'bg' in kwargs:
            self.bg_color = kwargs['bg']
            super().config(bg=kwargs['bg'])
            self.label.config(bg=kwargs['bg'])
            del kwargs['bg']
        if 'fg' in kwargs:
            self.fg_color = kwargs['fg']
            self.label.config(fg=kwargs['fg'])
            del kwargs['fg']
        if 'text' in kwargs:
            self.label.config(text=kwargs['text'])
            del kwargs['text']
        if kwargs:
            super().config(**kwargs)


class DocumentPreviewWindow:
    """Window for previewing and manually redacting documents"""

    def __init__(self, parent, image_path, on_save_callback):
        self.parent = parent
        self.image_path = image_path
        self.on_save_callback = on_save_callback

        # Selection state
        self.selections = []
        self.rect_ids = []
        self.current_rect = None
        self.start_x = None
        self.start_y = None

        # Image state
        self.original_image = None
        self.display_image = None
        self.photo_image = None
        self.scale_factor = 1.0

        # For multi-page PDFs
        self.pages = []
        self.current_page = 0

        self._setup_window()
        self._load_image()
        self._setup_canvas()
        self._setup_bindings()

    def _setup_window(self):
        """Create the preview window with modern styling"""
        self.window = tk.Toplevel(self.parent)
        self.window.title("Manual Redaction")
        self.window.geometry("950x750")
        self.window.minsize(700, 550)
        self.window.configure(bg=COLORS['bg'])

        # Make modal
        self.window.transient(self.parent)
        self.window.grab_set()

        # Main container
        main_frame = tk.Frame(self.window, bg=COLORS['bg'])
        main_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=15)

        # Header
        header_frame = tk.Frame(main_frame, bg=COLORS['bg'])
        header_frame.pack(fill=tk.X, pady=(0, 15))

        tk.Label(
            header_frame,
            text="Manual Redaction",
            font=('SF Pro Display', 18, 'bold'),
            fg=COLORS['primary'],
            bg=COLORS['bg']
        ).pack(side=tk.LEFT)

        tk.Label(
            header_frame,
            text="Click and drag to select areas to redact",
            font=FONTS['body'],
            fg=COLORS['secondary'],
            bg=COLORS['bg']
        ).pack(side=tk.LEFT, padx=(15, 0))

        # Toolbar - card style
        toolbar_card = tk.Frame(main_frame, bg=COLORS['surface'], highlightbackground=COLORS['border'], highlightthickness=1)
        toolbar_card.pack(fill=tk.X, pady=(0, 10))

        toolbar = tk.Frame(toolbar_card, bg=COLORS['surface'])
        toolbar.pack(fill=tk.X, padx=15, pady=10)

        # Left side - page nav (hidden by default)
        self.nav_frame = tk.Frame(toolbar, bg=COLORS['surface'])
        self.nav_frame.pack(side=tk.LEFT)

        self.prev_btn = self._create_button(self.nav_frame, "← Prev", self._prev_page, style='secondary')
        self.prev_btn.pack(side=tk.LEFT, padx=(0, 5))

        self.page_label = tk.Label(
            self.nav_frame,
            text="Page 1 of 1",
            font=FONTS['body'],
            fg=COLORS['secondary'],
            bg=COLORS['surface']
        )
        self.page_label.pack(side=tk.LEFT, padx=10)

        self.next_btn = self._create_button(self.nav_frame, "Next →", self._next_page, style='secondary')
        self.next_btn.pack(side=tk.LEFT)

        self.nav_frame.pack_forget()  # Hidden until multi-page

        # Right side - action buttons
        btn_frame = tk.Frame(toolbar, bg=COLORS['surface'])
        btn_frame.pack(side=tk.RIGHT)

        self._create_button(btn_frame, "Undo", self._undo_last, style='secondary').pack(side=tk.LEFT, padx=(0, 8))
        self._create_button(btn_frame, "Clear All", self._clear_all, style='secondary').pack(side=tk.LEFT, padx=(0, 20))
        self._create_button(btn_frame, "Cancel", self._cancel, style='secondary').pack(side=tk.LEFT, padx=(0, 8))
        self._create_button(btn_frame, "Apply Redactions", self._apply_redactions, style='primary').pack(side=tk.LEFT)

        # Canvas card
        canvas_card = tk.Frame(main_frame, bg=COLORS['surface'], highlightbackground=COLORS['border'], highlightthickness=1)
        canvas_card.pack(fill=tk.BOTH, expand=True)

        canvas_inner = tk.Frame(canvas_card, bg=COLORS['surface'])
        canvas_inner.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

        # Scrollbars
        self.v_scrollbar = ttk.Scrollbar(canvas_inner, orient=tk.VERTICAL)
        self.v_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.h_scrollbar = ttk.Scrollbar(canvas_inner, orient=tk.HORIZONTAL)
        self.h_scrollbar.pack(side=tk.BOTTOM, fill=tk.X)

        # Canvas
        self.canvas = tk.Canvas(
            canvas_inner,
            bg='#E5E7EB',
            xscrollcommand=self.h_scrollbar.set,
            yscrollcommand=self.v_scrollbar.set,
            highlightthickness=0
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.v_scrollbar.config(command=self.canvas.yview)
        self.h_scrollbar.config(command=self.canvas.xview)

        # Status bar
        self.status_var = tk.StringVar(value="Ready — draw rectangles to mark areas for redaction")
        status_bar = tk.Label(
            main_frame,
            textvariable=self.status_var,
            font=FONTS['body'],
            fg=COLORS['muted'],
            bg=COLORS['bg'],
            anchor='w'
        )
        status_bar.pack(fill=tk.X, pady=(10, 0))

    def _create_button(self, parent, text, command, style='secondary'):
        """Create a styled button with high contrast"""
        if style == 'primary':
            btn = ColorButton(
                parent, text=text, command=command,
                bg=COLORS['accent'], fg=COLORS['button_text'],
                font=FONTS['button'], padx=16, pady=8
            )
        else:
            btn = ColorButton(
                parent, text=text, command=command,
                bg='#64748B', fg=COLORS['button_text'],
                font=FONTS['body'], padx=12, pady=6
            )
        return btn

    def _load_image(self):
        """Load the image or PDF for preview"""
        path = Path(self.image_path)
        ext = path.suffix.lower()

        try:
            if ext == '.pdf':
                self.pages = convert_from_path(str(path), dpi=150)
                if self.pages:
                    self.original_image = self.pages[0]
                    if len(self.pages) > 1:
                        self.nav_frame.pack(side=tk.LEFT)
                        self._update_page_label()
            else:
                self.original_image = Image.open(str(path))
                if self.original_image.mode != 'RGB':
                    self.original_image = self.original_image.convert('RGB')
                self.pages = [self.original_image]

            self.page_selections = [[] for _ in self.pages]
            self.page_rect_ids = [[] for _ in self.pages]

        except Exception as e:
            messagebox.showerror("Error", f"Failed to load image: {e}")
            self.window.destroy()

    def _setup_canvas(self):
        """Setup canvas with the loaded image"""
        if not self.original_image:
            return

        canvas_width = 900
        canvas_height = 580

        img_width, img_height = self.original_image.size
        scale_x = canvas_width / img_width
        scale_y = canvas_height / img_height
        self.scale_factor = min(scale_x, scale_y, 1.0)

        display_width = int(img_width * self.scale_factor)
        display_height = int(img_height * self.scale_factor)

        self.display_image = self.original_image.copy()
        self.display_image = self.display_image.resize(
            (display_width, display_height),
            Image.LANCZOS
        )

        self.photo_image = ImageTk.PhotoImage(self.display_image)

        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self.photo_image, tags="image")
        self.canvas.config(scrollregion=(0, 0, display_width, display_height))

        self.selections = self.page_selections[self.current_page]
        self.rect_ids = []
        for sel in self.selections:
            self._draw_selection_rect(sel)

    def _setup_bindings(self):
        """Setup mouse event bindings"""
        self.canvas.bind("<ButtonPress-1>", self._on_mouse_down)
        self.canvas.bind("<B1-Motion>", self._on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_mouse_up)
        self.window.bind("<Escape>", lambda e: self._cancel())
        self.window.bind("<Control-z>", lambda e: self._undo_last())
        self.window.bind("<Command-z>", lambda e: self._undo_last())  # macOS

    def _on_mouse_down(self, event):
        self.start_x = self.canvas.canvasx(event.x)
        self.start_y = self.canvas.canvasy(event.y)
        self.current_rect = self.canvas.create_rectangle(
            self.start_x, self.start_y, self.start_x, self.start_y,
            outline=COLORS['danger'], width=2, dash=(4, 4)
        )

    def _on_mouse_drag(self, event):
        if self.current_rect:
            cur_x = self.canvas.canvasx(event.x)
            cur_y = self.canvas.canvasy(event.y)
            self.canvas.coords(self.current_rect, self.start_x, self.start_y, cur_x, cur_y)

    def _on_mouse_up(self, event):
        if self.current_rect:
            end_x = self.canvas.canvasx(event.x)
            end_y = self.canvas.canvasy(event.y)

            x1 = min(self.start_x, end_x)
            y1 = min(self.start_y, end_y)
            x2 = max(self.start_x, end_x)
            y2 = max(self.start_y, end_y)

            if (x2 - x1) > 5 and (y2 - y1) > 5:
                img_coords = (
                    int(x1 / self.scale_factor),
                    int(y1 / self.scale_factor),
                    int(x2 / self.scale_factor),
                    int(y2 / self.scale_factor)
                )
                self.selections.append(img_coords)
                self.page_selections[self.current_page] = self.selections

                self.canvas.itemconfig(self.current_rect, dash=(), fill='', outline=COLORS['danger'])
                self.rect_ids.append(self.current_rect)
                self.page_rect_ids[self.current_page] = self.rect_ids

                count = len(self.selections)
                self.status_var.set(f"{count} area{'s' if count != 1 else ''} selected for redaction")
            else:
                self.canvas.delete(self.current_rect)

            self.current_rect = None

    def _draw_selection_rect(self, img_coords):
        x1, y1, x2, y2 = img_coords
        dx1 = int(x1 * self.scale_factor)
        dy1 = int(y1 * self.scale_factor)
        dx2 = int(x2 * self.scale_factor)
        dy2 = int(y2 * self.scale_factor)

        rect_id = self.canvas.create_rectangle(
            dx1, dy1, dx2, dy2,
            outline=COLORS['danger'], width=2
        )
        self.rect_ids.append(rect_id)

    def _undo_last(self):
        if self.selections and self.rect_ids:
            self.selections.pop()
            rect_id = self.rect_ids.pop()
            self.canvas.delete(rect_id)
            self.page_selections[self.current_page] = self.selections
            self.page_rect_ids[self.current_page] = self.rect_ids
            count = len(self.selections)
            self.status_var.set(f"{count} area{'s' if count != 1 else ''} selected for redaction")

    def _clear_all(self):
        for rect_id in self.rect_ids:
            self.canvas.delete(rect_id)
        self.selections = []
        self.rect_ids = []
        self.page_selections[self.current_page] = []
        self.page_rect_ids[self.current_page] = []
        self.status_var.set("All selections cleared")

    def _prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            self.original_image = self.pages[self.current_page]
            self._setup_canvas()
            self._update_page_label()

    def _next_page(self):
        if self.current_page < len(self.pages) - 1:
            self.current_page += 1
            self.original_image = self.pages[self.current_page]
            self._setup_canvas()
            self._update_page_label()

    def _update_page_label(self):
        self.page_label.config(text=f"Page {self.current_page + 1} of {len(self.pages)}")
        self.prev_btn.config(state=tk.NORMAL if self.current_page > 0 else tk.DISABLED)
        self.next_btn.config(state=tk.NORMAL if self.current_page < len(self.pages) - 1 else tk.DISABLED)

    def _apply_redactions(self):
        total_selections = sum(len(sels) for sels in self.page_selections)
        if total_selections == 0:
            messagebox.showinfo("No Selections", "No areas selected for redaction.")
            return

        try:
            path = Path(self.image_path)
            ext = path.suffix.lower()

            if ext == '.pdf':
                redacted_pages = []
                for i, page_img in enumerate(self.pages):
                    img = page_img.copy()
                    if img.mode != 'RGB':
                        img = img.convert('RGB')

                    page_sels = self.page_selections[i] if i < len(self.page_selections) else []

                    if page_sels:
                        draw = ImageDraw.Draw(img)
                        for x1, y1, x2, y2 in page_sels:
                            draw.rectangle([x1, y1, x2, y2], fill='black')

                    redacted_pages.append(img)

                if redacted_pages:
                    redacted_pages[0].save(
                        str(path),
                        save_all=True,
                        append_images=redacted_pages[1:] if len(redacted_pages) > 1 else [],
                        resolution=150
                    )
            else:
                img = Image.open(str(path))
                if img.mode != 'RGB':
                    img = img.convert('RGB')

                draw = ImageDraw.Draw(img)
                for x1, y1, x2, y2 in self.page_selections[0]:
                    draw.rectangle([x1, y1, x2, y2], fill='black')

                img.save(str(path), quality=95)

            self.status_var.set("Redactions applied successfully!")
            messagebox.showinfo(
                "Success",
                f"Manual redactions applied!\n\n{total_selections} area{'s' if total_selections != 1 else ''} redacted."
            )

            if self.on_save_callback:
                self.on_save_callback()

            self.window.destroy()

        except Exception as e:
            messagebox.showerror("Error", f"Failed to apply redactions: {e}")

    def _cancel(self):
        self.window.destroy()


class PHIRedactorGUI:
    """Modern, clean GUI for PHI redaction"""

    SUPPORTED_FILETYPES = [
        ('All Supported', '*.pdf *.png *.jpg *.jpeg *.tiff *.tif *.bmp *.gif *.txt'),
        ('PDF files', '*.pdf'),
        ('Image files', '*.png *.jpg *.jpeg *.tiff *.tif *.bmp *.gif'),
        ('Text files', '*.txt'),
        ('All files', '*.*')
    ]

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("PHI Redactor")
        self.root.geometry("680x620")
        self.root.minsize(580, 550)
        self.root.configure(bg=COLORS['bg'])

        # Initialize redactor
        self.redactor = PHIRedactor()

        # Track state
        self.current_file = None
        self.processing = False
        self.last_output_path = None

        self._setup_styles()
        self._setup_ui()
        self._setup_bindings()

    def _setup_styles(self):
        """Configure ttk styles for modern look"""
        style = ttk.Style()

        # Use clam theme as base (more customizable)
        style.theme_use('clam')

        # Configure progress bar
        style.configure(
            'Custom.Horizontal.TProgressbar',
            troughcolor=COLORS['border'],
            background=COLORS['accent'],
            thickness=6
        )

        # Configure checkbuttons with larger font
        style.configure(
            'Custom.TCheckbutton',
            background=COLORS['surface'],
            foreground=COLORS['secondary'],
            font=FONTS['body']
        )

    def _setup_ui(self):
        """Create the modern user interface"""
        # Main container
        main_frame = tk.Frame(self.root, bg=COLORS['bg'])
        main_frame.pack(fill=tk.BOTH, expand=True, padx=30, pady=25)

        # ── Header ──────────────────────────────────────────────────────────
        header_frame = tk.Frame(main_frame, bg=COLORS['bg'])
        header_frame.pack(fill=tk.X, pady=(0, 20))

        # App icon/badge
        badge_frame = tk.Frame(header_frame, bg=COLORS['accent'], padx=10, pady=6)
        badge_frame.pack(side=tk.LEFT)
        tk.Label(
            badge_frame,
            text="PHI",
            font=('SF Pro Display', 16, 'bold'),
            fg=COLORS['button_text'],
            bg=COLORS['accent']
        ).pack()

        # Title and subtitle
        title_frame = tk.Frame(header_frame, bg=COLORS['bg'])
        title_frame.pack(side=tk.LEFT, padx=(12, 0))

        tk.Label(
            title_frame,
            text="PHI Redactor",
            font=('SF Pro Display', 20, 'bold'),
            fg=COLORS['primary'],
            bg=COLORS['bg']
        ).pack(anchor='w')

        tk.Label(
            title_frame,
            text="HIPAA-compliant document redaction",
            font=FONTS['body'],
            fg=COLORS['muted'],
            bg=COLORS['bg']
        ).pack(anchor='w')

        # ── File Selection Card ─────────────────────────────────────────────
        file_card = tk.Frame(main_frame, bg=COLORS['surface'], highlightbackground=COLORS['border'], highlightthickness=1)
        file_card.pack(fill=tk.X, pady=(0, 15))

        file_inner = tk.Frame(file_card, bg=COLORS['surface'])
        file_inner.pack(fill=tk.X, padx=20, pady=18)

        tk.Label(
            file_inner,
            text="Select Document",
            font=FONTS['header'],
            fg=COLORS['primary'],
            bg=COLORS['surface']
        ).pack(anchor='w')

        tk.Label(
            file_inner,
            text="PDF, PNG, JPG, TIFF, BMP, GIF, or TXT",
            font=FONTS['body'],
            fg=COLORS['muted'],
            bg=COLORS['surface']
        ).pack(anchor='w', pady=(2, 10))

        # File entry row
        entry_frame = tk.Frame(file_inner, bg=COLORS['surface'])
        entry_frame.pack(fill=tk.X)

        self.file_var = tk.StringVar()
        self.file_entry = tk.Entry(
            entry_frame,
            textvariable=self.file_var,
            font=FONTS['body'],
            fg=COLORS['primary'],
            bg='#F8FAFC',
            relief=tk.FLAT,
            highlightbackground=COLORS['border'],
            highlightthickness=1,
            highlightcolor=COLORS['accent']
        )
        self.file_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=8, padx=(0, 10))

        self.browse_btn = ColorButton(
            entry_frame,
            text="Browse...",
            command=self._browse_file,
            bg=COLORS['accent'],
            fg=COLORS['button_text'],
            font=FONTS['button'],
            padx=14,
            pady=8
        )
        self.browse_btn.pack(side=tk.LEFT)

        # ── Options Card ────────────────────────────────────────────────────
        options_card = tk.Frame(main_frame, bg=COLORS['surface'], highlightbackground=COLORS['border'], highlightthickness=1)
        options_card.pack(fill=tk.X, pady=(0, 15))

        options_inner = tk.Frame(options_card, bg=COLORS['surface'])
        options_inner.pack(fill=tk.X, padx=20, pady=15)

        tk.Label(
            options_inner,
            text="Options",
            font=FONTS['header'],
            fg=COLORS['primary'],
            bg=COLORS['surface']
        ).pack(anchor='w', pady=(0, 10))

        self.aggressive_var = tk.BooleanVar(value=False)
        aggressive_cb = ttk.Checkbutton(
            options_inner,
            text="Aggressive mode — redact more liberally (may over-redact)",
            variable=self.aggressive_var,
            style='Custom.TCheckbutton'
        )
        aggressive_cb.pack(anchor='w', pady=(0, 5))

        self.save_text_var = tk.BooleanVar(value=True)
        save_text_cb = ttk.Checkbutton(
            options_inner,
            text="Save extracted text — for LLM input",
            variable=self.save_text_var,
            style='Custom.TCheckbutton'
        )
        save_text_cb.pack(anchor='w')

        # ── Action Buttons ──────────────────────────────────────────────────
        button_frame = tk.Frame(main_frame, bg=COLORS['bg'])
        button_frame.pack(fill=tk.X, pady=(5, 15))

        self.redact_btn = ColorButton(
            button_frame,
            text="Redact PHI",
            command=self._start_redaction,
            bg=COLORS['accent'],
            fg=COLORS['button_text'],
            font=('Helvetica', 14, 'bold'),
            padx=28,
            pady=12
        )
        self.redact_btn.pack(side=tk.LEFT)

        self.manual_btn = ColorButton(
            button_frame,
            text="Review & Manual Redact",
            command=self._open_manual_redaction,
            bg='#64748B',
            fg=COLORS['button_text'],
            font=FONTS['button'],
            padx=14,
            pady=10
        )
        self.manual_btn.pack(side=tk.LEFT, padx=(12, 0))
        self.manual_btn.config(state=tk.DISABLED)  # Start disabled

        # ── Progress Bar ────────────────────────────────────────────────────
        self.progress = ttk.Progressbar(
            main_frame,
            mode='indeterminate',
            style='Custom.Horizontal.TProgressbar'
        )
        self.progress.pack(fill=tk.X, pady=(0, 15))

        # ── Results Card ────────────────────────────────────────────────────
        results_card = tk.Frame(main_frame, bg=COLORS['surface'], highlightbackground=COLORS['border'], highlightthickness=1)
        results_card.pack(fill=tk.BOTH, expand=True)

        results_inner = tk.Frame(results_card, bg=COLORS['surface'])
        results_inner.pack(fill=tk.BOTH, expand=True, padx=15, pady=15)

        tk.Label(
            results_inner,
            text="Results",
            font=FONTS['header'],
            fg=COLORS['primary'],
            bg=COLORS['surface']
        ).pack(anchor='w', pady=(0, 8))

        # Results text area with custom styling
        text_frame = tk.Frame(results_inner, bg=COLORS['border'])
        text_frame.pack(fill=tk.BOTH, expand=True)

        self.results_text = tk.Text(
            text_frame,
            wrap=tk.WORD,
            font=FONTS['mono'],
            fg=COLORS['secondary'],
            bg='#FAFBFC',
            relief=tk.FLAT,
            padx=12,
            pady=10,
            highlightthickness=0
        )
        self.results_text.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        # Scrollbar for results
        scrollbar = ttk.Scrollbar(self.results_text, orient=tk.VERTICAL, command=self.results_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.results_text.config(yscrollcommand=scrollbar.set)

        # Initial message
        self.results_text.insert(tk.END, "Select a document and click 'Redact PHI' to begin.\n\n")
        self.results_text.insert(tk.END, "All processing happens locally — no data leaves your computer.\n")
        self.results_text.config(state=tk.DISABLED)

        # ── Footer ──────────────────────────────────────────────────────────
        footer_frame = tk.Frame(main_frame, bg=COLORS['bg'])
        footer_frame.pack(fill=tk.X, pady=(12, 0))

        tk.Label(
            footer_frame,
            text="100% Local Processing • HIPAA Compliant",
            font=FONTS['body'],
            fg=COLORS['muted'],
            bg=COLORS['bg']
        ).pack(side=tk.LEFT)

    def _setup_bindings(self):
        """Set up keyboard shortcuts"""
        self.root.bind('<Control-o>', lambda e: self._browse_file())
        self.root.bind('<Command-o>', lambda e: self._browse_file())  # macOS
        self.root.bind('<Return>', lambda e: self._start_redaction())

        # Drag and drop
        try:
            self.root.drop_target_register('DND_Files')
            self.root.dnd_bind('<<Drop>>', self._handle_drop)
        except:
            pass

    def _browse_file(self):
        """Open file browser dialog"""
        filename = filedialog.askopenfilename(
            title="Select document to redact",
            filetypes=self.SUPPORTED_FILETYPES
        )
        if filename:
            self.file_var.set(filename)
            self.current_file = filename

    def _handle_drop(self, event):
        """Handle drag and drop file"""
        file_path = event.data.strip('{}')
        if os.path.isfile(file_path):
            self.file_var.set(file_path)
            self.current_file = file_path

    def _log(self, message: str):
        """Add message to results area"""
        self.results_text.config(state=tk.NORMAL)
        self.results_text.insert(tk.END, message + "\n")
        self.results_text.see(tk.END)
        self.results_text.config(state=tk.DISABLED)

    def _clear_log(self):
        """Clear results area"""
        self.results_text.config(state=tk.NORMAL)
        self.results_text.delete(1.0, tk.END)
        self.results_text.config(state=tk.DISABLED)

    def _start_redaction(self):
        """Start the redaction process"""
        file_path = self.file_var.get().strip()

        if not file_path:
            messagebox.showwarning("No File", "Please select a file to redact.")
            return

        if not os.path.isfile(file_path):
            messagebox.showerror("File Not Found", f"Cannot find file:\n{file_path}")
            return

        if self.processing:
            return

        # Update UI state
        self.processing = True
        self.redact_btn.config(state=tk.DISABLED, bg='#64748B')
        self.browse_btn.config(state=tk.DISABLED, bg='#64748B')
        self.manual_btn.config(state=tk.DISABLED, bg='#64748B')
        self.last_output_path = None
        self.progress.start(10)
        self._clear_log()

        # Run in background thread
        thread = threading.Thread(target=self._do_redaction, args=(file_path,))
        thread.daemon = True
        thread.start()

    def _do_redaction(self, file_path: str):
        """Perform the actual redaction"""
        try:
            self._log(f"Processing: {os.path.basename(file_path)}")
            self._log("Extracting text and detecting PHI...\n")

            self.redactor = PHIRedactor(aggressive=self.aggressive_var.get())

            result = self.redactor.redact_file(
                file_path,
                output_text=self.save_text_var.get()
            )

            self._log("✓ Redaction complete!\n")
            self._log(f"Output: {result['output_file']}")

            if result['text_output']:
                self._log(f"Text:   {result['text_output']}")

            self._log(f"\nRedactions: {result['redactions_count']}")

            if result['categories']:
                self._log("\nCategories:")
                for cat, count in sorted(result['categories'].items()):
                    self._log(f"  • {cat}: {count}")

            self._log("\n" + "─" * 45)
            self._log("Preview (first 500 chars):")
            self._log("─" * 45)
            preview = result['redacted_text'][:500] if result['redacted_text'] else "(no text)"
            self._log(preview)
            if len(result['redacted_text'] or '') > 500:
                self._log("...")

            self.last_output_path = result['output_file']
            self.root.after(0, self._enable_manual_btn)
            self.root.after(0, lambda: self._offer_open_folder(result['output_file']))

        except Exception as e:
            self._log(f"\n✗ Error: {str(e)}")
            self.root.after(0, lambda: messagebox.showerror("Error", str(e)))

        finally:
            self.root.after(0, self._reset_ui)

    def _enable_manual_btn(self):
        """Enable the manual redaction button with proper styling"""
        self.manual_btn.config(
            state=tk.NORMAL,
            fg=COLORS['button_text'],
            bg=COLORS['accent']
        )

    def _reset_ui(self):
        """Reset UI after processing"""
        self.processing = False
        self.redact_btn.config(state=tk.NORMAL, bg=COLORS['accent'], fg=COLORS['button_text'])
        self.browse_btn.config(state=tk.NORMAL, bg=COLORS['accent'], fg=COLORS['button_text'])
        self.progress.stop()

    def _offer_open_folder(self, output_path: str):
        """Ask user if they want to open the output folder"""
        if messagebox.askyesno(
            "Redaction Complete",
            "PHI redaction complete!\n\nWould you like to open the output folder?"
        ):
            folder = os.path.dirname(output_path)
            if sys.platform == 'win32':
                os.startfile(folder)
            elif sys.platform == 'darwin':
                os.system(f'open "{folder}"')
            else:
                os.system(f'xdg-open "{folder}"')

    def _open_manual_redaction(self):
        """Open the manual redaction preview window"""
        if not self.last_output_path:
            messagebox.showwarning(
                "No Output",
                "Please run auto-redaction first to generate an output file."
            )
            return

        if not os.path.isfile(self.last_output_path):
            messagebox.showerror(
                "File Not Found",
                f"Cannot find output file:\n{self.last_output_path}"
            )
            return

        ext = Path(self.last_output_path).suffix.lower()
        if ext == '.txt':
            messagebox.showinfo(
                "Not Supported",
                "Manual redaction is not available for text files.\n\n"
                "Text files can be edited directly in any text editor."
            )
            return

        def on_manual_redaction_complete():
            self._log("\n✓ Manual redactions applied successfully!")

        DocumentPreviewWindow(
            self.root,
            self.last_output_path,
            on_manual_redaction_complete
        )

    def run(self):
        """Start the application"""
        # Center window on screen
        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() // 2) - (width // 2)
        y = (self.root.winfo_screenheight() // 2) - (height // 2)
        self.root.geometry(f'{width}x{height}+{x}+{y}')

        self.root.mainloop()


def main():
    app = PHIRedactorGUI()
    app.run()


if __name__ == '__main__':
    main()
