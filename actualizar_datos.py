import urllib.request
import json
import re
import time
import os
import concurrent.futures
from datetime import datetime

HEADERS = {
    "Accept": "application/json, application/xml, text/xml, */*",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RadarArquitecturaEuskadi/15.0"
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

def descargar_con_reintentos(url, timeout=90, intentos=3):
    for i in range(intentos):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="ignore")
        except Exception as e:
            if i < intentos - 1:
                time.sleep(2 * (i + 1))
            else:
                raise e
    return ""

def limpiar(val):
    if not val:
        return ""
    if isinstance(val, dict):
        val = val.get("es") or val.get("eu") or next(iter(val.values()), "")
    return " ".join(str(val).split())

def parsear_fecha(raw):
    s = limpiar(raw)
    if not s:
        return ""
    m_f = re.search(r'(\d{4}-\d{2}-\d{2})', s)
    if m_f:
        return m_f.group(1)
    m_eu = re.search(r'(\d{2})/(\d{2})/(\d{4})', s)
    if m_eu:
        return f"{m_eu.group(3)}-{m_eu.group(2)}-{m_eu.group(1)}"
    return s[:10]

def parsear_importe(val):
    if not val or isinstance(val, bool):
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = re.sub(r'[^\d.,]', '', str(val)).strip()
    if not s:
        return 0.0
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        partes = s.split(",")
        if len(partes) == 2 and len(partes[1]) == 3 and len(partes[0]) <= 3:
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    elif "." in s:
        partes = s.split(".")
        if len(partes) > 2 or (len(partes) == 2 and len(partes[1]) == 3):
            s = s.replace(".", "")
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

def detectar_territorio(geo, texto, nuts=""):
    n = nuts.upper()
    if "ES211" in n:
        return "Álava"
    if "ES212" in n:
        return "Gipuzkoa"
    if "ES213" in n:
        return "Bizkaia"
    g = (geo + " " + texto).lower()
    if any(k in g for k in (
        "bizkaia", "vizcaya", "bilbao", "getxo", "barakaldo", "durango", "basauri", "leioa",
        "portugalete", "santurtzi", "gernika", "bermeo", "erandio", "galdakao", "sestao",
        "amorebieta", "mungia", "berriz", "elorrio", "ermua", "abanto", "muskiz", "ortuella",
        "trapagaran", "sopela", "plentzia", "gorliz", "bakio", "lekeitio", "ondarroa", "markina",
        "arrigorriaga", "etxebarri", "derio", "zamudio", "sondika", "loiu", "güeñes", "zalla", "balmaseda"
    )):
        return "Bizkaia"
    if any(k in g for k in (
        "gipuzkoa", "guipúzcoa", "donostia", "san sebastián", "san sebastian", "irun", "errenteria",
        "eibar", "zarautz", "arrasate", "mondragón", "tolosa", "hernani", "bergara", "azpeitia",
        "azkoitia", "lasarte", "pasaia", "hondarribia", "oñati", "elgoibar", "andoain", "beasain",
        "ordizia", "zumarraga", "urretxu", "legazpi", "zumaia", "getaria", "orio", "mutriku", "deba",
        "oiartzun", "lezo", "astigarraga", "usurbil", "villabona", "lazkao", "aretxabaleta", "eskoriatza"
    )):
        return "Gipuzkoa"
    if any(k in g for k in (
        "araba", "álava", "alava", "vitoria", "gasteiz", "llodio", "laudio", "amurrio",
        "salvatierra", "agurain", "oyón", "oion", "laguardia", "iruña de oca", "alegria-dulantzi",
        "zuia", "zigoitia", "legutio", "aramaio", "artziniega", "ayala", "aiara"
    )):
        return "Álava"
    return "Euskadi (Autonómico)"

def clasificar_estado_oficial(estado_raw, fecha_limite, hoy_str):
    e = estado_raw.lower()
    if any(k in e for k in ("desierto", "anulado", "desistimiento", "renuncia", "cancelad")):
        return "Desierta / Anulada"
    if any(k in e for k in ("plazo cerrado", "suspension", "suspensión", "recurso")):
        return "En evaluación"
    if any(k in e for k in ("adjudic", "formaliz", "finalizado", "histórico", "historico", "ejecución", "ejecucion", "modificación", "modificacion")):
        return "Adjudicada"
    if any(k in e for k in ("abierto", "plazo de presentación", "plazo de presentacion", "alerta temprana", "anuncio previo", "en redacción", "en redaccion")):
        if fecha_limite and fecha_limite < hoy_str:
            return "En evaluación"
        return "En plazo"
    return "En evaluación"

def extraer_tag_xml(xml_txt, etiquetas):
    for tag in etiquetas:
        m = re.search(rf'<{tag}\b[^>]*>([^<]+)</{tag}>', xml_txt, re.IGNORECASE)
        if m:
            val = limpiar(m.group(1))
            if val:
                return val
    return ""

def extraer_urls_xml_item(item):
    urls = []
    for k in ("dataXML", "contratacion_adjudicacion", "contratacion_formalizacion", "physicalUrl"):
        raw = limpiar(item.get(k) or "")
        if not raw:
            continue
        encontradas = re.findall(r'(?:https?://[^\s"\'<>]+|/[^\s"\'<>]+)\.xml', raw, re.IGNORECASE)
        if encontradas:
            for u in encontradas:
                if u not in urls:
                    urls.append(u)
        elif k == "dataXML" and raw not in urls:
            urls.append(raw)
    return urls

def enriquecer_con_xml(exp):
    xml_urls = exp.pop("_xmls", [])
    exp.pop("_menor", None)
    for xml_url in xml_urls:
        if not xml_url:
            continue
        if xml_url.startswith("/"):
            xml_url = "https://opendata.euskadi.eus" + xml_url
        try:
            time.sleep(0.1)
            xml_txt = descargar_con_reintentos(xml_url, timeout=15, intentos=2)

            m_cpv = RE_CPV.search(xml_txt)
            if m_cpv:
                exp["cpv"] = m_cpv.group(1)

            nuts = extraer_tag_xml(xml_txt, ("nutsCode",))
            if nuts:
                exp["territorio"] = detectar_territorio("", exp["territorio"], nuts)

            pbl_sin = parsear_importe(extraer_tag_xml(xml_txt, (
                "budgetWithoutVAT", "estimatedValue", "presupuestoSinIva", "valorEstimado", "TaxExclusiveAmount"
            )))
            if pbl_sin > 0:
                exp["pbl"] = round(pbl_sin, 2)
            elif exp["pbl"] <= 0:
                pbl_con = parsear_importe(extraer_tag_xml(xml_txt, ("budgetWithVAT", "presupuestoConIva")))
                if pbl_con > 0:
                    exp["pbl"] = round(pbl_con / 1.21, 2)

            adj_sin = parsear_importe(extraer_tag_xml(xml_txt, (
                "awardAmountWithoutVAT", "formalizedAmountWithoutVAT", "adjudicationAmountWithoutVAT",
                "amountWithoutVAT", "importeAdjudicacionSinIva", "importeFormalizacionSinIva", "PayableAmount"
            )))
            if adj_sin > 0:
                exp["adjudicacion"] = round(adj_sin, 2)
            elif not exp["adjudicacion"]:
                adj_con = parsear_importe(extraer_tag_xml(xml_txt, (
                    "awardAmountWithVAT", "formalizedAmountWithVAT", "adjudicationAmountWithVAT",
                    "amountWithVAT", "importeAdjudicacionConIva", "awardAmount", "adjudicationAmount"
                )))
                if adj_con > 0:
                    exp["adjudicacion"] = round(adj_con / 1.21, 2)

            lic_str = extraer_tag_xml(xml_txt, (
                "numberOfTenders", "receivedTenders", "tenderersCount", "offersReceived",
                "numTenderers", "numeroLicitadores", "ofertasRecibidas", "ReceivedTenderQuantity"
            ))
            if lic_str and lic_str.isdigit() and int(lic_str) > 0:
                exp["licitadores"] = int(lic_str)

            for pat in (
                r'<(?:awardee|winningBidder|winningParty|adjudicatario|contractor|successfulTenderer|tenderer)[^>]*>.*?<(?:name|razonSocial|nombre|companyName|socialName)[^>]*>([^<]+)</',
                r'<(?:awardeeName|adjudicatario|razonSocial|winningBidderName)[^>]*>([^<]+)</'
            ):
                m_gan = re.search(pat, xml_txt, re.IGNORECASE | re.DOTALL)
                if m_gan:
                    gan = limpiar(m_gan.group(1))
                    if gan and gan.lower() != exp["organismo"].lower():
                        exp["adjudicatario"] = gan
                        break

        except Exception:
            pass

    if exp["adjudicacion"] and exp["pbl"] > 0 and exp["pbl"] >= exp["adjudicacion"]:
        baja_calc = round(((exp["pbl"] - exp["adjudicacion"]) / exp["pbl"]) * 100, 2)
        if 0 <= baja_calc <= 75:
            exp["baja"] = baja_calc
        exp["estado"] = "Adjudicada"
    elif exp["adjudicatario"] and exp["estado"] != "Desierta / Anulada":
        exp["estado"] = "Adjudicada"

    return exp

def procesar_item(item, expedientes, hoy_str):
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

    fecha = parsear_fecha(item.get("contratacion_fecha_de_publicacion_documento") or item.get("creationDate"))
    fecha_limite = parsear_fecha(item.get("contratacion_fecha_limite_presentacion"))
    est_raw = limpiar(item.get("contratacion_estado_tramitacion") or "")
    estado = clasificar_estado_oficial(est_raw, fecha_limite, hoy_str)
    es_menor = str(item.get("contratacion_contrato_menor") or "").lower() == "true"

    enlace = limpiar(item.get("friendlyUrl") or item.get("contratacion_poder_adjudicador_url") or "")
    if enlace.startswith("/"):
        enlace = "https://www.contratacion.euskadi.eus" + enlace
    elif not enlace:
        enlace = "https://www.contratacion.euskadi.eus"

    xml_urls = extraer_urls_xml_item(item)

    if exp_id in expedientes:
        prev = expedientes[exp_id]
        for u in xml_urls:
            if u not in prev["_xmls"]:
                prev["_xmls"].append(u)
        if fecha and fecha > (prev["fecha"] or ""):
            prev["fecha"] = fecha
            prev["estado"] = estado
            prev["estado_oficial"] = est_raw or estado
            prev["enlace"] = enlace
    else:
        expedientes[exp_id] = {
            "id": exp_id,
            "titulo": titulo,
            "organismo": organismo,
            "territorio": detectar_territorio(geo, f"{organismo} {t_lower}"),
            "tipo": clasificar_tipo(t_lower),
            "cpv": m_cpv.group(1) if m_cpv else "71200000",
            "estado": estado,
            "estado_oficial": est_raw or estado,
            "fecha": fecha,
            "fecha_limite": fecha_limite,
            "pbl": 0.0,
            "adjudicacion": None,
            "baja": None,
            "adjudicatario": None,
            "licitadores": None,
            "enlace": enlace,
            "_xmls": xml_urls,
            "_menor": es_menor
        }

def procesar():
    expedientes = {}
    hoy_str = datetime.now().strftime("%Y-%m-%d")

    for url in URLS_OPENDATA_EUSKADI:
        try:
            print(f"Descargando: {url}", flush=True)
            raw = descargar_con_reintentos(url, timeout=120, intentos=3).strip()
            if not raw.startswith(("[", "{")):
                raw = re.sub(r'^[a-zA-Z0-9_]+\((.*)\);?$', r'\1', raw, flags=re.DOTALL)
            data = json.loads(raw)
            lista = data if isinstance(data, list) else (data.get("items") or data.get("contratos") or data.get("records") or [])
            print(f" -> {len(lista)} registros leídos", flush=True)
            for item in lista:
                if isinstance(item, dict):
                    procesar_item(item, expedientes, hoy_str)
        except Exception as e:
            print(f" -> Error ({url}): {e}", flush=True)

    if len(expedientes) == 0:
        print("AVISO: Open Data Euskadi no devolvió registros en este intento. Se conservan los datos anteriores sin sobrescribir.", flush=True)
        return

    lista_bruta = sorted(expedientes.values(), key=lambda x: x["fecha"] or "", reverse=True)
    print(f"Expedientes únicos detectados: {len(lista_bruta)}", flush=True)

    # Imprimir etiquetas con valor de un concurso ordinario adjudicado
    for e in lista_bruta:
        if e["estado"] == "Adjudicada" and not e.get("_menor") and e.get("_xmls"):
            u = e["_xmls"][0]
            full_u = u if u.startswith("http") else "https://opendata.euskadi.eus" + u
            try:
                txt = descargar_con_reintentos(full_u, timeout=15, intentos=2)
                pares = re.findall(r'<([a-zA-Z0-9_:-]+)\b[^>]*>([^<]{1,120})</\1>', txt)
                print(f"PARES XML CONCURSO ORDINARIO ({e['id']}): {pares}", flush=True)
            except Exception as ex:
                print("Error muestra XML:", ex, flush=True)
            break

    recientes = lista_bruta[:800]
    resto = lista_bruta[800:]
    for r in resto:
        r.pop("_xmls", None)
        r.pop("_menor", None)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        recientes_ok = list(executor.map(enriquecer_con_xml, recientes))

    lista_final = recientes_ok + resto
    with open("licitaciones.json", "w", encoding="utf-8") as f:
        json.dump({
            "ultima_actualizacion": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "total_registros": len(lista_final),
            "licitaciones": lista_final
        }, f, ensure_ascii=False, indent=2)

    print(f"TOTAL FINAL GUARDADO: {len(lista_final)} expedientes.", flush=True)

if __name__ == "__main__":
    procesar()
