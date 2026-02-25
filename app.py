import streamlit as st
import pandas as pd
import plotly.express as px

# Настройка страницы
st.set_page_config(page_title="Metal Marketing BI", layout="wide", page_icon="🏗️")

# Загрузка данных
@st.cache_data
def load_data():
    df = pd.read_csv('database_demo.csv')
    df['Дата'] = pd.to_datetime(df['Дата'])
    return df

try:
    df = load_data()

    # Заголовок
    st.title("🏗️ Аналитическая панель: Металлургия & Маркетинг")
    st.info("Демонстрационный отчет на основе сгенерированных данных")
    st.markdown("---")

    # Фильтры в сайдбаре
    st.sidebar.header("Настройки фильтрации")
    region_filter = st.sidebar.multiselect("Регион поставки", df['Регион'].unique(), default=df['Регион'].unique())
    cat_filter = st.sidebar.multiselect("Категория продукции", df['Категория'].unique(), default=df['Категория'].unique())

    # Применение фильтров
    filtered_df = df[df['Регион'].isin(region_filter) & df['Категория'].isin(cat_filter)]

    # KPI метрики
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Выручка (₽)", f"{filtered_df['Выручка'].sum():,.0f}")
    with col2:
        st.metric("Объем (Тонн)", f"{filtered_df['Тоннаж'].sum():,.1f}")
    with col3:
        st.metric("Затраты маркетинг", f"{filtered_df['Затраты_Маркетинг'].sum():,.0f}")
    with col4:
        total_rev = filtered_df['Выручка'].sum()
        total_cost = filtered_df['Затраты_Маркетинг'].sum()
        romi = ((total_rev - total_cost) / total_cost * 100) if total_cost > 0 else 0
        st.metric("ROMI", f"{romi:.1f}%")

    st.markdown("---")

    # Графики
    row2_col1, row2_col2 = st.columns(2)

    with row2_col1:
        st.subheader("📈 Динамика выручки")
        fig_line = px.line(filtered_df.sort_values('Дата'), x='Дата', y='Выручка', color='Категория', markers=True)
        st.plotly_chart(fig_line, use_container_width=True)

    with row2_col2:
        st.subheader("🎯 Эффективность каналов")
        fig_bar = px.bar(filtered_df.groupby('Источник_Лида')['Выручка'].sum().reset_index(), 
                         x='Источник_Лида', y='Выручка', color='Источник_Лида', text_auto='.2s')
        st.plotly_chart(fig_bar, use_container_width=True)

    st.subheader("🗺️ Распределение: Регион -> Продукция")
    fig_sun = px.sunburst(filtered_df, path=['Регион', 'Категория'], values='Выручка', 
                          color='Выручка', color_continuous_scale='Blues')
    st.plotly_chart(fig_sun, use_container_width=True)

    # Таблица
    with st.expander("🔍 Посмотреть сырые данные"):
        st.dataframe(filtered_df, use_container_width=True)

except Exception as e:
    st.error(f"Ошибка загрузки данных: {e}")
    st.info("Убедитесь, что файл database_demo.csv находится в той же папке, что и app.py")
