# -*- coding: utf-8 -*-
"""Агрегация базы КП/ЗК через DuckDB.

Правила расчёта (согласованы с референсным отчётом):
  COUNTUNIQUE   — число уникальных документов внутри группы (doc_number);
                  итог по выборке считается отдельно, а не суммой групп,
                  потому что один документ попадает в несколько категорий.
  SUM из Маржа  — сумма margin_eur (всегда EUR).
  CR КП-ЗК      — уникальные ЗК / уникальные КП.
  CR деньги     — маржа ЗК / маржа КП.
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
        kp_docs, zk_docs, kp_margin, zk_margin = self.con.execute(
            f"""
            SELECT
              COUNT(DISTINCT CASE WHEN doc_type='КП' THEN doc_number END),
              COUNT(DISTINCT CASE WHEN doc_type='ЗК' THEN doc_number END),
              COALESCE(SUM(CASE WHEN doc_type='КП' THEN margin_eur END), 0),
              COALESCE(SUM(CASE WHEN doc_type='ЗК' THEN margin_eur END), 0)
            FROM base WHERE {where}
            """,
            params,
        ).fetchone()
        kp_margin, zk_margin = float(kp_margin or 0), float(zk_margin or 0)
        return {
            "kp_docs": kp_docs,
            "zk_docs": zk_docs,
            "kp_margin": kp_margin,
            "zk_margin": zk_margin,
            "cr_count": (zk_docs / kp_docs) if kp_docs else None,
            "cr_money": (zk_margin / kp_margin) if kp_margin else None,
        }

    def _level(self, where: str, params: list, depth: int) -> list:
        """Агрегация по уровню категорий depth (1..5).

        Уникальные документы считаем на каждом уровне отдельно: суммировать
        детей нельзя, один документ может встречаться в нескольких подкатегориях.
        """
        cols = CAT_COLS[:depth]
        group = ", ".join(cols)
        extra = f" AND TRIM({CAT_COLS[depth-1]}) <> ''" if depth > 1 else ""
        rows = self.con.execute(
            f"""
            SELECT {group},
              COUNT(DISTINCT CASE WHEN doc_type='КП' THEN doc_number END) AS kp_docs,
              COALESCE(SUM(CASE WHEN doc_type='КП' THEN margin_eur END), 0) AS kp_margin,
              COUNT(DISTINCT CASE WHEN doc_type='ЗК' THEN doc_number END) AS zk_docs,
              COALESCE(SUM(CASE WHEN doc_type='ЗК' THEN margin_eur END), 0) AS zk_margin
            FROM base WHERE {where}{extra}
            GROUP BY {group}
            HAVING kp_docs > 0 OR zk_docs > 0
            """,
            params,
        ).fetchall()
        out = []
        for r in rows:
            path = [str(x or "") for x in r[:depth]]
            kp_docs, kp_margin, zk_docs, zk_margin = r[depth:]
            kp_margin, zk_margin = float(kp_margin or 0), float(zk_margin or 0)
            out.append({
                "path": path,
                "name": path[-1] or "(не указано)",
                "depth": depth,
                "kp_docs": kp_docs,
                "kp_margin": kp_margin,
                "zk_docs": zk_docs,
                "zk_margin": zk_margin,
                "cr_count": (zk_docs / kp_docs) if kp_docs else None,
                "cr_money": (zk_margin / kp_margin) if kp_margin else None,
            })
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
            SELECT YEAR(doc_date) AS y,
                   CASE WHEN MONTH(doc_date) <= 6 THEN 1 ELSE 2 END AS h,
              COUNT(DISTINCT CASE WHEN doc_type='КП' THEN doc_number END),
              COUNT(DISTINCT CASE WHEN doc_type='ЗК' THEN doc_number END),
              COALESCE(SUM(CASE WHEN doc_type='КП' THEN margin_eur END), 0),
              COALESCE(SUM(CASE WHEN doc_type='ЗК' THEN margin_eur END), 0)
            FROM base WHERE {where} AND doc_date IS NOT NULL
            GROUP BY y, h ORDER BY y, h
            """,
            params,
        ).fetchall()
        out = []
        for y, h, kp_docs, zk_docs, kp_margin, zk_margin in rows:
            kp_margin, zk_margin = float(kp_margin or 0), float(zk_margin or 0)
            out.append({
                "label": f"{h} полугодие {y}",
                "short": f"{h} полугодие",
                "cr_count": (zk_docs / kp_docs) if kp_docs else None,
                "cr_money": (zk_margin / kp_margin) if kp_margin else None,
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
