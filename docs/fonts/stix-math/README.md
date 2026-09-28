# STIX Math font

`STIXTwoMath-Regular.ttf` is distributed by Google Fonts from the STIX Fonts
project under SIL Open Font License 1.1.

- Font family: `STIX Two Math`
- Font SHA-256: `562551b15b836e6e01d1b7350909baf3c8c8d83260c1190fbf4544333e6936de`
- Font source: `https://github.com/google/fonts/tree/main/ofl/stixtwomath`
- Upstream: `http://www.stixfonts.org/`

The complete OFL text is in `OFL.txt`.

Docker rendering uses the repository Fontconfig file:

```bash
FONTCONFIG_FILE=/work/docs/fonts/fonts.conf fc-cache -f
```

The delivery Docker workflow mounts the repository at `/work`; `fonts.conf`
uses that stable container path.

Only explicit `$...$` inline math spans and designated formula boxes use this font.
