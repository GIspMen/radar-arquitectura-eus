import urllib.request
import json
import os
import re
from datetime import datetime

# Códigos CPV de Arquitectura, Urbanismo, Dirección de Obra y Asistencia Técnica
CPVS_ARQUITECTURA = ("712", "713", "714", "715")

# Endpoints oficiales de Open Data Euskadi (KontratazioA)
API_CONTRATOS = "https://api.euskadi.eus/procurements/contracts"
API_ANUNCIOS = "https://api.euskadi.eus/procurements/contracting-notices"

def limpiar_texto(texto):
    if not texto:
        return ""
    return re.sub(r'\s+', ' ', str(texto)).strip()

def es_arquitectura(cpvs_lista):
    if not cpvs_lista:
        return False
    for cpv in cpvs_lista:
        codigo = str(cpv.get("code", "") if isinstance(cpv, dict) else cpv)
        if codigo.startswith(CPVS_ARQUITECTURA):
            return True
    return False

def clasificar_tipo(titulo, cpvs):
    t = titulo.lower()
    if "urbaniza" in t or "plan general" in t or "plan parcial" in t or "planeamiento" in t or "pgou" in t:
        return "Urbanismo y Planeamiento"
    if "dirección de obra" in t or "direccion de obra" in t or "coordinación de seguridad" in t:
        return "Dirección de Obra"
    if "asistencia técnica" in t or "asistencia tecnica" in t or "consultoría" in t:
        return "Asistencia Técnica"
    return "Redacción de Proyecto"

def detectar_territorio(organismo, titulo):
    texto = (organismo + " " + titulo).lower()
    if any(k in texto for k in ["bizkaia", "vizcaya", "bilbao", "getxo", "barakaldo", "durango", "basauri", "leioa", "portugalete", "santurtzi", "gernika", "bermeo"]):
        return "Bizkaia"
    if any(k in texto for k in ["gipuzkoa", "guipúzcoa", "donostia", "san sebastián", "san sebastian", "irun", "errenteria", "eibar", "zarautz", "arrasate", "tolosa", "hernani"]):
        return "Gipuzkoa"
    if any(k in texto for k in ["araba", "álava", "alava", "vitoria", "gasteiz", "llodio", "laudio", "amurrio"]):
        return "Álava"
    return "Euskadi (Autonómico)"

def obtener_datos_api(url_base, desde_fecha="2023-01-01", max_paginas=80):
    resultados = []
    for pagina in range(1, max_paginas + 1):
        url = f"{url_base}?currentPage={pagina}&itemsOfPage=50&fromDate={desde_fecha}"
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "RadarArquitecturaEuskadi/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                items = data.get("items", [])
                if not items:
                    break
                resultados.extend(items)
                if len(items) < 50:
                    break
        except Exception as e:
            print(f"Aviso en página {pagina} ({url_base}): {e}")
            break
    return resultados

def procesar():
    print("Descargando datos desde 2023 de KontratazioA (Open Data Euskadi)...")
    raw_contratos = obtener_datos_api(API_CONTRATOS, desde_fecha="2023-01-01", max_paginas=150)
    raw_anuncios = obtener_datos_api(API_ANUNCIOS, desde_fecha="2025-01-01", max_paginas=60)

    expedientes = {}

    # 1. Procesar licitaciones activas / anuncios
    for item in raw_anuncios:
        cpvs = item.get("cpvs", [])
        if not es_arquitectura(cpvs):
            continue
        exp_id = item.get("code") or item.get("id")
        titulo = limpiar_texto(item.get("title", {}).get("es") if isinstance(item.get("title"), dict) else item.get("title", ""))
        organismo = limpiar_texto(item.get("contractingAuthority", {}).get("name", {}).get("es") if isinstance(item.get("contractingAuthority"), dict) else "Sector Público Vasco")
        pbl = float(item.get("budgetWithoutTax") or item.get("estimatedValue") or 0)
        fecha = (item.get("publicationDate") or "")[:10]

        if not exp_id or pbl <= 0:
            continue

        expedientes[exp_id] = {
            "id": str(exp_id),
            "titulo": titulo,
            "organismo": organismo,
            "territorio": detectar_territorio(organismo, titulo),
            "tipo": clasificar_tipo(titulo, cpvs),
            "cpv": str(cpvs[0].get("code") if isinstance(cpvs[0], dict) else cpvs[0]),
            "estado": "En plazo",
            "fecha": fecha,
            "pbl": round(pbl, 2),
            "adjudicacion": None,
            "baja": None,
            "adjudicatario": None,
            "licitadores": None,
            "enlace": item.get("url", "https://www.contratacion.euskadi.eus")
        }

    # 2. Procesar contratos adjudicados / formalizados (2023 - actualidad)
    for item in raw_contratos:
        cpvs = item.get("cpvs", [])
        if not es_arquitectura(cpvs):
            continue
        exp_id = item.get("code") or item.get("id")
        titulo = limpiar_texto(item.get("title", {}).get("es") if isinstance(item.get("title"), dict) else item.get("title", ""))
        organismo = limpiar_texto(item.get("contractingAuthority", {}).get("name", {}).get("es") if isinstance(item.get("contractingAuthority"), dict) else "Sector Público Vasco")
        pbl = float(item.get("budgetWithoutTax") or item.get("tenderAmount") or 0)
        adj = float(item.get("awardAmountWithoutTax") or item.get("formalizedAmountWithoutTax") or 0)
        licitadores = item.get("numberOfTenders") or item.get("offersReceived")
        ganador = item.get("awardee", {}).get("name") if isinstance(item.get("awardee"), dict) else item.get("awardeeName")
        fecha = (item.get("awardDate") or item.get("formalizationDate") or "")[:10]

        if not exp_id or pbl <= 0:
            continue

        baja = None
        if adj > 0 and pbl >= adj:
            baja = round(((pbl - adj) / pbl) * 100, 2)

        expedientes[exp_id] = {
            "id": str(exp_id),
            "titulo": titulo,
            "organismo": organismo,
            "territorio": detectar_territorio(organismo, titulo),
            "tipo": clasificar_tipo(titulo, cpvs),
            "cpv": str(cpvs[0].get("code") if isinstance(cpvs[0], dict) else cpvs[0]),
            "estado": "Adjudicada" if adj > 0 else "En evaluación",
            "fecha": fecha,
            "pbl": round(pbl, 2),
            "adjudicacion": round(adj, 2) if adj > 0 else None,
            "baja": baja,
            "adjudicatario": limpiar_texto(ganador) if ganador else None,
            "licitadores": int(licitadores) if licitadores is not None else None,
            "enlace": item.get("url", "https://www.contratacion.euskadi.eus")
        }

    lista_final = sorted(expedientes.values(), key=lambda x: x["fecha"] or "", reverse=True)
    salida = {
        "ultima_actualizacion": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total_registros": len(lista_final),
        "licitaciones": lista_final
    }

    with open("licitaciones.json", "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)

    print(f"Guardados {len(lista_final)} expedientes de arquitectura en licitaciones.json")

if __name__ == "__main__":
    procesar()
