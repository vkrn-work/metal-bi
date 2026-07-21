# -*- coding: utf-8 -*-
"""
prepare_base.py — автономная подготовка основной базы.

Что делает:
  1. Читает все файлы КП и ЗК из папок data/kp/ и data/zk/ (можно много файлов за разные годы).
  2. Приводит их к единой схеме (КП+ЗК в одну таблицу, тип документа проставляется).
  3. Классифицирует номенклатуру (Категория 1 + Тип металла) — по уникальным парам, быстро.
  4. Нормализует код клиента (старая/новая кодировка) и джойнит справочник клиентов.
  5. Джойнит справочник менеджеров (Компания, Отдел продаж).
  6. Пишет один Parquet в out/base.parquet.

Запуск:  python3 prepare_base.py
Опции:   --kp <dir> --zk <dir> --clients <xlsx> --managers <xlsx> --out <parquet>
"""
import argparse
import glob
import os
import re
import sys

import pandas as pd

import config
import classify as clf


# ----------------------------------------------------------------- утилиты
def _norm_header(h):
    return re.sub(r'\s+', ' ', str(h or '').strip()).lower()


def _resolve_columns(df, mapping):
    """Сопоставляет фактические заголовки с mapping по нормализованному имени."""
    norm_to_actual = {}
    for col in df.columns:
        norm_to_actual.setdefault(_norm_header(col), col)
    out = {}
    for want, target in mapping.items():
        actual = norm_to_actual.get(_norm_header(want))
        if actual is not None:
            out[actual] = target
    return out


def _pick_sheet(path, candidates):
    names = pd.ExcelFile(path).sheet_names
    for c in candidates:
        if c in names:
            return c
    return names[0]


def read_source(path, mapping, sheet, doc_type):
    chosen = _pick_sheet(path, [sheet, 'Лист1', 'Исходная', 'TDSheet'])
    df = pd.read_excel(path, sheet_name=chosen, dtype=str)
    cols = _resolve_columns(df, mapping)
    missing = set(mapping.values()) - set(cols.values())
    if missing:
        print(f"  ⚠ {os.path.basename(path)}: не найдены столбцы {sorted(missing)}")
    df = df[list(cols.keys())].rename(columns=cols)
    df['doc_type'] = doc_type
    for c in ['lvl1', 'lvl2', 'lvl3', 'lvl4', 'product', 'variant',
              'manager', 'client_code', 'currency', 'request_type', 'author', 'found_by']:
        if c not in df.columns:
            df[c] = ''
    return df


def read_many(folder, mapping, sheet, doc_type):
    files = sorted(glob.glob(os.path.join(folder, '*.xlsx')))
    if not files:
        print(f"  (в {folder} нет .xlsx)")
        return pd.DataFrame()
    frames = []
    for f in files:
        print(f"  читаю {os.path.basename(f)} ...")
        frames.append(read_source(f, mapping, sheet, doc_type))
    return pd.concat(frames, ignore_index=True)


def normalize_client_key(code):
    """Единый ключ клиента: только цифры без ведущих нулей.
    Сглаживает старую/новую кодировку (00-00018093, 000004423, UT-000...)."""
    if code is None:
        return None
    digits = re.sub(r'\D', '', str(code))
    if not digits:
        return None
    return digits.lstrip('0') or '0'


def parse_margin(v):
    if v is None:
        return None
    s = str(v).replace('\xa0', '').replace(' ', '').replace(',', '.')
    try:
        return float(s)
    except ValueError:
        return None


# ----------------------------------------------------------------- основной поток
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kp', default='data/kp')
    ap.add_argument('--zk', default='data/zk')
    ap.add_argument('--clients', default='data/clients.xlsx')
    ap.add_argument('--managers', default='data/managers.xlsx')
    ap.add_argument('--out', default='out/base.parquet')
    args = ap.parse_args()

    print("1) Читаю КП:")
    kp = read_many(args.kp, config.KP_MAP, 'Лист1', 'КП')
    print("2) Читаю ЗК:")
    zk = read_many(args.zk, config.ZK_MAP, config.ZK_SHEET, 'ЗК')

    base = pd.concat([kp, zk], ignore_index=True)
    if base.empty:
        print("Нет данных. Проверьте папки data/kp и data/zk.")
        sys.exit(1)
    print(f"   всего строк КП+ЗК: {len(base):,}")

    # 3) Категории: берём готовые из файла (проставлены рабочим скриптом),
    #    классификатор — запасной вариант для строк без категорий.
    print("3) Категории и Тип металла...")
    cat_cols = ['category_1', 'category_2', 'category_3', 'category_4', 'category_5']
    for c in cat_cols:
        if c not in base.columns:
            base[c] = ''
    base[cat_cols] = base[cat_cols].fillna('')
    filled = (base['category_1'].astype(str).str.strip() != '').mean()
    print(f"   категории из файла заполнены: {filled*100:.1f}% строк")

    # Тип металла — быстрый расчёт по уникальным (lvl1, product, variant)
    mkey = ['lvl1', 'product', 'variant']
    um = base[mkey].fillna('').drop_duplicates()
    mcache = {tuple(r): clf.classify_metal_only(*r) for r in um.itertuples(index=False)}
    base['metal_type'] = [mcache[tuple(x)] for x in base[mkey].fillna('').itertuples(index=False)]

    # Запасной классификатор только для строк без категорий в файле
    empty = base['category_1'].astype(str).str.strip() == ''
    if empty.any():
        print(f"   у {int(empty.sum())} строк категорий нет — считаю классификатором")
        key_cols = ['lvl1', 'lvl2', 'lvl3', 'lvl4', 'product', 'variant']
        uk = base.loc[empty, key_cols].fillna('').drop_duplicates()
        ccache = {tuple(r): clf.classify(*r) for r in uk.itertuples(index=False)}
        for idx in base.index[empty]:
            r = ccache[tuple(base.loc[idx, key_cols].fillna(''))]
            for k in range(1, 6):
                base.at[idx, f'category_{k}'] = r[f'category_{k}']
    base['standard'] = ''

    # 4) Нормализация кода клиента + джойн клиентов
    print("4) Джойню справочник клиентов...")
    base['client_key'] = base['client_code'].map(normalize_client_key)
    if os.path.exists(args.clients):
        cl = pd.read_excel(args.clients, sheet_name=0, dtype=str)
        cl_cols = _resolve_columns(cl, {config.CLIENTS_KEY: 'client_code_ref', **config.CLIENTS_ATTRS})
        cl = cl[list(cl_cols.keys())].rename(columns=cl_cols)
        cl['client_key'] = cl['client_code_ref'].map(normalize_client_key)
        cl = cl.drop(columns=['client_code_ref']).drop_duplicates('client_key')
        base = base.merge(cl, on='client_key', how='left')
        matched = base['client_industry'].notna().mean() if 'client_industry' in base else 0
        print(f"   клиентов подтянуто: {matched*100:.1f}% строк")
    else:
        print(f"   ⚠ файл клиентов не найден: {args.clients}")

    # 5) Джойн менеджеров
    print("5) Джойню справочник менеджеров...")
    if os.path.exists(args.managers):
        mg = pd.read_excel(args.managers, sheet_name=0, dtype=str)
        mg_cols = _resolve_columns(mg, config.MANAGERS_MAP)
        mg = mg[list(mg_cols.keys())].rename(columns=mg_cols)
        mg['manager'] = mg['manager'].str.strip()
        mg = mg.drop_duplicates('manager')
        base['manager'] = base['manager'].fillna('').str.strip()
        base = base.merge(mg, on='manager', how='left')
        miss = sorted(set(base.loc[base['company'].isna(), 'manager']) - {''})
        if miss:
            print(f"   ⚠ менеджеров нет в справочнике ({len(miss)}): {', '.join(miss[:8])}"
                  + (' ...' if len(miss) > 8 else ''))
    else:
        print(f"   ⚠ файл менеджеров не найден: {args.managers}")
        base['company'] = ''
        base['sales_dept'] = ''

    # 6) Типы и выгрузка
    print("6) Финализирую типы и пишу Parquet...")
    base['margin_eur'] = base['margin_eur'].map(parse_margin)
    base['doc_date'] = pd.to_datetime(base['doc_date'], dayfirst=True, errors='coerce').dt.date

    for c in config.TARGET_FIELDS:
        if c not in base.columns:
            base[c] = None
    attr_cols = list(config.CLIENTS_ATTRS.values())
    final_cols = config.TARGET_FIELDS + [c for c in attr_cols if c in base.columns]
    out = base[final_cols].copy()

    os.makedirs(os.path.dirname(args.out) or '.', exist_ok=True)
    for c in out.columns:
        if out[c].dtype == object:
            out[c] = out[c].fillna('').replace({'nan': '', 'None': '', 'NaT': ''})
    out.to_parquet(args.out, index=False, compression='zstd')
    print(f"\n✅ Готово: {len(out):,} строк → {args.out}")
    print("\nСводка по Категории 1:")
    print(out['category_1'].value_counts().head(25).to_string())


if __name__ == '__main__':
    main()
