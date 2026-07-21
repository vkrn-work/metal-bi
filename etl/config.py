# -*- coding: utf-8 -*-
"""Соответствия столбцов исходных файлов → единая схема."""

# Итоговые поля основной базы (плюс атрибуты клиента, добавляются джойном)
TARGET_FIELDS = [
    'doc_type', 'doc_number', 'doc_date', 'manager', 'company', 'sales_dept',
    'client_code', 'client_key',
    'category_1', 'category_2', 'category_3', 'category_4', 'category_5',
    'metal_type', 'standard',
    'product', 'variant', 'margin_eur', 'currency', 'request_type', 'author', 'found_by',
]

# КП: заголовки как в файле → единые имена. Уровни ищем по нормализованному имени (двойные пробелы и т.п.).
KP_MAP = {
    'Дата КП': 'doc_date',
    'Номер КП': 'doc_number',
    'Менеджер': 'manager',
    'Код клиента': 'client_code',
    'Категория 1': 'category_1', 'Категория 2': 'category_2', 'Категория 3': 'category_3',
    'Категория 4': 'category_4', 'Категория 5': 'category_5',
    '1 уровень': 'lvl1', '2 уровень': 'lvl2', '3 уровень': 'lvl3', '4 уровень': 'lvl4',
    'Номенклатура': 'product',
    'Характеристика': 'variant',
    'Валюта': 'currency',
    'Маржа по позиции вкп': 'margin_eur',   # маржа всегда в EUR
    'Тип заявки': 'request_type',
    'Автор': 'author',
    'Нашел': 'found_by',
}

# ЗК (лист "Лист1" — очищенный, 20 столбцов)
ZK_MAP = {
    'Дата ЗК': 'doc_date',
    'Номер ЗК': 'doc_number',
    'Менеджер': 'manager',
    'Код клиента': 'client_code',
    'Категория 1': 'category_1', 'Категория 2': 'category_2', 'Категория 3': 'category_3',
    'Категория 4': 'category_4', 'Категория 5': 'category_5',
    '1 уровень': 'lvl1', '2 уровень': 'lvl2', '3 уровень': 'lvl3', '4 уровень': 'lvl4',
    'Номенклатура': 'product',
    'Характеристика': 'variant',
    'Валюта': 'currency',
    'Маржа': 'margin_eur',                   # маржа всегда в EUR
    'Тип заявки': 'request_type',
    'Автор': 'author',
    'Нашел': 'found_by',
}
ZK_SHEET = 'Лист1'  # берём очищенный лист, не TDSheet

# Справочник менеджеров: Менеджер → Компания, Отделы продаж
MANAGERS_MAP = {'Менеджер': 'manager', 'Отделы продаж': 'sales_dept', 'Компания': 'company'}

# Справочник клиентов: ключ + атрибуты, которые тянем в базу
CLIENTS_KEY = 'Код клиента (1с)'
CLIENTS_ATTRS = {
    'Клиент.Отрасль': 'client_industry',
    'Клиент.Бизнес-регион': 'client_region',
    'Дата регистрации в 1с': 'client_reg_date',
    'Страна': 'client_country',
    'Клиент.Годовой оборот, евро (1с)': 'client_turnover_eur',
    'Масштаб бизнеса': 'client_scale',
    'Зрелость компании': 'client_maturity',
    'Длительность сотрудничества': 'client_tenure',
    'КП-активность': 'client_kp_activity',
    'Коммерческий результат': 'client_commercial_result',
}
