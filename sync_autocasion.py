#!/usr/bin/env python3
import html
import json
import os
import re
from urllib.parse import urljoin, urlparse

import requests

DEALER_URL = "https://www.autocasion.com/profesional/r-m-sportwagen-adv-66fbf2062815a5"
BASE_URL = "https://www.autocasion.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)",
    "Accept-Language": "es-ES,es;q=0.9",
}

DROP_STOCK_NAMES = frozenset({"MINI One 3P"})
SWIFT_ILUSTRATIVAS_URLS = [
    "https://upload.wikimedia.org/wikipedia/commons/thumb/9/9a/2018_Suzuki_Swift_SZ5_Boosterjet_SHVS_1.0_Front.jpg/1280px-2018_Suzuki_Swift_SZ5_Boosterjet_SHVS_1.0_Front.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/0/08/2020_Suzuki_Swift_Facelift_IMG_1880.jpg/1280px-2020_Suzuki_Swift_Facelift_IMG_1880.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/2/2c/2020_Suzuki_Swift_Facelift_IMG_1884.jpg/1280px-2020_Suzuki_Swift_Facelift_IMG_1884.jpg",
]

def swift_reference_urls_extra() -> list[str]:
    u = list(SWIFT_ILUSTRATIVAS_URLS)
    u.append("https://images.unsplash.com/photo-1541899481282-d53bffe3c35d?auto=format&w=1200&q=80")
    return u


def normalize_image_url(url: str) -> str:
    return (
        url.replace(
            "/unsafe/500x/filters:watermark(watermark.png,-30,-30,0):format(jpeg):quality(80)/",
            "/unsafe/1400x/",
        ).replace("/unsafe/120x68/", "/unsafe/1400x/")
    )


def price_for_sort(car: dict) -> int:
    ficha = car.get("ficha") if isinstance(car.get("ficha"), dict) else {}
    schema = ficha.get("schema") if isinstance(ficha.get("schema"), dict) else {}
    raw = schema.get("price_eur")
    if raw is not None:
        text = "".join(ch for ch in str(raw).replace(",", ".") if ch.isdigit() or ch == ".")
        if text:
            try:
                val = float(text)
                return int(round(val))
            except ValueError:
                pass
    euro = "".join(ch for ch in str(car.get("price", "")) if ch.isdigit())
    try:
        return int(euro) if euro else 0
    except ValueError:
        return 0


def apply_swift_orientation_photos(cars: list[dict]) -> None:
    for car in cars:
        name_upper = str(car.get("name", "")).upper()
        if "SUZUKI" not in name_upper or "SWIFT" not in name_upper:
            continue
        if not car.get("image_urls"):
            car["image_urls"] = swift_reference_urls_extra()
            car["imagenes_orientativas"] = True
        else:
            car["imagenes_orientativas"] = False


def enrich_missing_specs_from_public_sources(cars: list[dict]) -> None:
    """Completa campos vacíos con datos técnicos públicos cuando faltan en el anuncio."""
    for car in cars:
        name = str(car.get("name", "")).upper()
        ficha = car.get("ficha") if isinstance(car.get("ficha"), dict) else {}
        if not isinstance(ficha, dict):
            continue
        tech = ficha.get("ficha_tecnica") if isinstance(ficha.get("ficha_tecnica"), dict) else {}
        if not isinstance(tech, dict):
            continue

        if "PORSCHE" in name and "MACAN GTS" in name:
            current = tech.get("Consumos y emisiones")
            if not isinstance(current, list):
                current = []
            joined = " | ".join(current)
            if "0,00 litros/100 km" in joined or "CO2 (g/km) - g/km" in joined:
                tech["Consumos y emisiones"] = [
                    "Consumo urbano (NEDC) 12,30 litros/100 km",
                    "Consumo en carretera (NEDC) 8,40 litros/100 km",
                    "Consumo combinado (NEDC) 9,90 litros/100 km",
                    "CO2 combinado (NEDC) 225 g/km",
                    "Consumo combinado (WLTP) 11,7 - 11,3 litros/100 km",
                    "CO2 combinado (WLTP) 265 - 255 g/km",
                    "Depósito de combustible (l) 65,00 litros",
                ]
                ficha["nota_datos_completados"] = (
                    "Consumos y emisiones completados con especificaciones públicas del fabricante "
                    "para Macan GTS 2.9 V6 biturbo (NEDC/WLTP)."
                )

        if "MINI" in name and "COOPER SE" in name:
            current = tech.get("Consumos y emisiones")
            if not isinstance(current, list):
                current = []
            joined = " | ".join(current)
            if "0,00 litros/100 km" in joined or "CO2 (g/km) - g/km" in joined:
                tech["Consumos y emisiones"] = [
                    "Consumo eléctrico combinado (WLTP) 17,6 - 15,2 kWh/100 km",
                    "Consumo eléctrico combinado (NEDC) 16,1 - 14,9 kWh/100 km",
                    "Emisiones CO2 0 g/km",
                    "Autonomía eléctrica (WLTP) 203 - 234 km",
                    "Batería bruta 32,60 kWh",
                    "Batería útil 28,90 kWh",
                ]
                ficha["nota_datos_completados"] = (
                    "Consumos eléctricos completados con datos técnicos públicos del modelo MINI Cooper SE."
                )


def scrape_stock() -> list[dict]:
    source = requests.get(DEALER_URL, headers=HEADERS, timeout=30).text
    articles = re.findall(r'<article class="anuncio[\s\S]*?</article>', source)
    cars = []

    for article in articles:
        name_match = re.search(r'<h2 itemprop="name">([\s\S]*?)</h2>', article)
        if not name_match:
            continue

        name = " ".join(html.unescape(name_match.group(1)).split())
        path_match = re.search(r'data-path="([^"]+)"', article)
        path = path_match.group(1) if path_match else ""

        price_match = re.search(r'<p class="precio">([\s\S]*?)</p>', article)
        price = ""
        if price_match:
            text = re.sub(r"<[^>]+>", " ", price_match.group(1))
            price = " ".join(html.unescape(text).split())

        details = [
            " ".join(html.unescape(item).split())
            for item in re.findall(r"<li>([^<]+)</li>", article)
        ]

        image_urls = []
        for item in re.findall(r'<img[^>]+(?:data-src|src)="([^"]+)"', article):
            image_url = html.unescape(item)
            if image_url.startswith("//"):
                image_url = "https:" + image_url
            if not image_url.startswith("http"):
                image_url = urljoin(BASE_URL, image_url)
            if "/ad/" in image_url and image_url not in image_urls:
                image_urls.append(image_url)

        cars.append(
            {
                "name": name,
                "url": urljoin(BASE_URL, path),
                "price": price,
                "details": details[:4],
                "image_urls": image_urls,
            }
        )

    return cars


def tidy_html_fragment(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    return " ".join(html.unescape(text).split())


def parse_jsonld_product(page: str) -> dict | None:
    match = re.search(r'<script type="application/ld\+json">([\s\S]*?)</script>', page)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("@type") != "Product":
        return None
    offers = data.get("offers") if isinstance(data.get("offers"), dict) else {}
    brand = data.get("brand")
    brand_name = brand.get("name") if isinstance(brand, dict) else brand
    return {
        "name": data.get("name"),
        "brand": brand_name,
        "color": data.get("color"),
        "short_description": data.get("description"),
        "price_eur": offers.get("price"),
    }


def parse_ad_detail_page(page: str) -> dict:
    """Extrae bloques de la ficha de anuncio (HTML estático de Autocasión)."""
    ficha: dict = {}

    schema = parse_jsonld_product(page)
    if schema:
        ficha["schema"] = schema

    basic = re.search(r'<ul class="datos-basicos-ficha">([\s\S]*?)</ul>', page)
    if basic:
        lis = re.findall(r"<li[^>]*>([\s\S]*?)</li>", basic.group(1))
        ficha["datos_basicos"] = [tidy_html_fragment(li) for li in lis if tidy_html_fragment(li)]

    price_block = re.search(r'<ul class="tabla-precio">([\s\S]*?)</ul>', page)
    rows: list[dict[str, str]] = []
    if price_block:
        for chunk in re.findall(r"<li[^>]*>([\s\S]*?)</li>", price_block.group(1)):
            chunk = re.sub(r'<p class="nota"[\s\S]*?</p>', "", chunk)
            spans = re.findall(r"<span[^>]*>([\s\S]*?)</span>", chunk)
            texts: list[str] = []
            for span in spans:
                if "tooltip" in span and "icon-exclamacion" in span:
                    continue
                clean = tidy_html_fragment(span)
                if not clean or clean.startswith("PVP orientativo"):
                    continue
                texts.append(clean)
            line = tidy_html_fragment(chunk)
            if not texts and line:
                rows.append({"label": line, "value": ""})
            elif texts:
                label = texts[0].rstrip(":")
                value = " ".join(texts[1:]) if len(texts) > 1 else ""
                if not value and line:
                    rest = line.replace(label, "", 1).strip().lstrip(":").strip()
                    if rest:
                        value = rest
                rows.append({"label": label, "value": value})
    ficha["precio_detalle"] = rows

    equipment: dict[str, list[str]] = {}
    for match in re.finditer(r'<ul class="equipamiento (tab-equ-\d+)"[^>]*>([\s\S]*?)</ul>', page):
        inner = match.group(2)
        lis = re.findall(r"<li([^>]*)>([\s\S]*?)</li>", inner)
        if not lis:
            continue
        header = tidy_html_fragment(lis[0][1])
        items: list[str] = []
        for _, body in lis[1:]:
            line = tidy_html_fragment(re.sub(r"<span[^>]*>SERIE</span>", " (serie)", body))
            line = tidy_html_fragment(re.sub(r"<span[^>]*>OPCIONAL</span>", " Opcional:", line))
            line = tidy_html_fragment(line.replace("Opcional:", " Opcional "))
            if line:
                items.append(line)
        if header:
            equipment[header] = items
    ficha["equipamiento"] = equipment

    extras: list[str] = []
    modal_extra = re.search(r'id="modalExtra"[\s\S]*?<div class="modal-body">([\s\S]*?)</div>', page)
    if modal_extra:
        extras = [
            tidy_html_fragment(x)
            for x in re.findall(r"<li[^>]*>([\s\S]*?)</li>", modal_extra.group(1))
            if tidy_html_fragment(x)
        ]
    ficha["extras_seleccionados"] = extras

    ficha_tecnica: dict[str, list[str]] = {}
    tabs_match = re.search(
        r'<div class="bloque ficha-tecnica">([\s\S]*?)<div class="ver-completa">',
        page,
    )
    if tabs_match:
        tabs_block = tabs_match.group(1)
        titles: dict[int, str] = {}
        for num, fragment in re.findall(
            r'<li[^>]*class="[^"]*tab-spec-(\d+)[^"]*"[^>]*>([\s\S]*?)</li>',
            tabs_block,
        ):
            titles[int(num)] = tidy_html_fragment(fragment)
        for i in range(1, 12):
            spec = re.search(
                rf'<ul class="tab-spec-{i}(?: active)?">([\s\S]*?)</ul>',
                tabs_block,
            )
            if not spec:
                continue
            lines = []
            for li_inner in re.findall(r"<li[^>]*>([\s\S]*?)</li>", spec.group(1)):
                t = tidy_html_fragment(li_inner)
                if t:
                    lines.append(t)
            key = titles.get(i, f"Apartado {i}")
            if lines:
                ficha_tecnica[key] = lines
    ficha["ficha_tecnica"] = ficha_tecnica

    desc_match = re.search(
        r'<div class="comentarios">\s*<h2>\s*Descripción\s*</h2>\s*([\s\S]*?)</div>\s*<div class="ofertas">',
        page,
        re.IGNORECASE,
    )
    if desc_match:
        chunk = desc_match.group(1)
        paragraphs = []
        for attrs, inner in re.findall(r"<p([^>]*)>([\s\S]*?)</p>", chunk):
            if 'class="ref"' in attrs or "class='ref'" in attrs:
                ref_line = tidy_html_fragment(inner)
                if ref_line:
                    ficha["referencia_publicado"] = ref_line
                continue
            text = tidy_html_fragment(inner)
            if text:
                paragraphs.append(text)
        ficha["descripcion"] = paragraphs

    dealer_block = re.search(r'<div class="datos-concesionario">([\s\S]*?)</div>\s*<div class="contactar-concesionario', page)
    dealer_data: dict[str, str] = {}
    if dealer_block:
        block = dealer_block.group(1)
        dealer_name = re.search(r"<p>\s*<a[^>]*>([\s\S]*?)</a>", block)
        dealer_type = re.search(r"<span>([\s\S]*?)</span>", block)
        dealer_profile = re.search(r'<a href="([^"]+)"', block)
        dealer_more = re.search(r'<a class="saber-mas" href="([^"]+)"', block)
        if dealer_name:
            dealer_data["nombre"] = tidy_html_fragment(dealer_name.group(1))
        if dealer_type:
            dealer_data["tipo"] = tidy_html_fragment(dealer_type.group(1))
        if dealer_profile:
            dealer_data["perfil_url"] = urljoin(BASE_URL, html.unescape(dealer_profile.group(1)))
        if dealer_more:
            dealer_data["mas_info_url"] = urljoin(BASE_URL, html.unescape(dealer_more.group(1)))

    phone_match = re.search(r'data-phone="([^"]+)"', page)
    if phone_match:
        dealer_data["telefono"] = " ".join(html.unescape(phone_match.group(1)).split())

    init_payload_match = re.search(r"init\('([^']+\\{&quot;scope&quot;:&quot;ficha-general&quot;[^']*)'\)", page)
    if not init_payload_match:
        init_payload_match = re.search(r"init\('([^']+)'\)", page)
    if init_payload_match:
        raw = html.unescape(init_payload_match.group(1))
        json_match = re.search(r"(\{[\s\S]*\})", raw)
        if json_match:
            try:
                analytics_data = json.loads(json_match.group(1))
                if isinstance(analytics_data, dict):
                    compact = {}
                    for key in [
                        "type_ad",
                        "type_advertiser",
                        "price",
                        "brand",
                        "family",
                        "province",
                        "fuel",
                    ]:
                        if key in analytics_data:
                            compact[key] = analytics_data[key]
                    if compact:
                        ficha["metadatos_anuncio"] = compact
            except json.JSONDecodeError:
                pass

    if dealer_data:
        ficha["vendedor"] = dealer_data

    ficha["fuente_autoocasion"] = True

    return ficha


def fetch_car_ficha(car_url: str) -> dict:
    try:
        response = requests.get(car_url, headers=HEADERS, timeout=35)
        if response.status_code != 200:
            return {}
        return parse_ad_detail_page(response.text)
    except requests.RequestException:
        return {}


def download_images(cars: list[dict]) -> None:
    os.makedirs("assets/autocasion", exist_ok=True)
    for index, car in enumerate(cars, 1):
        car_dir = f"assets/autocasion/coche-{index:02d}"
        os.makedirs(car_dir, exist_ok=True)

        local_images = []
        for image_index, image_url in enumerate(car["image_urls"], 1):
            saved = False
            for candidate in [normalize_image_url(image_url), image_url]:
                ext = os.path.splitext(urlparse(candidate).path)[1] or ".jpg"
                output = f"{car_dir}/foto-{image_index:03d}{ext}"
                try:
                    response = requests.get(candidate, headers=HEADERS, timeout=30)
                    if response.status_code == 200 and response.content:
                        with open(output, "wb") as file:
                            file.write(response.content)
                        local_images.append(output)
                        saved = True
                        break
                except requests.RequestException:
                    continue
            if not saved:
                continue

        car["images"] = local_images


def find_matching_div_end(page: str, div_open_index: int) -> int | None:
    content_start = page.find(">", div_open_index) + 1
    depth = 1
    pos = content_start
    length = len(page)
    while depth > 0 and pos < length:
        next_open = page.find("<div", pos)
        next_close = page.find("</div>", pos)
        if next_close == -1:
            return None
        if next_open != -1 and next_open < next_close:
            depth += 1
            pos = next_open + 4
        else:
            depth -= 1
            if depth == 0:
                return next_close + len("</div>")
            pos = next_close + len("</div>")
    return None


def update_index_html(cars: list[dict]) -> None:
    with open("index.html", encoding="utf-8") as file:
        source = file.read()

    cards = []
    for idx, car in enumerate(cars):
        images = car.get("images", [])
        primary = (
            images[0]
            if images
            else "https://images.unsplash.com/photo-1493238792000-8113da705763?auto=format&fit=crop&w=1000&q=80"
        )
        details = " · ".join(car.get("details", []))
        gallery = "".join(
            [
                f'<img src="{html.escape(path)}" alt="{html.escape(car["name"])} foto {position + 1}" loading="lazy">'
                for position, path in enumerate(images)
            ]
        )
        orient = car.get("imagenes_orientativas")
        badge_label = f"{len(images)} FOTOS · ORIENTATIVAS" if orient else f"{len(images)} FOTOS"

        cards.append(
            f"""
        <article class="car-card" data-type="stock-real" data-stock-index="{idx}">
          <div class="car-image">
            <img src="{html.escape(primary)}" alt="{html.escape(car["name"])}">
            <div class="car-overlay">
              <span class="car-overlay-hint">Pulse para ver la ficha completa</span>
            </div>
          </div>
          <div class="car-body">
            <div class="car-title-wrap">
              <h3 class="car-name">{html.escape(car["name"])}</h3>
            </div>
            <div class="car-bottom-meta">
              <span class="badge">{html.escape(badge_label)}</span>
              <p class="price">{html.escape(car.get("price", ""))}</p>
              <p style="color:#bdbdbd;font-size:.9rem;">{html.escape(details)}</p>
            </div>
            <div class="mini-gallery">{gallery if gallery else '<p style="color:#888;">Sin fotos disponibles en el anuncio.</p>'}</div>
          </div>
        </article>"""
        )

    filters = """<div class="catalog-filters">
        <button class="filter-btn active" data-filter="all">Stock completo disponible</button>
      </div>"""

    grid = f"""<div class="cars-grid" id="carsGrid">
{''.join(cards)}
      </div>"""

    catalog_filters = source.find('<div class="catalog-filters">')
    cars_grid = source.find('<div class="cars-grid" id="carsGrid">')
    if catalog_filters != -1 and cars_grid != -1:
        grid_end = find_matching_div_end(source, cars_grid)
        if grid_end is not None:
            new_block = filters + "\n      " + grid
            source = source[:catalog_filters] + new_block + source[grid_end:]

    if ".mini-gallery {" not in source:
        css = """

    .mini-gallery {
      margin-top: 0.5rem;
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(70px, 1fr));
      gap: 0.35rem;
      max-height: 220px;
      overflow: auto;
      padding-right: 0.15rem;
    }

    .mini-gallery img {
      width: 100%;
      aspect-ratio: 4/3;
      object-fit: cover;
      border-radius: 8px;
      border: 1px solid var(--line);
    }
"""
        source = source.replace(
            """    .car-body {
      padding: 1rem;
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
      flex: 1;
    }""",
            """    .car-body {
      padding: 1rem;
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
      flex: 1;
    }"""
            + css,
        )

    source = source.replace(
        """    const filterBtns = document.querySelectorAll(".filter-btn");
    const carCards = document.querySelectorAll(".car-card");
    filterBtns.forEach((btn) => {
      btn.addEventListener("click", () => {
        filterBtns.forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        const selected = btn.dataset.filter;
        carCards.forEach((card) => {
          const types = card.dataset.type;
          const visible = selected === "all" || types.includes(selected);
          card.style.display = visible ? "flex" : "none";
        });
      });
    });""",
        "",
    )

    compact = [
        {
            "url": vehicle["url"],
            "name": vehicle["name"],
            "ficha": vehicle.get("ficha") or {},
            "imagenes_orientativas": bool(vehicle.get("imagenes_orientativas")),
        }
        for vehicle in cars
    ]
    script_block = (
        '\n  <script type="application/json" id="stockDetailsData">'
        + json.dumps({"cars": compact}, ensure_ascii=False)
        + "</script>\n"
    )
    if 'id="stockDetailsData"' in source:
        source = re.sub(
            r"<script[^>]*id=\"stockDetailsData\"[^>]*>[\s\S]*?</script>",
            script_block.strip(),
            source,
            count=1,
        )
    else:
        source = source.replace('<div id="carDetailModal"', script_block + '  <div id="carDetailModal"')

    with open("index.html", "w", encoding="utf-8") as file:
        file.write(source)


def main() -> None:
    cars = scrape_stock()
    cars = [c for c in cars if str(c.get("name", "")).strip() not in DROP_STOCK_NAMES]
    for vehicle in cars:
        vehicle["ficha"] = fetch_car_ficha(vehicle["url"])
    enrich_missing_specs_from_public_sources(cars)
    cars.sort(key=price_for_sort, reverse=True)
    apply_swift_orientation_photos(cars)
    download_images(cars)

    with open("autocasion_stock.json", "w", encoding="utf-8") as file:
        json.dump({"source_url": DEALER_URL, "cars": cars}, file, ensure_ascii=False, indent=2)

    update_index_html(cars)
    print(f"Coches: {len(cars)}")
    print(f"Fotos descargadas: {sum(len(car.get('images', [])) for car in cars)}")


if __name__ == "__main__":
    main()
