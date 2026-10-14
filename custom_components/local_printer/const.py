"""Constants for the Local Printer & Scanner integration."""

DOMAIN = "local_printer"

CONF_HOST = "host"
CONF_ESCL_URL = "escl_url"
CONF_IPP_URL = "ipp_url"
CONF_NAME = "name"

SCAN_DIR = "scans"
EVENT_SCAN_DONE = f"{DOMAIN}_scan_done"
EVENT_PRINT_DONE = f"{DOMAIN}_print_done"

UPDATE_INTERVAL = 30  # seconds

# Where eSCL and IPP usually live when the user only enters an IP (order = priority)
ESCL_CANDIDATES = [
    ("http", 8080, "/eSCL"),  # HP
    ("http", 80, "/eSCL"),  # Canon, Brother, Epson, Xerox...
    ("https", 443, "/eSCL"),  # Epson, newer HP
    ("http", 9095, "/eSCL"),  # Lexmark
]
IPP_CANDIDATES = [
    ("http", 631, "/ipp/print"),
    ("http", 631, "/ipp/printer"),
    ("http", 631, "/ipp"),
    ("https", 443, "/ipp/print"),
]

# Option keys are lowercase so they can be used as HA translation keys
SOURCES = ["platen", "adf", "adf_duplex"]
COLOR_MODES = {"color": "RGB24", "grayscale": "Grayscale8", "black_white": "BlackAndWhite1"}
FORMATS = {"pdf": "application/pdf", "jpg": "image/jpeg", "png": "image/png"}

# Sizes in 1/300 inch (eSCL), IPP media keywords and millimeters
PAPER_SIZES = {
    "a4": {"escl": (2480, 3508), "ipp": "iso_a4_210x297mm", "mm": (210, 297)},
    "letter": {"escl": (2550, 3300), "ipp": "na_letter_8.5x11in", "mm": (215.9, 279.4)},
    "a5": {"escl": (1748, 2480), "ipp": "iso_a5_148x210mm", "mm": (148, 210)},
    "legal": {"escl": (2550, 4200), "ipp": "na_legal_8.5x14in", "mm": (215.9, 355.6)},
}
SCAN_AREA_MAX = "max"

SIDES = ["one-sided", "two-sided-long-edge"]
PRINT_COLORS = ["monochrome", "color"]

DEFAULT_RESOLUTION = 300
PREFERRED_PRINT_DPI = 300
