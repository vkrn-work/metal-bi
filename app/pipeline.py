# -*- coding: utf-8 -*-
"""Пересборка базы из выгрузок CRM.

Файлы, загруженные через интерфейс, раскладываются в рабочую папку и
прогоняются через etl/prepare_base.py — тот самый конвейер, что работает
локально. Результат кладётся в data/base.parquet и подхватывается дашбордом.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ETL_DIR = os.path.join(ROOT, "etl")


class RebuildJob:
    """Состояние фоновой пересборки — его показывает страница загрузки."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.log = []
        self.finished_at = None
        self.ok = None

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "running": self.running,
                "log": list(self.log),
                "finished_at": self.finished_at,
                "ok": self.ok,
            }

    def _say(self, line: str) -> None:
        with self.lock:
            self.log.append(line)

    # ------------------------------------------------------------------
    def start(self, work_dir: str, out_parquet: str, on_done) -> bool:
        """Запускает пересборку в фоне. Возвращает False, если уже идёт."""
        with self.lock:
            if self.running:
                return False
            self.running = True
            self.log = []
            self.ok = None
            self.finished_at = None
        threading.Thread(
            target=self._run, args=(work_dir, out_parquet, on_done), daemon=True
        ).start()
        return True

    def _run(self, work_dir: str, out_parquet: str, on_done) -> None:
        started = time.time()
        try:
            script = os.path.join(ETL_DIR, "prepare_base.py")
            if not os.path.exists(script):
                raise RuntimeError("etl/prepare_base.py не найден в сборке")

            tmp_out = out_parquet + ".new"
            cmd = [
                sys.executable, script,
                "--kp", os.path.join(work_dir, "kp"),
                "--zk", os.path.join(work_dir, "zk"),
                "--clients", os.path.join(work_dir, "clients.xlsx"),
                "--managers", os.path.join(work_dir, "managers.xlsx"),
                "--out", tmp_out,
            ]
            self._say("Запускаю конвейер подготовки базы...")
            proc = subprocess.Popen(
                cmd, cwd=ETL_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
            )
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    self._say(line)
            code = proc.wait()
            if code != 0:
                raise RuntimeError(f"конвейер завершился с кодом {code}")
            if not os.path.exists(tmp_out):
                raise RuntimeError("конвейер не создал parquet")

            # подменяем базу только после успешной сборки
            if os.path.exists(out_parquet):
                shutil.copy2(out_parquet, out_parquet + ".bak")
            os.replace(tmp_out, out_parquet)
            self._say(f"Готово за {time.time() - started:.0f} с. База обновлена.")
            ok = True
        except Exception as exc:  # noqa: BLE001 — текст ошибки нужен в интерфейсе
            self._say(f"ОШИБКА: {exc}")
            ok = False
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
            with self.lock:
                self.running = False
                self.ok = ok
                self.finished_at = datetime.now().strftime("%d.%m.%Y %H:%M")
            if ok:
                try:
                    on_done()
                except Exception as exc:  # noqa: BLE001
                    self._say(f"Не удалось перечитать базу: {exc}")


def etl_available():
    """Проверяет, что конвейер полностью на месте (classify.py + справочники)."""
    if not os.path.exists(os.path.join(ETL_DIR, "prepare_base.py")):
        return False, "нет etl/prepare_base.py"
    if not os.path.exists(os.path.join(ETL_DIR, "classify.py")):
        return False, "нет etl/classify.py — скопируйте его из emk_bi вместе с папкой references/"
    refs = os.path.join(ETL_DIR, "references")
    if not os.path.isdir(refs) or not any(f.endswith(".csv") for f in os.listdir(refs)):
        return False, "пустая папка etl/references — скопируйте CSV-справочники из emk_bi"
    return True, "конвейер готов"
