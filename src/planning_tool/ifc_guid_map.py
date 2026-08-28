"""
Extract GUID → Mark from an IFC file, then match Mark to schedule Module IDs.

Mark is read from any property set (property name "Mark", case-insensitive),
not from IfcElement.Tag. One module can own many GUIDs.

This file is standalone for now. Review the mapping, then we can wire it
into the 4D viewer.

Requires: pip install ifcopenshell pandas

Usage:
    python -m planning_tool.ifc_guid_map model.ifc
    python -m planning_tool.ifc_guid_map model.ifc schedule.csv
    python -m planning_tool.ifc_guid_map model.ifc schedule.csv out.json
"""
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Optional

# Restrict to these classes, or set to None to keep every IfcElement.
IFC_CLASSES: Optional[tuple[str, ...]] = (
    "IfcBeam",
    "IfcColumn",
    "IfcSlab",
)

# Property names treated as the module mark (case-insensitive).
MARK_PROPERTY_NAMES = {"mark"}

SCHEDULE_ID_COLUMNS = ("Module ID", "Module_ID")


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)) and value:
        return _text(value[0])
    return str(value).strip()


def extract_guid_marks(ifc_path: Path) -> list[dict]:
    """One row per element that has a Mark property."""
    try:
        import ifcopenshell
        from ifcopenshell.util.element import get_psets
    except ImportError as exc:
        raise RuntimeError(
            "ifcopenshell is required to read Mark values from IFC. "
            "Install it with: pip install ifcopenshell"
        ) from exc

    model = ifcopenshell.open(str(ifc_path))
    rows: list[dict] = []

    if IFC_CLASSES:
        elements: list = []
        for ifc_class in IFC_CLASSES:
            elements.extend(model.by_type(ifc_class))
    else:
        elements = list(model.by_type("IfcElement"))

    seen: set[tuple[str, str]] = set()
    for element in elements:
        guid = getattr(element, "GlobalId", None)
        if not guid:
            continue

        property_sets = get_psets(element, psets_only=True, should_inherit=True)
        mark = None
        pset_name = None
        for name, properties in property_sets.items():
            if not isinstance(properties, dict):
                continue
            for property_name, value in properties.items():
                if property_name.casefold() in MARK_PROPERTY_NAMES:
                    mark = _text(value)
                    pset_name = name
                    break
            if mark:
                break

        if not mark:
            continue

        key = (guid, mark)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "GUID": guid,
                "Mark": mark,
                "IFC_Class": element.is_a(),
                "STEP_ID": f"#{element.id()}",
                "Property_Set": pset_name or "",
            }
        )
    return rows


def load_schedule_module_ids(csv_path: Path) -> list[str]:
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return []
        column = next(
            (name for name in SCHEDULE_ID_COLUMNS if name in reader.fieldnames),
            None,
        )
        if column is None:
            raise ValueError(
                f"{csv_path} has no Module ID column. "
                f"Found: {list(reader.fieldnames)}"
            )
        ids = []
        seen: set[str] = set()
        for row in reader:
            module_id = _text(row.get(column))
            if module_id and module_id not in seen:
                seen.add(module_id)
                ids.append(module_id)
        return ids


def match_to_schedule(
    guid_rows: Iterable[dict],
    module_ids: Optional[Iterable[str]] = None,
) -> dict:
    """Group GUIDs by Mark. If module_ids is given, keep only matching Marks."""
    grouped: dict[str, list[str]] = defaultdict(list)
    for row in guid_rows:
        mark = row["Mark"]
        guid = row["GUID"]
        if guid not in grouped[mark]:
            grouped[mark].append(guid)

    schedule_ids = list(module_ids) if module_ids is not None else None
    if schedule_ids is None:
        matched = dict(grouped)
        missing_in_ifc: list[str] = []
    else:
        wanted = set(schedule_ids)
        matched = {mid: grouped[mid] for mid in schedule_ids if mid in grouped}
        missing_in_ifc = [mid for mid in schedule_ids if mid not in grouped]

    extra_in_ifc = sorted(
        mark for mark in grouped if schedule_ids is None or mark not in set(schedule_ids)
    ) if schedule_ids is not None else []

    return {
        "module_to_guids": matched,
        "unmatched_modules": missing_in_ifc,
        "unmatched_marks": extra_in_ifc,
        "element_count": sum(len(v) for v in matched.values()),
        "module_count": len(matched),
    }


def write_guid_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["GUID", "Mark", "IFC_Class", "STEP_ID", "Property_Set"],
        )
        writer.writeheader()
        writer.writerows(rows)


def write_mapping_json(result: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "module_to_guids": result["module_to_guids"],
        "unmatched_modules": result["unmatched_modules"],
        "unmatched_marks": result["unmatched_marks"],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(__doc__.strip())
        return 1

    ifc_path = Path(args[0])
    schedule_path = Path(args[1]) if len(args) > 1 else None
    out_json = Path(args[2]) if len(args) > 2 else ifc_path.with_name("module_guids.json")
    out_csv = ifc_path.with_name("ifc_guid_mark.csv")

    rows = extract_guid_marks(ifc_path)
    write_guid_csv(rows, out_csv)
    print(f"Extracted {len(rows)} GUID–Mark rows → {out_csv}")

    module_ids = load_schedule_module_ids(schedule_path) if schedule_path else None
    result = match_to_schedule(rows, module_ids)
    write_mapping_json(result, out_json)
    print(
        f"Matched {result['module_count']} modules, "
        f"{result['element_count']} GUIDs → {out_json}"
    )
    if result["unmatched_modules"]:
        print(
            f"Schedule modules with no IFC Mark: {result['unmatched_modules']}"
        )
    if result["unmatched_marks"]:
        print(
            f"IFC Marks not in schedule: {result['unmatched_marks']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
