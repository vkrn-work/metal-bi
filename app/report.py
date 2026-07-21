# -*- coding: utf-8 -*-
"""Агрегация базы КП/ЗК через DuckDB.

Правила расчёта (методика — в METRICS.md):
  КП / Заказ    — число уникальных документов (doc_number), в которых встретилась
                  категория. Единица счёта — документ, а не строка: тридцать позиций
                  труб в одном КП это один коммерческий шанс, а не тридцать.
  Позиции       — число строк (упоминаний). Мера объёма спроса, НЕ знаменатель конверсии.
  Маржа         — сумма margin_eur (всегда EUR).
  CR КП-ЗК      — уникальные ЗК / уникальные КП. Отвечает на вопрос
                  «с какой вероятностью категория из предложения дойдёт до заказа».
  Медиана заказа — медиана маржи одного заказа по категории. Медиана, а не среднее:
                  единичные сделки на десятки миллионов ломают среднее.
  Ожидаемая маржа — CR × медиана заказа. Сколько евро приносит одно предложение
                  с этой категорией. Это и есть показатель приоритета направления.
  Достоверность — при числе заказов меньше MIN_RELIABLE_ORDERS доверительный интервал
                  CR слишком широк, строка помечается как ненадёжная.
  Доли категорий — доля категории в сумме по всем категориям уровня 1.
"""
from __future__ import annotations

import os
import threading
from datetime import date, timedelta

import duckdb

CAT_COLS = ["category_1", "category_2", "category_3", "category_4", "category_5"]

# Фильтры «Клиент 1..5» и «Выборка» — какие поля базы за ними стоят
CLIENT_FILTERS = {
    "client_1": ("client_industry", "Отрасль"),
    "client_2": ("client_country", "Страна"),
    "client_3": ("client_scale", "Масштаб бизнеса"),
    "client_4": ("client_maturity", "Зрелость компании"),
    "client_5": ("client_tenure", "Длительность сотрудничества"),
    "selection": ("client_commercial_result", "Коммерческий результат"),
}
SOURCE_FIELD = "request_type"

# Меньше этого числа заказов — доверительный интервал CR слишком широк
MIN_RELIABLE_ORDERS = 30

_LOCK = threading.Lock()


class Report:
    """Держит parquet в памяти DuckDB и отвечает на запросы дашборда."""

    def __init__(self, parquet_path: str):
        self.parquet_path = parquet_path
        self.con = None
        self.rows = 0
        self.min_date = None
        self.max_date = None
        self.load()

    # ------------------------------------------------------------ загрузка
    def load(self) -> None:
        """(Пере)загружает parquet в память. Вызывается при старте и после аплоада."""
        with _LOCK:
            if not os.path.exists(self.parquet_path):
                self.con, self.rows = None, 0
                self.min_date = self.max_date = None
                return
            con = duckdb.connect(":memory:")
            con.execute("CREATE TABLE base AS SELECT * FROM read_parquet(?)", [self.parquet_path])
            dtype = con.execute(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name='base' AND column_name='doc_date'"
            ).fetchone()[0]
            if dtype.upper() not in ("DATE", "TIMESTAMP"):
                con.execute("ALTER TABLE base ALTER doc_date TYPE DATE USING TRY_CAST(doc_date AS DATE)")
            con.execute("ALTER TABLE base ALTER margin_eur TYPE DOUBLE USING TRY_CAST(margin_eur AS DOUBLE)")
            for c in CAT_COLS:
                con.execute(f"UPDATE base SET {c} = COALESCE(TRIM(CAST({c} AS VARCHAR)), '')")
            self.con = con
            self.rows = con.execute("SELECT COUNT(*) FROM base").fetchone()[0]
            self.min_date, self.max_date = con.execute(
                "SELECT MIN(doc_date), MAX(doc_date) FROM base"
            ).fetchone()

    @property
    def ready(self) -> bool:
        return self.con is not None and self.rows > 0

    # ------------------------------------------------------------ фильтры
    def filter_options(self) -> dict:
        if not self.ready:
            return {"ready": False}

        def distinct(col: str):
            rows = self.con.execute(
                f"SELECT DISTINCT TRIM(CAST({col} AS VARCHAR)) v FROM base "
                f"WHERE v IS NOT NULL AND v <> '' ORDER BY v"
            ).fetchall()
            return [r[0] for r in rows]

        out = {
            "ready": True,
            "rows": self.rows,
            "min_date": str(self.min_date),
            "max_date": str(self.max_date),
            "source": distinct(SOURCE_FIELD),
            "filters": {},
        }
        for key, (col, label) in CLIENT_FILTERS.items():
            out["filters"][key] = {"label": label, "column": col, "values": distinct(col)}
        return out

    # ------------------------------------------------------------ WHERE
    def _where(self, f: dict):
        clauses, params = [], []
        # CAST обязателен: DuckDB < 1.2 не приводит параметр-строку к DATE сам
        if f.get("date_from"):
            clauses.append("doc_date >= CAST(? AS DATE)")
            params.append(f["date_from"])
        if f.get("date_to"):
            clauses.append("doc_date <= CAST(? AS DATE)")
            params.append(f["date_to"])

        def multi(col, values):
            if values:
                clauses.append(f"TRIM(CAST({col} AS VARCHAR)) IN ({','.join('?' * len(values))})")
                params.extend(values)

        multi(SOURCE_FIELD, f.get("source"))
        for key, (col, _l) in CLIENT_FILTERS.items():
            multi(col, f.get(key))
        return (" AND ".join(clauses) or "TRUE"), params

    # ------------------------------------------------------------ показатели
    def _totals(self, where: str, params: list) -> dict:
        """Итог по всей выборке. Считается отдельно, а не суммой категорий:
        один документ попадает сразу в несколько категорий."""
        row = self.con.execute(
            f"""
            WITH f AS (SELECT * FROM base WHERE {where}),
            d AS (
              SELECT doc_type, doc_number,
                     SUM(margin_eur) AS doc_margin, COUNT(*) AS lines
              FROM f GROUP BY doc_type, doc_number
            )
            SELECT
              COUNT(*)          FILTER (WHERE doc_type='КП'),
              COALESCE(SUM(lines)       FILTER (WHERE doc_type='КП'), 0),
              COALESCE(SUM(doc_margin)  FILTER (WHERE doc_type='КП'), 0),
              COUNT(*)          FILTER (WHERE doc_type='ЗК'),
              COALESCE(SUM(lines)       FILTER (WHERE doc_type='ЗК'), 0),
              COALESCE(SUM(doc_margin)  FILTER (WHERE doc_type='ЗК'), 0),
              MEDIAN(doc_margin)        FILTER (WHERE doc_type='ЗК')
            FROM d
            """,
            params,
        ).fetchone()
        return self._pack(row)

    @staticmethod
    def _pack(row) -> dict:
        """Собирает показатели строки из сырых агрегатов."""
        kp_docs, kp_lines, kp_margin, zk_docs, zk_lines, zk_margin, zk_median = row
        kp_docs, zk_docs = int(kp_docs or 0), int(zk_docs or 0)
        kp_margin, zk_margin = float(kp_margin or 0), float(zk_margin or 0)
        zk_median = float(zk_median) if zk_median is not None else None
        cr = (zk_docs / kp_docs) if kp_docs else None
        return {
            "kp_docs": kp_docs,
            "kp_lines": int(kp_lines or 0),
            "kp_margin": kp_margin,
            "zk_docs": zk_docs,
            "zk_lines": int(zk_lines or 0),
            "zk_margin": zk_margin,
            "cr_count": cr,
            "zk_median": zk_median,
            # ожидаемая маржа на одно предложение = шанс × типичный размер заказа
            "expected": (cr * zk_median) if (cr is not None and zk_median is not None) else None,
            "reliable": zk_docs >= MIN_RELIABLE_ORDERS,
        }

    def _level(self, where: str, params: list, depth: int) -> list:
        """Агрегация по уровню категорий depth (1..5).

        Сначала сворачиваем в документы (строки → документ), потом считаем
        показатели. Уникальные документы считаем на каждом уровне отдельно:
        суммировать детей нельзя, один документ попадает в разные подкатегории.
        """
        cols = CAT_COLS[:depth]
        group = ", ".join(cols)
        extra = f" AND TRIM({CAT_COLS[depth-1]}) <> ''" if depth > 1 else ""
        rows = self.con.execute(
            f"""
            WITH f AS (SELECT * FROM base WHERE {where}{extra}),
            d AS (
              SELECT {group}, doc_type, doc_number,
                     SUM(margin_eur) AS doc_margin, COUNT(*) AS lines
              FROM f GROUP BY {group}, doc_type, doc_number
            )
            SELECT {group},
              COUNT(*)          FILTER (WHERE doc_type='КП')            AS kp_docs,
              COALESCE(SUM(lines)      FILTER (WHERE doc_type='КП'), 0) AS kp_lines,
              COALESCE(SUM(doc_margin) FILTER (WHERE doc_type='КП'), 0) AS kp_margin,
              COUNT(*)          FILTER (WHERE doc_type='ЗК')            AS zk_docs,
              COALESCE(SUM(lines)      FILTER (WHERE doc_type='ЗК'), 0) AS zk_lines,
              COALESCE(SUM(doc_margin) FILTER (WHERE doc_type='ЗК'), 0) AS zk_margin,
              MEDIAN(doc_margin)       FILTER (WHERE doc_type='ЗК')     AS zk_median
            FROM d GROUP BY {group}
            HAVING kp_docs > 0 OR zk_docs > 0
            """,
            params,
        ).fetchall()
        out = []
        for r in rows:
            path = [str(x or "") for x in r[:depth]]
            node = self._pack(r[depth:])
            node["path"] = path
            node["name"] = path[-1] or "(не указано)"
            node["depth"] = depth
            out.append(node)
        return out

    def _tree(self, where: str, params: list) -> list:
        by_key, roots = {}, []
        for depth in range(1, 6):
            for node in self._level(where, params, depth):
                node["children"] = []
                by_key[tuple(node["path"])] = node
                if depth == 1:
                    roots.append(node)
                else:
                    parent = by_key.get(tuple(node["path"][:-1]))
                    if parent is not None:
                        parent["children"].append(node)
        for node in by_key.values():
            node["children"].sort(key=lambda n: (-n["kp_docs"], n["name"]))
            node.pop("path", None)
        roots.sort(key=lambda n: (-n["kp_docs"], n["name"]))
        return roots

    def _halfyears(self, where: str, params: list) -> list:
        rows = self.con.execute(
            f"""
            WITH f AS (SELECT * FROM base WHERE {where} AND doc_date IS NOT NULL),
            d AS (
              SELECT YEAR(doc_date) AS y,
                     CASE WHEN MONTH(doc_date) <= 6 THEN 1 ELSE 2 END AS h,
                     doc_type, doc_number, SUM(margin_eur) AS doc_margin
              FROM f GROUP BY y, h, doc_type, doc_number
            )
            SELECT y, h,
              COUNT(*) FILTER (WHERE doc_type='КП'),
              COUNT(*) FILTER (WHERE doc_type='ЗК'),
              MEDIAN(doc_margin) FILTER (WHERE doc_type='ЗК')
            FROM d GROUP BY y, h ORDER BY y, h
            """,
            params,
        ).fetchall()
        out = []
        for y, h, kp_docs, zk_docs, zk_median in rows:
            cr = (zk_docs / kp_docs) if kp_docs else None
            med = float(zk_median) if zk_median is not None else None
            out.append({
                "label": f"{h} полугодие {y}",
                "short": f"{h} полугодие",
                "cr_count": cr,
                "expected": (cr * med) if (cr is not None and med is not None) else None,
            })
        return out

    @staticmethod
    def _shares(tree: list, field: str, top: int = 10) -> list:
        total = sum(n[field] for n in tree) or 1
        ranked = sorted(tree, key=lambda n: -n[field])
        head, tail = ranked[:top], ranked[top:]
        out = [{"name": n["name"], "value": n[field], "share": n[field] / total} for n in head]
        if tail:
            rest = sum(n[field] for n in tail)
            out.append({"name": "Другие", "value": rest, "share": rest / total})
        return out

    @staticmethod
    def _prev_window(f: dict):
        """Предыдущий период той же длины — для стрелок «к периоду ранее»."""
        if not (f.get("date_from") and f.get("date_to")):
            return None
        try:
            d1 = date.fromisoformat(f["date_from"])
            d2 = date.fromisoformat(f["date_to"])
        except ValueError:
            return None
        span = (d2 - d1).days + 1
        prev = dict(f)
        prev["date_to"] = str(d1 - timedelta(days=1))
        prev["date_from"] = str(d1 - timedelta(days=span))
        return prev

    # ------------------------------------------------------------ точка входа
    def build(self, f: dict) -> dict:
        if not self.ready:
            return {"ready": False}
        where, params = self._where(f)
        totals = self._totals(where, params)
        tree = self._tree(where, params)

        prev_totals = None
        prev = self._prev_window(f)
        if prev:
            pw, pp = self._where(prev)
            pt = self._totals(pw, pp)
            if pt["kp_docs"]:
                prev_totals = pt

        return {
            "ready": True,
            "period": {"from": f.get("date_from"), "to": f.get("date_to")},
            "data_period": {"from": str(self.min_date), "to": str(self.max_date)},
            "totals": totals,
            "prev_totals": prev_totals,
            "tree": tree,
            "shares_kp": self._shares(tree, "kp_docs"),
            "shares_zk": self._shares(tree, "zk_docs"),
            "halfyears": self._halfyears(where, params),
        }
