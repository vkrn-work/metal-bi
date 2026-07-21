# -*- coding: utf-8 -*-
"""Агрегация базы КП/ЗК через DuckDB.

Правила расчёта (методика — в METRICS.md и на странице /metrics):
  КП / Заказ    — число уникальных документов (doc_number), в которых встретилась
                  категория. Единица счёта — документ, а не строка.
  Позиции       — число строк. Мера объёма спроса, НЕ знаменатель конверсии.
  CR КП→ЗК      — уникальные ЗК / уникальные КП.
  Медиана заказа — медиана маржи одного заказа. Медиана, а не среднее.
  Ожидаемая маржа — CR × медиана заказа. Показатель приоритета направления.
  Дельта        — изменение CR к предыдущему периоду той же длины.
  Достоверность — при числе заказов меньше MIN_RELIABLE_ORDERS строка ненадёжна.
"""
from __future__ import annotations

import os
import threading
from datetime import date, timedelta

import duckdb

CAT_COLS = ["category_1", "category_2", "category_3", "category_4", "category_5"]

# Селекторы фильтров: ключ в запросе -> (поле базы, подпись)
CLIENT_FILTERS = {
    "source": ("request_type", "Источник"),
    "tenure": ("client_tenure", "Длительность сотрудничества"),
    "maturity": ("client_maturity", "Зрелость"),
    "scale": ("client_scale", "Масштаб клиента"),
    "result": ("client_commercial_result", "Ком. результат"),
}

# Пустой источник в выгрузке = клиент обратился напрямую
DIRECT_SOURCE = "Отдел продаж"

# Варианты порога «Мин. КП» для топов номенклатуры
MIN_KP_OPTIONS = [10, 20, 50, 100, 200]
DEFAULT_MIN_KP = 100

# Меньше этого числа заказов — доверительный интервал CR слишком широк
MIN_RELIABLE_ORDERS = 30

MONTHS_RU = ["янв", "фев", "мар", "апр", "май", "июн",
             "июл", "авг", "сен", "окт", "ноя", "дек"]

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
            # Пустой источник -> «Отдел продаж». Дублирует ETL, чтобы старые базы тоже работали.
            con.execute(
                "UPDATE base SET request_type = ? "
                "WHERE request_type IS NULL OR TRIM(CAST(request_type AS VARCHAR)) = ''",
                [DIRECT_SOURCE],
            )
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
            "min_kp_options": MIN_KP_OPTIONS,
            "min_kp_default": DEFAULT_MIN_KP,
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
            "expected": (cr * zk_median) if (cr is not None and zk_median is not None) else None,
            "reliable": zk_docs >= MIN_RELIABLE_ORDERS,
        }

    def _level(self, where: str, params: list, depth: int) -> list:
        """Агрегация по уровню категорий depth (1..5).

        Сначала сворачиваем в документы, потом считаем показатели.
        Суммировать детей нельзя: один документ попадает в разные подкатегории.
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

    def _monthly(self, where: str, params: list) -> list:
        """Ряд по месяцам для скаттеров в карточках показателей."""
        rows = self.con.execute(
            f"""
            WITH f AS (SELECT * FROM base WHERE {where} AND doc_date IS NOT NULL),
            d AS (
              SELECT DATE_TRUNC('month', doc_date) AS m, doc_type, doc_number,
                     SUM(margin_eur) AS doc_margin
              FROM f GROUP BY m, doc_type, doc_number
            )
            SELECT m,
              COUNT(*) FILTER (WHERE doc_type='КП'),
              COUNT(*) FILTER (WHERE doc_type='ЗК'),
              MEDIAN(doc_margin) FILTER (WHERE doc_type='ЗК')
            FROM d GROUP BY m ORDER BY m
            """,
            params,
        ).fetchall()
        out = []
        for m, kp_docs, zk_docs, zk_median in rows:
            out.append({
                "month": str(m)[:7],
                "label": MONTHS_RU[m.month - 1],
                "kp_docs": int(kp_docs or 0),
                "zk_docs": int(zk_docs or 0),
                "cr_count": (zk_docs / kp_docs) if kp_docs else None,
                "zk_median": float(zk_median) if zk_median is not None else None,
            })
        return out

    def _products(self, where: str, params: list, min_kp: int, top: int = 10) -> dict:
        """Лучшие и худшие позиции номенклатуры по конверсии.

        Порог min_kp обязателен: без него в топ попадут позиции с одним КП
        и одним заказом, то есть конверсией 100%.
        """
        rows = self.con.execute(
            f"""
            WITH f AS (SELECT * FROM base WHERE {where}),
            d AS (
              SELECT product, doc_type, doc_number, SUM(margin_eur) AS doc_margin
              FROM f WHERE TRIM(CAST(product AS VARCHAR)) <> ''
              GROUP BY product, doc_type, doc_number
            )
            SELECT product,
              COUNT(*) FILTER (WHERE doc_type='КП') AS kp_docs,
              COUNT(*) FILTER (WHERE doc_type='ЗК') AS zk_docs,
              MEDIAN(doc_margin) FILTER (WHERE doc_type='ЗК') AS zk_median
            FROM d GROUP BY product
            HAVING kp_docs >= ?
            """,
            params + [min_kp],
        ).fetchall()
        items = []
        for product, kp_docs, zk_docs, zk_median in rows:
            items.append({
                "name": product,
                "kp_docs": int(kp_docs or 0),
                "zk_docs": int(zk_docs or 0),
                "cr_count": (zk_docs / kp_docs) if kp_docs else 0.0,
                "zk_median": float(zk_median) if zk_median is not None else None,
            })
        items.sort(key=lambda x: (-x["cr_count"], -x["kp_docs"]))
        return {
            "min_kp": min_kp,
            "qualified": len(items),
            "best": items[:top],
            "worst": list(reversed(items[-top:])) if items else [],
        }

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
    def _apply_delta(tree: list, prev_tree: list) -> None:
        """Проставляет каждой строке изменение CR к предыдущему периоду.

        delta_pp   — изменение в процентных пунктах;
        delta_rel  — то же в процентах от прежнего значения.
        """
        prev_by_name = {}

        def collect(nodes, path):
            for n in nodes:
                key = tuple(path + [n["name"]])
                prev_by_name[key] = n
                collect(n.get("children", []), list(key))

        collect(prev_tree, [])

        def walk(nodes, path):
            for n in nodes:
                key = tuple(path + [n["name"]])
                p = prev_by_name.get(key)
                n["cr_prev"] = p["cr_count"] if p else None
                if p and p["cr_count"] and n["cr_count"] is not None:
                    n["delta_pp"] = (n["cr_count"] - p["cr_count"]) * 100
                    n["delta_rel"] = (n["cr_count"] - p["cr_count"]) / p["cr_count"]
                else:
                    n["delta_pp"] = None
                    n["delta_rel"] = None
                walk(n.get("children", []), list(key))

        walk(tree, [])

    @staticmethod
    def _prev_window(f: dict):
        """Предыдущий период той же длины — для дельт и стрелок."""
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
                self._apply_delta(tree, self._tree(pw, pp))
        if prev_totals is None:
            self._apply_delta(tree, [])

        try:
            min_kp = int(f.get("min_kp") or DEFAULT_MIN_KP)
        except (TypeError, ValueError):
            min_kp = DEFAULT_MIN_KP

        return {
            "ready": True,
            "period": {"from": f.get("date_from"), "to": f.get("date_to")},
            "data_period": {"from": str(self.min_date), "to": str(self.max_date)},
            "totals": totals,
            "prev_totals": prev_totals,
            "tree": tree,
            "shares_kp": self._shares(tree, "kp_docs"),
            "shares_zk": self._shares(tree, "zk_docs"),
            "monthly": self._monthly(where, params),
            "products": self._products(where, params, min_kp),
        }
