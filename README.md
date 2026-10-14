# Local Printer & Scanner for Home Assistant

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=lazarvgd&repository=ha-local-printer&category=integration)
[![Add integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=local_printer)
[![Import the "Print a file" blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https://github.com/lazarvgd/ha-local-printer/blob/main/blueprints/script/local_printer/print_file.yaml)

Scan, print, copy and monitor network printers and multifunction devices **entirely on your local network** — no vendor app, no cloud account, no internet connection required.

The integration talks to the device using open standards that almost every network printer made in the last ~10 years supports:

| Feature | Protocol |
|---|---|
| Scanning (flatbed and document feeder, simplex and duplex) | **eSCL** (Apple AirScan / Mopria Scan) |
| Printer status, toner/ink levels, error reasons | **IPP** |
| Printing PDFs and images | **IPP** with native PDF, **PWG raster** (IPP Everywhere) or **Apple raster** (AirPrint) |
| Automatic discovery | **zeroconf / mDNS** (`_uscan`, `_uscans`, `_ipp`, `_ipps`) |

It was built for an HP LaserJet MFP M141w — a printer that refuses to scan through the HP Smart app without internet and does not accept PDF files at all — but nothing in it is HP-specific.

## Why this exists

I have a network multifunction printer at home. Printing over the network always worked, but **scanning did not**: the only way to scan was the vendor's mobile app, and that app only works through the vendor's cloud. When the internet connection at home was down — which happens often — scanning was simply impossible, even from a phone standing next to the printer.

The typical situation: I'm in another office, someone at home needs to send me a document. They can put the paper on the glass, but they can't start the scan — that needs the app, my account and a working cloud connection.

The printer, it turns out, speaks open, local protocols (eSCL for scanning, IPP for printing) all along. This integration uses them directly, so now anyone at home just puts the paper on the scanner and I press **Scan** in the Home Assistant app — from anywhere I can reach my Home Assistant — and the PDF is waiting for me. No vendor app, no vendor account, no cloud.

## Features

- **Buttons:** Scan, Copy (scan + print), Print last scan, Identify printer (beep/blink)
- **Last scan preview** image on the device page and in dashboards
- **Phone notification** when a scan is ready, with a link that opens the PDF — no YAML, pick your phone in the integration options
- **Scans** sensor listing stored scans with links, **Print history** sensor (jobs sent from Home Assistant, kept across restarts, plus the printer's own job list from any device)
- **Print any file from your phone or computer** with the bundled blueprint: tap it on a dashboard, pick a PDF or image, done
- **Sensors:** printer state with reasons, scanner state, document feeder state, one sensor per toner/ink cartridge, last scan, last print
- **Settings:** scan source, color mode, resolution, file format (PDF/JPG/PNG), scan area, paper size, duplex printing, color printing, number of copies — options are built from what your device actually reports
- **Services** for automations: `scan`, `copy`, `print_file`, `print_upload`, `print_last_scan`, `delete_scan` (all under `local_printer.`, most return a response)
- **Events:** `local_printer_scan_done`, `local_printer_print_done`
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

## Using it

### Where are my scans?

Scans are stored on the Home Assistant host in the media folder, sub-folder `scans` — on Home Assistant OS that is **`/media/scans`**, file names look like `scan_2026-10-14_12-05-31.pdf`.

To store scans somewhere else — for example on a **NAS or a shared folder on your computer** — first connect it under **Settings → System → Storage → Add network storage** (SMB/NFS, usage *Media*), then set **Folder for scans** under **Settings → Devices & services → Local Printer & Scanner → Configure** (for example `/media/nas/scans`). The folder is checked for write access when you save.

Home Assistant's own media browser only shows images, video and audio, so **PDF scans do not appear in Media → My media**. Open them instead:

- from the **phone notification** (see below) — tap it to open the PDF,
- from the **Scans** sensor or the dashboard card below — every file has a link,
- or pick **JPG** as scan format if you prefer to browse scans in the media browser.

Links are signed: they open in any browser without logging in again while they are valid (1 day on the dashboard, 7 days in notifications). Delete old scans with the `local_printer.delete_scan` action.

### Get a notification when a scan is ready

In the same **Configure** dialog pick your phone(s). From now on every scan sends "Scan ready — tap to open".

### Print a file from your phone or computer

1. Click **Import blueprint** at the top of this page and create a script from it (choose your printer).
2. Add the script to a dashboard. Tapping it opens a form with a **file picker** — choose a PDF or image, set copies, **Run**.

The same is available as the `local_printer.print_upload` action. Printing from the phone's own share sheet (AirPrint / Mopria) also keeps working as before — this is for printing through Home Assistant, e.g. when you are away.

### Dashboard card

Replace `my_printer` with your device's entity prefix (see the device page):

```yaml
type: vertical-stack
cards:
  - type: entities
    entities:
      - sensor.my_printer_printer
      - sensor.my_printer_scanner
      - sensor.my_printer_black_cartridge
  - type: grid
    columns: 3
    square: false
    cards:
      - type: button
        entity: button.my_printer_scan
        name: Scan
        tap_action:
          action: perform-action
          perform_action: button.press
          target:
            entity_id: button.my_printer_scan
      - type: button
        entity: button.my_printer_copy
        name: Copy
        tap_action:
          action: perform-action
          perform_action: button.press
          target:
            entity_id: button.my_printer_copy
      - type: button
        entity: script.print_a_file
        name: Print a file
        tap_action:
          action: more-info
  - type: picture-entity
    entity: image.my_printer_last_scan_preview
    show_state: false
  - type: markdown
    title: Scans
    content: >-
      {% for f in state_attr('sensor.my_printer_scans', 'files') or [] %}
      <a href="{{ f.url }}" target="_blank">{{ f.name }}</a> · {{ f.size_kb }} KB<br>
      {% else %}No scans yet.{% endfor %}
  - type: markdown
    title: Print history
    content: >-
      {% for j in (state_attr('sensor.my_printer_print_history', 'jobs') or [])[:10] %}
      {{ j.filename }} · {{ j.copies }}× · {{ as_timestamp(j.time) | timestamp_custom('%d.%m. %H:%M') }}<br>
      {% else %}Nothing printed from Home Assistant yet.{% endfor %}
```

## Example automations

Notify your phone when a scan is ready (if you prefer an automation over the built-in option):

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
          data:
            clickAction: "{{ trigger.event.data.url }}"
            url: "{{ trigger.event.data.url }}"
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

> **Tested only on an HP LaserJet MFP M141w so far.** The integration contains nothing HP-specific — it uses the vendor-neutral eSCL and IPP standards and adapts to what each device reports — so it **should work with other brands too** (Canon, Epson, Brother, Xerox, Kyocera, Ricoh, Lexmark, Samsung, ...). If your device is not listed below, please try it and report the result.

A good sign your device is supported: it can **scan to an iPhone/Mac without drivers (AirScan) or is "Mopria Scan" certified**, and it **prints via AirPrint or Mopria**. Scanner-only and printer-only devices are supported; only the matching entities are created.

Tested on:

| Device | Scan | Print | Notes |
|---|---|---|---|
| HP LaserJet MFP M141w | ✅ | ✅ | flatbed only, PWG raster (no PDF support) |

Please [open an issue](https://github.com/lazarvgd/ha-local-printer/issues) with your model and what worked (scan, print, feeder, duplex, toner levels) so it can be added to the table.

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
├── coordinator.py  polling, scan/print/copy jobs, scan list, print history, notifications
├── views.py        authenticated endpoint serving stored scans (/api/local_printer/scans/...)
└── config_flow.py, sensor.py, button.py, select.py, number.py, image.py   Home Assistant glue
```

`escl.py`, `ipp.py`, `raster.py`, `planner.py` and `discovery.py` do not depend on Home Assistant and are covered by unit tests.

## Development

```bash
# unit tests of the protocol/raster/planner modules
python3 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/python -m pytest

# end-to-end tests inside a real Home Assistant instance (fake printer)
python3 -m venv .venv-ha
.venv-ha/bin/pip install pytest-homeassistant-custom-component pypdfium2
.venv-ha/bin/python -m pytest -c tests_ha/pytest.ini --rootdir . tests_ha
```

## License

[MIT](LICENSE)
