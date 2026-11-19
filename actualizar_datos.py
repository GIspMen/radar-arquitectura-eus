import urllib.request
import urllib.parse
import json
import re
from datetime import datetime

# Códigos CPV de Arquitectura, Ingeniería, Urbanismo, Dirección de Obra y Asistencia Técnica
CPVS_ARQUITECTURA = ("712", "713", "714", "715")

# API oficial de Open Data Euskadi (Catálogo CKAN + API KontratazioA)
CKAN_SEARCH = "https://opendata.euskadi.eus/api/3/action/package_search?q=contrataciones+administrativas"
API_KONTRATAZIOA = "https://api.euskadi.eus/procurements"

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (compatible; RadarArquitecturaEuskadi/2.0)"
}

def limpiar_texto(texto):
    if not texto:
        return ""
    if isinstance(texto, dict):
        texto = texto.get("es") or texto.get("eu") or next(iter(texto.values()), "")
    return re.sub(r'\s+', ' ', str(texto)).strip()

def extraer_cpvs(item):
    encontrados = []
    # Buscar en posibles estructuras de Open Data Euskadi
    for clave in ["cpvs", "cpv", "cpvCodes", "mainCpv", "additionalCpvs"]:
        val = item.get(clave)
        if not val:
            continue
        if isinstance(val, list):
            for x in val:
                c = x.get("code") or x.get("id") if isinstance(x, dict) else str(x)
                if c:
                    encontrados.append(re.sub(r'\D', '', str(c)))
        elif isinstance(val, dict):
            c = val.get("code") or val.get("id")
            if c:
                encontrados.append(re.sub(r'\D', '', str(c)))
        else:
            encontrados.extend(re.findall(r'\d{8}', str(val)))
    return list(dict.fromkeys(encontrados))

def es_arquitectura(cpvs_lista):
    return any(c.startswith(CPVS_ARQUITECTURA) for c in cpvs_lista)

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

def descargar_json(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))

def buscar_recursos_opendata():
    """Busca los ficheros JSON oficiales publicados en Open Data Euskadi para 2023-2026."""
    urls_json = []
    try:
        data = descargar_json(CKAN_SEARCH)
        paquetes = data.get("result", {}).get("results", [])
        for pkg in paquetes:
            nombre_pkg = pkg.get("name", "")
            if any(anio in nombre_pkg for anio in ["2023", "2024", "2025", "2026"]):
                for res in pkg.get("resources", []):
                    fmt = (res.get("format") or "").upper()
                    url = res.get("url") or ""
                    if fmt == "JSON" or url.lower().endswith(".json"):
                        urls_json.append((nombre_pkg, url))
    except Exception as e:
        print(f"Aviso buscando en catálogo Open Data: {e}")
    return urls_json

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

def procesar_registro(item, expedientes):
    cpvs = extraer_cpvs(item)
    if not es_arquitectura(cpvs):
        return

    exp_id = str(item.get("code") or item.get("expedientCode") or item.get("id") or item.get("numeroExpediente") or "")
    titulo = limpiar_texto(item.get("title") or item.get("objeto") or item.get("name") or "")
    if not titulo:
        return
    if not exp_id:
        exp_id = str(abs(hash(titulo)))

    organismo = limpiar_texto(
        (item.get("contractingAuthority") or {}).get("name")
        if isinstance(item.get("contractingAuthority"), dict)
        else (item.get("entidadContratante") or item.get("organoContratacion") or "Sector Público Vasco")
    )

    pbl = parsear_importe(
        item.get("budgetWithoutTax") or item.get("estimatedValue") or item.get("presupuestoSinIva") or item.get("presupuestoBase")
    )
    adj = parsear_importe(
        item.get("awardAmountWithoutTax") or item.get("formalizedAmountWithoutTax") or item.get("importeAdjudicacionSinIva") or item.get("importeAdjudicacion")
    )

    if pbl <= 0 and adj <= 0:
        return
    if pbl <= 0:
        pbl = adj

    licitadores_raw = item.get("numberOfTenders") or item.get("offersReceived") or item.get("numeroLicitadores") or item.get("licitadores")
    licitadores = None
    if licitadores_raw is not None:
        nums = re.findall(r'\d+', str(licitadores_raw))
        if nums:
            licitadores = int(nums[0])

    ganador_raw = item.get("awardee") or item.get("adjudicatario") or item.get("awardees")
    if isinstance(ganador_raw, list) and ganador_raw:
        ganador_raw = ganador_raw[0]
    ganador = limpiar_texto(ganador_raw.get("name") if isinstance(ganador_raw, dict) else ganador_raw)

    fecha_raw = str(
        item.get("awardDate") or item.get("formalizationDate") or item.get("publicationDate")
        or item.get("fechaAdjudicacion") or item.get("fechaPublicacion") or ""
    )
    fecha_match = re.search(r'(\d{4}-\d{2}-\d{2})', fecha_raw)
    fecha = fecha_match.group(1) if fecha_match else fecha_raw[:10]

    estado_raw = limpiar_texto(item.get("status") or item.get("estado") or "").lower()
    if adj > 0 or "adjudic" in estado_raw or "formaliz" in estado_raw:
        estado = "Adjudicada"
    elif "plazo" in estado_raw or "abiert" in estado_raw or "anuncio" in estado_raw:
        estado = "En plazo"
    else:
        estado = "En evaluación"

    baja = None
    if adj > 0 and pbl >= adj:
        baja = round(((pbl - adj) / pbl) * 100, 2)

    enlace = item.get("url") or item.get("enlace") or "https://www.contratacion.euskadi.eus"

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

def procesar():
    expedientes = {}

    # 1. Descargar datasets anuales de Open Data Euskadi (2023 - 2026)
    recursos = buscar_recursos_opendata()
    print(f"Recursos JSON encontrados en Open Data Euskadi: {len(recursos)}")
    for nombre_pkg, url in recursos:
        try:
            print(f"Descargando {nombre_pkg}: {url}")
            contenido = descargar_json(url)
            lista = contenido if isinstance(contenido, list) else (
                contenido.get("items") or contenido.get("contratos") or contenido.get("records") or []
            )
            print(f" -> {len(lista)} registros totales en {nombre_pkg}")
            for item in lista:
                if isinstance(item, dict):
                    procesar_registro(item, expedientes)
        except Exception as e:
            print(f"Error leyendo {url}: {e}")

    # 2. Consultar también la API en vivo de KontratazioA para licitaciones recientes / abiertas
    for endpoint in ["contracts", "contracting-notices", "tenders"]:
        for pagina in range(1, 25):
            url = f"{API_KONTRATAZIOA}/{endpoint}?currentPage={pagina}&itemsOfPage=50"
            try:
                data = descargar_json(url)
                items = data.get("items", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                if not items:
                    break
                for item in items:
                    if isinstance(item, dict):
                        procesar_registro(item, expedientes)
                if len(items) < 50:
                    break
            except Exception:
                break

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
