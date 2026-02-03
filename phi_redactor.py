#!/usr/bin/env python3
"""
PHI Redactor - Fast, portable tool for redacting Protected Health Information
from clinical documents (PDF, PNG, JPG, etc.)

Designed for HIPAA compliance when passing clinical notes through LLMs.
Runs entirely locally - no data leaves your machine.

Author: Clinical AI Tools
License: MIT
"""

import re
import os
import sys
import argparse
import tempfile
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Set, Dict
from concurrent.futures import ThreadPoolExecutor
import json

# Image processing
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

# PDF processing
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.colors import black

# OCR
import pytesseract
from pdf2image import convert_from_path


@dataclass
class RedactionMatch:
    """Represents a detected PHI element"""
    text: str
    category: str
    start: int
    end: int
    confidence: float = 1.0


@dataclass
class OCRWord:
    """Represents a single word from OCR with its bounding box and character positions"""
    text: str
    bbox: Tuple[int, int, int, int]  # (x, y, w, h)
    char_start: int
    char_end: int
    block_num: int = 0
    par_num: int = 0
    line_num: int = 0


class PHIPatterns:
    """
    Compiled regex patterns for the 18 HIPAA identifiers plus common clinical PHI.
    Optimized for speed with pre-compiled patterns.
    """

    def __init__(self):
        self.patterns = self._compile_patterns()
        self.aggressive_patterns = self._compile_aggressive_patterns()
        self.common_names = self._load_common_names()
        self.aggressive_names = self._load_aggressive_names()

        # Clinical phrases that should NOT be treated as names
        self._non_name_phrases = {
            'internal medicine', 'physical therapy', 'occupational therapy',
            'family medicine', 'emergency medicine', 'general surgery',
            'orthopedic surgery', 'plastic surgery', 'cardiac surgery',
            'sports medicine', 'pain management', 'primary care',
            'urgent care', 'intensive care', 'critical care',
            'home health', 'public health', 'mental health',
            'physical examination', 'range of motion', 'blood pressure',
            'heart rate', 'respiratory rate', 'chief complaint',
            'history of present illness', 'review of systems',
            'assessment and plan', 'differential diagnosis',
            'follow up', 'no show', 'vital signs',
            'united states', 'new york', 'los angeles', 'san francisco',
            'las vegas', 'san diego', 'san antonio', 'el paso',
            'north carolina', 'south carolina', 'south dakota',
            'north dakota', 'west virginia', 'new jersey', 'new mexico',
            'new hampshire', 'rhode island',
            'referring clinic', 'referring provider', 'billing details',
            'patient demographics', 'clinical context', 'medical history',
            'diagnosis codes', 'requested procedures', 'insurance details',
            'intake coordinator', 'office staff',
        }

    def _compile_patterns(self) -> List[Tuple[re.Pattern, str, float]]:
        """Compile all PHI detection patterns. Returns (pattern, category, confidence)."""
        patterns = []

        # === HIPAA Safe Harbor 18 Identifiers ===

        # Reusable name fragment: matches "John", "KJ", "J.", "O'Brien"
        _nm = r"[A-Z][A-Za-z']*\.?"
        # Full name: one or more name fragments separated by spaces
        _full = _nm + r'(?:\s+' + _nm + r')*'
        # Words that should NOT be treated as part of a name (common verbs/actions after names)
        _stop_words = r'(?:Signed|Checked|Verified|Approved|Reviewed|Printed|Created|Updated|Entered|Submitted|Completed)'

        # 1. Names (titles + capitalized words pattern, stop at action verbs)
        patterns.append((
            re.compile(
                r'\b(?:Dr\.?|Mr\.?|Mrs\.?|Ms\.?|Miss|Prof\.?|Professor)\s+'
                + _nm + r'(?:\s+(?!' + _stop_words + r'\b)' + _nm + r')*\b'
            ),
            'NAME', 0.95
        ))

        # Patient/Provider/From/To/Name label patterns with colon
        patterns.append((
            re.compile(
                r'\b(?:Patient|Provider|Physician|Attending|Referring|From|To|Name)[\s:]+('
                + _full + r')\b'
            ),
            'NAME', 0.95
        ))

        # Legal name / preferred name / AKA (catches "(Legal: Katharine)" etc.)
        patterns.append((
            re.compile(
                r'\b(?:Legal|Legal\s+Name|Preferred|Preferred\s+Name|Birth\s+Name|'
                r'AKA|Also\s+Known\s+As|Maiden\s+Name|Former\s+Name)[\s:]+('
                + _full + r')\b',
                re.IGNORECASE
            ),
            'NAME', 0.95
        ))

        # Clinical role name labels (broader set)
        patterns.append((
            re.compile(
                r'\b(?:PCP|Referring\s+Physician|Surgeon|Therapist|Nurse\s+Practitioner|'
                r'Physician\s+Assistant|PA\-C|NP|Guarantor|Emergency\s+Contact|'
                r'Next\s+of\s+Kin|Guardian|Caregiver|Responsible\s+Party|'
                r'Primary\s+Care|Admitting\s+(?:Physician|Doctor)|'
                r'Consulting\s+(?:Physician|Doctor)|Ordering\s+(?:Physician|Provider))[\s:]+('
                + _full + r')\b'
            ),
            'NAME', 0.95
        ))

        # "signed by" / "reviewed by" etc.
        patterns.append((
            re.compile(
                r'(?:signed\s+by|authenticated\s+by|verified\s+by|cosigned\s+by|'
                r'reviewed\s+by|approved\s+by|dictated\s+by|transcribed\s+by)[\s:]+'
                + _full,
                re.IGNORECASE
            ),
            'NAME', 0.95
        ))

        # Name followed by medical credentials (signature lines)
        patterns.append((
            re.compile(
                r'\b(' + _full + r'),?\s+(?:MD|M\.D\.|DO|D\.O\.|DC|D\.C\.|'
                r'DPT|D\.P\.T\.|PT|OT|PA\-?C|APRN|NP|RN|BSN|MSN|DNP|'
                r'PhD|Ph\.D\.|PharmD|OD|DDS|DMD|LCSW|LMFT|LPC|PsyD|'
                r'FAAOS|FACP|FACS|FACEP)\b'
            ),
            'NAME', 0.9
        ))

        # 2. Geographic data (addresses)
        # Street address: handles "1650 NW 21st Avenue", "123 Main St", etc.
        patterns.append((
            re.compile(
                r'\b\d{1,5}\s+(?:(?:N|S|E|W|NE|NW|SE|SW|North|South|East|West)\s+)?'
                r'(?:(?:[A-Z][a-z]+|\d+(?:st|nd|rd|th))\s*)+(?:Street|St|Avenue|Ave|Boulevard|Blvd|Road|Rd|Drive|Dr|Lane|Ln|Way|Court|Ct|Circle|Cir|Place|Pl|Terrace|Ter|Parkway|Pkwy|Highway|Hwy)\b\.?',
                re.IGNORECASE
            ),
            'ADDRESS', 0.95
        ))

        # "Address:" label followed by content through end of line
        patterns.append((
            re.compile(
                r'\bAddress[\s:]+[^\n]+',
                re.IGNORECASE
            ),
            'ADDRESS', 0.95
        ))

        # City, State ZIP
        patterns.append((
            re.compile(
                r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*,?\s*(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY)\s*,?\s*\d{5}(?:-\d{4})?\b'
            ),
            'ADDRESS', 0.95
        ))

        # ZIP codes with context labels (normal mode)
        patterns.append((
            re.compile(
                r'\b(?:ZIP|Zip\s*Code|Postal\s*Code)[\s:#]*\d{5}(?:-\d{4})?\b',
                re.IGNORECASE
            ),
            'ZIP_CODE', 0.9
        ))

        # 3. Dates (all formats)
        # MM/DD/YYYY, MM-DD-YYYY, MM.DD.YYYY
        patterns.append((
            re.compile(
                r'\b(?:0?[1-9]|1[0-2])[/\-.](?:0?[1-9]|[12]\d|3[01])[/\-.](?:19|20)\d{2}\b'
            ),
            'DATE', 0.95
        ))

        # YYYY-MM-DD (ISO format)
        patterns.append((
            re.compile(
                r'\b(?:19|20)\d{2}[/\-.](?:0?[1-9]|1[0-2])[/\-.](?:0?[1-9]|[12]\d|3[01])\b'
            ),
            'DATE', 0.95
        ))

        # Written dates: January 15, 2024
        patterns.append((
            re.compile(
                r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December|'
                r'Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s*(?:19|20)\d{2}\b',
                re.IGNORECASE
            ),
            'DATE', 0.95
        ))

        # Date of birth patterns
        patterns.append((
            re.compile(
                r'\b(?:DOB|Date\s*of\s*Birth|Birth\s*Date|Birthdate)[\s:]*[\d/\-\.]+\b',
                re.IGNORECASE
            ),
            'DOB', 0.98
        ))

        # 4. Phone numbers
        patterns.append((
            re.compile(
                r'\b(?:\+?1[\s\-.]?)?\(?[2-9]\d{2}\)?[\s\-.]?[2-9]\d{2}[\s\-.]?\d{4}\b'
            ),
            'PHONE', 0.9
        ))

        # Phone with labels
        patterns.append((
            re.compile(
                r'\b(?:Phone|Tel|Telephone|Cell|Mobile|Fax|Ph)[\s:#]*[\(\d][\d\s\(\)\-\.]+\b',
                re.IGNORECASE
            ),
            'PHONE', 0.95
        ))

        # 5. Fax numbers (similar to phone but labeled)
        patterns.append((
            re.compile(
                r'\b(?:Fax|Facsimile)[\s:#]*[\(\d][\d\s\(\)\-\.]+\b',
                re.IGNORECASE
            ),
            'FAX', 0.95
        ))

        # 6. Email addresses
        patterns.append((
            re.compile(
                r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Z|a-z]{2,}\b'
            ),
            'EMAIL', 0.98
        ))

        # 7. Social Security Numbers
        patterns.append((
            re.compile(
                r'\b(?:SSN|Social\s*Security)[\s:#]*\d{3}[\s\-.]?\d{2}[\s\-.]?\d{4}\b',
                re.IGNORECASE
            ),
            'SSN', 0.99
        ))
        patterns.append((
            re.compile(r'\b\d{3}[\s\-]\d{2}[\s\-]\d{4}\b'),
            'SSN', 0.9
        ))

        # 8. Medical Record Numbers and Personal Health Numbers
        patterns.append((
            re.compile(
                r'\b(?:MRN|Medical\s*Record\s*(?:Number|No|#)?|Patient\s*ID|Pt\s*ID|Chart\s*(?:Number|No|#)|'
                r'Personal\s*Health\s*(?:Number|No|#)?|PHN|Health\s*(?:Card|ID)\s*(?:Number|No|#)?)[\s:#]*[A-Z0-9\-]*\b',
                re.IGNORECASE
            ),
            'MRN', 0.95
        ))

        # 9. Health plan beneficiary numbers
        patterns.append((
            re.compile(
                r'\b(?:Insurance\s*ID|Member\s*ID|Beneficiary\s*(?:ID|Number)|Policy\s*(?:Number|No|#)|Group\s*(?:Number|No|#))[\s:#]*[A-Z0-9\-]+\b',
                re.IGNORECASE
            ),
            'INSURANCE_ID', 0.95
        ))

        # 10. Account numbers
        patterns.append((
            re.compile(
                r'\b(?:Account\s*(?:Number|No|#)|Acct\s*(?:Number|No|#)?)[\s:#]*[A-Z0-9\-]+\b',
                re.IGNORECASE
            ),
            'ACCOUNT', 0.95
        ))

        # 11. Certificate/license numbers
        patterns.append((
            re.compile(
                r'\b(?:License\s*(?:Number|No|#)?|NPI|DEA\s*(?:Number|No|#)?)[\s:#]*[A-Z0-9\-]+\b',
                re.IGNORECASE
            ),
            'LICENSE', 0.95
        ))

        # 12. Vehicle identifiers (less common in clinical, but included)
        patterns.append((
            re.compile(
                r'\b(?:VIN|Vehicle\s*ID)[\s:#]*[A-HJ-NPR-Z0-9]{17}\b',
                re.IGNORECASE
            ),
            'VEHICLE', 0.95
        ))

        # 13. Device identifiers/serial numbers
        patterns.append((
            re.compile(
                r'\b(?:Device\s*ID|Serial\s*(?:Number|No|#)?|UDI)[\s:#]*[A-Z0-9\-]+\b',
                re.IGNORECASE
            ),
            'DEVICE', 0.95
        ))

        # 14. Web URLs
        patterns.append((
            re.compile(
                r'\bhttps?://[^\s<>"{}|\\^`\[\]]+\b'
            ),
            'URL', 0.98
        ))

        # 15. IP addresses
        patterns.append((
            re.compile(
                r'\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b'
            ),
            'IP_ADDRESS', 0.9
        ))

        # 16. Biometric identifiers (usually noted textually)
        patterns.append((
            re.compile(
                r'\b(?:fingerprint|retinal\s*scan|voice\s*print|facial\s*recognition)[\s:]+[^\n]+',
                re.IGNORECASE
            ),
            'BIOMETRIC', 0.9
        ))

        # 17. Full face photos (can't detect, but note references)
        patterns.append((
            re.compile(
                r'\b(?:photo\s*ID|photograph|image\s*of\s*patient)',
                re.IGNORECASE
            ),
            'PHOTO_REF', 0.9
        ))

        # 18. Other unique identifying numbers
        patterns.append((
            re.compile(
                r'\b(?:ID|Identification)[\s:#]+[A-Z0-9\-]{6,}\b',
                re.IGNORECASE
            ),
            'OTHER_ID', 0.85
        ))

        # === Additional Clinical PHI Patterns ===

        # Gender/Sex
        patterns.append((
            re.compile(
                r'\b(?:Sex|Gender)[\s:]+(?:Male|Female|M|F|Non[- ]?binary|Transgender|Trans|Other)\b',
                re.IGNORECASE
            ),
            'GENDER', 0.9
        ))

        # Pronouns (reveals gender identity)
        patterns.append((
            re.compile(
                r'\b(?:Pronouns?)[\s:]+[A-Za-z/]+(?:\s*/\s*[A-Za-z]+)*\b',
                re.IGNORECASE
            ),
            'PRONOUNS', 0.95
        ))

        # Gender-affirming care / transition-related medical history
        patterns.append((
            re.compile(
                r'\b[Gg]ender[\s\-]affirming\s+(?:care|surgery|procedure|treatment|hormone)[^.]*\.?',
                re.IGNORECASE
            ),
            'SENSITIVE_DX', 0.95
        ))
        patterns.append((
            re.compile(
                r'\b[Tt]op\s+surgery\b[^.]*\.?',
                re.IGNORECASE
            ),
            'SENSITIVE_DX', 0.95
        ))
        patterns.append((
            re.compile(
                r'\bongoing\s+(?:testosterone|estrogen|hormone)\s+therapy\b[^.]*\.?',
                re.IGNORECASE
            ),
            'SENSITIVE_DX', 0.95
        ))

        # Race/Ethnicity
        patterns.append((
            re.compile(
                r'\b(?:Race|Ethnicity)[\s:]+[A-Za-z /\-]+?(?=\s*(?:\n|,\s*[A-Z]|\b(?:Sex|Gender|DOB|Age|MRN|Patient|Date|Phone|Address|Insurance|SSN|Weight|Height|BMI|Allergies|Medications)\b))',
                re.IGNORECASE
            ),
            'RACE_ETHNICITY', 0.9
        ))

        # Tax Identification Number (TIN/EIN)
        patterns.append((
            re.compile(
                r'\b(?:(?:Referring\s+)?(?:Clinic|Provider|Practice)?\s*TIN|'
                r'Tax\s*(?:ID|Identification)|EIN|Employer\s*ID)[\s:#]*\d{2}[\s\-]?\d{7}\b',
                re.IGNORECASE
            ),
            'TIN', 0.95
        ))

        # Occupation (can be identifying)
        patterns.append((
            re.compile(
                r'\b(?:Occupation|Employer|Place\s+of\s+(?:Work|Employment))[\s:]+[^\n]+',
                re.IGNORECASE
            ),
            'OCCUPATION', 0.85
        ))

        # Primary Carrier / Insurance company name
        patterns.append((
            re.compile(
                r'\b(?:Primary\s+Carrier|Insurance\s+(?:Company|Provider|Carrier)|Health\s+Plan|Payer)[\s:]+[^\n]+',
                re.IGNORECASE
            ),
            'INSURANCE_ID', 0.9
        ))

        # Ages over 89 (HIPAA requires grouping as 90+)
        patterns.append((
            re.compile(
                r'\b(?:age[d]?|year[s]?\s*old)[\s:]*(?:9\d|1\d{2})\b',
                re.IGNORECASE
            ),
            'AGE_90PLUS', 0.95
        ))

        # Specific age mentions
        patterns.append((
            re.compile(
                r'\b\d{1,3}[\s\-]?(?:year|yr|y/?o|years?)[\s\-]?(?:old|of\s*age)?\b',
                re.IGNORECASE
            ),
            'AGE', 0.8
        ))

        # Room/bed numbers (can be identifying in small facilities)
        patterns.append((
            re.compile(
                r'\b(?:Room|Rm|Bed)[\s:#]*[A-Z]?\d+[A-Z]?\b',
                re.IGNORECASE
            ),
            'LOCATION', 0.8
        ))

        # Admission/discharge dates
        patterns.append((
            re.compile(
                r'\b(?:Admission|Admit|Discharge|DOS|Date\s*of\s*Service)[\s:]+[\d/\-\.]+\b',
                re.IGNORECASE
            ),
            'SERVICE_DATE', 0.95
        ))

        return patterns

    def _compile_aggressive_patterns(self) -> List[Tuple[re.Pattern, str, float]]:
        """Compile additional patterns used only in aggressive mode."""
        patterns = []

        # Standalone ZIP codes (high false positive rate in normal mode)
        patterns.append((
            re.compile(r'\b\d{5}(?:-\d{4})?\b'),
            'ZIP_CODE', 0.5
        ))

        # Standalone Male/Female without label
        patterns.append((
            re.compile(r'\b(?:Male|Female)\b', re.IGNORECASE),
            'GENDER', 0.5
        ))

        # Broader date patterns (2-digit year)
        patterns.append((
            re.compile(
                r'\b(?:0?[1-9]|1[0-2])[/\-.](?:0?[1-9]|[12]\d|3[01])[/\-.]\d{2}\b'
            ),
            'DATE', 0.5
        ))

        # Consecutive capitalized word pairs (potential names)
        patterns.append((
            re.compile(r'\b([A-Z][a-z]{2,})\s+([A-Z][a-z]{2,})\b'),
            'POSSIBLE_NAME', 0.5
        ))

        return patterns

    def _load_common_names(self) -> Set[str]:
        """Load common first and last names for enhanced detection (~200 entries)"""
        first_names = {
            'james', 'john', 'robert', 'michael', 'william', 'david', 'richard',
            'joseph', 'thomas', 'charles', 'christopher', 'daniel', 'matthew',
            'anthony', 'mark', 'donald', 'steven', 'paul', 'andrew', 'joshua',
            'kenneth', 'kevin', 'brian', 'george', 'timothy', 'ronald', 'edward',
            'jason', 'jeffrey', 'ryan', 'jacob', 'nicholas', 'gary', 'eric',
            'jonathan', 'stephen', 'larry', 'justin', 'scott', 'brandon',
            'benjamin', 'samuel', 'raymond', 'gregory', 'frank', 'alexander',
            'patrick', 'jack', 'dennis', 'jerry', 'tyler', 'aaron', 'jose',
            'adam', 'nathan', 'henry', 'peter', 'zachary', 'douglas', 'harold',
            'mary', 'patricia', 'jennifer', 'linda', 'elizabeth', 'barbara',
            'susan', 'jessica', 'sarah', 'karen', 'nancy', 'margaret', 'lisa',
            'betty', 'dorothy', 'sandra', 'ashley', 'kimberly', 'emily', 'donna',
            'michelle', 'carol', 'amanda', 'melissa', 'deborah', 'stephanie',
            'rebecca', 'sharon', 'laura', 'cynthia', 'kathleen', 'amy', 'angela',
            'shirley', 'anna', 'brenda', 'pamela', 'emma', 'nicole', 'helen',
            'samantha', 'katherine', 'christine', 'debra', 'rachel', 'carolyn',
            'janet', 'catherine', 'maria', 'heather', 'diane', 'ruth', 'julie',
            'olivia', 'joyce', 'virginia', 'victoria', 'kelly', 'lauren', 'christina',
            'joan', 'evelyn', 'judith', 'megan', 'andrea', 'cheryl', 'hannah',
            'jacqueline', 'martha', 'gloria', 'teresa', 'ann', 'sara', 'madison',
            'frances', 'kathryn', 'janice', 'jean', 'abigail', 'alice', 'judy',
        }
        last_names = {
            'smith', 'johnson', 'williams', 'brown', 'jones', 'garcia', 'miller',
            'davis', 'rodriguez', 'martinez', 'hernandez', 'lopez', 'gonzalez',
            'wilson', 'anderson', 'thomas', 'taylor', 'moore', 'jackson', 'martin',
            'lee', 'perez', 'thompson', 'white', 'harris', 'sanchez', 'clark',
            'ramirez', 'lewis', 'robinson', 'walker', 'young', 'allen', 'king',
            'wright', 'scott', 'torres', 'nguyen', 'hill', 'flores', 'green',
            'adams', 'nelson', 'baker', 'hall', 'rivera', 'campbell', 'mitchell',
            'carter', 'roberts', 'gomez', 'phillips', 'evans', 'turner', 'diaz',
            'parker', 'cruz', 'edwards', 'collins', 'reyes', 'stewart', 'morris',
            'morales', 'murphy', 'cook', 'rogers', 'gutierrez', 'ortiz', 'morgan',
            'cooper', 'peterson', 'bailey', 'reed', 'kelly', 'howard', 'ramos',
            'kim', 'cox', 'ward', 'richardson', 'watson', 'brooks', 'chavez',
            'wood', 'james', 'bennett', 'gray', 'mendoza', 'ruiz', 'hughes',
            'price', 'alvarez', 'castillo', 'sanders', 'patel', 'myers', 'long',
            'ross', 'foster', 'jimenez', 'powell', 'jenkins', 'perry', 'russell',
            'sullivan', 'bell', 'coleman', 'butler', 'henderson', 'barnes',
            'gonzales', 'fisher', 'vasquez', 'simmons', 'griffin', 'mcdonald',
        }
        return first_names | last_names

    def _load_aggressive_names(self) -> Set[str]:
        """Names that are also common English words -- only used in aggressive mode."""
        return {
            'may', 'grace', 'bill', 'art', 'mark', 'frank', 'chase', 'heath',
            'hunter', 'mason', 'reed', 'wade', 'lane', 'grant', 'cole',
            'drew', 'dale', 'glen', 'joy', 'hope', 'faith', 'dawn', 'eve',
            'iris', 'ivy', 'lily', 'rose', 'ruby', 'pearl', 'summer',
            'autumn', 'april', 'august', 'cruz', 'bishop', 'chance',
            'cash', 'sterling', 'stone', 'fox', 'wolf', 'hawk',
            'angel', 'christian', 'trinity', 'destiny', 'harmony',
            'melody', 'charity', 'mercy', 'patience', 'serenity',
            'page', 'clay', 'ray', 'pat', 'terry', 'robin', 'sandy',
            'sherry', 'jean', 'will', 'bob', 'don', 'gene', 'rick',
        }

    def _has_name_context(self, text: str, position: int) -> bool:
        """Check if a standalone name appears near a clinical label (within 80 chars)."""
        window_start = max(0, position - 80)
        window_end = min(len(text), position + 80)
        window = text[window_start:window_end].lower()

        context_labels = [
            'patient', 'provider', 'physician', 'attending', 'referring',
            'dr.', 'dr ', 'mr.', 'mr ', 'mrs.', 'mrs ', 'ms.', 'ms ',
            'name:', 'name ', 'signed by', 'authenticated by',
            'pcp', 'surgeon', 'therapist', 'nurse', 'guarantor',
            'emergency contact', 'next of kin', 'guardian', 'caregiver',
            'dictated by', 'reviewed by', 'approved by', 'cosigned by',
        ]

        return any(label in window for label in context_labels)

    def find_phi(self, text: str, aggressive: bool = False,
                 known_names: Optional[Set[str]] = None) -> List[RedactionMatch]:
        """
        Find all PHI in text using compiled patterns.

        Args:
            text: The text to scan for PHI
            aggressive: If True, use lower confidence threshold and additional patterns
            known_names: Pre-discovered name tokens (e.g. from earlier pages) to
                         also redact on this pass
        """
        matches = []
        seen_spans = set()
        confidence_threshold = 0.5 if aggressive else 0.7

        def _is_non_name_phrase(match_text: str) -> bool:
            """Check if the matched text is a known clinical/geographic phrase."""
            lower = match_text.lower()
            return any(phrase in lower for phrase in self._non_name_phrases)

        def _add_match(start, end, text_val, category, confidence):
            span = (start, end)
            if confidence < confidence_threshold:
                return
            if not any(s[0] <= span[0] < s[1] or s[0] < span[1] <= s[1]
                       for s in seen_spans):
                matches.append(RedactionMatch(
                    text=text_val,
                    category=category,
                    start=start,
                    end=end,
                    confidence=confidence,
                ))
                seen_spans.add(span)

        # Run normal patterns
        for pattern, category, confidence in self.patterns:
            for match in pattern.finditer(text):
                match_text = match.group()
                if category in ('NAME', 'POSSIBLE_NAME') and _is_non_name_phrase(match_text):
                    continue
                _add_match(match.start(), match.end(), match_text, category, confidence)

        # Run aggressive patterns if enabled
        if aggressive:
            for pattern, category, confidence in self.aggressive_patterns:
                for match in pattern.finditer(text):
                    match_text = match.group()
                    if category == 'POSSIBLE_NAME' and _is_non_name_phrase(match_text):
                        continue
                    _add_match(match.start(), match.end(), match_text, category, confidence)

        # Check for standalone names from common names list
        name_set = self.common_names | (self.aggressive_names if aggressive else set())
        words = re.finditer(r'\b([A-Z][a-z]+)\b', text)
        for word in words:
            if word.group(1).lower() in self.aggressive_names and not aggressive:
                # Ambiguous word-names need context in normal mode
                if self._has_name_context(text, word.start()):
                    _add_match(word.start(), word.end(), word.group(),
                               'POSSIBLE_NAME', 0.7)
            elif word.group(1).lower() in self.common_names:
                # Non-ambiguous common names
                conf = 0.6 if aggressive else 0.7
                _add_match(word.start(), word.end(), word.group(),
                           'POSSIBLE_NAME', conf)

        # Name propagation: names discovered by labeled patterns (e.g.
        # "Patient: KJ Burmaster") should also be redacted when they appear
        # bare elsewhere in the document (e.g. "KJ is approximately...").
        _label_words = {
            'dr', 'mr', 'mrs', 'ms', 'miss', 'prof', 'professor',
            'patient', 'provider', 'physician', 'attending', 'referring',
            'from', 'to', 'legal', 'preferred', 'birth', 'name', 'aka',
            'also', 'known', 'as', 'maiden', 'former', 'pcp', 'surgeon',
            'therapist', 'nurse', 'practitioner', 'assistant', 'guarantor',
            'emergency', 'contact', 'next', 'of', 'kin', 'guardian',
            'caregiver', 'responsible', 'party', 'admitting', 'doctor',
            'consulting', 'ordering', 'primary', 'care', 'signed',
            'authenticated', 'verified', 'cosigned', 'reviewed', 'approved',
            'dictated', 'transcribed', 'by',
            'md', 'do', 'dc', 'dpt', 'pt', 'ot', 'np', 'rn', 'bsn',
            'msn', 'dnp', 'phd', 'pharmd', 'od', 'dds', 'dmd', 'lcsw',
            'lmft', 'lpc', 'psyd', 'aprn', 'faaos', 'facp', 'facs', 'facep',
        }
        discovered_tokens = set()
        for m in matches:
            if m.category == 'NAME':
                for tok in re.findall(r"[A-Z][A-Za-z']+", m.text):
                    if tok.lower() not in _label_words and len(tok) >= 2:
                        discovered_tokens.add(tok)

        # Merge in any pre-discovered names from earlier pages / passes
        if known_names:
            discovered_tokens |= known_names

        for token in discovered_tokens:
            for hit in re.finditer(r'\b' + re.escape(token) + r'\b', text):
                _add_match(hit.start(), hit.end(), hit.group(), 'NAME', 0.85)

        return sorted(matches, key=lambda m: m.start)

    def extract_name_tokens(self, matches: List[RedactionMatch]) -> Set[str]:
        """Extract name tokens from a set of matches for cross-page propagation."""
        _label_words = {
            'dr', 'mr', 'mrs', 'ms', 'miss', 'prof', 'professor',
            'patient', 'provider', 'physician', 'attending', 'referring',
            'from', 'to', 'legal', 'preferred', 'birth', 'name', 'aka',
            'also', 'known', 'as', 'maiden', 'former', 'pcp', 'surgeon',
            'therapist', 'nurse', 'practitioner', 'assistant', 'guarantor',
            'emergency', 'contact', 'next', 'of', 'kin', 'guardian',
            'caregiver', 'responsible', 'party', 'admitting', 'doctor',
            'consulting', 'ordering', 'primary', 'care', 'signed',
            'authenticated', 'verified', 'cosigned', 'reviewed', 'approved',
            'dictated', 'transcribed', 'by',
            'md', 'do', 'dc', 'dpt', 'pt', 'ot', 'np', 'rn', 'bsn',
            'msn', 'dnp', 'phd', 'pharmd', 'od', 'dds', 'dmd', 'lcsw',
            'lmft', 'lpc', 'psyd', 'aprn', 'faaos', 'facp', 'facs', 'facep',
        }
        tokens = set()
        for m in matches:
            if m.category == 'NAME':
                for tok in re.findall(r"[A-Z][A-Za-z']+", m.text):
                    if tok.lower() not in _label_words and len(tok) >= 2:
                        tokens.add(tok)
        return tokens


class ImageRedactor:
    """Handle image-based redaction with OCR"""

    def __init__(self, phi_patterns: PHIPatterns, aggressive: bool = False):
        self.phi_patterns = phi_patterns
        self.aggressive = aggressive
        self.redaction_color = (0, 0, 0)  # Black boxes
        self.bbox_padding = 2  # pixels of padding around redaction boxes

    def _preprocess_for_ocr(self, img: Image.Image) -> Tuple[Image.Image, float]:
        """
        Preprocess image for better OCR accuracy.
        Returns (processed_image, scale_factor) where scale_factor maps
        processed coordinates back to original coordinates.
        """
        scale_factor = 1.0

        # Upscale small images
        min_dim = min(img.width, img.height)
        if min_dim < 1000:
            scale_factor = 1000.0 / min_dim
            new_w = int(img.width * scale_factor)
            new_h = int(img.height * scale_factor)
            img = img.resize((new_w, new_h), Image.LANCZOS)

        # Convert to grayscale
        gray = img.convert('L')

        # Enhance contrast (+50%)
        enhancer = ImageEnhance.Contrast(gray)
        gray = enhancer.enhance(1.5)

        # Sharpen (+30%)
        enhancer = ImageEnhance.Sharpness(gray)
        gray = enhancer.enhance(1.3)

        # Adaptive binary thresholding for very flat/low-contrast scans
        # Check if the image has low contrast by examining histogram spread
        hist = gray.histogram()
        total_pixels = sum(hist)
        # Find the 5th and 95th percentile brightness values
        cumsum = 0
        p5, p95 = None, None
        for i, count in enumerate(hist):
            cumsum += count
            if p5 is None and cumsum >= total_pixels * 0.05:
                p5 = i
            if p95 is None and cumsum >= total_pixels * 0.95:
                p95 = i
                break
        p5 = p5 if p5 is not None else 0
        p95 = p95 if p95 is not None else 255

        # Only threshold truly flat/low-contrast scans (e.g. faded faxes).
        # Normal documents with good text/background separation should be
        # left alone — Tesseract handles them well as-is.
        if (p95 - p5) < 40 and p95 < 200:
            threshold = (p5 + p95) // 2
            gray = gray.point(lambda x: 255 if x > threshold else 0)

        return gray, scale_factor

    def _single_pass_ocr(self, img: Image.Image) -> Tuple[str, List[OCRWord]]:
        """
        Perform OCR once, returning both the reconstructed full text
        and a list of OCRWord objects mapping character positions to bounding boxes.
        """
        ocr_data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)

        word_list: List[OCRWord] = []
        full_text_parts: List[str] = []
        char_pos = 0

        prev_block = -1
        prev_par = -1
        prev_line = -1

        n_words = len(ocr_data['text'])
        for i in range(n_words):
            word_text = ocr_data['text'][i]
            conf = int(ocr_data['conf'][i])
            block_num = ocr_data['block_num'][i]
            par_num = ocr_data['par_num'][i]
            line_num = ocr_data['line_num'][i]

            # Skip low-confidence empty entries
            if conf == -1 or not word_text.strip():
                # Track structural boundaries even for empty entries
                if block_num != prev_block and prev_block != -1:
                    full_text_parts.append('\n\n')
                    char_pos += 2
                elif par_num != prev_par and prev_par != -1:
                    full_text_parts.append('\n')
                    char_pos += 1
                elif line_num != prev_line and prev_line != -1:
                    full_text_parts.append('\n')
                    char_pos += 1
                prev_block = block_num
                prev_par = par_num
                prev_line = line_num
                continue

            # Insert separators for structural boundaries
            if prev_block != -1 and block_num != prev_block:
                full_text_parts.append('\n\n')
                char_pos += 2
            elif prev_par != -1 and par_num != prev_par:
                full_text_parts.append('\n')
                char_pos += 1
            elif prev_line != -1 and line_num != prev_line:
                full_text_parts.append('\n')
                char_pos += 1
            elif word_list:
                # Space between words on the same line
                full_text_parts.append(' ')
                char_pos += 1

            word_text_stripped = word_text.strip()
            char_start = char_pos
            char_end = char_pos + len(word_text_stripped)

            word_list.append(OCRWord(
                text=word_text_stripped,
                bbox=(
                    ocr_data['left'][i],
                    ocr_data['top'][i],
                    ocr_data['width'][i],
                    ocr_data['height'][i],
                ),
                char_start=char_start,
                char_end=char_end,
                block_num=block_num,
                par_num=par_num,
                line_num=line_num,
            ))

            full_text_parts.append(word_text_stripped)
            char_pos = char_end

            prev_block = block_num
            prev_par = par_num
            prev_line = line_num

        full_text = ''.join(full_text_parts)
        return full_text, word_list

    def _map_matches_to_boxes(self, matches: List[RedactionMatch],
                              word_list: List[OCRWord]) -> List[Tuple[RedactionMatch, List[Tuple[int, int, int, int]]]]:
        """
        For each PHI match span [start, end], find overlapping OCRWord entries
        by character position and return their bounding boxes.
        """
        results = []
        for match in matches:
            boxes = []
            for word in word_list:
                # Check if this word overlaps with the match span
                if word.char_end > match.start and word.char_start < match.end:
                    boxes.append(word.bbox)
            if boxes:
                results.append((match, boxes))
        return results

    def redact_image(self, image_path: str, output_path: str,
                     return_text: bool = False,
                     known_names: Optional[Set[str]] = None) -> Tuple[str, List[RedactionMatch]]:
        """
        Redact PHI from an image file.
        Returns the extracted text and list of redactions made.
        """
        # Load image
        img = Image.open(image_path)

        # Convert to RGB if needed
        if img.mode != 'RGB':
            img = img.convert('RGB')

        # Preprocess for OCR
        processed_img, scale_factor = self._preprocess_for_ocr(img)

        # Single-pass OCR on preprocessed image
        full_text, word_list = self._single_pass_ocr(processed_img)

        # Find PHI in text
        phi_matches = self.phi_patterns.find_phi(
            full_text, aggressive=self.aggressive, known_names=known_names
        )

        # Map matches to bounding boxes
        match_boxes = self._map_matches_to_boxes(phi_matches, word_list)

        # Draw redaction boxes on the ORIGINAL image
        draw = ImageDraw.Draw(img)
        redactions_made = []
        pad = self.bbox_padding

        for phi_match, boxes in match_boxes:
            for (x, y, w, h) in boxes:
                if w > 0 and h > 0:
                    # Scale coordinates back to original image space
                    ox = int(x / scale_factor) - pad
                    oy = int(y / scale_factor) - pad
                    ox2 = int((x + w) / scale_factor) + pad
                    oy2 = int((y + h) / scale_factor) + pad
                    # Clamp to image bounds
                    ox = max(0, ox)
                    oy = max(0, oy)
                    ox2 = min(img.width, ox2)
                    oy2 = min(img.height, oy2)
                    draw.rectangle([ox, oy, ox2, oy2], fill=self.redaction_color)
            redactions_made.append(phi_match)

        # Save redacted image
        img.save(output_path, quality=95)

        # Create redacted text version
        redacted_text = self._redact_text(full_text, phi_matches)

        if return_text:
            return redacted_text, redactions_made
        return output_path, redactions_made

    def _redact_text(self, text: str, matches: List[RedactionMatch]) -> str:
        """Replace PHI in text with category labels"""
        sorted_matches = sorted(matches, key=lambda m: m.start, reverse=True)

        result = text
        for match in sorted_matches:
            replacement = f"[{match.category}]"
            result = result[:match.start] + replacement + result[match.end:]

        return result


class PDFRedactor:
    """Handle PDF redaction"""

    def __init__(self, phi_patterns: PHIPatterns, aggressive: bool = False):
        self.phi_patterns = phi_patterns
        self.aggressive = aggressive
        self.image_redactor = ImageRedactor(phi_patterns, aggressive=aggressive)

    def redact_pdf(self, pdf_path: str, output_path: str,
                   dpi: int = 300) -> Tuple[str, List[RedactionMatch]]:
        """
        Redact PHI from a PDF file.
        Two-pass approach:
          1. OCR all pages and discover names
          2. Redact all pages with cross-page name propagation
        """
        all_redactions = []
        all_text = []

        # Convert PDF pages to images
        images = convert_from_path(pdf_path, dpi=dpi)

        with tempfile.TemporaryDirectory() as temp_dir:
            # Save all page images
            page_paths = []
            for i, page_img in enumerate(images):
                temp_path = os.path.join(temp_dir, f'page_{i}.png')
                page_img.save(temp_path, 'PNG')
                page_paths.append(temp_path)

            # Pass 1: OCR all pages, collect name tokens
            all_name_tokens: Set[str] = set()
            for page_path in page_paths:
                img = Image.open(page_path).convert('RGB')
                processed, _ = self.image_redactor._preprocess_for_ocr(img)
                text, _ = self.image_redactor._single_pass_ocr(processed)
                matches = self.phi_patterns.find_phi(
                    text, aggressive=self.aggressive
                )
                all_name_tokens |= self.phi_patterns.extract_name_tokens(matches)

            # Pass 2: Redact with cross-page name propagation
            redacted_images = []
            for i, page_path in enumerate(page_paths):
                redacted_path = os.path.join(temp_dir, f'redacted_{i}.png')

                text, redactions = self.image_redactor.redact_image(
                    page_path, redacted_path, return_text=True,
                    known_names=all_name_tokens
                )

                all_text.append(f"--- Page {i+1} ---\n{text}")
                all_redactions.extend(redactions)
                redacted_images.append(Image.open(redacted_path))

            if redacted_images:
                redacted_images[0].save(
                    output_path,
                    save_all=True,
                    append_images=redacted_images[1:] if len(redacted_images) > 1 else [],
                    resolution=dpi
                )

        return '\n\n'.join(all_text), all_redactions


class TextRedactor:
    """Handle plain text redaction"""

    def __init__(self, phi_patterns: PHIPatterns, aggressive: bool = False):
        self.phi_patterns = phi_patterns
        self.aggressive = aggressive

    def redact_text(self, text: str) -> Tuple[str, List[RedactionMatch]]:
        """Redact PHI from plain text"""
        matches = self.phi_patterns.find_phi(text, aggressive=self.aggressive)

        sorted_matches = sorted(matches, key=lambda m: m.start, reverse=True)

        result = text
        for match in sorted_matches:
            replacement = f"[{match.category}]"
            result = result[:match.start] + replacement + result[match.end:]

        return result, matches


class PHIRedactor:
    """Main redaction orchestrator"""

    SUPPORTED_EXTENSIONS = {
        '.pdf': 'pdf',
        '.png': 'image',
        '.jpg': 'image',
        '.jpeg': 'image',
        '.tiff': 'image',
        '.tif': 'image',
        '.bmp': 'image',
        '.gif': 'image',
        '.txt': 'text',
        '.text': 'text',
    }

    def __init__(self, aggressive: bool = False):
        """
        Initialize redactor.

        Args:
            aggressive: If True, redact more aggressively (lower confidence threshold)
        """
        self.phi_patterns = PHIPatterns()
        self.aggressive = aggressive
        self.image_redactor = ImageRedactor(self.phi_patterns, aggressive=aggressive)
        self.pdf_redactor = PDFRedactor(self.phi_patterns, aggressive=aggressive)
        self.text_redactor = TextRedactor(self.phi_patterns, aggressive=aggressive)

    def redact_file(self, input_path: str, output_path: Optional[str] = None,
                    output_text: bool = True) -> dict:
        """
        Redact PHI from a file.

        Args:
            input_path: Path to input file
            output_path: Path for redacted output (auto-generated if not provided)
            output_text: If True, also save extracted/redacted text

        Returns:
            Dictionary with results including paths and redaction summary
        """
        input_path = Path(input_path)

        if not input_path.exists():
            raise FileNotFoundError(f"Input file not found: {input_path}")

        ext = input_path.suffix.lower()
        if ext not in self.SUPPORTED_EXTENSIONS:
            raise ValueError(f"Unsupported file type: {ext}")

        file_type = self.SUPPORTED_EXTENSIONS[ext]

        if output_path is None:
            output_path = input_path.parent / f"{input_path.stem}_redacted{ext}"
        output_path = Path(output_path)

        if file_type == 'pdf':
            redacted_text, redactions = self.pdf_redactor.redact_pdf(
                str(input_path), str(output_path)
            )
        elif file_type == 'image':
            redacted_text, redactions = self.image_redactor.redact_image(
                str(input_path), str(output_path), return_text=True
            )
        else:  # text
            with open(input_path, 'r', encoding='utf-8', errors='ignore') as f:
                text = f.read()
            redacted_text, redactions = self.text_redactor.redact_text(text)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(redacted_text)

        text_output_path = None
        if output_text and redacted_text:
            text_output_path = output_path.parent / f"{output_path.stem}_text.txt"
            with open(text_output_path, 'w', encoding='utf-8') as f:
                f.write(redacted_text)

        categories = {}
        for r in redactions:
            categories[r.category] = categories.get(r.category, 0) + 1

        return {
            'input_file': str(input_path),
            'output_file': str(output_path),
            'text_output': str(text_output_path) if text_output_path else None,
            'redactions_count': len(redactions),
            'categories': categories,
            'redacted_text': redacted_text
        }


def main():
    """Command-line interface"""
    parser = argparse.ArgumentParser(
        description='PHI Redactor - Redact Protected Health Information from clinical documents',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s document.pdf
  %(prog)s scan.png -o redacted_scan.png
  %(prog)s notes.txt --no-text-output
  %(prog)s clinical_note.pdf -a  # Aggressive mode

Supported formats: PDF, PNG, JPG, JPEG, TIFF, BMP, GIF, TXT

This tool runs entirely locally - no data leaves your machine.
        """
    )

    parser.add_argument('input', help='Input file to redact')
    parser.add_argument('-o', '--output', help='Output file path (auto-generated if not provided)')
    parser.add_argument('-a', '--aggressive', action='store_true',
                       help='Aggressive mode - redact more liberally')
    parser.add_argument('--no-text-output', action='store_true',
                       help='Do not save extracted text file')
    parser.add_argument('-q', '--quiet', action='store_true',
                       help='Quiet mode - minimal output')
    parser.add_argument('--json', action='store_true',
                       help='Output results as JSON')

    args = parser.parse_args()

    try:
        redactor = PHIRedactor(aggressive=args.aggressive)

        if not args.quiet:
            print(f"Processing: {args.input}")
            if args.aggressive:
                print("Mode: Aggressive (lower confidence threshold)")

        result = redactor.redact_file(
            args.input,
            args.output,
            output_text=not args.no_text_output
        )

        if args.json:
            result_json = {k: v for k, v in result.items() if k != 'redacted_text'}
            print(json.dumps(result_json, indent=2))
        elif not args.quiet:
            print(f"\n✓ Redaction complete!")
            print(f"  Output: {result['output_file']}")
            if result['text_output']:
                print(f"  Text:   {result['text_output']}")
            print(f"  Redactions: {result['redactions_count']}")
            if result['categories']:
                print(f"  Categories:")
                for cat, count in sorted(result['categories'].items()):
                    print(f"    - {cat}: {count}")

        return 0

    except Exception as e:
        if args.json:
            print(json.dumps({'error': str(e)}))
        else:
            print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
