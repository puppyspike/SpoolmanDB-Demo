#!/usr/bin/env python3
"""
Wandelt ein ausgefuelltes SpoolmanDB-Issue-Formular in einen Filament-Eintrag um,
validiert ihn gegen filaments.schema.json und fuegt ihn in die richtige
Hersteller-Datei unter filaments/ ein.

Aufruf:
    python scripts/issue_to_filament.py issue.json [--dry-run]

issue.json ist die flache Key->Value-Map, die github-issue-parser ausgibt
(Keys = die `id`s aus dem Issue-Formular).

Exit-Code 0 = ok, 1 = Validierungsfehler (Meldung auf stdout, dient als Kommentar).
"""

import json
import re
import sys
from pathlib import Path

import jsonschema

REPO = Path(__file__).resolve().parent.parent
FIL_DIR = REPO / "filaments"
SCHEMA = REPO / "filaments.schema.json"

NONE_SENTINELS = {"", "-", "(nicht angegeben)", "(none)", "none", "_No response_"}


class UserError(Exception):
    """Fehler, der dem Nutzer als Kommentar angezeigt wird."""


def clean(v):
    if v is None:
        return None
    v = str(v).strip()
    return None if v in NONE_SENTINELS else v


def to_number(s, feld):
    s = clean(s)
    if s is None:
        return None
    s = s.replace(",", ".") if s.count(",") == 1 and "." not in s else s
    try:
        f = float(s)
        return int(f) if f.is_integer() else f
    except ValueError:
        raise UserError(f"Feld **{feld}**: '{s}' ist keine gueltige Zahl.")


def to_int(s, feld):
    n = to_number(s, feld)
    if n is None:
        return None
    if isinstance(n, float) and not n.is_integer():
        raise UserError(f"Feld **{feld}**: '{n}' muss eine ganze Zahl sein.")
    return int(n)


def parse_int_range(s, feld):
    s = clean(s)
    if s is None:
        return None
    parts = [p for p in re.split(r"[,\s/-]+", s) if p]
    if len(parts) != 2:
        raise UserError(f"Feld **{feld}**: bitte genau zwei Werte angeben, z. B. `240, 260`.")
    return [to_int(parts[0], feld), to_int(parts[1], feld)]


def parse_diameters(s):
    s = clean(s)
    if s is None:
        raise UserError("Feld **diameters**: mindestens ein Durchmesser noetig, z. B. `1.75`.")
    out = []
    for tok in re.split(r"[,\s]+", s):
        tok = tok.strip()
        if not tok:
            continue
        out.append(to_number(tok, "diameters"))
    if not out:
        raise UserError("Feld **diameters**: mindestens ein Durchmesser noetig.")
    # uniqueItems
    return list(dict.fromkeys(out))


def parse_weights(s):
    """Eine Zeile pro Gewicht: `weight, spool_weight, spool_type`
    spool_weight + spool_type optional. Beispiel: `1000, 193, plastic`"""
    s = clean(s)
    if s is None:
        raise UserError("Feld **weights**: mindestens eine Zeile noetig, z. B. `1000, 193, plastic`.")
    weights = []
    for raw in s.splitlines():
        raw = raw.strip().lstrip("-*").strip()
        if not raw:
            continue
        parts = [p.strip() for p in raw.split(",")]
        w = {"weight": to_number(parts[0], "weights.weight")}
        if len(parts) >= 2 and clean(parts[1]) is not None:
            w["spool_weight"] = to_number(parts[1], "weights.spool_weight")
        if len(parts) >= 3 and clean(parts[2]) is not None:
            st = parts[2].lower()
            if st not in {"plastic", "cardboard", "metal"}:
                raise UserError(
                    f"Feld **weights**: spool_type '{parts[2]}' ungueltig "
                    "(erlaubt: plastic, cardboard, metal)."
                )
            w["spool_type"] = st
        weights.append(w)
    if not weights:
        raise UserError("Feld **weights**: mindestens eine Zeile noetig.")
    return weights


def parse_colors(s):
    """Eine Zeile pro Farbe: `Name, hex` oder `Name, hex1 hex2 ...` (multi-color).
    `#` vor dem Hex ist erlaubt. Beispiel: `Galaxy Black, 1A1A1A`"""
    s = clean(s)
    if s is None:
        raise UserError("Feld **colors**: mindestens eine Farbe noetig, z. B. `Schwarz, 1A1A1A`.")
    colors = []
    for raw in s.splitlines():
        raw = raw.strip().lstrip("-*").strip()
        if not raw:
            continue
        if "," not in raw:
            raise UserError(f"Feld **colors**: Zeile '{raw}' braucht `Name, hex`.")
        name, rest = raw.split(",", 1)
        name = name.strip()
        hexes = [h.strip().lstrip("#") for h in rest.split() if h.strip()]
        if not name:
            raise UserError(f"Feld **colors**: Name fehlt in Zeile '{raw}'.")
        if not hexes:
            raise UserError(f"Feld **colors**: Hex-Code fehlt fuer '{name}'.")
        c = {"name": name}
        if len(hexes) == 1:
            c["hex"] = hexes[0]
        else:
            c["hexes"] = list(dict.fromkeys(hexes))
        colors.append(c)
    if not colors:
        raise UserError("Feld **colors**: mindestens eine Farbe noetig.")
    return colors


def build_filament(d):
    name = clean(d.get("name")) or "{color_name}"
    material = clean(d.get("material"))
    if material is None:
        raise UserError("Feld **material** ist Pflicht (z. B. PLA, PETG, ABS-CF).")

    fil = {
        "name": name,
        "material": material.upper() if material.isascii() else material,
        "density": to_number(d.get("density"), "density"),
        "weights": parse_weights(d.get("weights")),
        "diameters": parse_diameters(d.get("diameters")),
        "colors": parse_colors(d.get("colors")),
    }
    if fil["density"] is None:
        raise UserError("Feld **density** ist Pflicht (g/cm3, z. B. 1.24).")

    # optionale Felder
    for key in ("extruder_temp", "bed_temp"):
        v = to_int(d.get(key), key)
        if v is not None:
            fil[key] = v
    for key in ("extruder_temp_range", "bed_temp_range"):
        v = parse_int_range(d.get(key), key)
        if v is not None:
            fil[key] = v
    for key in ("finish", "pattern", "fill", "multi_color_direction"):
        v = clean(d.get(key))
        if v is not None:
            fil[key] = v.lower()
    for key in ("translucent", "glow"):
        v = clean(d.get(key))
        if v is not None:
            fil[key] = str(v).lower() in {"true", "ja", "yes", "x", "[x]"}
    return fil


def find_manufacturer_file(manufacturer):
    """Bestehende Datei case-insensitiv finden, sonst Slug-Dateinamen bilden."""
    target = manufacturer.strip().lower()
    for path in sorted(FIL_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if str(data.get("manufacturer", "")).strip().lower() == target:
            return path, data
    slug = re.sub(r"[^a-z0-9]+", "", manufacturer.lower())
    if not slug:
        raise UserError("Feld **manufacturer**: ungueltiger Name.")
    return FIL_DIR / f"{slug}.json", None


def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    paths = [a for a in args if not a.startswith("--")]
    if not paths:
        print("Usage: issue_to_filament.py issue.json [--dry-run]")
        sys.exit(2)

    parsed = json.loads(Path(paths[0]).read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))

    try:
        manufacturer = clean(parsed.get("manufacturer"))
        if manufacturer is None:
            raise UserError("Feld **manufacturer** ist Pflicht.")
        new_fil = build_filament(parsed)
        path, existing = find_manufacturer_file(manufacturer)

        if existing is None:
            doc = {"manufacturer": manufacturer, "filaments": [new_fil]}
            created = True
        else:
            doc = existing
            doc.setdefault("filaments", [])
            if new_fil in doc["filaments"]:
                raise UserError("Dieser exakte Filament-Eintrag existiert bereits in der Datei.")
            doc["filaments"].append(new_fil)
            created = False

        jsonschema.validate(doc, schema)

    except UserError as e:
        print(f"VALIDATION_FAILED\n\n{e}")
        sys.exit(1)
    except jsonschema.ValidationError as e:
        loc = " -> ".join(str(p) for p in e.absolute_path) or "(Wurzel)"
        print(
            "VALIDATION_FAILED\n\n"
            f"Schema-Fehler bei `{loc}`:\n> {e.message}\n\n"
            "Bitte das Formular korrigieren."
        )
        sys.exit(1)

    print("VALIDATION_OK")
    print(f"Hersteller-Datei: filaments/{path.name} ({'neu' if created else 'ergaenzt'})")
    print("Neuer Eintrag:")
    print(json.dumps(new_fil, indent=2, ensure_ascii=False))

    if not dry_run:
        FIL_DIR.mkdir(exist_ok=True)
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        # Pfad fuer den Workflow-Commit ausgeben
        gh_out = __import__("os").environ.get("GITHUB_OUTPUT")
        if gh_out:
            with open(gh_out, "a", encoding="utf-8") as fh:
                fh.write(f"changed_file=filaments/{path.name}\n")


if __name__ == "__main__":
    main()
