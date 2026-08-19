import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import unicodedata
import zipfile
from collections import defaultdict
from datetime import datetime
import xml.etree.ElementTree as ET

import requests

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CONFIG_PATH = os.path.join(ROOT, "config", "app_config.json")
TERRITORY_PATH = os.path.join(ROOT, "data", "territorio.json")
TERRITORY_XLSX_PATH = os.path.join(ROOT, "data", "ATLANTICO.xlsx")
GEOJSON_PATH = os.path.join(ROOT, "data", "regiao.geojson")
OUTPUT_JSON = os.path.join(ROOT, "data", "dashboard_data.json")
OUTPUT_JS = os.path.join(ROOT, "data", "dashboard_data.js")
OUTPUT_META = os.path.join(ROOT, "data", "metadata.json")

# Summary order: regionais first, then the territory total row
SUMMARY_ORDER = ["ACARAÚ", "ITAPIPOCA", "ITAPAJÉ", "TRAIRÍ", "ATLÂNTICO"]

# API sucursais that belong to this territory.
# "ITAPAJÉ" in the API covers both ITAPAJÉ and TRAIRÍ regionais —
# the split is done by GPS coordinate → GeoJSON polygon lookup,
# exactly like SÃO BENEDITO was split into TIANGUÁ and INHUÇU in NORTE.
TERRITORY_TOTAL_KEY = "ATLÂNTICO"

DATETIME_FORMATS = [
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z",
]
TOP_LEVEL_DATE_KEYS = [
    "dataAtualizacao", "data_atualizacao", "ultimaAtualizacao", "ultima_atualizacao",
    "updatedAt", "updated_at", "dataBase", "data_base", "timestamp", "lastUpdate",
]
ROW_DATE_KEYS = [
    "dataAtualizacao", "DataAtualizacao", "ultimaAtualizacao", "updatedAt", "updated_at",
    "dtAtualizacao", "dt_atualizacao", "dataBase", "data_base", "timestamp",
]
ADDRESS_KEYS = ["Endereco", "endereco", "ENDERECO", "logradouro", "Logradouro", "localizacao", "Localizacao"]
BAIRRO_KEYS = ["bairro", "Bairro", "BAIRRO", "bairroCliente", "BairroCliente"]
OBSERVACAO_KEYS = ["observacao", "Observacao", "observação", "Observação", "OBSERVACAO", "OBSERVAÇÃO", "obs", "Obs", "OBS"]


def norm(value):
    value = "" if value is None else str(value)
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", value).strip().upper()


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def dump_json(path, payload):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def parse_int(value, default=0):
    try:
        return int(float(str(value).replace(".", "").replace(",", ".") if isinstance(value, str) and value.count(",") == 1 and value.count(".") > 1 else str(value).replace(",", ".").strip()))
    except Exception:
        try:
            return int(float(str(value).replace(",", ".").strip()))
        except Exception:
            return default


def parse_float(value, default=0.0):
    try:
        text = str(value).strip().replace(" ", "")
        if text.count(",") == 1 and text.count(".") > 1:
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", ".")
        return float(text)
    except Exception:
        return default


def parse_duration_hours(value):
    text = str(value or "").strip()
    if not text:
        return 0.0
    if ":" not in text:
        return parse_float(text, 0.0)
    parts = text.split(":")
    try:
        if len(parts) == 2:
            hours, minutes = [int(float(item)) for item in parts]
            return hours + (minutes / 60.0)
        if len(parts) == 3:
            hours, minutes, seconds = [int(float(item)) for item in parts]
            return hours + (minutes / 60.0) + (seconds / 3600.0)
    except Exception:
        return 0.0
    return 0.0


def parse_datetime(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except Exception:
        pass
    for fmt in DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            continue
    return None


def safe_team(value):
    text = str(value or "").strip()
    return "" if norm(text) in {"", "-", "AGUARDAR", "AGUARDANDO", "N/D"} else text


def strip_leading_double_zero(value):
    text = str(value or "").strip()
    return text[2:] if text.startswith("00") else text


def pick_text(item, keys):
    if not isinstance(item, dict):
        return ""
    for key in keys:
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def has_pendencia(value):
    return "PENDENCIA" in norm(value)


def extract_json_from_text(text):
    stripped = text.strip()
    if not stripped:
        return []
    try:
        return json.loads(stripped)
    except Exception:
        pass
    for open_char, close_char in (("[", "]"), ("{", "}")):
        start = stripped.find(open_char)
        end = stripped.rfind(close_char)
        if start != -1 and end != -1 and end > start:
            fragment = stripped[start:end + 1]
            try:
                return json.loads(fragment)
            except Exception:
                continue
    raise RuntimeError("Não foi possível interpretar o payload JSON.")


def extract_rows(payload):
    if isinstance(payload, list):
        return payload, payload
    if isinstance(payload, dict):
        for key in ("Dados", "dados", "data", "rows", "Rows", "itens", "items", "value", "Value"):
            if isinstance(payload.get(key), list):
                return payload[key], payload
    raise RuntimeError("Formato de payload não suportado.")


def fetch_payload_from_url(url):
    response = requests.get(url, timeout=180)
    response.raise_for_status()
    try:
        return response.json()
    except Exception:
        return extract_json_from_text(response.text)


def load_payload(source_path=None, url=None):
    if source_path:
        with open(source_path, "r", encoding="utf-8") as fh:
            return extract_json_from_text(fh.read())
    if url:
        return fetch_payload_from_url(url)
    raise RuntimeError("Fonte não informada.")


def read_xlsx_rows(path):
    with zipfile.ZipFile(path) as zf:
        shared_strings = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            for si in root.findall("a:si", ns):
                shared_strings.append("".join((node.text or "") for node in si.iterfind(".//a:t", ns)))

        workbook = ET.fromstring(zf.read("xl/workbook.xml"))
        ns = {
            "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
            "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        }
        sheets = workbook.findall("a:sheets/a:sheet", ns)
        if not sheets:
            return []
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rel_ns = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}
        target_by_rid = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels.findall("r:Relationship", rel_ns)}
        first_sheet_rid = sheets[0].attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        sheet_target = target_by_rid[first_sheet_rid]
        if not sheet_target.startswith("worksheets/"):
            sheet_target = f"worksheets/{os.path.basename(sheet_target)}"
        sheet_xml = ET.fromstring(zf.read(f"xl/{sheet_target}"))

        rows = []
        sheet_ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        for row in sheet_xml.findall(".//a:sheetData/a:row", sheet_ns):
            values = {}
            for cell in row.findall("a:c", sheet_ns):
                ref = cell.attrib.get("r", "")
                match = re.match(r"[A-Z]+", ref)
                col = match.group(0) if match else ref
                cell_type = cell.attrib.get("t")
                value_node = cell.find("a:v", sheet_ns)
                if value_node is None:
                    value = ""
                else:
                    raw = value_node.text or ""
                    value = shared_strings[int(raw)] if cell_type == "s" else raw
                values[col] = value
            rows.append(values)
        return rows


def load_territory():
    """Load municipality→regional mapping, preferring ATLANTICO.xlsx over territorio.json.

    The ATLANTICO.xlsx uses the API sucursal names as regional values (ACARAÚ, ITAPIPOCA,
    ITAPAJÉ). The TRAIRÍ split is encoded in territorio.json where Paracuru, Paraipaba and
    Trairi are mapped to TRAIRÍ rather than ITAPAJÉ. The xlsx is the authoritative source
    for ACARAÚ and ITAPIPOCA; territorio.json handles the ITAPAJÉ/TRAIRÍ split.
    We always read territorio.json as the final source of truth so the split is respected.
    """
    if os.path.exists(TERRITORY_XLSX_PATH):
        rows = read_xlsx_rows(TERRITORY_XLSX_PATH)
        territory = []
        for row in rows[1:]:  # skip header
            municipio = str(row.get("A", "")).strip()
            regional = str(row.get("B", "")).strip()
            if municipio and regional:
                territory.append({"municipio": municipio, "regional": regional})
        if territory:
            # Merge with territorio.json to honour the ITAPAJÉ→TRAIRÍ split.
            # territorio.json wins on a per-municipio basis.
            try:
                override = load_json(TERRITORY_PATH)
                override_map = {norm(r["municipio"]): r for r in override}
                merged = []
                seen = set()
                for entry in territory:
                    key = norm(entry["municipio"])
                    if key in override_map:
                        merged.append(override_map[key])
                    else:
                        merged.append(entry)
                    seen.add(key)
                # Add any entries only in territorio.json (shouldn't happen but be safe)
                for entry in override:
                    if norm(entry["municipio"]) not in seen:
                        merged.append(entry)
                return merged
            except Exception:
                return territory
    return load_json(TERRITORY_PATH)


def discover_data_updated_at(source_payload, rows):
    candidates = []
    if isinstance(source_payload, dict):
        for key in TOP_LEVEL_DATE_KEYS:
            dt = parse_datetime(source_payload.get(key))
            if dt:
                candidates.append(dt)
        meta = source_payload.get("meta")
        if isinstance(meta, dict):
            for key in TOP_LEVEL_DATE_KEYS + ["updated_at_iso", "data_updated_at_iso", "updated_at_display", "data_updated_at_display"]:
                dt = parse_datetime(meta.get(key))
                if dt:
                    candidates.append(dt)
    for row in rows[:500]:
        if not isinstance(row, dict):
            continue
        for key in ROW_DATE_KEYS:
            dt = parse_datetime(row.get(key))
            if dt:
                candidates.append(dt)
    return max(candidates) if candidates else None


def point_in_ring(lon, lat, ring):
    inside = False
    if len(ring) < 3:
        return False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        denominator = (yj - yi) if (yj - yi) != 0 else 1e-12
        intersects = ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / denominator + xi)
        if intersects:
            inside = not inside
        j = i
    return inside


def point_in_polygon(lon, lat, polygon_coords):
    if not polygon_coords:
        return False
    if not point_in_ring(lon, lat, polygon_coords[0]):
        return False
    for hole in polygon_coords[1:]:
        if point_in_ring(lon, lat, hole):
            return False
    return True


def point_in_geometry(lon, lat, geometry):
    if not geometry:
        return False
    geo_type = geometry.get("type")
    coords = geometry.get("coordinates")
    if geo_type == "Polygon":
        return point_in_polygon(lon, lat, coords)
    if geo_type == "MultiPolygon":
        return any(point_in_polygon(lon, lat, poly) for poly in coords)
    return False


def classify_municipio(lat, lon, geojson):
    for feature in geojson.get("features", []):
        if point_in_geometry(lon, lat, feature.get("geometry")):
            return (
                feature.get("properties", {}).get("name")
                or feature.get("properties", {}).get("description")
                or ""
            )
    return None


def pick_coordinate_cliente(legacy_row):
    if not isinstance(legacy_row, dict):
        return None, None
    raw = str(legacy_row.get("Coordenada_Cliente") or "").strip().replace(";", ",")
    if not raw:
        return None, None
    parts = [piece.strip() for piece in raw.split(",") if piece.strip()]
    if len(parts) < 2:
        return None, None
    try:
        return float(parts[0]), float(parts[1])
    except Exception:
        return None, None


def build_legacy_index(legacy_rows):
    index = {}
    for item in legacy_rows:
        if not isinstance(item, dict):
            continue
        numero = str(item.get("Num_Incidencia") or item.get("numero") or "").strip()
        if not numero:
            continue
        lat, lon = pick_coordinate_cliente(item)
        has_coord = lat is not None and lon is not None
        current = index.get(numero)
        if current is None:
            index[numero] = item
        elif not (pick_coordinate_cliente(current)[0] is not None) and has_coord:
            index[numero] = item
    return index


def fallback_from_sucursal(sucursal, territory_rows):
    sucursal_norm = norm(sucursal)
    if not sucursal_norm:
        return None, None
    for row in territory_rows:
        if norm(row["municipio"]) == sucursal_norm:
            return row["municipio"], row["regional"]
    return None, None


def summarize_region(region, rows):
    incid = len(rows)
    equipes = sorted({row["equipe"] for row in rows if row["equipe"]})
    qtde_equipes = len(equipes)
    return {
        "regional": region,
        "clientes_afetados": sum(row["clientes_afetados"] for row in rows),
        "incidencias_ativas": incid,
        "nao_despachadas": sum(1 for row in rows if not row["equipe"]),
        "qtde_equipes": qtde_equipes,
        "qtde_equipes_recomendadas": math.ceil(incid / 4) if incid else 0,
        "aporte_recomendado": max(math.ceil((incid - (qtde_equipes * 4)) / 4), 0) if incid else 0,
        "mais_de_um_aviso": sum(1 for row in rows if row["avisos"] > 1),
        "dur_lt_8": sum(1 for row in rows if row["duracao_horas"] < 8),
        "dur_8_16": sum(1 for row in rows if 8 <= row["duracao_horas"] < 16),
        "dur_16_24": sum(1 for row in rows if 16 <= row["duracao_horas"] < 24),
        "dur_gt_24": sum(1 for row in rows if 24 <= row["duracao_horas"] < 48),
        "dur_gt_48": sum(1 for row in rows if row["duracao_horas"] >= 48),
    }


def rank_rows(rows, key, limit):
    ranked = [row for row in rows if (row.get(key) or 0) > 0]
    ranked.sort(key=lambda row: (row.get(key, 0), row.get("clientes_afetados", 0), row.get("numero_display", "")), reverse=True)
    return ranked[:limit]


def merge_main_and_legacy(main_row, legacy_row, territory_rows, geojson, valid_sucursais, valid_polos):
    numero = str(main_row.get("numero") or main_row.get("Num_Incidencia") or "").strip()
    sucursal = str(main_row.get("sucursal") or main_row.get("Sucursal") or "").strip()
    polo = str(main_row.get("polo") or main_row.get("Polo") or "").strip()
    observacao = pick_text(main_row, OBSERVACAO_KEYS) or pick_text(legacy_row or {}, OBSERVACAO_KEYS)

    if valid_sucursais and norm(sucursal) not in valid_sucursais:
        return None, "outside"

    if valid_polos and norm(polo) not in valid_polos:
        return None, "outside"

    status = str(main_row.get("statusExecucao") or main_row.get("Estado") or main_row.get("estado") or "").strip()
    if norm(status) == "FINALIZADO":
        return None, "finalizado"

    lat = lon = None
    if legacy_row:
        lat, lon = pick_coordinate_cliente(legacy_row)

    municipio = classify_municipio(lat, lon, geojson) if lat is not None and lon is not None else None
    regional = None
    map_visible = bool(municipio)

    if municipio:
        for row in territory_rows:
            if norm(row["municipio"]) == norm(municipio):
                regional = row["regional"]
                municipio = row["municipio"]
                break

    if not regional:
        municipio, regional = fallback_from_sucursal(sucursal, territory_rows)

    if not regional:
        return None, "unmapped"

    merged = {
        "numero": numero,
        "numero_display": strip_leading_double_zero(numero),
        "duracao": str(main_row.get("duracao") or main_row.get("Duracao") or "").strip(),
        "duracao_horas": parse_duration_hours(main_row.get("duracao") or main_row.get("Duracao") or main_row.get("duracao_horas")),
        "clientes_afetados": parse_int(main_row.get("clientesAfetadosAtual") or main_row.get("clientes_afetados") or main_row.get("Clientes") or main_row.get("clientes")),
        "conh": round(parse_float(main_row.get("conh") or main_row.get("Conh") or main_row.get("CHI") or (legacy_row or {}).get("Consh")), 4),
        "municipio": municipio or sucursal or "",
        "municipio_norm": norm(municipio or sucursal or ""),
        "regional": regional,
        "equipe": safe_team(main_row.get("equipe") or main_row.get("Equipe") or main_row.get("Viatura") or (legacy_row or {}).get("Viatura")),
        "avisos": parse_int(main_row.get("numeroAvisos") or main_row.get("Total_Avisos") or (legacy_row or {}).get("Total_Avisos") or 0),
        "sucursal": sucursal,
        "polo": polo,
        "pendencia": "Sim" if has_pendencia(observacao) else "Não",
        "status_execucao": status,
        "lat": lat,
        "lon": lon,
        "coord_source": "Coordenada_Cliente" if lat is not None and lon is not None else "",
        "endereco": pick_text(legacy_row or {}, ADDRESS_KEYS) or pick_text(main_row, ADDRESS_KEYS),
        "ponto_eletrico": pick_text(main_row, ["pontoEletrico", "PontoEletrico", "Ponto_Eletrico"]) or pick_text(legacy_row or {}, ["Ponto_Eletrico", "pontoEletrico", "PontoEletrico"]),
        "bairro": pick_text(legacy_row or {}, BAIRRO_KEYS) or pick_text(main_row, BAIRRO_KEYS),
        "map_visible": map_visible,
    }
    return merged, "ok"


def build_dashboard(main_rows, legacy_rows, config, main_payload=None, source_label=""):
    territory_rows = load_territory()
    geojson = load_json(GEOJSON_PATH)
    valid_sucursais = {norm(item) for item in config.get("territory_sucursais", [])}
    valid_polos = {norm(item) for item in config.get("territory_polos", ["ATLANTICO"])}
    data_updated_at = discover_data_updated_at(main_payload, main_rows) or datetime.now()
    legacy_index = build_legacy_index(legacy_rows)

    filtered = []
    dropped_outside = 0
    dropped_finalizado = 0
    dropped_unmapped = 0

    for item in main_rows:
        if not isinstance(item, dict):
            continue
        legacy_row = legacy_index.get(str(item.get("numero") or item.get("Num_Incidencia") or "").strip())
        merged, status = merge_main_and_legacy(item, legacy_row, territory_rows, geojson, valid_sucursais, valid_polos)
        if status == "outside":
            dropped_outside += 1
            continue
        if status == "finalizado":
            dropped_finalizado += 1
            continue
        if status == "unmapped":
            dropped_unmapped += 1
            continue
        filtered.append(merged)

    summary_rows = []
    for region in SUMMARY_ORDER:
        if norm(region) == norm(TERRITORY_TOTAL_KEY):
            region_rows = filtered
        else:
            region_rows = [row for row in filtered if norm(row["regional"]) == norm(region)]
        summary_rows.append(summarize_region(region, region_rows))

    city_rows = []
    for feature in geojson.get("features", []):
        municipio = feature.get("properties", {}).get("name") or feature.get("properties", {}).get("description") or ""
        city_filtered = [row for row in filtered if row["map_visible"] and row["municipio_norm"] == norm(municipio)]
        equipes = sorted({row["equipe"] for row in city_filtered if row["equipe"]})
        regional = next((row["regional"] for row in territory_rows if norm(row["municipio"]) == norm(municipio)), feature.get("properties", {}).get("regional", ""))
        city_rows.append({
            "municipio": municipio,
            "regional": regional,
            "incidencias": len(city_filtered),
            "equipes": len(equipes),
            "tem_equipe_ativa": bool(equipes),
        })

    team_groups = defaultdict(list)
    for row in filtered:
        if row["equipe"]:
            team_groups[row["equipe"]].append(row)
    team_rows = []
    for equipe, rows in team_groups.items():
        team_rows.append({
            "equipe": equipe,
            "regional": rows[0]["regional"],
            "municipio": rows[0]["municipio"],
            "incidencias": len(rows),
            "clientes_afetados": sum(item["clientes_afetados"] for item in rows),
            "chi": round(sum(item["conh"] for item in rows), 2),
        })
    team_rows.sort(key=lambda row: (row["incidencias"], row["clientes_afetados"], row["chi"], row["equipe"]), reverse=True)

    return {
        "meta": {
            "title": config.get("title", "PAINEL OPERACIONAL - ATLÂNTICO"),
            "updated_at_iso": datetime.now().isoformat(timespec="seconds"),
            "updated_at_display": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
            "data_updated_at_iso": data_updated_at.isoformat(timespec="seconds"),
            "data_updated_at_display": data_updated_at.strftime("%d/%m/%Y %H:%M:%S"),
            "refresh_seconds": int(config.get("refresh_seconds", 180)),
            "source_mode": source_label,
        },
        "summary": {"regionais": summary_rows},
        "map": {"geojson": geojson, "cidades_list": city_rows},
        "rankings": {
            "duracao": rank_rows(filtered, "duracao_horas", 10),
            "chi": rank_rows(filtered, "conh", 5),
            "cli": rank_rows(filtered, "clientes_afetados", 5),
        },
        "teams": {
            "rows": team_rows,
            "totals": {"equipes_ativas": len(team_rows), "incidencias_com_equipe": sum(row["incidencias"] for row in team_rows)},
        },
        "rows": filtered,
        "metadata": {
            "raw_total": len(main_rows),
            "legacy_total": len(legacy_rows),
            "territory_total": len(filtered),
            "dropped_outside_territory": dropped_outside,
            "dropped_finalizado": dropped_finalizado,
            "dropped_unmapped": dropped_unmapped,
            "mapped_on_map": sum(1 for row in filtered if row["map_visible"]),
            "mapped_by_sucursal_fallback": sum(1 for row in filtered if not row["map_visible"]),
        },
    }


def write_outputs(payload):
    dump_json(OUTPUT_JSON, payload)
    with open(OUTPUT_JS, "w", encoding="utf-8") as fh:
        fh.write("window.DASHBOARD_DATA = ")
        json.dump(payload, fh, ensure_ascii=False)
        fh.write(";\n")
    meta = {
        "updated_at_iso": payload["meta"]["updated_at_iso"],
        "updated_at_display": payload["meta"]["updated_at_display"],
        "data_updated_at_iso": payload["meta"]["data_updated_at_iso"],
        "data_updated_at_display": payload["meta"]["data_updated_at_display"],
        "raw_total": payload["metadata"]["raw_total"],
        "legacy_total": payload["metadata"]["legacy_total"],
        "territory_total": payload["metadata"]["territory_total"],
        "mapped_on_map": payload["metadata"]["mapped_on_map"],
        "mapped_by_sucursal_fallback": payload["metadata"]["mapped_by_sucursal_fallback"],
        "hash": hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
    }
    dump_json(OUTPUT_META, meta)
    return meta


def git_commit_and_push(config):
    if not config.get("git_auto_commit"):
        return
    subprocess.run(["git", "add", "data/dashboard_data.json", "data/dashboard_data.js", "data/metadata.json", "data/territorio.json"], cwd=ROOT, check=True)
    status = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    if not status:
        return
    msg = f"{config.get('git_commit_prefix', 'auto: update dashboard data')} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    subprocess.run(["git", "commit", "-m", msg], cwd=ROOT, check=True)
    subprocess.run(["git", "push", config.get("git_remote", "origin"), config.get("git_branch", "main")], cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser(description="Atualiza o Painel Operacional - Atlântico")
    parser.add_argument("--source", help="Arquivo JSON local da API principal (compatibilidade com versão anterior).")
    parser.add_argument("--main-source", help="Arquivo JSON local da API principal.")
    parser.add_argument("--legacy-source", help="Arquivo JSON local da API legada com coordenadas.")
    parser.add_argument("--no-git", action="store_true", help="Não faz git commit/push, mesmo se configurado.")
    args = parser.parse_args()

    config = load_json(CONFIG_PATH)
    if args.no_git:
        config["git_auto_commit"] = False

    main_payload = load_payload(args.main_source or args.source, config.get("main_api_url") or config.get("api_url"))
    legacy_payload = load_payload(args.legacy_source, config.get("legacy_api_url"))

    main_rows, main_original = extract_rows(main_payload)
    legacy_rows, _ = extract_rows(legacy_payload)

    source_label = "API corporativa + API legada" if not (args.main_source or args.source or args.legacy_source) else "arquivo local"
    dashboard = build_dashboard(main_rows, legacy_rows, config, main_payload=main_original, source_label=source_label)
    meta = write_outputs(dashboard)
    git_commit_and_push(config)

    print(f"Atualizado com {dashboard['metadata']['territory_total']} incidências válidas. Mapas com coordenada: {dashboard['metadata']['mapped_on_map']}. Fallback por sucursal: {dashboard['metadata']['mapped_by_sucursal_fallback']}. Hash: {meta['hash'][:12]}")


if __name__ == "__main__":
    main()
