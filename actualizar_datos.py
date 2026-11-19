import urllib.request
import json
import re
from datetime import datetime

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RadarArquitecturaEuskadi/4.0"
}

# Las 4 URLs oficiales verificadas de Open Data Euskadi (417.000+ registros 2023-2026)
URLS_OPENDATA_EUSKADI = [
    "https://opendata.euskadi.eus/contenidos/ds_contrataciones/contrataciones_admin_2026/opendata/contratos.json",
    "https://opendata.euskadi.eus/contenidos/ds_contrataciones/contrataciones_admin_2025/opendata/contratos.json",
    "https://opendata.euskadi.eus/contenidos/ds_contrataciones/contrataciones_admin_2024/opendata/contratos.json",
    "https://opendata.euskadi.eus/contenidos/ds_contrataciones/contrataciones_admin_2023/opendata/contratos.json",
]

PALABRAS_ARQUITECTURA = (
    "redacción de proyecto", "redaccion de proyecto", "proyecto básico", "proyecto basico",
    "proyecto de ejecución", "proyecto de ejecucion", "dirección de obra", "direccion de obra",
    "dirección facultativa", "direccion facultativa", "proyecto de urbanización", "proyecto de urbanizacion",
    "plan general", "plan parcial", "plan especial", "estudio de detalle", "planeamiento",
    "servicios de arquitectura", "asistencia técnica para la redacción", "asistencia tecnica para la redaccion",
    "concurso de proyectos", "anteproyecto"
)

def limpiar_texto(texto):
    if not texto:
        return ""
    if isinstance(texto, dict):
        texto = texto.get("es") or texto.get("eu") or next(iter(texto.values()), "")
    return re.sub(r'\s+', ' ', str(texto)).strip()

def extraer_cpvs(texto_completo):
    # Detecta 71200000, 71200000-0, 71240000, etc.
    codigos = re.findall(r'\b(71[2345]\d{5})(?:-\d)?\b', texto_completo)
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
    if val is None or isinstance(val, bool):
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, dict):
        for k in ["amount", "value", "withoutTax", "sinIva", "importe"]:
            if k in val:
                return parsear_importe(val[k])
        return 0.0
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

def buscar_valor(item, claves_posibles):
    for k in claves_posibles:
        if k in item and item[k] not in (None, "", [], {}):
            return item[k]
    return None

def procesar_item(item, expedientes):
    texto_json = json.dumps(item, ensure_ascii=False)
    cpvs = extraer_cpvs(texto_json)

    titulo = limpiar_texto(buscar_valor(item, [
        "objetoContrato", "objeto", "titulo", "title", "descripcion", "name", "asunto", "denominacion"
    ]))
    if not titulo:
        return

    # Filtrar por CPV de arquitectura/urbanismo (712-715) O por palabras clave exactas de arquitectura
    es_arq = bool(cpvs) or any(p in titulo.lower() for p in PALABRAS_ARQUITECTURA)
    if not es_arq:
        return

    exp_id = str(buscar_valor(item, [
        "codigoExpediente", "numeroExpediente", "expediente", "code", "expedientCode", "id", "referencia"
    ]) or "").strip()
    if not exp_id:
        exp_id = str(abs(hash(titulo)))

    org_raw = buscar_valor(item, [
        "entidadContratante", "organoContratacion", "poderAdjudicador", "organismo", "entidad", "contractingAuthority"
    ])
    organismo = limpiar_texto(org_raw.get("name") if isinstance(org_raw, dict) else org_raw) or "Sector Público Vasco"

    pbl = parsear_importe(buscar_valor(item, [
        "presupuestoSinIva", "presupuestoLicitacionSinIva", "presupuestoBaseSinIva",
        "presupuestoBase", "presupuestoLicitacion", "budgetWithoutTax", "valorEstimado",
        "importeLicitacion", "presupuesto"
    ]))
    adj = parsear_importe(buscar_valor(item, [
        "importeAdjudicacionSinIva", "importeFormalizacionSinIva", "importeAdjudicacion",
        "importeFormalizacion", "awardAmountWithoutTax", "formalizedAmountWithoutTax",
        "importeAdjudicado", "precioAdjudicacion"
    ]))

    if pbl <= 0 and adj <= 0:
        return
    if pbl <= 0:
        pbl = adj

    lic_raw = buscar_valor(item, [
        "numeroLicitadores", "numLicitadores", "ofertasRecibidas", "licitadores",
        "numberOfTenders", "numeroOfertas", "participantes"
    ])
    licitadores = None
    if lic_raw is not None:
        nums = re.findall(r'\d+', str(lic_raw))
        if nums:
            licitadores = int(nums[0])

    ganador_raw = buscar_valor(item, [
        "adjudicatario", "empresaAdjudicataria", "contratista", "ganador", "awardee", "adjudicatarios"
    ])
    if isinstance(ganador_raw, list) and ganador_raw:
        ganador_raw = ganador_raw[0]
    ganador = limpiar_texto(ganador_raw.get("name") if isinstance(ganador_raw, dict) else ganador_raw)

    fecha_raw = str(buscar_valor(item, [
        "fechaAdjudicacion", "fechaFormalizacion", "fechaPublicacion",
        "awardDate", "publicationDate", "fecha", "fechaContrato"
    ]) or "")
    fecha_match = re.search(r'(\d{4}-\d{2}-\d{2})', fecha_raw)
    if not fecha_match:
        fecha_eu = re.search(r'(\d{2})/(\d{2})/(\d{4})', fecha_raw)
        fecha = f"{fecha_eu.group(3)}-{fecha_eu.group(2)}-{fecha_eu.group(1)}" if fecha_eu else fecha_raw[:10]
    else:
        fecha = fecha_match.group(1)

    estado_raw = limpiar_texto(buscar_valor(item, ["estado", "situacion", "status", "fase"]) or "").lower()
    if adj > 0 or "adjudic" in estado_raw or "formaliz" in estado_raw or "resuelt" in estado_raw:
        estado = "Adjudicada"
    elif "plazo" in estado_raw or "abiert" in estado_raw or "anuncio" in estado_raw or "licitaci" in estado_raw or "publicad" in estado_raw:
        estado = "En plazo"
    else:
        estado = "En evaluación"

    baja = None
    if adj > 0 and pbl >= adj:
        baja = round(((pbl - adj) / pbl) * 100, 2)

    enlace = limpiar_texto(buscar_valor(item, ["url", "enlace", "link", "urlDetalle", "ficha"])) or "https://www.contratacion.euskadi.eus"

    expedientes[exp_id] = {
        "id": exp_id,
        "titulo": titulo,
        "organismo": organismo,
        "territorio": detectar_territorio(organismo, titulo),
        "tipo": clasificar_tipo(titulo),
        "cpv": cpvs[0] if cpvs else "71200000",
        "estado": estado,
        "fecha": fecha,
        "pbl": round(pbl, 2),
        "adjudicacion": round(adj, 2) if adj > 0 else None,
        "baja": baja,
        "adjudicatario": ganador if ganador else None,
        "licitadores": licitadores,
        "enlace": enlace
    }

def procesar():
    expedientes = {}
    muestra_impresa = False

    for url in URLS_OPENDATA_EUSKADI:
        try:
            print(f"Descargando: {url}", flush=True)
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=90) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
            raw_clean = re.sub(r'^[a-zA-Z0-9_]+\((.*)\);?\s*$', r'\1', raw.strip(), flags=re.DOTALL)
            data = json.loads(raw_clean)
            lista = data if isinstance(data, list) else (
                data.get("items") or data.get("contratos") or data.get("licitaciones") or data.get("records") or []
            )
            print(f" -> {len(lista)} registros leídos", flush=True)
            if lista and not muestra_impresa:
                print(f"Claves del primer registro: {list(lista[0].keys())}", flush=True)
                muestra_impresa = True
            for item in lista:
                if isinstance(item, dict):
                    procesar_item(item, expedientes)
        except Exception as e:
            print(f" -> Aviso ({url}): {e}", flush=True)

    lista_final = sorted(expedientes.values(), key=lambda x: x["fecha"] or "", reverse=True)
    salida = {
        "ultima_actualizacion": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total_registros": len(lista_final),
        "licitaciones": lista_final
    }

    with open("licitaciones.json", "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)

    print(f"TOTAL FINAL GUARDADO: {len(lista_final)} expedientes de arquitectura.", flush=True)

if __name__ == "__main__":
    procesar()
