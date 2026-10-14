# Local Printer & Scanner for Home Assistant

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=lazarvgd&repository=ha-local-printer&category=integration)
[![Add integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=local_printer)

Scan, print, copy and monitor network printers and multifunction devices **entirely on your local network** — no vendor app, no cloud account, no internet connection required.

The integration talks to the device using open standards that almost every network printer made in the last ~10 years supports:

| Feature | Protocol |
|---|---|
| Scanning (flatbed and document feeder, simplex and duplex) | **eSCL** (Apple AirScan / Mopria Scan) |
| Printer status, toner/ink levels, error reasons | **IPP** |
| Printing PDFs and images | **IPP** with native PDF, **PWG raster** (IPP Everywhere) or **Apple raster** (AirPrint) |
| Automatic discovery | **zeroconf / mDNS** (`_uscan`, `_uscans`, `_ipp`, `_ipps`) |

It was built for an HP LaserJet MFP M141w — a printer that refuses to scan through the HP Smart app without internet and does not accept PDF files at all — but nothing in it is HP-specific.

## Features

- **Buttons:** Scan, Copy (scan + print), Print last scan, Identify printer (beep/blink)
- **Sensors:** printer state with reasons, scanner state, document feeder state, one sensor per toner/ink cartridge, last scan, last print
- **Settings:** scan source, color mode, resolution, file format (PDF/JPG/PNG), scan area, paper size, duplex printing, color printing, number of copies — options are built from what your device actually reports
- **Services** for automations: `local_printer.scan`, `local_printer.print_file`, `local_printer.print_last_scan`, `local_printer.copy` (all return a response)
- **Events:** `local_printer_scan_done`, `local_printer_print_done`
- Scans are saved to **Media → scans**, so you can open and download them from the Home Assistant app
- Multi-page scans from a document feeder are merged into one PDF
- If the scanner cannot produce PDF, the integration builds the PDF from JPEG pages
- If the printer cannot print PDF, the integration renders it to PWG or Apple raster itself
- The device's IP address is kept up to date through zeroconf
- Paper size defaults to A4 or Letter based on the country configured in Home Assistant

## Installation

### HACS (recommended)

Click **Open in HACS** above, or:

1. HACS → ⋮ → **Custom repositories**
2. Repository: `https://github.com/lazarvgd/ha-local-printer`, type: **Integration**
3. Install **Local Printer & Scanner**, then restart Home Assistant

### Manual

Copy `custom_components/local_printer` into the `custom_components` folder of your Home Assistant configuration and restart.

## Setup

Click **Add integration** above, or open **Settings → Devices & services**. Supported printers are usually **discovered automatically** there — click **Add** on the discovered device.

To add a device manually: **Add integration → Local Printer & Scanner** and enter its IP address. The integration probes the usual eSCL and IPP locations (ports 80, 443, 631, 8080, 9095).

Give the printer a fixed IP address (DHCP reservation) on your router for the most reliable setup.

## Example automations

Notify your phone when a scan is ready:

```yaml
automation:
  - alias: "Scan finished"
    triggers:
      - trigger: event
        event_type: local_printer_scan_done
    actions:
      - action: notify.mobile_app_my_phone
        data:
          title: "Scan ready"
          message: "{{ trigger.event.data.filename }} ({{ trigger.event.data.size_kb }} KB)"
```

Scan with explicit settings and use the result:

```yaml
- action: local_printer.scan
  data:
    source: adf_duplex
    color: grayscale
    resolution: 300
    format: pdf
  response_variable: scan
- action: local_printer.print_file
  data:
    file_path: "{{ scan.path }}"
    copies: 2
```

Warn when toner is low:

```yaml
automation:
  - alias: "Toner low"
    triggers:
      - trigger: numeric_state
        entity_id: sensor.my_printer_black_cartridge
        below: 15
    actions:
      - action: notify.mobile_app_my_phone
        data:
          message: "Printer toner is below 15%"
```

`print_file` accepts paths inside the media folder and folders listed in [`allowlist_external_dirs`](https://www.home-assistant.io/integrations/homeassistant/#allowlist_external_dirs).

## Compatibility

Any network device that supports eSCL (scanning) and/or IPP (printing) should work. Scanner-only and printer-only devices are supported; only the matching entities are created.

Tested on:

| Device | Scan | Print | Notes |
|---|---|---|---|
| HP LaserJet MFP M141w | ✅ | ✅ | flatbed only, PWG raster (no PDF support) |

Please open an issue or pull request with your results for other models.

Known limitations:

- Duplex printing for printers without native PDF support relies on `pwg-raster-document-sheet-back` / URF `DM` handling and has not been verified on real hardware yet.
- Printers that only accept PCLm or vendor languages (PCL, PostScript) without PDF/PWG/URF are not supported.
- Only standard paper sizes (A4, A5, Letter, Legal) are offered.

## How it works

```
custom_components/local_printer/
├── discovery.py    find eSCL/IPP endpoints on a host
├── escl.py         eSCL client: capabilities, status, scan jobs
├── ipp.py          minimal IPP encoder/decoder: attributes, Print-Job, Identify-Printer
├── raster.py       PDF/image → PWG raster / Apple URF, PDF merging
├── planner.py      pure decision logic: which format to send, which scan settings to use
├── coordinator.py  polling + scan/print/copy jobs
└── config_flow.py, sensor.py, button.py, select.py, number.py   Home Assistant glue
```

`escl.py`, `ipp.py`, `raster.py`, `planner.py` and `discovery.py` do not depend on Home Assistant and are covered by unit tests.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/python -m pytest
```

## License

[MIT](LICENSE)
