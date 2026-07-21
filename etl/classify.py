# -*- coding: utf-8 -*-
"""
classify.py — порт движка классификации из Google Apps Script (Скрипт_номенклатуры v8).

СТАТУС ПОРТА:
  ✅ Категория 1 (полный приоритетный каскад) — портирована и работает без внешних справочников.
  ✅ Тип металла (Черный / Нержавеющий / Цветной) — портирован.
  ⏳ Категории 2–5 и Стандарт — зависят от 5 внешних справочников
     (Никель, Медь-никель, Рельсы, Стандарты ТПА, Дюймы-DN).
     Допортируются и сверяются с эталоном после того, как вы выгрузите эти справочники в CSV
     в папку references/. Пока эти поля возвращаются пустыми.

Логика Категории 1 перенесена 1:1 из исходного скрипта (те же приоритеты, бренды, марки, регэкспы).
"""
import csv
import os
import re

re._MAXCACHE = 20000  # держим скомпилированные шаблоны в кэше (ускоряет прогон)

I = re.IGNORECASE

# ---------------------------------------------------------------- внешние справочники
_NICKEL_CODE_RE = None   # альтернация кодов/марок никеля (UNS, состав, имя сплава)
_CUNI_RE = None          # альтернация марок медь-никель
_REF_LOADED = False


def load_references(ref_dir=None):
    """Загружает справочники Никель и Медь-никель из CSV (для Категории 1 = Никель).
    W.Nr-колонку не используем: в выгрузке она местами испорчена Excel'ем (даты)."""
    global _NICKEL_CODE_RE, _CUNI_RE, _REF_LOADED
    if ref_dir is None:
        ref_dir = os.path.join(os.path.dirname(__file__), 'references')

    def read_rows(name):
        path = os.path.join(ref_dir, name)
        if not os.path.exists(path):
            return []
        with open(path, encoding='utf-8') as f:
            return [row for row in csv.reader(f)]

    nickel_tokens = set()
    for row in read_rows('nickel.csv'):
        # col0 = имя сплава (Alloy 200 / Monel K-500), col1 = состав (NiCu30Fe), col2 = UNS (N02200)
        for idx in (0, 1, 2):
            if idx < len(row):
                t = normalize_material_text(row[idx])
                # берём только распознаваемые марки/коды: длина >=4 и есть цифра
                if len(t) >= 4 and any(c.isdigit() for c in t):
                    nickel_tokens.add(t)
        # col3 = W.Nr. Excel местами испортил его в дату: "4562-01-01" = 1.4562 (месяц = префикс).
        if len(row) > 3 and row[3]:
            raw = row[3].strip()
            m = re.match(r'^(\d{4})-(\d{2})-\d{2}', raw)
            if m:  # восстановление из даты: год=4 цифры, месяц=префикс
                nickel_tokens.add(f"{int(m.group(2))}.{m.group(1)}")
            else:  # нормальные коды, возможно списком через запятую: "2.4060, 2.4066"
                for part in raw.split(','):
                    t = normalize_material_text(part)
                    if re.match(r'^\d\.\d{3,4}$', t):
                        nickel_tokens.add(t)
    cuni_tokens = set()
    for row in read_rows('copper_nickel.csv'):
        if row:
            t = normalize_material_text(row[0])
            if len(t) >= 4 and any(c.isdigit() for c in t):
                cuni_tokens.add(t)

    def build(tokens):
        if not tokens:
            return None
        alt = '|'.join(re.escape(t) for t in sorted(tokens, key=len, reverse=True))
        return re.compile(r'(?:^|[^a-zа-я0-9])(?:' + alt + r')(?=$|[^a-zа-я0-9])', I)

    _NICKEL_CODE_RE = build(nickel_tokens)
    _CUNI_RE = build(cuni_tokens)
    _load_tree_references(ref_dir)
    _REF_LOADED = True


def find_nickel_alloy_from_reference(characteristic, nomenclature):
    if not _REF_LOADED:
        load_references()
    if _NICKEL_CODE_RE is None:
        return ''
    text = normalize_material_text((characteristic or '') + ' ' + (nomenclature or ''))
    m = _NICKEL_CODE_RE.search(text)
    return m.group(0).strip() if m else ''


def find_copper_nickel_from_reference(characteristic, nomenclature):
    if not _REF_LOADED:
        load_references()
    if _CUNI_RE is None:
        return ''
    text = normalize_material_text((characteristic or '') + ' ' + (nomenclature or ''))
    m = _CUNI_RE.search(text)
    return m.group(0).strip() if m else ''

# ---------------------------------------------------------------- нормализация
def norm(s):
    if s is None:
        return ''
    s = str(s).strip().lower().replace('ё', 'е')
    return re.sub(r'\s+', ' ', s)

def normalize_material_text(text):
    s = norm(text)
    s = re.sub(r'[\u2010\u2011\u2012\u2013\u2014\u2212]', '-', s)  # разные тире → '-'
    s = re.sub(r'\s*-\s*', '-', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s

def normalize_level_name(value):
    return re.sub(r'^\d+\s*[_-]\s*', '', norm(value)).strip()

# ---------------------------------------------------------------- токены
def includes_any(s, needles):
    return any(n in s for n in needles)

def has_token(text, token):
    src = norm(text)
    esc = re.escape(norm(token))
    return re.search(r'(?:^|[^a-zа-я0-9])' + esc + r'(?=$|[^a-zа-я0-9])', src, I) is not None

def has_any_token(text, tokens):
    return any(has_token(text, t) for t in tokens)

def has_exact_grade(text, grade):
    src = normalize_material_text(text)
    esc = re.escape(normalize_material_text(grade))
    return re.search(r'(?:^|[^a-zа-я0-9])' + esc + r'(?=$|[^a-zа-я0-9])', src, I) is not None

# ---------------------------------------------------------------- константы
SPECIAL_SHEET_BRANDS = [
    ('bisalloy', 'Bisalloy'), ('brinar', 'Brinar'), ('creusabro', 'Creusabro'),
    ('dillidur', 'Dillidur'), ('durostat', 'Durostat'), ('forf', 'FORF'),
    ('hardox', 'Hardox'), ('magstrong', 'Magstrong'), ('nm', 'NM'),
    ('rexard', 'Rexard'), ('quard', 'Quard'), ('raex', 'Raex'),
    ('relia', 'Relia'), ('rox', 'ROX'), ('roc', 'ROC'),
    ('sidur', 'Sidur'), ('xar', 'XAR'),
]
TITANIUM_VT_GRADES = ['вт1-00', 'вт1-0', 'вт1-2', 'вт3-1', 'вт5', 'вт5-1', 'вт6',
                      'вт6с', 'вт8', 'вт9', 'вт14', 'вт20', 'вт22', 'вт-0']

# ---------------------------------------------------------------- цветные металлы
def is_c17200_bronze(text):
    s = normalize_material_text(text)
    return re.search(r'(?:^|[^a-z0-9])(?:uns\s*)?c\s*[- ]?\s*17200(?=$|[^a-z0-9])', s, I) is not None

def is_titanium_grade2(text):
    s = normalize_material_text(text)
    return re.search(r'(?:^|[^a-z0-9])ti\s*gr(?:ade)?\.?\s*[-:]?\s*2(?=$|[^a-z0-9])', s, I) is not None

def is_ampcoloy972_copper(text):
    s = normalize_material_text(text)
    return re.search(r'(?:^|[^a-z0-9])ampcoloy\s*[-:]?\s*972(?=$|[^a-z0-9])', s, I) is not None

def is_beryllium(full):
    s = normalize_material_text(full)
    if is_c17200_bronze(s):
        return False
    if re.search(r'(?:^|[^a-zа-я0-9])берилл[а-я]*(?=$|[^a-zа-я0-9])', s, I):
        return True
    return re.search(r'(?:^|[^a-z0-9])beryllium(?=$|[^a-z0-9])', s, I) is not None

def is_magnesium(text):
    s = normalize_material_text(text)
    if re.search(r'(?:^|[^a-zа-я0-9])магни[йяе](?=$|[^a-zа-я0-9])', s, I) or \
       re.search(r'(?:^|[^a-z0-9])magnesium(?=$|[^a-z0-9])', s, I):
        return True
    return re.search(r'3(?:[.,]0)?\s*al\s*-\s*1(?:[.,]0)?\s*zn\s*-\s*0[.,]2(?:0)?\s*mn', s, I) is not None

def is_titanium(full):
    s = normalize_material_text(full)
    if is_titanium_grade2(s):
        return True
    if re.search(r'(?:^|[^a-zа-я0-9])титан(?:а|овый|овая|овое|овые|ового|овой)?(?=$|[^a-zа-я0-9])', s, I):
        return True
    if any(has_exact_grade(s, g) for g in TITANIUM_VT_GRADES):
        return True
    if any(has_exact_grade(s, g.replace('вт', 'vt', 1)) for g in TITANIUM_VT_GRADES):
        return True
    if re.search(r'(?:^|[^a-z0-9])ti\s*[- ]?6al\s*[- ]?4v(?=$|[^a-z0-9])', s, I):
        return True
    if re.search(r'(?:^|[^a-z0-9])6a14v(?=$|[^a-z0-9])', s, I):
        return True
    if re.search(r'(?:^|[^a-z0-9])titanium\s+(?:grade|gr\.?)\s*(?:1|2|3|4|5|7|9|12|23)(?=$|[^a-z0-9])', s, I):
        return True
    return False

def is_aluminum(full):
    s = normalize_material_text(full)
    if re.search(r'(?:^|[^a-zа-я0-9])алюмин', s, I):
        return True
    if re.search(r'(?:^|[^a-z0-9])(?:aluminium|aluminum|alclad)(?=$|[^a-z0-9])', s, I):
        return True
    if has_token(s, 'al'):
        return True
    if any(has_exact_grade(s, g) for g in ['ад-1', 'ад1', 'в95очт2', 'амг3', '6061-t6']):
        return True
    if re.search(r'(?:^|[^a-zа-я0-9])(?:[aа][dд]\s*-?\s*1|[vв]95(?:och|оч)[tт]2|[aа][mм][gг]3)(?=$|[^a-zа-я0-9])', s, I):
        return True
    if not re.search(r'(?:^|[^a-z0-9])ti\s*[- ]?6al\s*[- ]?4v', s, I) and \
       re.search(r'(?:^|[^a-z0-9])6al\s*[- ]?4v(?=$|[^a-z0-9])', s, I):
        return True
    has_grade = re.search(r'(?:^|[^a-z0-9])(?:aa\s*|en\s*aw[- ]?|al\s*)?(?:1050|1060|1070|1100|1200|2014|2017|2024|3003|3004|5005|5052|5083|5086|5754|6005|6060|6061|6063|6082|7020|7050|7075)(?:-t\d+)?(?=$|[^a-z0-9])', s, I)
    has_ctx = re.search(r'(?:aa|en\s*aw|алюмин|aluminium|aluminum|alclad|(?:^|[^a-z0-9])al(?:$|[^a-z0-9]))', s, I)
    return bool(has_grade and has_ctx)

def is_copper(full):
    s = normalize_material_text(full)
    if is_ampcoloy972_copper(s):
        return True
    if re.search(r'(?:^|[^a-zа-я0-9])(?:медь|меди|медный|медная|медное|медные|медного|медной)(?=$|[^a-zа-я0-9])', s, I):
        return True
    if re.search(r'(?:^|[^a-z0-9])copper(?=$|[^a-z0-9])', s, I):
        return True
    if has_token(s, 'cu'):
        return True
    return False

def is_brass_grade(text):
    s = normalize_material_text(text)
    grades = ['л96', 'л90', 'л80', 'л68', 'л63', 'лс59-1', 'л60с2', 'лжмц59-1-1', 'ло70-1']
    return any(has_exact_grade(s, g) for g in grades)

def is_brass(full):
    s = normalize_material_text(full)
    return bool(re.search(r'(?:^|[^a-zа-я0-9])латун', s, I) or
                re.search(r'(?:^|[^a-z0-9])brass(?=$|[^a-z0-9])', s, I) or
                is_brass_grade(s))

def is_bronze(full):
    s = normalize_material_text(full)
    return bool(is_c17200_bronze(s) or
                re.search(r'(?:^|[^a-zа-я0-9])бронз', s, I) or
                re.search(r'(?:^|[^a-z0-9])bronze(?=$|[^a-z0-9])', s, I))

# ---------------------------------------------------------------- никель / нержавейка
def has_explicit_nickel_marker(text):
    s = normalize_material_text(text)
    return bool(includes_any(s, ['inconel', 'инконел', 'monel', 'монел', 'hastelloy', 'хастеллой',
                                 'incoloy', 'инколой', 'nimonic', 'нимоник']) or
                re.search(r'\balloy\s*(?:200|201|400|600|601|625|718|800|825)\b', s, I) or
                re.search(r'\buns\s*n\d{5}\b', s, I))

def has_copper_nickel_marker(text):
    s = normalize_material_text(text)
    return bool(re.search(r'(?:^|[^a-z0-9])cu\s*[- ]?\s*ni(?:\d|$|[^a-z0-9])', s, I) or
                includes_any(s, ['медь-никел', 'медно-никел', 'copper-nickel', 'copper nickel']))

def is_nickel_strict(n2, n3, n4, full):
    f = normalize_material_text(full)
    if includes_any(f, ['никелевый', 'никелевые', 'никелевое', 'никелев', 'никелевый сплав']):
        return True
    if has_explicit_nickel_marker(f):
        return True
    if has_copper_nickel_marker(f):
        return True
    return False

def is_stainless(full):
    s = normalize_material_text(full)
    if includes_any(s, ['нерж', 'нержав', 'aisi', 'inox', 'stainless', 'duplex']):
        return True
    if re.search(r'(?:^|[^a-zа-я0-9])[aа](?:2|4)-70(?=$|[^a-zа-я0-9])', s, I): return True
    if re.search(r'\b17\s*[- ]\s*4ph\b', s, I): return True
    if re.search(r'\b1\.(?:4\d{3}|45\d{2}|48\d{2})\b', s, I): return True
    if re.search(r'\bx\d{1,3}cr[a-z0-9-]*', s, I): return True
    if re.search(r'(?:^|[^a-zа-я0-9])\d{1,2}\s*[xх]\s*\d{1,2}\s*[hн]\s*\d{1,2}[a-zа-я0-9-]*(?=$|[^a-zа-я0-9])', s, I): return True
    if re.search(r'(?:^|[^a-z0-9])(?:tp|f|s)\s*(?:201|202|301|302|303|304l?|304h|304n|305|309s?|310s?|316l?|316ti|316n|316h|317l?|321h?|347h?|410|420|430|431|440c|904l|2205|2507)(?=$|[^a-z0-9])', s, I): return True
    if re.search(r'(?:^|[^a-z0-9])(?:304l|304h|304n|309s|310s|316l|316ti|316n|316h|317l|321h|347h|440c|904l)(?=$|[^a-z0-9])', s, I): return True
    has_plain = re.search(r'(?:^|[^a-z0-9])(?:304|316|321|347|410|420|430|431|2205|2507)(?=$|[^a-z0-9])', s, I)
    context = includes_any(s, ['aisi', 'ss ', 'inox', 'нерж', 'нержав', 'stainless', 'duplex'])
    if has_plain and context: return True
    if re.search(r'\bastm\s*a(?:182|213|240|249|269|276|312|358|403|479)\b', s, I): return True
    if re.search(r'\ben\s*(?:10088(?:-\d+)?|10216-5|10217-7|10297-2)\b', s, I): return True
    return False

def should_force_stainless(characteristic, nomenclature):
    s = normalize_material_text(characteristic)
    product = norm(nomenclature or '')
    if not s:
        return False
    checks = [
        re.search(r'(?:^|[^a-zа-я0-9])12\s*[xх]\s*18\s*[hн]\s*10\s*[tт](?=$|[^a-zа-я0-9])', s, I),
        re.search(r'(?:^|[^a-z0-9])(?:304l?|316l?|904l)(?=$|[^a-z0-9])', s, I),
        re.search(r'(?:^|[^a-z0-9])(?:aisi|duplex)(?=$|[^a-z0-9])', s, I),
        re.search(r'(?:^|[^a-zа-я0-9])[aа](?:2|4)-70(?=$|[^a-zа-я0-9])', s, I),
        re.search(r'(?:^|[^a-z0-9])17-4ph(?=$|[^a-z0-9])', s, I),
    ]
    if any(checks):
        return True
    fastener_ctx = includes_any(product + ' ' + s,
                                ['шпильк', 'болт', 'гайк', 'шайб', 'крепеж', 'stud', 'bolt', 'nut', 'washer', 'fastener'])
    has_short_a2a4 = re.search(r'(?:^|[^a-zа-я0-9])[aа](?:2|4)(?!-\d)(?=$|[^a-zа-я0-9])', s, I)
    return bool(fastener_ctx and has_short_a2a4)

# ---------------------------------------------------------------- спец. листы, рельсы
def detect_high_strength_brand(full):
    brands = [('amstrong', 'AMSTRONG'), ('dillimax', 'Dillimax'), ('domex', 'Domex'),
              ('imex', 'IMEX'), ('magstong', 'MAGSTONG'), ('quend', 'Quend'),
              ('simaxx', 'SIMAXX'), ('strenx', 'Strenx'), ('weldox', 'Weldox')]
    s = norm(full)
    for key, display in brands:
        if re.search(r'(?:^|[^a-z0-9])' + re.escape(key) + r'(?=$|[^a-z0-9]|\d)', s, I):
            return display
    return ''

def is_high_strength_sheet(full):
    s = norm(full)
    return bool(detect_high_strength_brand(s) or
                re.search(r'(?:^|[^a-z0-9])s(?:690|700|960)(?:mc)?(?=$|[^a-z0-9])', s, I) or
                re.search(r'\ben\s*10149\s*[-–]?\s*2\b', s, I))

def detect_special_sheet_brand(full):
    s = norm(full)
    for key, display in SPECIAL_SHEET_BRANDS:
        if key == 'nm':
            has_sheet_ctx = re.search(r'(?:^|[^а-яa-z0-9])(?:лист|плита|plate|sheet)[а-яa-z]*(?=$|[^а-яa-z0-9])', s, I)
            has_nm_grade = re.search(r'(?:^|[^a-z0-9])nm\s*[- ]?(?:300|360|400|450|500|550|600)(?=$|[^0-9])', s, I)
            if has_sheet_ctx and has_nm_grade:
                return display
            continue
        if re.search(r'(?:^|[^a-z0-9])' + re.escape(key) + r'(?=$|[^a-z0-9]|\d)', s, I):
            return display
    return ''

def is_rail_fastener_level(lvl4):
    s = normalize_level_name(lvl4)
    return ('рельсовый крепеж' in s or
            includes_any(s, ['прижимная планка', 'рельсовая планка', 'рельсовая прокладка']))

def is_rail_fastener_s_level(lvl4):
    s = normalize_level_name(lvl4)
    return 'рельсовый крепеж' in s and 'планк' in s and 'проклад' in s

def is_rail_level(lvl4):
    s = normalize_level_name(lvl4)
    if is_rail_fastener_level(s):
        return False
    return bool(re.search(r'(?:^|[^а-яa-z0-9])рельс(?:ы|а|у|ом|е|ов|ам|ами|ах)?(?=$|[^а-яa-z0-9])', s, I) or
                has_token(s, 'rail'))

def is_rail_related(n2, n3, n4, full):
    if is_rail_fastener_s_level(n4) or is_rail_fastener_level(n4) or is_rail_level(n4):
        return True
    if includes_any(full, ['gantrex', 'beket', 'r a i l', 'rail', 'рельс', 'рельсов']):
        return True
    if includes_any(n2, ['рельс', 'рельсов']):
        return True
    if includes_any(n3, ['рельс', 'рельсов', 'рельсовый крепеж']):
        return True
    return False

def is_level4_armature(lvl4):
    return normalize_level_name(lvl4) == 'арматура'

def is_strip_nomenclature(nomenclature):
    return re.search(r'(?:^|[^а-яa-z0-9])полос[а-я]*(?=$|[^а-яa-z0-9])', norm(nomenclature), I) is not None

# ---------------------------------------------------------------- тип металла
def detect_metal_type(lvl1_raw, full):
    lvl1 = norm(lvl1_raw)
    if (is_beryllium(full) or is_magnesium(full) or is_titanium(full) or is_aluminum(full) or
            is_copper(full) or is_brass(full) or is_bronze(full) or is_nickel_strict('', '', '', full)):
        return 'Цветной'
    if is_stainless(full):
        return 'Нержавеющий'
    if '2_нержав' in lvl1:
        return 'Нержавеющий'
    if '1_черный' in lvl1:
        return 'Черный'
    if '3_цвет' in lvl1:
        return 'Цветной'
    return detect_metal_type_fallback(full)

def detect_metal_type_fallback(full):
    if is_nickel_strict('', '', '', full):
        return 'Цветной'
    if is_stainless(full):
        return 'Нержавеющий'
    if (is_beryllium(full) or is_magnesium(full) or is_titanium(full) or is_copper(full) or
            is_brass(full) or is_bronze(full) or is_aluminum(full)):
        return 'Цветной'
    return 'Черный'

# ---------------------------------------------------------------- Категория 1
_CANON_LIST = [
    'Днище', 'Крепежные изделия', 'Специальные листы', 'Фитинги', 'Трубы',
    'Листовой прокат (лист, лента, рулон)', 'Рельсы и рельсовый крепеж', 'Балки',
    'Подшипники', 'Фланцы', 'ГОСТ', 'Трубопроводная арматура (ТПА)', 'Профили',
    'Прутки', 'Никель', 'Поковка (заготовка)', 'Фланцевая прокладка',
    'Лист высокопрочный', 'Цветной металл', 'Проволока', 'Прочее',
]
def _build_canon():
    m = {norm(x): x for x in _CANON_LIST}
    m[norm('Лист износостойкий')] = 'Специальные листы'
    m[norm('Износостойкие листы')] = 'Специальные листы'
    m[norm('Листы специальные')] = 'Специальные листы'
    return m
CANON_CAT1 = _build_canon()

_ATK_RE = re.compile(r'(?:^|[^a-zа-я0-9])[aа][tт][kк](?=$|[^a-zа-я0-9])', I)

def detect_explicit_category_from_levels(n2, n3, n4):
    for n in (n2, n3, n4):
        if CANON_CAT1.get(n):
            return CANON_CAT1[n]
    return ''

def detect_category1_by_keywords(full):
    f = full
    if 'крепление для труб' in f or 'хомут' in f: return 'Прочее'
    if detect_special_sheet_brand(f) or is_high_strength_sheet(f): return 'Специальные листы'
    if includes_any(f, ['рельс', 'рельсов', 'rail', 'gantrex', 'beket']): return 'Рельсы и рельсовый крепеж'
    if 'гост' in f or _ATK_RE.search(f) or has_any_token(f, ['ост', 'сто', 'ту']): return 'ГОСТ'
    if includes_any(f, ['проклад', 'gasket']) and not includes_any(f, ['рельс', 'gantrex', 'beket']): return 'Фланцевая прокладка'
    if includes_any(f, ['тпа', 'кран', 'задвиж', 'клапан', 'затвор', 'регулятор', 'конденсатоотводчик', 'компенсатор', 'фильтр', 'strainer']): return 'Трубопроводная арматура (ТПА)'
    if includes_any(f, ['фитинг', 'отвод', 'тройник', 'переход', 'муфта', 'ниппел', 'крестовин', 'пробк', 'бобышк', 'штуцер', 'olet', 'weldolet', 'sockolet']): return 'Фитинги'
    if includes_any(f, ['фланц', 'фланец', 'b16.5', 'b16.47', 'b16.48', 'spectacle', 'paddle', 'spacer']) or has_any_token(f, ['wn', 'so', 'lj']): return 'Фланцы'
    if includes_any(f, ['труба', 'pipe', 'tube', 'sch']): return 'Трубы'
    if includes_any(f, ['крепеж', 'болт', 'гайк', 'шпильк', 'шайб', 'винт', 'анкер', 'studbolt', 'u-bolt', 'ubolt']): return 'Крепежные изделия'
    if 'подшип' in f: return 'Подшипники'
    if includes_any(f, ['балк', 'heb', 'hea', 'heaa', 'hem', 'ipe', 'ipn', 'двутавр', 'двутавров']): return 'Балки'
    if includes_any(f, ['профил', 'швеллер', 'уголок', 'upn', 'upe', 'pfc', 'шпунт', 'полособульб']): return 'Профили'
    if includes_any(f, ['прут', 'круг', 'полоса', 'шестигран', 'квадрат']): return 'Прутки'
    if 'проволок' in f: return 'Проволока'
    if includes_any(f, ['лист', 'плита', 'лента', 'рулон', 'coil', 'штрипс']): return 'Листовой прокат (лист, лента, рулон)'
    if includes_any(f, ['поковк', 'forging', 'заготовк']): return 'Поковка (заготовка)'
    return 'Прочее'

def detect_category1(lvl1, lvl2, lvl3, lvl4, full, metal_type, nomenclature,
                     nickel_ref=False, cuni_ref=False):
    n1, n2, n3, n4 = norm(lvl1), norm(lvl2), norm(lvl3), norm(lvl4)
    nomen = norm(nomenclature)
    if includes_any(full, ['днищ', 'заготовка днищ', 'днище']): return 'Днище'
    if detect_special_sheet_brand(full) or is_high_strength_sheet(full): return 'Специальные листы'
    if is_level4_armature(n4): return 'Прочее'
    if 'трубопроводная арматура' in n3 or has_token(n3, 'тпа'): return 'Трубопроводная арматура (ТПА)'
    if is_rail_related(n2, n3, n4, full): return 'Рельсы и рельсовый крепеж'
    if ('4_гост' in n1 or '4_гост' in n2 or '4_гост' in n3 or '4_гост' in n4 or
            'гост' in full or _ATK_RE.search(full) or has_any_token(full, ['ост', 'сто', 'ту'])):
        return 'ГОСТ'
    if nickel_ref or cuni_ref or is_nickel_strict(n2, n3, n4, full): return 'Никель'
    if metal_type == 'Цветной': return 'Цветной металл'
    if is_strip_nomenclature(nomen): return 'Прутки'
    explicit = detect_explicit_category_from_levels(n2, n3, n4)
    if explicit: return explicit
    return detect_category1_by_keywords(full)

# ---------------------------------------------------------------- публичная функция
def classify(lvl1, lvl2, lvl3, lvl4, product, variant):
    """Возвращает dict с category_1..5, metal_type, standard.
    Сейчас заполнены category_1 и metal_type; остальное — после подключения справочников."""
    parts = [norm(lvl1), norm(lvl2), norm(lvl3), norm(lvl4), norm(product), norm(variant)]
    full = ' '.join(p for p in parts if p)
    ch, nomen = variant, product

    nickel_ref = find_nickel_alloy_from_reference(ch, nomen)
    cuni_ref = find_copper_nickel_from_reference(ch, nomen)

    metal_type = 'Цветной' if (nickel_ref or cuni_ref) else detect_metal_type(lvl1, full)
    force_c17200 = is_c17200_bronze(full)
    force_ti2 = is_titanium_grade2(full)
    force_ampcoloy = is_ampcoloy972_copper(full)
    force_beryllium = is_beryllium(full)
    force_magnesium = is_magnesium(full)
    force_stainless = should_force_stainless(ch, nomen)
    has_nonferrous = bool(nickel_ref or cuni_ref or force_beryllium or force_magnesium or force_ti2 or
                          force_ampcoloy or force_c17200 or is_titanium(full) or is_aluminum(full) or
                          is_copper(full) or is_brass(full) or is_bronze(full))
    apply_stainless = force_stainless and not has_nonferrous
    if has_nonferrous:
        metal_type = 'Цветной'
    elif apply_stainless:
        metal_type = 'Нержавеющий'

    cat1 = detect_category1(lvl1, lvl2, lvl3, lvl4, full, metal_type, nomen,
                            nickel_ref=nickel_ref, cuni_ref=cuni_ref)

    std_family = detect_standard_family(full)
    std_concrete = detect_concrete_standard(full)
    ctx = {'full': full, 'nomen': nomen, 'ch': ch, 'metalType': metal_type,
           'stdFamily': std_family, 'stdConcrete': std_concrete, 'lvl4': lvl4}
    res = build_category_tree(cat1, ctx)

    preserve = res['cat1'] in ('Трубопроводная арматура (ТПА)', 'Рельсы и рельсовый крепеж')
    if apply_stainless:
        res['metalType'] = 'Нержавеющий'
        if not preserve: res['cat3'] = 'Нержавеющий'
    if force_beryllium:
        res['metalType'] = 'Цветной'
        if not preserve: res['cat3'] = 'Бериллий'
    if force_magnesium:
        res['metalType'] = 'Цветной'
        if not preserve: res['cat3'] = 'Магний'
    if force_ti2:
        res['metalType'] = 'Цветной'
        if not preserve: res['cat3'] = 'Титан'
    if force_ampcoloy:
        res['metalType'] = 'Цветной'
        if not preserve: res['cat3'] = 'Медь'
    if force_c17200:
        res['metalType'] = 'Цветной'
        if not preserve: res['cat3'] = 'Бронза'
    if is_acrylic_or_milprf(nomen, ch) and res['cat1'] != 'Трубопроводная арматура (ТПА)':
        res['cat3'] = 'Не металл'
        mil = detect_milprf_standard(full)
        if mil['family']: res['cat4'] = mil['family']
        if mil['standard']: res['cat5'] = mil['standard']
    apply_drawing_category_rule(res, nomen, ch)
    if is_sdr17(nomen, ch) and res['cat1'] != 'Трубопроводная арматура (ТПА)':
        res['cat3'] = 'Не металл'; res['cat4'] = ''; res['cat5'] = 'SDR-17'
    apply_forced_black_metal_rule(res)

    return {
        'category_1': res['cat1'],
        'category_2': res['cat2'], 'category_3': res['cat3'],
        'category_4': res['cat4'], 'category_5': res['cat5'],
        'metal_type': res['metalType'], 'standard': res['stdFamily'],
    }


# ============================================================================
#  КАТЕГОРИИ 2–5: порт buildCategoryTree_ и детекторов
# ============================================================================
_RAILS_ROWS = []
_TPA_REF = []          # список (value, family)
_INCH_DN = {}          # ключ (нормализованный дюйм) -> DN
DRAWING_CAT4_RESERVED = {'Специальные листы', 'Рельсы и рельсовый крепеж',
                         'Трубопроводная арматура (ТПА)', 'Балки', 'Профили'}


def _load_tree_references(ref_dir):
    global _RAILS_ROWS, _TPA_REF, _INCH_DN
    def rows(name):
        p = os.path.join(ref_dir, name)
        return list(csv.reader(open(p, encoding='utf-8'))) if os.path.exists(p) else []
    _RAILS_ROWS = rows('rails.csv')
    _TPA_REF = []
    for r in rows('tpa_standards.csv'):
        if r and r[0].strip():
            v = normalize_tpa_standard_value(r[0])
            _TPA_REF.append((v, detect_tpa_standard_family(v)))
    _INCH_DN = {}
    for r in rows('inch_dn.csv'):
        if len(r) >= 2 and r[0].strip().lower() not in ('nps', ''):
            k = normalize_inch_token(r[0])
            if k:
                _INCH_DN[k] = re.sub(r'\s+', '', str(r[1]).strip())

def get_special_sheet_brand_key(display):
    for key, disp in SPECIAL_SHEET_BRANDS:
        if norm(disp) == norm(display):
            return key
    return ''

# ---- стандарты ----
def format_std_token(token):
    t = re.sub(r'\s+', ' ', str(token or '')).strip()
    t = re.sub(r'^ГОСТ\s*Р\s*(?:ИСО|ISO)\s*', 'ГОСТ Р ИСО ', t, flags=I)
    t = re.sub(r'^ГОСТ\s*(?:ИСО|ISO)\s*', 'ГОСТ ISO ', t, flags=I)
    t = re.sub(r'^ГОСТ\s*Р\s*', 'ГОСТ Р ', t, flags=I)
    t = re.sub(r'^ГОСТ\s*', 'ГОСТ ', t, flags=I)
    t = re.sub(r'^[aа][tт][kк](?=$|[^a-zа-я0-9])', 'АТК', t, flags=I)
    t = re.sub(r'\bGB\s*/\s*T\b', 'GB/T', t, flags=I)
    t = re.sub(r'\bNB\s*/\s*T\b', 'NB/T', t, flags=I)
    t = re.sub(r'\bMIL\s*[- ]?PRF\s*[- ]?', 'MIL-PRF-', t, flags=I)
    t = re.sub(r'\bEN(\d)', r'EN \1', t)
    t = re.sub(r'\bDIN(\d)', r'DIN \1', t)
    t = re.sub(r'\bJIS([A-Z])\s*(\d)', r'JIS \1\2', t)
    t = re.sub(r'\bMSS SP\s*(\d)', r'MSS SP-\1', t)
    t = re.sub(r'\s*/\s*M\b', '', t, flags=I)
    def _yy(m):
        n = int(m.group(2)); return m.group(1) + ('20' if n <= 30 else '19') + m.group(2)
    t = re.sub(r'(ГОСТ(?:\s+Р(?:\s+ИСО)?|\s+ISO)?\s+\d[\d.\/-]*-)(\d{2})\b', _yy, t, flags=I)
    return t

def detect_gost_standards(full):
    source = re.sub(r'[\u2010\u2011\u2012\u2013\u2014\u2212]', '-', str(full or ''))
    out = []
    rx = re.compile(r'(?:^|[^a-zа-я0-9])((?:гост\s*р\s*(?:исо|iso)\s*|гост\s*(?:исо|iso)\s*|гост\s*р\s*|гост\s*)\d{1,6}(?:\.\d+)*(?:/\d+)*(?:-\d{1,4})?)(?=$|[^a-zа-я0-9])', I)
    for m in rx.finditer(source):
        std = format_std_token(m.group(1).upper())
        if std and std not in out:
            out.append(std)
    return out

def detect_standard_family(full):
    f = norm(full); fam = []
    if re.search(r'гост\s*р\s*(?:исо|iso)', f, I): fam.append('ГОСТ Р ИСО')
    elif re.search(r'гост\s*(?:исо|iso)', f, I): fam.append('ГОСТ ISO')
    elif re.search(r'гост\s*р\s*\d', f, I): fam.append('ГОСТ Р')
    elif 'гост' in f: fam.append('ГОСТ')
    if re.search(r'(?:^|[^a-zа-я0-9])[aа][tт][kк](?=$|[^a-zа-я0-9])', f, I): fam.append('АТК')
    if has_token(f, 'ост'): fam.append('ОСТ')
    if 'сто цкти' in f: fam.append('СТО ЦКТИ')
    elif has_token(f, 'сто'): fam.append('СТО')
    if has_token(f, 'ту'): fam.append('ТУ')
    if re.search(r'\bgb\s*/\s*t\b', f, I): fam.append('GB/T')
    if re.search(r'\bnb\s*/\s*t\b', f, I): fam.append('NB/T')
    if re.search(r'\beemua\b', f, I): fam.append('EEMUA')
    if re.search(r'\bams\b', f, I): fam.append('AMS')
    if re.search(r'\bmil\s*[- ]?prf\b', f, I): fam.append('MIL-PRF')
    if re.search(r'\bastm\s*b\s*\d', f, I): fam.append('ASTM B')
    elif re.search(r'\bastm\b', f, I): fam.append('ASTM')
    if re.search(r'\basme\b', f, I): fam.append('ASME')
    if re.search(r'\ben\s*(?:iso\s*)?\d', f, I): fam.append('EN ISO' if re.search(r'\ben\s*iso\s*\d', f, I) else 'EN')
    if re.search(r'\bdin\s*\d', f, I): fam.append('DIN')
    if re.search(r'\biso\s*\d', f, I) and 'EN ISO' not in fam and 'ГОСТ ISO' not in fam and 'ГОСТ Р ИСО' not in fam: fam.append('ISO')
    if re.search(r'\bbs\s*\d', f, I): fam.append('BS')
    if re.search(r'\bjis\s*[a-z]?\s*\d', f, I): fam.append('JIS')
    if re.search(r'\bmss\s*sp', f, I): fam.append('MSS')
    if re.search(r'\bapi\b', f, I): fam.append('API')
    seen = []
    for x in fam:
        if x not in seen: seen.append(x)
    return ' / '.join(seen)

def detect_concrete_standard(full):
    g = detect_gost_standards(full)
    if g: return g[0]
    pats = [
        r'(?:^|[^a-zа-я0-9])([aа][tт][kк]\s*[\d.\-–/]+)(?=$|[^a-zа-я0-9])',
        r'(?:^|[^a-zа-я0-9])(ост\s*[\d.\-–/]+)(?=$|[^a-zа-я0-9])',
        r'(?:^|[^a-zа-я0-9])(сто\s+цкти(?:\s+|[-–]\s*)[a-zа-я0-9][a-zа-я0-9.\-–/]*)(?=$|[^a-zа-я0-9])',
        r'(?:^|[^a-zа-я0-9])(сто(?:\s+|[-–]\s*)[a-zа-я0-9][a-zа-я0-9.\-–/]*)(?=$|[^a-zа-я0-9])',
        r'(?:^|[^a-zа-я0-9])(ту(?:\s+|[-–]\s*)[a-zа-я0-9][a-zа-я0-9.\-–/]*)(?=$|[^a-zа-я0-9])',
        r'\bgb\s*/\s*t\s*\d[\d.\/-]*(?:[-–]\d{2,4})?', r'\bnb\s*/\s*t\s*\d[\d.\/-]*(?:[-–]\d{2,4})?',
        r'\beemua\s*\d[\d.\/-]*', r'\bams\s*[- ]?\s*qq\s*[- ]?\s*[a-z]\s*[- ]?\s*\d[\d.\/-]*',
        r'\bams\s*[- ]?\s*\d[\d.\/-]*', r'\bmil\s*[- ]?prf\s*[- ]?\d[\w.\/-]*',
        r'\ben\s*iso\s*\d[\d.\/-]*', r'\ben\s*\d{3,5}(?:[-–]\d{1,3})?',
        r'\bastm\s*[a-z]\s*\d{2,4}[a-z]?(?:\s*/\s*m)?(?:[-–]\d{2,4})?',
        r'\basme\s*(?:sa|sb|b)\s*[- ]?\d+(?:\.\d+)*', r'\bbs\s*\d{3,5}', r'\bdin\s*\d{3,6}',
        r'\bjis\s*[a-z]\s*\d{3,5}', r'\bmss\s*sp[-–]?\s*\d{1,3}', r'\bapi\s*(?:rp\s*)?\d{1,4}[a-z]?',
    ]
    for p in pats:
        m = re.search(p, str(full), I)
        if m:
            token = next((g for g in m.groups() if g), None) or m.group(0)
            return format_std_token(token.upper())
    return ''

def detect_gost_standard_info(full):
    gs = detect_gost_standards(full)
    if gs:
        fams = []
        for s in gs:
            fam = 'ГОСТ'
            if re.match(r'^ГОСТ Р ИСО\b', s, I): fam = 'ГОСТ Р ИСО'
            elif re.match(r'^ГОСТ ISO\b', s, I): fam = 'ГОСТ ISO'
            elif re.match(r'^ГОСТ Р\b', s, I): fam = 'ГОСТ Р'
            if fam not in fams: fams.append(fam)
        return {'family': ' / '.join(fams), 'standard': ' / '.join(gs)}
    c = detect_concrete_standard(full)
    if re.match(r'^[aа][tт][kк](?=$|[^a-zа-я0-9])', c, I):
        return {'family': 'АТК', 'standard': re.sub(r'^[aа][tт][kк](?=$|[^a-zа-я0-9])', 'АТК', c, flags=I)}
    if re.match(r'^ОСТ(?=$|[^a-zа-я0-9])', c, I): return {'family': 'ОСТ', 'standard': c}
    if re.match(r'^СТО ЦКТИ(?=$|[^a-zа-я0-9])', c, I): return {'family': 'СТО ЦКТИ', 'standard': c}
    if re.match(r'^СТО(?=$|[^a-zа-я0-9])', c, I): return {'family': 'СТО', 'standard': c}
    if re.match(r'^ТУ(?=$|[^a-zа-я0-9])', c, I): return {'family': 'ТУ', 'standard': c}
    return {'family': detect_standard_family(full) or 'ГОСТ', 'standard': c}

# ---- продуктовые детекторы ----
def detect_sheet_form_by_nomenclature(nomenclature):
    s = norm(nomenclature)
    if not s: return ''
    if re.search(r'(?:^|[^а-яa-z0-9])рулон[а-я]*(?=$|[^а-яa-z0-9])', s, I) or has_token(s, 'coil'): return 'Рулон'
    if re.search(r'(?:^|[^а-яa-z0-9])лент[а-я]*(?=$|[^а-яa-z0-9])', s, I): return 'Лента'
    if re.search(r'(?:^|[^а-яa-z0-9])плит[а-я]*(?=$|[^а-яa-z0-9])', s, I): return 'Лист, плита'
    if re.search(r'(?:^|[^а-яa-z0-9])лист[а-я]*(?=$|[^а-яa-z0-9])', s, I): return 'Лист'
    return ''

def detect_product_type(full):
    f = full
    if includes_any(f, ['труба', 'pipe']): return 'Трубы'
    if includes_any(f, ['фланц', 'фланец', 'b16.5', 'b16.47', 'b16.48']) or has_any_token(f, ['wn', 'so', 'lj']): return 'Фланцы'
    if includes_any(f, ['фитинг', 'отвод', 'тройник', 'муфта', 'ниппел', 'переход', 'olet', 'weldolet']): return 'Фитинги'
    if includes_any(f, ['болт', 'гайк', 'шпильк', 'шайб', 'винт', 'анкер', 'studbolt', 'u-bolt', 'ubolt']): return 'Крепежные изделия'
    if includes_any(f, ['прут', 'круг', 'полоса', 'шестигран', 'квадрат']): return 'Прутки'
    if includes_any(f, ['лист', 'плита', 'лента', 'рулон', 'coil']): return 'Листовой прокат (лист, лента, рулон)'
    if includes_any(f, ['поковк', 'forging', 'заготовк']): return 'Поковка (заготовка)'
    if includes_any(f, ['клапан', 'кран', 'задвиж', 'затвор', 'арматур', 'тпа']): return 'Трубопроводная арматура (ТПА)'
    if includes_any(f, ['днищ']): return 'Днище'
    return 'Прочее'

def detect_material_product_type(nomenclature, full):
    sf = detect_sheet_form_by_nomenclature(nomenclature)
    if sf == 'Лист, плита': return sf
    return detect_product_type(full)

def detect_nickel_subtype(full):
    s = normalize_material_text(full)
    if has_explicit_nickel_marker(s): return 'Никель'
    if has_copper_nickel_marker(s): return 'Медь-никель'
    return 'Никель'

def detect_non_ferrous_subtype(full):
    if is_c17200_bronze(full): return 'Бронза'
    if is_beryllium(full): return 'Бериллий'
    if is_magnesium(full): return 'Магний'
    if is_titanium(full): return 'Титан'
    if is_nickel_strict('', '', '', full): return detect_nickel_subtype(full)
    if is_copper(full): return 'Медь'
    if is_brass(full): return 'Латунь'
    if is_bronze(full): return 'Бронза'
    if is_aluminum(full): return 'Алюминий'
    return 'Другое'

def detect_nickel_alloy(full):
    s = str(full or '')
    pats = [r'\b(?:inconel|инконел)\s*[- ]?\s*([a-z0-9-]+)', r'\b(?:monel|монел)\s*[- ]?\s*([a-z0-9-]+)',
            r'\b(?:hastelloy|хастеллой)\s*[- ]?\s*([a-z0-9-]+)', r'\b(?:incoloy|инколой)\s*[- ]?\s*([a-z0-9-]+)',
            r'\b(?:nimonic|нимоник)\s*[- ]?\s*([a-z0-9-]+)', r'\balloy\s*(200|201|400|600|601|625|718|800|825)\b',
            r'\buns\s*(n\d{5})\b', r'\b(cuni[\w-]*)\b']
    for p in pats:
        m = re.search(p, s, I)
        if m: return re.sub(r'\s+', ' ', m.group(0)).strip()
    return ''

def detect_fastener_type(f):
    if 'u-bolt' in f or 'ubolt' in f or 'u-образ' in f or 'у-образ' in f: return 'Болт'
    if 'гайк' in f or ' nut' in f: return 'Гайка'
    if 'шпильк' in f or 'stud' in f: return 'Шпилька'
    if 'шайб' in f or 'washer' in f: return 'Шайба'
    if 'болт' in f or ' bolt' in f: return 'Болт'
    if 'винт' in f or 'screw' in f: return 'Винт'
    if 'анкер' in f or 'anchor' in f: return 'Анкерный крепеж'
    return ''

def is_fastener_set_by_nomenclature(nomenclature):
    s = norm(nomenclature)
    if not s: return False
    found = [re.search(r'(?:^|[^а-яa-z0-9])(?:шпильк[а-я]*|шпилек)(?=$|[^а-яa-z0-9])', s, I),
             re.search(r'(?:^|[^а-яa-z0-9])болт[а-я]*(?=$|[^а-яa-z0-9])', s, I),
             re.search(r'(?:^|[^а-яa-z0-9])(?:гайк[а-я]*|гаек)(?=$|[^а-яa-z0-9])', s, I),
             re.search(r'(?:^|[^а-яa-z0-9])шайб[а-я]*(?=$|[^а-яa-z0-9])', s, I)]
    return sum(1 for x in found if x) >= 2

def detect_fitting_group(f):
    if includes_any(f, ['отвод', 'колено', 'уголок', 'elbow', 'return']): return 'Отвод, колено, уголок'
    if 'тройник' in f or has_token(f, 'tee'): return 'Тройник'
    if includes_any(f, ['переход', 'reducer', 'swage']): return 'Переход'
    if includes_any(f, ['муфта', 'coupling', 'union', 'американк']): return 'Муфта, американка'
    if includes_any(f, ['ниппел', 'сгон', 'nipple']): return 'Ниппель, сгон'
    if includes_any(f, ['заглуш', 'cap', 'крышк']): return 'Заглушка, крышка'
    if includes_any(f, ['крестовин', 'cross']): return 'Крестовина'
    if includes_any(f, ['пробк', 'plug']): return 'Пробка'
    if includes_any(f, ['бобышк', 'olet', 'weldolet', 'sockolet']): return 'Бобышка'
    if includes_any(f, ['штуцер', 'nozzle']): return 'Штуцер'
    return ''

def detect_flange_type(f):
    if includes_any(f, ['b16.48', 'spectacle', 'paddle', 'spacer']): return 'Заглушка поворотная'
    if has_token(f, 'bl') or includes_any(f, ['blind', 'глух']): return 'Глухой'
    if has_token(f, 'wn') or includes_any(f, ['воротник', 'type 11']): return 'Воротниковый'
    if has_token(f, 'so') or includes_any(f, ['плоск', 'slip-on', 'type 01']): return 'Плоский'
    if has_token(f, 'lj') or includes_any(f, ['свободн', 'lap joint', 'type 02']): return 'Свободный'
    if includes_any(f, ['резьб', 'thrd', 'thread']): return 'Резьбовой'
    if has_token(f, 'sw') or 'раструб' in f: return 'Раструбный'
    if includes_any(f, ['отборт', 'втулк', 'кольц', 'type 32']): return 'Отбортовка, втулка, кольцо'
    if includes_any(f, ['api 6a']): return 'Ответный'
    return ''

def detect_gasket_type(f):
    if includes_any(f, ['ptfe', 'тефлон', 'фторопласт', 'резин', 'графит', 'nonmetal', 'неметалл', 'неметал']): return 'Неметаллическая'
    if includes_any(f, ['спираль', 'swg', 'spiral wound', 'навит']): return 'Полуметаллическая'
    if includes_any(f, ['rtj', 'ring joint', 'металл']): return 'Металлическая'
    return ''

def detect_pipe_type(f):
    if includes_any(f, ['профильн', 'profile']): return 'Труба профильная'
    if includes_any(f, ['трубная заготовк', 'заготовк']): return 'Трубная заготовка'
    return 'Труба круглая'

def detect_bar_type(f):
    if 'полоса' in f: return 'Полоса'
    if includes_any(f, ['шестигран']): return 'Шестигранник'
    if includes_any(f, ['квадрат']): return 'Квадрат'
    if includes_any(f, ['круг', 'прут']): return 'Круг'
    return ''

def detect_beam_series(f):
    if 'heb' in f: return 'HEB'
    if 'hea' in f: return 'HEA'
    if 'ipe' in f: return 'IPE'
    if 'ipn' in f: return 'IPN'
    if re.search(r'\bh\s*\d', f): return 'H'
    return ''

def detect_profile_group(f):
    if includes_any(f, ['швеллер', 'upn', 'upe', 'pfc']): return 'Швеллеры'
    if includes_any(f, ['уголок']): return 'Уголок'
    if includes_any(f, ['шпунт']): return 'Шпунт'
    return 'Профили'

def detect_profile_series(f):
    if 'upn' in f: return 'UPN'
    if 'upe' in f: return 'UPE'
    if 'pfc' in f: return 'PFC'
    return ''

def detect_size_token(f, series):
    if series:
        m = re.search(r'\b' + series.lower() + r'\s*\d{2,4}\b', f, I)
        if m: return re.sub(r'\s+', ' ', m.group(0).upper())
    m2 = re.search(r'\b\d{2,4}\b', f)
    return m2.group(0) if m2 else ''

# ---- спец. листы: твёрдость/толщина ----
def _fmt_dim(value):
    return str(int(value)) if round(value) == value else str(value).replace('.', ',')

def _norm_dim(value):
    try:
        return _fmt_dim(float(str(value).replace(',', '.')))
    except ValueError:
        return ''

def _plausible_thk(value, hardness):
    try:
        n = float(str(value).replace(',', '.'))
    except ValueError:
        return False
    if n <= 0 or n > 300: return False
    if hardness and str(value).replace(',', '.') == str(hardness): return False
    return True

def detect_special_sheet_hardness(full, brand_display):
    s = re.sub(r'[\u2010\u2011\u2012\u2013\u2014\u2212]', '-', norm(full))
    bk = get_special_sheet_brand_key(brand_display)
    if bk:
        m = re.search(r'(?:^|[^a-z0-9])' + re.escape(bk) + r'\s*[-_/]?\s*(?:hbw?\s*)?([2-7]\d{2})(?=$|[^0-9])', s, I)
        if m: return m.group(1)
    m = re.search(r'(?:твердост\w*\s*[:=-]?\s*)([2-7]\d{2})(?=$|[^0-9])', s, I)
    if m: return m.group(1)
    m = re.search(r'(?:^|[^0-9])([2-7]\d{2})\s*hbw?\b', s, I)
    if m: return m.group(1)
    return ''

def detect_special_sheet_thickness(full, brand_display, hardness):
    s = norm(full).replace('×', 'x').replace('х', 'x')
    s = re.sub(r'[\u2010\u2011\u2012\u2013\u2014\u2212]', '-', s)
    m = re.search(r'толщин\w*\s*[:=-]?\s*(\d{1,3}(?:[.,]\d{1,3})?)\s*(?:мм|mm)?\b', s, I)
    if m: return _norm_dim(m.group(1))
    for token in re.findall(r'\d{1,4}(?:[.,]\d{1,3})?(?:\s*x\s*\d{1,4}(?:[.,]\d{1,3})?){1,2}', s, I):
        vals = []
        for x in re.split(r'\s*x\s*', token, flags=I):
            try:
                v = float(x.replace(',', '.'))
                if v > 0: vals.append(v)
            except ValueError:
                pass
        pl = [v for v in vals if v <= 300]
        if pl: return _fmt_dim(min(pl))
    if hardness:
        m = re.search(r'(?:^|[^0-9])' + re.escape(hardness) + r'\s*(?:hbw?\s*)?[-_/x*]?\s*(\d{1,3}(?:[.,]\d{1,3})?)\s*(?:мм|mm)\b', s, I)
        if m and _plausible_thk(m.group(1), hardness): return _norm_dim(m.group(1))
    for m in re.finditer(r'(\d{1,3}(?:[.,]\d{1,3})?)\s*(?:мм|mm)(?=$|[^a-zа-я0-9])', s, I):
        if _plausible_thk(m.group(1), hardness): return _norm_dim(m.group(1))
    bk = get_special_sheet_brand_key(brand_display)
    if bk and hardness:
        m = re.search(r'(?:^|[^a-z0-9])' + re.escape(bk) + r'\s*[-_/]?\s*' + re.escape(hardness) + r'\s+(\d{1,3}(?:[.,]\d{1,3})?)(?=$|[^0-9])', s, I)
        if m and _plausible_thk(m.group(1), hardness): return _norm_dim(m.group(1))
    return ''

def detect_high_strength_standard(full):
    if re.search(r'\ben\s*10149\s*[-–]?\s*2\b', norm(full), I):
        return {'family': 'EN', 'standard': 'EN 10149-2'}
    return {'family': '', 'standard': ''}

# ---- рельсы ----
def detect_rail_brand(f):
    if 'gantrex' in f: return 'Gantrex'
    if 'beket' in f: return 'Beket'
    if 'valente' in f: return 'Valente'
    return ''

def detect_rail_designation(nomenclature):
    s = str(nomenclature or '')
    for p in [r'(?:^|[^a-zа-я0-9])(?:р|r)\s*[- ]?(\d{2,3})(?=$|[^a-zа-я0-9])',
              r'(?:^|[^a-z0-9])(?:uic|qu|a)\s*[- ]?\d{2,3}(?:e\d)?(?=$|[^a-z0-9])',
              r'(?:^|[^a-z0-9])\d{2,3}e\d(?=$|[^a-z0-9])']:
        m = re.search(p, s, I)
        if m: return re.sub(r'\s+', '', m.group(0).strip().upper())
    return ''

def detect_rail_item_type(f):
    if 'проклад' in f: return 'Прокладка'
    if includes_any(f, ['прижимн', 'планк']): return 'Прижимная планка'
    if includes_any(f, ['зажим', 'крепеж']): return 'Крепеж'
    return ''

def _normalize_reference_token(v):
    return re.sub(r'\s+', ' ', normalize_material_text(v)).strip()

def contains_reference_token(source, token):
    s = _normalize_reference_token(source); t = _normalize_reference_token(token)
    if not s or not t or len(t) < 2: return False
    esc = re.escape(t).replace(r'\ ', r'\s*')
    return re.search(r'(?:^|[^a-zа-я0-9])' + esc + r'(?=$|[^a-zа-я0-9])', s, I) is not None

def _is_ref_header(row):
    text = norm(' '.join(str(x) for x in row))
    return includes_any(text, ['марка сплава', 'обозначение', 'аналог', 'синоним', 'вид рельса', 'тип рельса']) or text == 'марка'

def find_rail_type_from_reference(nomenclature):
    for row in _RAILS_ROWS:
        if not row or _is_ref_header(row): continue
        for cell in row:
            token = str(cell or '').strip()
            if token and contains_reference_token(nomenclature, token):
                for c in row:
                    if str(c or '').strip(): return str(c).strip()
                return token
    return ''

# ---- ТПА ----
def normalize_inch_symbols(v):
    return (str(v or '').replace('“', '"').replace('”', '"').replace('„', '"').replace('‟', '"')
            .replace('″', '"').replace('′', "'").replace('’', "'").replace('‘', "'").replace("''", '"'))

def normalize_inch_token(v):
    s = normalize_inch_symbols(v).strip().lower()
    s = re.sub(r'дюйм(?:а|ов)?', '', s); s = re.sub(r'inch(?:es)?', '', s)
    s = s.replace('"', '').replace(',', '.'); s = re.sub(r'\s+', ' ', s).strip()
    if not s: return ''
    m = re.match(r'^(\d+)\s+(\d+)\s*/\s*(\d+)$', s)
    if m: return str(int(m.group(1)) + int(m.group(2)) / int(m.group(3)))
    m = re.match(r'^(\d+)\s*/\s*(\d+)$', s)
    if m: return str(int(m.group(1)) / int(m.group(2)))
    try:
        return str(int(s)) if float(s) == int(float(s)) else str(float(s))
    except ValueError:
        return re.sub(r'\s+', '', s)

def _compact_tpa_key(v):
    return re.sub(r'[^a-zа-я0-9]', '', normalize_material_text(v), flags=I)

def normalize_tpa_standard_value(v):
    s = re.sub(r'[\u2010\u2011\u2012\u2013\u2014\u2212]', '-', str(v or '').upper())
    s = re.sub(r'\s+', ' ', s).strip()
    s = re.sub(r'^MSS\s+SP\s*-?\s*', 'MSS SP-', s, flags=I)
    s = re.sub(r'^API\s+RP\s*', 'API RP ', s, flags=I)
    s = re.sub(r'^ASME\s+B\s*', 'ASME B', s, flags=I)
    s = re.sub(r'^EN\s+ISO\s*', 'EN ISO ', s, flags=I)
    return s

def detect_tpa_standard_family(v):
    s = normalize_tpa_standard_value(v)
    for pref, fam in [('EN ISO', 'EN ISO'), ('API', 'API'), ('ASME', 'ASME'), ('BS', 'BS'), ('EN', 'EN'),
                      ('DIN', 'DIN'), ('ISO', 'ISO'), ('MSS', 'MSS'), ('ASTM', 'ASTM'), ('JIS', 'JIS')]:
        if re.match(r'^' + pref + r'\b', s, I): return fam
    return ''

def detect_tpa_standards(full):
    source = normalize_material_text(full); key = _compact_tpa_key(source)
    found = []
    for value, family in _TPA_REF:
        k = _compact_tpa_key(value)
        if not k or len(k) < 4: continue
        idx = key.find(k)
        if idx >= 0: found.append((idx, value, family or detect_tpa_standard_family(value)))
    found.sort(key=lambda x: x[0])
    stds, fams = [], []
    for _, v, fam in found:
        if v not in stds: stds.append(v)
        if fam and fam not in fams: fams.append(fam)
    return {'family': ' / '.join(fams), 'standards': ' / '.join(stds)}

def detect_tpa_group(full):
    s = norm(full)
    if includes_any(s, ['фильтр', 'strainer']) or has_token(s, 'cone'): return 'Фильтр'
    if 'задвиж' in s: return 'Задвижка'
    if 'затвор' in s: return 'Затвор дисковый'
    if 'кран' in s: return 'Кран'
    if 'компенсатор' in s: return 'Компенсатор'
    if 'конденсатоотводчик' in s: return 'Конденсатоотводчик'
    if 'регулятор' in s: return 'Регулятор давления'
    if 'клапан' in s: return 'Клапан'
    return 'Прочее'

def detect_tpa_subtype(full, group):
    s = norm(full)
    if group == 'Фильтр':
        if 'фильтр сетч' in s or includes_any(s, ['mesh strainer', 'screen strainer']): return 'Фильтр сетчатый'
        if 'фильтр конич' in s or has_token(s, 'cone') or 'cone strainer' in s: return 'Фильтр конический'
        if 'фильтр корзин' in s or 'basket strainer' in s: return 'Фильтр корзинчатый'
        return ''
    if includes_any(s, ['обратн']): return 'Клапан обратный'
    if includes_any(s, ['предохран']): return 'Клапан предохранительный'
    if includes_any(s, ['регулир', 'control', 'сегмент']): return 'Клапан запорно-регулирующий'
    if group == 'Кран': return 'Кран шаровой'
    if group == 'Задвижка': return 'Задвижка клиновая'
    if group == 'Клапан': return 'Клапан запорный'
    if group == 'Прочее': return ''
    return group or ''

def detect_tpa_dn(nomenclature, full):
    nomen = normalize_inch_symbols(str(nomenclature or ''))
    fulltext = normalize_inch_symbols(str(full or ''))
    m = re.search(r'\bdn\s*[-:]?\s*(\d{1,4})(?!\s*/)(?=$|[^0-9])', nomen, I)
    if m: return 'DN' + m.group(1)
    m = re.search(r'\bdn\s*[-:]?\s*(\d{1,4})(?!\s*/)(?=$|[^0-9])', fulltext, I)
    if m: return 'DN' + m.group(1)
    inch_pat = r'(?:\b(?:dn|nps)\s*)?((?:\d+\s+)?\d+\s*/\s*\d+|\d+(?:[.,]\d+)?)\s*(?:"|\'\'|inch(?:es)?|дюйм(?:а|ов)?)'
    m = re.search(inch_pat, nomen, I) or re.search(inch_pat, fulltext, I)
    if m:
        key = normalize_inch_token(m.group(1))
        if key in _INCH_DN: return re.sub(r'\s+', '', str(_INCH_DN[key]))
    return ''

# ---- прочие правила ----
def is_drawing_reference(text):
    return re.search(r'(?:^|[^а-яa-z0-9])(?:чертеж[а-я]*|черт\.?|эск(?:из)?\.?|drawing|dwg)(?=$|[^а-яa-z0-9])', norm(text), I) is not None

def is_sdr17(nomen, ch):
    s = normalize_material_text(' '.join(x for x in [nomen, ch] if x))
    return re.search(r'(?:^|[^a-z0-9])sdr\s*[- ]?\s*17(?=$|[^a-z0-9])', s, I) is not None

def is_acrylic_or_milprf(nomen, ch):
    s = normalize_material_text(' '.join(x for x in [nomen, ch] if x))
    return re.search(r'(?:^|[^a-zа-я0-9])акрил[а-я]*(?=$|[^a-zа-я0-9])', s, I) or re.search(r'\bmil\s*[- ]?prf\b', s, I)

def detect_milprf_standard(text):
    m = re.search(r'\bmil\s*[- ]?prf\s*[- ]?\d[\w.\/-]*', str(text or ''), I)
    if m: return {'family': 'MIL-PRF', 'standard': format_std_token(re.sub(r'\s+', '-', m.group(0).upper()))}
    return {'family': '', 'standard': ''}

# ---- дерево категорий ----
def build_category_tree(cat1, ctx):
    full = norm(ctx['full'])
    material = norm(' '.join(x for x in [ctx['nomen'], ctx['ch']] if x))
    out = {'cat1': cat1 or 'Прочее', 'cat2': '', 'cat3': '', 'cat4': '', 'cat5': '',
           'metalType': ctx.get('metalType', ''), 'stdFamily': ctx.get('stdFamily', '')}
    stdFamily = ctx.get('stdFamily', ''); stdConcrete = ctx.get('stdConcrete', '')
    drawing = 'По чертежу' if is_drawing_reference(material) else ''
    c1 = out['cat1']; nomen = ctx['nomen']; ch = ctx['ch']; lvl4 = ctx.get('lvl4', '')

    if c1 == 'Днище':
        out['cat2'] = 'Заготовка' if 'заготов' in full else 'Днище'
        out['cat3'] = detect_metal_class(material, out['metalType']); out['cat4'] = stdFamily
        out['cat5'] = stdConcrete or drawing; return out
    if c1 == 'ГОСТ':
        gi = detect_gost_standard_info(full); out['stdFamily'] = gi['family'] or 'ГОСТ'
        out['cat2'] = detect_sheet_form_by_nomenclature(nomen) or detect_product_type(full)
        out['cat3'] = 'Не металл' if is_sdr17(nomen, ch) else ('Латунь' if is_brass_grade(material) else detect_metal_class(material, out['metalType']))
        out['cat4'] = gi['family'] or 'ГОСТ'; out['cat5'] = gi['standard'] or stdConcrete or drawing
        if out['cat3'] in ('Латунь', 'Алюминий', 'Магний'): out['metalType'] = 'Цветной'
        return out
    if c1 == 'Никель':
        ntext = norm(' '.join(x for x in [nomen, ch] if x))
        explicit = has_explicit_nickel_marker(ntext)
        cuni = find_copper_nickel_from_reference(ch, nomen)
        nick = find_nickel_alloy_from_reference(ch, nomen)
        out['cat2'] = detect_material_product_type(nomen, ntext)
        out['cat3'] = 'Никель' if explicit else ('Медь-никель' if (cuni or has_copper_nickel_marker(ntext)) else 'Никель')
        out['cat4'] = drawing or stdConcrete or stdFamily
        out['cat5'] = (cuni or detect_nickel_alloy(ntext)) if out['cat3'] == 'Медь-никель' else (nick or detect_nickel_alloy(ntext))
        out['metalType'] = 'Цветной'; return out
    if c1 == 'Цветной металл':
        out['cat2'] = detect_material_product_type(nomen, full)
        out['cat3'] = 'Латунь' if is_brass_grade(material) else detect_non_ferrous_subtype(material)
        out['cat4'] = stdFamily; out['cat5'] = stdConcrete or drawing; out['metalType'] = 'Цветной'; return out
    if c1 == 'Специальные листы':
        wb = detect_special_sheet_brand(full)
        if wb:
            hard = detect_special_sheet_hardness(full, wb); thick = detect_special_sheet_thickness(full, wb, hard)
            out['cat2'] = 'Лист износостойкий'; out['cat3'] = wb; out['cat4'] = hard; out['cat5'] = thick; return out
        hsb = detect_high_strength_brand(full); hss = detect_high_strength_standard(full)
        out['cat2'] = 'Лист высокопрочный'; out['cat3'] = hsb
        out['cat4'] = hss['family'] or stdFamily; out['cat5'] = hss['standard'] or stdConcrete; return out
    if c1 == 'Рельсы и рельсовый крепеж':
        l4s = is_rail_fastener_s_level(lvl4); l4f = is_rail_fastener_level(lvl4); nrail = is_rail_nomenclature(nomen)
        is_rail = (not l4s) and (nrail or (not l4f and is_rail_level(lvl4)))
        out['cat2'] = 'Рельсовый крепеж (вид S)' if l4s else ('Рельсы' if is_rail else 'Рельсовый крепеж')
        out['cat3'] = detect_rail_brand(full)
        out['cat4'] = (find_rail_type_from_reference(nomen) or detect_rail_designation(nomen)) if is_rail else (detect_rail_item_type(nomen) or detect_rail_item_type(full))
        rg = detect_gost_standards(full)
        out['cat5'] = ' / '.join(rg) if rg else (stdConcrete or '')
        if not is_rail: out['metalType'] = 'Черный'
        return out
    if c1 == 'Фланцевая прокладка':
        t = detect_gasket_type(full); out['cat2'] = t
        out['cat3'] = 'Не металл' if t == 'Неметаллическая' else detect_metal_class(material, out['metalType'])
        out['cat4'] = stdFamily; out['cat5'] = stdConcrete or drawing; return out
    if c1 == 'Крепежные изделия':
        out['cat2'] = 'Комплекты' if is_fastener_set_by_nomenclature(nomen) else detect_fastener_type(full)
        out['cat3'] = detect_metal_class(material, out['metalType']); out['cat4'] = stdFamily; out['cat5'] = stdConcrete or drawing; return out
    if c1 == 'Фитинги':
        out['cat2'] = detect_fitting_group(full); out['cat3'] = detect_metal_class(material, out['metalType'])
        out['cat4'] = stdFamily; out['cat5'] = stdConcrete or drawing; return out
    if c1 == 'Фланцы':
        out['cat2'] = detect_flange_type(full); out['cat3'] = detect_metal_class(material, out['metalType'])
        out['cat4'] = stdFamily; out['cat5'] = stdConcrete or drawing; return out
    if c1 == 'Трубы':
        out['cat2'] = detect_pipe_type(full)
        out['cat3'] = 'Не металл' if is_sdr17(nomen, ch) else detect_metal_class(material, out['metalType'])
        out['cat4'] = stdFamily; out['cat5'] = stdConcrete or drawing; return out
    if c1 == 'Трубопроводная арматура (ТПА)':
        ts = detect_tpa_standards(full)
        out['cat2'] = detect_tpa_group(full); out['cat3'] = detect_tpa_subtype(full, out['cat2'])
        out['cat4'] = detect_tpa_dn(nomen, full); out['cat5'] = ts['standards']; out['stdFamily'] = ts['family']; return out
    if c1 == 'Балки':
        n4 = norm(lvl4)
        if 'двутавр' in n4: out['cat2'] = 'Балка двутавровая'
        elif 'тавр' in n4 and 'двутавр' not in n4: out['cat2'] = 'Балка тавровая'
        elif 'двутавр' in full: out['cat2'] = 'Балка двутавровая'
        elif 'тавр' in full: out['cat2'] = 'Балка тавровая'
        else: out['cat2'] = 'Балка двутавровая'
        out['cat3'] = detect_metal_class(material, out['metalType'])
        out['cat4'] = detect_beam_series(full); out['cat5'] = detect_size_token(full, out['cat4']); return out
    if c1 == 'Профили':
        out['cat2'] = detect_profile_group(full); out['cat3'] = detect_metal_class(material, out['metalType'])
        out['cat4'] = detect_profile_series(full); out['cat5'] = detect_size_token(full, out['cat4']); return out
    if c1 == 'Прутки':
        out['cat2'] = detect_bar_type(full); out['cat3'] = detect_metal_class(material, out['metalType'])
        out['cat4'] = stdFamily; out['cat5'] = stdConcrete or drawing; return out
    if c1 == 'Проволока':
        out['cat2'] = 'Пружинная' if 'пружин' in full else ('В катушках' if 'катуш' in full else '')
        out['cat3'] = detect_metal_class(material, out['metalType']); out['cat4'] = stdFamily; out['cat5'] = stdConcrete or ''; return out
    if c1 == 'Листовой прокат (лист, лента, рулон)':
        out['cat2'] = detect_sheet_form_by_nomenclature(nomen)
        out['cat3'] = 'Не металл' if is_sdr17(nomen, ch) else detect_metal_class(material, out['metalType'])
        out['cat4'] = stdFamily; out['cat5'] = stdConcrete or ''; return out
    if c1 == 'Поковка (заготовка)':
        out['cat2'] = 'Круглая поковка' if 'круг' in full else 'Поковка'
        out['cat3'] = detect_metal_class(material, out['metalType']); out['cat4'] = stdFamily; out['cat5'] = stdConcrete or drawing; return out
    if c1 == 'Прочее' and is_level4_armature(lvl4):
        out['cat2'] = 'Арматура'; out['cat3'] = detect_metal_class(material, out['metalType'])
        out['cat4'] = stdFamily; out['cat5'] = stdConcrete or drawing; return out
    return out


def is_rail_nomenclature(nomenclature):
    s = norm(nomenclature)
    if not s: return False
    if includes_any(s, ['планк', 'проклад', 'зажим', 'крепеж', 'клемм', 'подклад', 'наклад', 'скреплен', 'болт', 'гайк', 'шайб']):
        return False
    return bool(re.search(r'(?:^|[^а-яa-z0-9])рельс(?:ы|а|у|ом|е|ов|ам|ами|ах)?(?=$|[^а-яa-z0-9])', s, I) or
                re.search(r'(?:^|[^a-z0-9])rails?(?=$|[^a-z0-9])', s, I))


def apply_drawing_category_rule(result, nomen, ch):
    if not is_drawing_reference(' '.join(x for x in [nomen, ch] if x)):
        return result
    if result['cat1'] in DRAWING_CAT4_RESERVED:
        return result
    result['cat4'] = 'По чертежу'
    if norm(result['cat5']) == 'по чертежу':
        result['cat5'] = ''
    return result


def apply_forced_black_metal_rule(result):
    if result['cat1'] == 'Трубопроводная арматура (ТПА)':
        result['metalType'] = 'Черный'
        return result
    if result['cat1'] == 'Рельсы и рельсовый крепеж' and norm(result['cat2']).startswith('рельсовый крепеж'):
        result['metalType'] = 'Черный'
    return result


def detect_metal_class(full, metal_type):
    if is_c17200_bronze(full): return 'Бронза'
    if is_beryllium(full): return 'Бериллий'
    if is_magnesium(full): return 'Магний'
    if is_titanium(full): return 'Титан'
    if is_nickel_strict('', '', '', full): return 'Никель'
    if is_copper(full): return 'Медь'
    if is_brass(full): return 'Латунь'
    if is_bronze(full): return 'Бронза'
    if is_aluminum(full): return 'Алюминий'
    if metal_type == 'Нержавеющий': return 'Нержавеющий'
    if metal_type == 'Черный': return 'Черный'
    if metal_type == 'Цветной': return 'Цветной'
    return metal_type or ''


def classify_metal_only(lvl1, product, variant):
    """Быстрый расчёт только Типа металла (Чёрный/Нержавеющий/Цветной) — без дерева."""
    full = ' '.join(p for p in [norm(lvl1), norm(product), norm(variant)] if p)
    ch, nomen = variant, product
    nickel_ref = find_nickel_alloy_from_reference(ch, nomen)
    cuni_ref = find_copper_nickel_from_reference(ch, nomen)
    if nickel_ref or cuni_ref:
        return 'Цветной'
    metal_type = detect_metal_type(lvl1, full)
    has_nonferrous = (is_beryllium(full) or is_magnesium(full) or is_titanium_grade2(full) or
                      is_ampcoloy972_copper(full) or is_c17200_bronze(full) or is_titanium(full) or
                      is_aluminum(full) or is_copper(full) or is_brass(full) or is_bronze(full))
    if has_nonferrous:
        return 'Цветной'
    if should_force_stainless(ch, nomen):
        return 'Нержавеющий'
    return metal_type
