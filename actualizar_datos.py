import urllib.request
import json
import re
import concurrent.futures
from datetime import datetime

HEADERS = {
    "Accept": "application/json, application/xml, text/xml, */*",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RadarArquitecturaEuskadi/6.0"
}

URLS_OPENDATA_EUSKADI = [
    "https://opendata.euskadi.eus/contenidos/ds_contrataciones/contrataciones_admin_2026/opendata/contratos.json",
    "https://opendata.euskadi.eus/contenidos/ds_contrataciones/contrataciones_admin_2025/opendata/contratos.json",
    "https://opendata.euskadi.eus/contenidos/ds_contrataciones/contrataciones_admin_2024/opendata/contratos.json",
    "https://opendata.euskadi.eus/contenidos/ds_contrataciones/contrataciones_admin_2023/opendata/contratos.json",
]

RE_CPV = re.compile(r'\b(71[2345]\d{5})')
PALABRAS_ARQ = (
    "redacción de proyecto", "redaccion de proyecto", "proyecto básico", "proyecto basico",
    "proyecto de ejecución", "proyecto de ejecucion", "dirección de obra", "direccion de obra",
    "dirección facultativa", "direccion facultativa", "proyecto de urbanización", "proyecto de urbanizacion",
    "plan general", "plan parcial", "plan especial", "estudio de detalle", "planeamiento",
    "servicios de arquitectura", "concurso de proyectos", "anteproyecto", "arquitect"
)

def limpiar(val):
    if not val:
        return ""
    if isinstance(val, dict):
        val = val.get("es") or val.get("eu") or next(iter(val.values()), "")
    return " ".join(str(val).split())

def parsear_importe(val):
    if not val or isinstance(val, bool):
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = re.sub(r'[^\d.,]', '', str(val))
    if not s:
        return 0.0
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0

def clasificar_tipo(t):
    if any(k in t for k in ("urbaniza", "plan general", "plan parcial", "planeamiento", "pgou", "plan especial", "estudio de detalle")):
        return "Urbanismo y Planeamiento"
    if any(k in t for k in ("dirección de obra", "direccion de obra", "dirección facultativa", "direccion facultativa", "coordinación de seguridad")):
        return "Dirección de Obra"
    if any(k in t for k in ("asistencia técnica", "asistencia tecnica", "consultoría", "consultoria", "oficina técnica")):
        return "Asistencia Técnica"
    return "Redacción de Proyecto"

def detectar_territorio(geo, texto):
    g = (geo + " " + texto).lower()
    if any(k in g for k in ("bizkaia", "vizcaya", "bilbao", "getxo", "barakaldo", "durango", "basauri", "leioa", "portugalete", "santurtzi", "gernika", "bermeo", "erandio", "galdakao", "sestao", "amorebieta", "mungia")):
        return "Bizkaia"
    if any(k in g for k in ("gipuzkoa", "guipúzcoa", "donostia", "san sebastián", "san sebastian", "irun", "errenteria", "eibar", "zarautz", "arrasate", "tolosa", "hernani", "bergara", "azpeitia", "lasarte", "pasaia", "hondarribia")):
        return "Gipuzkoa"
    if any(k in g for k in ("araba", "álava", "alava", "vitoria", "gasteiz", "llodio", "laudio", "amurrio", "salvatierra", "agurain")):
        return "Álava"
    return "Euskadi (Autonómico)"

def enriquecer_con_xml(exp):
    """Lee el dataXML oficial de cada expediente de arquitectura para sacar importes, baja, ganador y licitadores."""
    xml_url = exp.pop("_xml", "")
    if not xml_url:
        return exp
    if xml_url.startswith("/"):
        xml_url = "https://opendata.euskadi.eus" + xml_url
    try:
        req = urllib.request.Request(xml_url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=12) as resp:
            xml_txt = resp.read().decode("utf-8", errors="ignore")

        m_cpv = RE_CPV.search(xml_txt)
        if m_cpv:
            exp["cpv"] = m_cpv.group(1)

        # Presupuesto sin IVA
        for pat in (
            r'presupuestoSinIva[^>]*>([^<]+)<',
            r'valorEstimado[^>]*>([^<]+)<',
            r'TaxExclusiveAmount[^>]*>([^<]+)<',
            r'presupuestoLicitacionSinIva[^>]*>([^<]+)<'
        ):
            m = re.search(pat, xml_txt, re.IGNORECASE)
            if m:
                val = parsear_importe(m.group(1))
                if val > 0:
                    exp["pbl"] = round(val, 2)
                    break

        # Importe adjudicación sin IVA
        for pat in (
            r'importeAdjudicacionSinIva[^>]*>([^<]+)<',
            r'importeFormalizacionSinIva[^>]*>([^<]+)<',
            r'PayableAmount[^>]*>([^<]+)<',
            r'importeAdjudicacion[^>]*>([^<]+)<'
        ):
            m = re.search(pat, xml_txt, re.IGNORECASE)
            if m:
                val = parsear_importe(m.group(1))
                if val > 0:
                    exp["adjudicacion"] = round(val, 2)
                    break

        # Licitadores
        m_lic = re.search(r'(?:numeroLicitadores|numLicitadores|ofertasRecibidas|ReceivedTenderQuantity)[^>]*>(\d+)<', xml_txt, re.IGNORECASE)
        if m_lic:
            exp["licitadores"] = int(m_lic.group(1))

        # Adjudicatario
        for pat in (
            r'<adjudicatario[^>]*>([^<]+)<',
            r'<razonSocial[^>]*>([^<]+)<',
            r'<WinningParty>.*?<Name[^>]*>([^<]+)<'
        ):
            m = re.search(pat, xml_txt, re.IGNORECASE | re.DOTALL)
            if m and limpiar(m.group(1)):
                exp["adjudicatario"] = limpiar(m.group(1))
                break

        if exp["adjudicacion"] and exp["pbl"] >= exp["adjudicacion"] and exp["pbl"] > 0:
            exp["baja"] = round(((exp["pbl"] - exp["adjudicacion"]) / exp["pbl"]) * 100, 2)
            exp["estado"] = "Adjudicada"
    except Exception:
        pass
    return exp

def procesar_item(item, expedientes):
    titulo = limpiar(item.get("contratacion_objeto_contrato") or item.get("contratacion_titulo_contrato") or item.get("documentName"))
    if not titulo:
        return

    materia = limpiar(item.get("contratacion_materia_contrato") or "")
    desc = limpiar(item.get("documentDescription") or "")
    bloque_busqueda = f"{titulo} {materia} {desc}"

    m_cpv = RE_CPV.search(bloque_busqueda)
    t_lower = bloque_busqueda.lower()

    if not m_cpv and not any(p in t_lower for p in PALABRAS_ARQ):
        return

    exp_id = limpiar(item.get("contratacion_expediente")) or str(abs(hash(titulo)))
    organismo = limpiar(item.get("contratacion_poder_adjudicador_titulo") or item.get("contratacion_organo_contratacion") or item.get("institution")) or "Sector Público Vasco"
    geo = limpiar(item.get("contratacion_ambito_geografico") or "")

    fecha_raw = limpiar(item.get("contratacion_fecha_de_publicacion_documento") or item.get("creationDate") or "")
    m_f = re.search(r'(\d{4}-\d{2}-\d{2})', fecha_raw)
    if m_f:
        fecha = m_f.group(1)
    else:
        m_eu = re.search(r'(\d{2})/(\d{2})/(\d{4})', fecha_raw)
        fecha = f"{m_eu.group(3)}-{m_eu.group(2)}-{m_eu.group(1)}" if m_eu else fecha_raw[:10]

    est_raw = limpiar(item.get("contratacion_estado_tramitacion") or "").lower()
    adj_txt = limpiar(item.get("contratacion_adjudicacion") or "")
    if adj_txt or any(k in est_raw for k in ("adjudic", "formaliz", "resuelt")):
        estado = "Adjudicada"
    elif any(k in est_raw for k in ("plazo", "abiert", "anuncio", "licitaci", "publicad", "vigente")):
        estado = "En plazo"
    else:
        estado = "En evaluación"

    enlace = limpiar(item.get("friendlyUrl") or item.get("contratacion_poder_adjudicador_url") or "")
    if enlace.startswith("/"):
        enlace = "https://www.contratacion.euskadi.eus" + enlace
    elif not enlace:
        enlace = "https://www.contratacion.euskadi.eus"

    expedientes[exp_id] = {
        "id": exp_id,
        "titulo": titulo,
        "organismo": organismo,
        "territorio": detectar_territorio(geo, f"{organismo} {t_lower}"),
        "tipo": clasificar_tipo(t_lower),
        "cpv": m_cpv.group(1) if m_cpv else "71200000",
        "estado": estado,
        "fecha": fecha,
        "pbl": 0.0,
        "adjudicacion": None,
        "baja": None,
        "adjudicatario": adj_txt if adj_txt and not adj_txt.startswith("http") else None,
        "licitadores": None,
        "enlace": enlace,
        "_xml": limpiar(item.get("dataXML") or "")
    }

def procesar():
    expedientes = {}
    for url in URLS_OPENDATA_EUSKADI:
        try:
            print(f"Descargando: {url}", flush=True)
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=90) as resp:
                raw = resp.read().decode("utf-8", errors="ignore").strip()
            if not raw.startswith(("[", "{")):
                raw = re.sub(r'^[a-zA-Z0-9_]+\((.*)\);?$', r'\1', raw, flags=re.DOTALL)
            data = json.loads(raw)
            lista = data if isinstance(data, list) else (data.get("items") or data.get("contratos") or data.get("records") or [])
            print(f" -> {len(lista)} registros leídos", flush=True)
            for item in lista:
                if isinstance(item, dict):
                    procesar_item(item, expedientes)
        except Exception as e:
            print(f" -> Error ({url}): {e}", flush=True)

    lista_bruta = sorted(expedientes.values(), key=lambda x: x["fecha"] or "", reverse=True)
    print(f"Expedientes de arquitectura detectados: {len(lista_bruta)}. Enriqueciendo datos económicos...", flush=True)

    # Enriquecer en paralelo los 600 más recientes con su XML oficial (tarda ~15 segundos)
    recientes = lista_bruta[:600]
    resto = lista_bruta[600:]
    for r in resto:
        r.pop("_xml", None)

    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
        recientes_ok = list(executor.map(enriquecer_con_xml, recientes))

    lista_final = recientes_ok + resto
    with open("licitaciones.json", "w", encoding="utf-8") as f:
        json.dump({
            "ultima_actualizacion": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "total_registros": len(lista_final),
            "licitaciones": lista_final
        }, f, ensure_ascii=False, indent=2)

    print(f"TOTAL FINAL GUARDADO: {len(lista_final)} expedientes de arquitectura.", flush=True)

if __name__ == "__main__":
    procesar()
