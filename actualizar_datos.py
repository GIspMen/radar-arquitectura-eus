import urllib.request
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime

# Códigos CPV de Arquitectura, Ingeniería, Urbanismo, Dirección de Obra y Asistencia Técnica
CPVS_ARQUITECTURA = ("712", "713", "714", "715")

HEADERS = {
    "Accept": "application/json, application/xml, text/xml, */*",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RadarArquitecturaEuskadi/3.0"
}

# Rutas directas de Open Data Euskadi (Contrataciones administrativas 2023 - 2026)
URLS_OPENDATA_EUSKADI = [
    # Contratos formalizados / adjudicados por año
    "https://opendata.euskadi.eus/contenidos/ds_contrataciones/contrataciones_admin_2026/opendata/contratos.json",
    "https://opendata.euskadi.eus/contenidos/ds_contrataciones/contrataciones_admin_2025/opendata/contratos.json",
    "https://opendata.euskadi.eus/contenidos/ds_contrataciones/contrataciones_admin_2024/opendata/contratos.json",
    "https://opendata.euskadi.eus/contenidos/ds_contrataciones/contrataciones_admin_2023/opendata/contratos.json",
    # Licitaciones / anuncios en curso
    "https://opendata.euskadi.eus/contenidos/ds_contrataciones/licitaciones_admin_2026/opendata/licitaciones.json",
    "https://opendata.euskadi.eus/contenidos/ds_contrataciones/licitaciones_admin_2025/opendata/licitaciones.json",
]

# Feed oficial ATOM de KontratazioA (Gobierno Vasco - en tiempo real)
FEEDS_ATOM_EUSKADI = [
    "https://www.contratacion.euskadi.eus/ac70cPublicidadWar/suscripcion/atom/es",
    "https://www.contratacion.euskadi.eus/w32-kpeperfi/es/contenidos/ds_contrataciones/perfil_contratante/opendata/perfil_contratante.atom"
]

def limpiar_texto(texto):
    if not texto:
        return ""
    if isinstance(texto, dict):
        texto = texto.get("es") or texto.get("eu") or next(iter(texto.values()), "")
    return re.sub(r'\s+', ' ', str(texto)).strip()

def extraer_cpvs_de_texto_o_dict(obj):
    texto_completo = json.dumps(obj, ensure_ascii=False) if isinstance(obj, (dict, list)) else str(obj)
    codigos = re.findall(r'\b(71[2345]\d{5})\b', texto_completo)
    return list(dict.fromkeys(codigos))

def clasificar_tipo(titulo):
    t = titulo.lower()
    if any(k in t for k in ["urbaniza", "plan general", "plan parcial", "planeamiento", "pgou", "plan especial", "estudio de detalle"]):
        return "Urbanismo y Planeamiento"
    if any(k in t for k in ["dirección de obra", "direccion de obra", "dirección facultativa", "direccion facultativa", "coordinación de seguridad"]):
        return "Dirección de Obra"
    if any(k in t for k in ["asistencia técnica", "asistencia tecnica", "consultoría", "consultoria", "oficina técnica"]):
        return "Asistencia Técnica"
    return "Redacción de Proyecto"

def detectar_territorio(organismo, titulo):
    texto = (organismo + " " + titulo).lower()
    if any(k in texto for k in ["bizkaia", "vizcaya", "bilbao", "getxo", "barakaldo", "durango", "basauri", "leioa", "portugalete", "santurtzi", "gernika", "bermeo", "erandio", "galdakao", "sestao", "amorebieta", "mungia"]):
        return "Bizkaia"
    if any(k in texto for k in ["gipuzkoa", "guipúzcoa", "donostia", "san sebastián", "san sebastian", "irun", "errenteria", "eibar", "zarautz", "arrasate", "tolosa", "hernani", "bergara", "azpeitia", "lasarte", "pasaia", "hondarribia"]):
        return "Gipuzkoa"
    if any(k in texto for k in ["araba", "álava", "alava", "vitoria", "gasteiz", "llodio", "laudio", "amurrio", "salvatierra", "agurain"]):
        return "Álava"
    return "Euskadi (Autonómico)"

def parsear_importe(val):
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, dict):
        val = val.get("amount") or val.get("value") or val.get("withoutTax") or 0
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

def descargar_raw(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", errors="ignore")

def procesar_item_json(item, expedientes):
    cpvs = extraer_cpvs_de_texto_o_dict(item)
    if not cpvs:
        return

    exp_id = str(
        item.get("codigoExpediente") or item.get("numeroExpediente") or item.get("code")
        or item.get("expedientCode") or item.get("id") or ""
    ).strip()

    titulo = limpiar_texto(
        item.get("objetoContrato") or item.get("objeto") or item.get("title")
        or item.get("descripcion") or item.get("name") or ""
    )
    if not titulo:
        return
    if not exp_id:
        exp_id = str(abs(hash(titulo)))

    organismo = limpiar_texto(
        item.get("entidadContratante") or item.get("organoContratacion")
        or item.get("poderAdjudicador") or (item.get("contractingAuthority") or {}).get("name")
        or "Sector Público Vasco"
    )

    pbl = parsear_importe(
        item.get("presupuestoSinIva") or item.get("presupuestoLicitacionSinIva")
        or item.get("presupuestoBase") or item.get("budgetWithoutTax")
        or item.get("valorEstimado") or item.get("importeLicitacion")
    )
    adj = parsear_importe(
        item.get("importeAdjudicacionSinIva") or item.get("importeAdjudicacion")
        or item.get("awardAmountWithoutTax") or item.get("formalizedAmountWithoutTax")
        or item.get("importeFormalizacionSinIva")
    )

    if pbl <= 0 and adj <= 0:
        return
    if pbl <= 0:
        pbl = adj

    lic_raw = (
        item.get("numeroLicitadores") or item.get("numLicitadores")
        or item.get("ofertasRecibidas") or item.get("numberOfTenders")
    )
    licitadores = None
    if lic_raw is not None:
        nums = re.findall(r'\d+', str(lic_raw))
        if nums:
            licitadores = int(nums[0])

    ganador_raw = (
        item.get("adjudicatario") or item.get("empresaAdjudicataria")
        or item.get("contratista") or item.get("awardee")
    )
    if isinstance(ganador_raw, list) and ganador_raw:
        ganador_raw = ganador_raw[0]
    ganador = limpiar_texto(ganador_raw.get("name") if isinstance(ganador_raw, dict) else ganador_raw)

    fecha_raw = str(
        item.get("fechaAdjudicacion") or item.get("fechaFormalizacion")
        or item.get("fechaPublicacion") or item.get("awardDate")
        or item.get("publicationDate") or item.get("fecha") or ""
    )
    fecha_match = re.search(r'(\d{4}-\d{2}-\d{2})', fecha_raw)
    if not fecha_match:
        fecha_eu = re.search(r'(\d{2})/(\d{2})/(\d{4})', fecha_raw)
        fecha = f"{fecha_eu.group(3)}-{fecha_eu.group(2)}-{fecha_eu.group(1)}" if fecha_eu else fecha_raw[:10]
    else:
        fecha = fecha_match.group(1)

    estado_raw = limpiar_texto(item.get("estado") or item.get("situacion") or item.get("status") or "").lower()
    if adj > 0 or "adjudic" in estado_raw or "formaliz" in estado_raw:
        estado = "Adjudicada"
    elif "plazo" in estado_raw or "abiert" in estado_raw or "anuncio" in estado_raw or "licitaci" in estado_raw:
        estado = "En plazo"
    else:
        estado = "En evaluación"

    baja = None
    if adj > 0 and pbl >= adj:
        baja = round(((pbl - adj) / pbl) * 100, 2)

    enlace = item.get("url") or item.get("enlace") or item.get("link") or "https://www.contratacion.euskadi.eus"

    expedientes[exp_id] = {
        "id": exp_id,
        "titulo": titulo,
        "organismo": organismo,
        "territorio": detectar_territorio(organismo, titulo),
        "tipo": clasificar_tipo(titulo),
        "cpv": cpvs[0],
        "estado": estado,
        "fecha": fecha,
        "pbl": round(pbl, 2),
        "adjudicacion": round(adj, 2) if adj > 0 else None,
        "baja": baja,
        "adjudicatario": ganador if ganador else None,
        "licitadores": licitadores,
        "enlace": enlace
    }

def procesar_atom(xml_texto, expedientes):
    root = ET.fromstring(xml_texto)
    for entry in root.iter():
        if entry.tag.endswith("entry"):
            bloque = ET.tostring(entry, encoding="unicode")
            cpvs = re.findall(r'\b(71[2345]\d{5})\b', bloque)
            if not cpvs:
                continue
            titulo, enlace, fecha, exp_id = "", "https://www.contratacion.euskadi.eus", "", ""
            for child in entry:
                tag = child.tag.split("}")[-1]
                if tag == "title" and child.text:
                    titulo = limpiar_texto(child.text)
                elif tag == "id" and child.text:
                    exp_id = child.text.strip().split("/")[-1]
                elif tag == "updated" and child.text:
                    fecha = child.text[:10]
                elif tag == "link" and child.attrib.get("href"):
                    enlace = child.attrib["href"]
            pbl_m = re.search(r'TaxExclusiveAmount[^>]*>([\d.]+)<', bloque)
            adj_m = re.search(r'PayableAmount[^>]*>([\d.]+)<', bloque)
            lic_m = re.search(r'ReceivedTenderQuantity[^>]*>(\d+)<', bloque)
            org_m = re.search(r'PartyName>.*?<[^>]*Name[^>]*>([^<]+)<', bloque, re.DOTALL)
            gan_m = re.search(r'WinningParty>.*?<[^>]*Name[^>]*>([^<]+)<', bloque, re.DOTALL)

            pbl = float(pbl_m.group(1)) if pbl_m else 0.0
            adj = float(adj_m.group(1)) if adj_m else 0.0
            if pbl <= 0 and adj > 0:
                pbl = adj
            if not titulo or pbl <= 0:
                continue
            organismo = limpiar_texto(org_m.group(1)) if org_m else "Sector Público Vasco"
            ganador = limpiar_texto(gan_m.group(1)) if gan_m else None
            licitadores = int(lic_m.group(1)) if lic_m else None
            baja = round(((pbl - adj) / pbl) * 100, 2) if (adj > 0 and pbl >= adj) else None

            expedientes[exp_id or titulo[:40]] = {
                "id": exp_id or str(abs(hash(titulo))),
                "titulo": titulo,
                "organismo": organismo,
                "territorio": detectar_territorio(organismo, titulo),
                "tipo": clasificar_tipo(titulo),
                "cpv": cpvs[0],
                "estado": "Adjudicada" if adj > 0 else "En plazo",
                "fecha": fecha,
                "pbl": round(pbl, 2),
                "adjudicacion": round(adj, 2) if adj > 0 else None,
                "baja": baja,
                "adjudicatario": ganador,
                "licitadores": licitadores,
                "enlace": enlace
            }

def procesar():
    expedientes = {}

    # 1. Descargar ficheros JSON estáticos oficiales de Open Data Euskadi (2023 - 2026)
    for url in URLS_OPENDATA_EUSKADI:
        try:
            print(f"Probando Open Data Euskadi: {url}")
            raw = descargar_raw(url)
            # Limpiar posible envoltura JSONP (jsonCallback(...))
            raw_clean = re.sub(r'^[a-zA-Z0-9_]+\((.*)\);?\s*$', r'\1', raw.strip(), flags=re.DOTALL)
            data = json.loads(raw_clean)
            lista = data if isinstance(data, list) else (
                data.get("items") or data.get("contratos") or data.get("licitaciones") or data.get("records") or []
            )
            print(f" -> OK: {len(lista)} registros descargados")
            for item in lista:
                if isinstance(item, dict):
                    procesar_item_json(item, expedientes)
        except Exception as e:
            print(f" -> No disponible ({url}): {e}")

    # 2. Descargar sindicación ATOM en vivo de Contratación Pública en Euskadi
    for feed_url in FEEDS_ATOM_EUSKADI:
        try:
            print(f"Probando Feed ATOM Euskadi: {feed_url}")
            xml_txt = descargar_raw(feed_url)
            procesar_atom(xml_txt, expedientes)
            print(f" -> Feed ATOM procesado correctamente")
        except Exception as e:
            print(f" -> Aviso Feed ATOM ({feed_url}): {e}")

    lista_final = sorted(expedientes.values(), key=lambda x: x["fecha"] or "", reverse=True)
    salida = {
        "ultima_actualizacion": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total_registros": len(lista_final),
        "licitaciones": lista_final
    }

    with open("licitaciones.json", "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)

    print(f"TOTAL FINAL GUARDADO: {len(lista_final)} expedientes de arquitectura.")

if __name__ == "__main__":
    procesar()
