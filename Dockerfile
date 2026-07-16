FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_PORT=8501

WORKDIR /app

COPY requirements-dashboard.txt ./
RUN python -m pip install --no-cache-dir --no-compile -r requirements-dashboard.txt \
    && find /usr/local/lib/python3.12/site-packages -type d \( -name "__pycache__" -o -name "tests" -o -name "test" \) -prune -exec rm -rf '{}' + \
    && find /usr/local/lib/python3.12/site-packages -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete \
    && rm -rf /root/.cache/pip

COPY dashboard ./dashboard
COPY data/processed/ibay_rentals_master.csv.gz ./data/processed/ibay_rentals_master.csv.gz

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health').read()"

CMD ["streamlit", "run", "dashboard/app.py"]
