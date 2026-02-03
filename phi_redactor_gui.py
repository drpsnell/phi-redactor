#!/usr/bin/env python3
"""
PHI Redactor GUI - Simple graphical interface for the PHI Redactor tool

Designed for busy clinicians who need quick, easy PHI redaction.
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


class DocumentPreviewWindow:
    """Window for previewing and manually redacting documents"""

    def __init__(self, parent, image_path, on_save_callback):
        """
        Initialize the preview window.

        Args:
            parent: Parent tkinter window
            image_path: Path to the image/PDF to preview
            on_save_callback: Function to call after manual redactions are applied
        """
        self.parent = parent
        self.image_path = image_path
        self.on_save_callback = on_save_callback

        # Selection state
        self.selections = []  # List of (x1, y1, x2, y2) tuples in image coordinates
        self.rect_ids = []    # Canvas rectangle IDs for each selection
        self.current_rect = None
        self.start_x = None
        self.start_y = None

        # Image state
        self.original_image = None
        self.display_image = None
        self.photo_image = None
        self.scale_factor = 1.0

        # For multi-page PDFs
        self.pages = []  # List of PIL Images
        self.current_page = 0

        self._setup_window()
        self._load_image()
        self._setup_canvas()
        self._setup_bindings()

    def _setup_window(self):
        """Create the preview window"""
        self.window = tk.Toplevel(self.parent)
        self.window.title("Manual Redaction - Select Areas to Redact")
        self.window.geometry("900x700")
        self.window.minsize(600, 500)

        # Make it modal-ish (keep focus but allow interaction with parent)
        self.window.transient(self.parent)
        self.window.grab_set()

        # Main container
        main_frame = ttk.Frame(self.window, padding="5")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Toolbar
        toolbar = ttk.Frame(main_frame)
        toolbar.pack(fill=tk.X, pady=(0, 5))

        # Instructions
        ttk.Label(
            toolbar,
            text="Click and drag to select areas to redact. Selected areas will be blacked out.",
            font=('Helvetica', 10)
        ).pack(side=tk.LEFT, padx=5)

        # Toolbar buttons (right side)
        btn_frame = ttk.Frame(toolbar)
        btn_frame.pack(side=tk.RIGHT)

        self.undo_btn = ttk.Button(
            btn_frame, text="Undo Last", command=self._undo_last
        )
        self.undo_btn.pack(side=tk.LEFT, padx=2)

        self.clear_btn = ttk.Button(
            btn_frame, text="Clear All", command=self._clear_all
        )
        self.clear_btn.pack(side=tk.LEFT, padx=2)

        ttk.Separator(btn_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, padx=5, fill=tk.Y)

        self.apply_btn = ttk.Button(
            btn_frame, text="Apply Redactions", command=self._apply_redactions
        )
        self.apply_btn.pack(side=tk.LEFT, padx=2)

        self.cancel_btn = ttk.Button(
            btn_frame, text="Cancel", command=self._cancel
        )
        self.cancel_btn.pack(side=tk.LEFT, padx=2)

        # Page navigation (for multi-page PDFs)
        self.nav_frame = ttk.Frame(main_frame)
        self.nav_frame.pack(fill=tk.X, pady=(0, 5))

        self.prev_btn = ttk.Button(
            self.nav_frame, text="< Prev Page", command=self._prev_page
        )
        self.prev_btn.pack(side=tk.LEFT, padx=2)

        self.page_label = ttk.Label(self.nav_frame, text="Page 1 of 1")
        self.page_label.pack(side=tk.LEFT, padx=10)

        self.next_btn = ttk.Button(
            self.nav_frame, text="Next Page >", command=self._next_page
        )
        self.next_btn.pack(side=tk.LEFT, padx=2)

        # Hide navigation by default (shown only for multi-page)
        self.nav_frame.pack_forget()

        # Canvas with scrollbars
        canvas_frame = ttk.Frame(main_frame)
        canvas_frame.pack(fill=tk.BOTH, expand=True)

        # Scrollbars
        self.v_scrollbar = ttk.Scrollbar(canvas_frame, orient=tk.VERTICAL)
        self.v_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.h_scrollbar = ttk.Scrollbar(canvas_frame, orient=tk.HORIZONTAL)
        self.h_scrollbar.pack(side=tk.BOTTOM, fill=tk.X)

        # Canvas
        self.canvas = tk.Canvas(
            canvas_frame,
            bg='gray',
            xscrollcommand=self.h_scrollbar.set,
            yscrollcommand=self.v_scrollbar.set
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.v_scrollbar.config(command=self.canvas.yview)
        self.h_scrollbar.config(command=self.canvas.xview)

        # Status bar
        self.status_var = tk.StringVar(value="Ready - draw rectangles to select areas for redaction")
        status_bar = ttk.Label(main_frame, textvariable=self.status_var, relief=tk.SUNKEN)
        status_bar.pack(fill=tk.X, pady=(5, 0))

    def _load_image(self):
        """Load the image or PDF for preview"""
        path = Path(self.image_path)
        ext = path.suffix.lower()

        try:
            if ext == '.pdf':
                # Convert PDF pages to images
                self.pages = convert_from_path(str(path), dpi=150)
                if self.pages:
                    self.original_image = self.pages[0]
                    if len(self.pages) > 1:
                        self.nav_frame.pack(fill=tk.X, pady=(0, 5))
                        self._update_page_label()
            else:
                # Load image directly
                self.original_image = Image.open(str(path))
                if self.original_image.mode != 'RGB':
                    self.original_image = self.original_image.convert('RGB')
                self.pages = [self.original_image]

            # Initialize selections list for each page
            self.page_selections = [[] for _ in self.pages]
            self.page_rect_ids = [[] for _ in self.pages]

        except Exception as e:
            messagebox.showerror("Error", f"Failed to load image: {e}")
            self.window.destroy()

    def _setup_canvas(self):
        """Setup canvas with the loaded image"""
        if not self.original_image:
            return

        # Calculate scale to fit window (with some padding)
        canvas_width = 850
        canvas_height = 550

        img_width, img_height = self.original_image.size

        # Calculate scale factor to fit image in canvas
        scale_x = canvas_width / img_width
        scale_y = canvas_height / img_height
        self.scale_factor = min(scale_x, scale_y, 1.0)  # Don't upscale

        # Resize for display
        display_width = int(img_width * self.scale_factor)
        display_height = int(img_height * self.scale_factor)

        self.display_image = self.original_image.copy()
        self.display_image = self.display_image.resize(
            (display_width, display_height),
            Image.LANCZOS
        )

        # Convert to PhotoImage
        self.photo_image = ImageTk.PhotoImage(self.display_image)

        # Clear canvas and add image
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self.photo_image, tags="image")

        # Configure scroll region
        self.canvas.config(scrollregion=(0, 0, display_width, display_height))

        # Restore selections for current page
        self.selections = self.page_selections[self.current_page]
        self.rect_ids = []
        for sel in self.selections:
            self._draw_selection_rect(sel)

    def _setup_bindings(self):
        """Setup mouse event bindings"""
        self.canvas.bind("<ButtonPress-1>", self._on_mouse_down)
        self.canvas.bind("<B1-Motion>", self._on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_mouse_up)

        # Keyboard shortcuts
        self.window.bind("<Escape>", lambda e: self._cancel())
        self.window.bind("<Control-z>", lambda e: self._undo_last())

    def _on_mouse_down(self, event):
        """Handle mouse button press"""
        # Get canvas coordinates (accounting for scroll)
        self.start_x = self.canvas.canvasx(event.x)
        self.start_y = self.canvas.canvasy(event.y)

        # Create rectangle
        self.current_rect = self.canvas.create_rectangle(
            self.start_x, self.start_y, self.start_x, self.start_y,
            outline='red', width=2, dash=(4, 4)
        )

    def _on_mouse_drag(self, event):
        """Handle mouse drag"""
        if self.current_rect:
            cur_x = self.canvas.canvasx(event.x)
            cur_y = self.canvas.canvasy(event.y)
            self.canvas.coords(self.current_rect, self.start_x, self.start_y, cur_x, cur_y)

    def _on_mouse_up(self, event):
        """Handle mouse button release"""
        if self.current_rect:
            end_x = self.canvas.canvasx(event.x)
            end_y = self.canvas.canvasy(event.y)

            # Normalize coordinates (ensure x1 < x2, y1 < y2)
            x1 = min(self.start_x, end_x)
            y1 = min(self.start_y, end_y)
            x2 = max(self.start_x, end_x)
            y2 = max(self.start_y, end_y)

            # Only add if rectangle has meaningful size
            if (x2 - x1) > 5 and (y2 - y1) > 5:
                # Store in image coordinates (scale back)
                img_coords = (
                    int(x1 / self.scale_factor),
                    int(y1 / self.scale_factor),
                    int(x2 / self.scale_factor),
                    int(y2 / self.scale_factor)
                )
                self.selections.append(img_coords)
                self.page_selections[self.current_page] = self.selections

                # Update rectangle to solid line
                self.canvas.itemconfig(self.current_rect, dash=(), fill='', outline='red')
                self.rect_ids.append(self.current_rect)
                self.page_rect_ids[self.current_page] = self.rect_ids

                self.status_var.set(f"{len(self.selections)} area(s) selected for redaction")
            else:
                # Too small, delete it
                self.canvas.delete(self.current_rect)

            self.current_rect = None

    def _draw_selection_rect(self, img_coords):
        """Draw a selection rectangle from image coordinates"""
        x1, y1, x2, y2 = img_coords
        # Convert to display coordinates
        dx1 = int(x1 * self.scale_factor)
        dy1 = int(y1 * self.scale_factor)
        dx2 = int(x2 * self.scale_factor)
        dy2 = int(y2 * self.scale_factor)

        rect_id = self.canvas.create_rectangle(
            dx1, dy1, dx2, dy2,
            outline='red', width=2
        )
        self.rect_ids.append(rect_id)

    def _undo_last(self):
        """Remove the last selection"""
        if self.selections and self.rect_ids:
            self.selections.pop()
            rect_id = self.rect_ids.pop()
            self.canvas.delete(rect_id)
            self.page_selections[self.current_page] = self.selections
            self.page_rect_ids[self.current_page] = self.rect_ids
            self.status_var.set(f"{len(self.selections)} area(s) selected for redaction")

    def _clear_all(self):
        """Clear all selections on current page"""
        for rect_id in self.rect_ids:
            self.canvas.delete(rect_id)
        self.selections = []
        self.rect_ids = []
        self.page_selections[self.current_page] = []
        self.page_rect_ids[self.current_page] = []
        self.status_var.set("All selections cleared")

    def _prev_page(self):
        """Go to previous page"""
        if self.current_page > 0:
            self.current_page -= 1
            self.original_image = self.pages[self.current_page]
            self._setup_canvas()
            self._update_page_label()

    def _next_page(self):
        """Go to next page"""
        if self.current_page < len(self.pages) - 1:
            self.current_page += 1
            self.original_image = self.pages[self.current_page]
            self._setup_canvas()
            self._update_page_label()

    def _update_page_label(self):
        """Update the page navigation label"""
        self.page_label.config(text=f"Page {self.current_page + 1} of {len(self.pages)}")
        self.prev_btn.config(state=tk.NORMAL if self.current_page > 0 else tk.DISABLED)
        self.next_btn.config(state=tk.NORMAL if self.current_page < len(self.pages) - 1 else tk.DISABLED)

    def _apply_redactions(self):
        """Apply the manual redactions to the image file"""
        # Check if any selections exist across all pages
        total_selections = sum(len(sels) for sels in self.page_selections)
        if total_selections == 0:
            messagebox.showinfo("No Selections", "No areas selected for redaction.")
            return

        try:
            path = Path(self.image_path)
            ext = path.suffix.lower()

            if ext == '.pdf':
                # For PDFs, apply redactions to each page and save
                redacted_pages = []
                for i, page_img in enumerate(self.pages):
                    img = page_img.copy()
                    if img.mode != 'RGB':
                        img = img.convert('RGB')

                    # Get selections for this page
                    page_sels = self.page_selections[i] if i < len(self.page_selections) else []

                    if page_sels:
                        draw = ImageDraw.Draw(img)
                        for x1, y1, x2, y2 in page_sels:
                            draw.rectangle([x1, y1, x2, y2], fill='black')

                    redacted_pages.append(img)

                # Save multi-page PDF
                if redacted_pages:
                    redacted_pages[0].save(
                        str(path),
                        save_all=True,
                        append_images=redacted_pages[1:] if len(redacted_pages) > 1 else [],
                        resolution=150
                    )
            else:
                # For images, load original, apply redactions, save
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
                f"Manual redactions applied!\n\n{total_selections} area(s) redacted."
            )

            # Call the callback and close
            if self.on_save_callback:
                self.on_save_callback()

            self.window.destroy()

        except Exception as e:
            messagebox.showerror("Error", f"Failed to apply redactions: {e}")

    def _cancel(self):
        """Close the window without saving"""
        self.window.destroy()


class PHIRedactorGUI:
    """Simple, fast GUI for PHI redaction"""
    
    SUPPORTED_FILETYPES = [
        ('All Supported', '*.pdf *.png *.jpg *.jpeg *.tiff *.tif *.bmp *.gif *.txt'),
        ('PDF files', '*.pdf'),
        ('Image files', '*.png *.jpg *.jpeg *.tiff *.tif *.bmp *.gif'),
        ('Text files', '*.txt'),
        ('All files', '*.*')
    ]
    
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("PHI Redactor - HIPAA Compliant Document Redaction")
        self.root.geometry("700x550")
        self.root.minsize(500, 450)

        # Initialize redactor
        self.redactor = PHIRedactor()

        # Track state
        self.current_file = None
        self.processing = False
        self.last_output_path = None  # Track output for manual redaction

        self._setup_ui()
        self._setup_bindings()
    
    def _setup_ui(self):
        """Create the user interface"""
        # Main container with padding
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Header
        header = ttk.Label(
            main_frame,
            text="PHI Redactor",
            font=('Helvetica', 18, 'bold')
        )
        header.pack(pady=(0, 5))
        
        subtitle = ttk.Label(
            main_frame,
            text="Quickly redact Protected Health Information from clinical documents",
            font=('Helvetica', 10)
        )
        subtitle.pack(pady=(0, 15))
        
        # File selection frame
        file_frame = ttk.LabelFrame(main_frame, text="Select Document", padding="10")
        file_frame.pack(fill=tk.X, pady=(0, 10))
        
        # File path entry
        self.file_var = tk.StringVar()
        self.file_entry = ttk.Entry(file_frame, textvariable=self.file_var, width=50)
        self.file_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        
        # Browse button
        self.browse_btn = ttk.Button(
            file_frame,
            text="Browse...",
            command=self._browse_file
        )
        self.browse_btn.pack(side=tk.LEFT)
        
        # Options frame
        options_frame = ttk.LabelFrame(main_frame, text="Options", padding="10")
        options_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Aggressive mode checkbox
        self.aggressive_var = tk.BooleanVar(value=False)
        aggressive_cb = ttk.Checkbutton(
            options_frame,
            text="Aggressive mode (redact more liberally - may over-redact)",
            variable=self.aggressive_var
        )
        aggressive_cb.pack(anchor=tk.W)
        
        # Save text output checkbox
        self.save_text_var = tk.BooleanVar(value=True)
        save_text_cb = ttk.Checkbutton(
            options_frame,
            text="Save extracted text (for LLM input)",
            variable=self.save_text_var
        )
        save_text_cb.pack(anchor=tk.W)
        
        # Button frame for action buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(pady=10)

        # Redact button
        self.redact_btn = ttk.Button(
            button_frame,
            text="Redact PHI",
            command=self._start_redaction,
            style='Accent.TButton'
        )
        self.redact_btn.pack(side=tk.LEFT, padx=5, ipadx=20, ipady=5)

        # Manual redaction button (disabled until auto-redaction completes)
        self.manual_btn = ttk.Button(
            button_frame,
            text="Review & Manual Redact",
            command=self._open_manual_redaction,
            state=tk.DISABLED
        )
        self.manual_btn.pack(side=tk.LEFT, padx=5, ipadx=10, ipady=5)

        # Progress bar
        self.progress = ttk.Progressbar(main_frame, mode='indeterminate')
        self.progress.pack(fill=tk.X, pady=(0, 10))
        
        # Status/Results area
        results_frame = ttk.LabelFrame(main_frame, text="Results", padding="10")
        results_frame.pack(fill=tk.BOTH, expand=True)
        
        self.results_text = scrolledtext.ScrolledText(
            results_frame,
            wrap=tk.WORD,
            height=10,
            font=('Consolas', 10)
        )
        self.results_text.pack(fill=tk.BOTH, expand=True)
        self.results_text.insert(tk.END, "Select a file and click 'Redact PHI' to begin.\n\n")
        self.results_text.insert(tk.END, "Supported formats: PDF, PNG, JPG, TIFF, BMP, GIF, TXT\n\n")
        self.results_text.insert(tk.END, "⚠️ All processing happens locally - no data leaves your computer.")
        self.results_text.config(state=tk.DISABLED)
        
        # Footer
        footer = ttk.Label(
            main_frame,
            text="🔒 100% Local Processing - HIPAA Compliant",
            font=('Helvetica', 9, 'italic')
        )
        footer.pack(pady=(10, 0))
        
        # Configure styles
        style = ttk.Style()
        try:
            style.configure('Accent.TButton', font=('Helvetica', 12, 'bold'))
        except:
            pass
    
    def _setup_bindings(self):
        """Set up keyboard shortcuts and drag-drop"""
        self.root.bind('<Control-o>', lambda e: self._browse_file())
        self.root.bind('<Return>', lambda e: self._start_redaction())
        
        # Enable drag and drop if available
        try:
            self.root.drop_target_register('DND_Files')
            self.root.dnd_bind('<<Drop>>', self._handle_drop)
        except:
            pass  # Drag and drop not available
    
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
        file_path = event.data
        # Clean up path (remove braces on some systems)
        file_path = file_path.strip('{}')
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
        """Start the redaction process in a background thread"""
        file_path = self.file_var.get().strip()
        
        if not file_path:
            messagebox.showwarning("No File", "Please select a file to redact.")
            return
        
        if not os.path.isfile(file_path):
            messagebox.showerror("File Not Found", f"Cannot find file: {file_path}")
            return
        
        if self.processing:
            return
        
        # Update UI state
        self.processing = True
        self.redact_btn.config(state=tk.DISABLED)
        self.browse_btn.config(state=tk.DISABLED)
        self.manual_btn.config(state=tk.DISABLED)  # Disable until new redaction completes
        self.last_output_path = None  # Reset output path
        self.progress.start(10)
        self._clear_log()
        
        # Run in background thread
        thread = threading.Thread(target=self._do_redaction, args=(file_path,))
        thread.daemon = True
        thread.start()
    
    def _do_redaction(self, file_path: str):
        """Perform the actual redaction (runs in background thread)"""
        try:
            self._log(f"Processing: {os.path.basename(file_path)}")
            self._log("Extracting text and detecting PHI...")
            
            # Update redactor settings
            self.redactor = PHIRedactor(aggressive=self.aggressive_var.get())
            
            # Perform redaction
            result = self.redactor.redact_file(
                file_path,
                output_text=self.save_text_var.get()
            )
            
            # Report results
            self._log("\n✓ Redaction complete!\n")
            self._log(f"Output file: {result['output_file']}")
            
            if result['text_output']:
                self._log(f"Text output: {result['text_output']}")
            
            self._log(f"\nTotal redactions: {result['redactions_count']}")
            
            if result['categories']:
                self._log("\nPHI Categories found:")
                for cat, count in sorted(result['categories'].items()):
                    self._log(f"  • {cat}: {count}")
            
            self._log("\n" + "="*50)
            self._log("Redacted text preview (first 500 chars):")
            self._log("="*50)
            preview = result['redacted_text'][:500] if result['redacted_text'] else "(no text)"
            self._log(preview)
            if len(result['redacted_text'] or '') > 500:
                self._log("...")

            # Store output path and enable manual redaction button
            self.last_output_path = result['output_file']
            self.root.after(0, lambda: self.manual_btn.config(state=tk.NORMAL))

            # Offer to open output folder
            self.root.after(0, lambda: self._offer_open_folder(result['output_file']))

        except Exception as e:
            self._log(f"\n❌ Error: {str(e)}")
            self.root.after(0, lambda: messagebox.showerror("Error", str(e)))
        
        finally:
            # Reset UI state
            self.root.after(0, self._reset_ui)
    
    def _reset_ui(self):
        """Reset UI after processing"""
        self.processing = False
        self.redact_btn.config(state=tk.NORMAL)
        self.browse_btn.config(state=tk.NORMAL)
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
                f"Cannot find output file: {self.last_output_path}"
            )
            return

        # Check if file type is supported for manual redaction
        ext = Path(self.last_output_path).suffix.lower()
        if ext == '.txt':
            messagebox.showinfo(
                "Not Supported",
                "Manual redaction is not available for text files.\n\n"
                "Text files can be edited directly in any text editor."
            )
            return

        def on_manual_redaction_complete():
            """Callback when manual redactions are saved"""
            self._log("\n✓ Manual redactions applied successfully!")

        # Open preview window
        DocumentPreviewWindow(
            self.root,
            self.last_output_path,
            on_manual_redaction_complete
        )

    def run(self):
        """Start the application"""
        self.root.mainloop()


def main():
    app = PHIRedactorGUI()
    app.run()


if __name__ == '__main__':
    main()
